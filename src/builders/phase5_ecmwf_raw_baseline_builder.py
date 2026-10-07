"""Build only a new isolated artifact; validate before no-overwrite publication."""
import argparse
import csv
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from src.baseline.contracts import ROOT, OUTPUT, SOURCES, CONTRACT, VERSION, PHASE4_SEMANTIC
from src.baseline.source import load, fingerprints
from src.baseline.metrics import summarize
from src.baseline.storage import statements, insert, read, semantic_hash, canonical
from src.data_v1.source_io import sha256_file, open_snapshot
from src.data_v1.contracts import require
from src.audit.phase5_baseline_v1_audit import audit_connection, audit

def prepare():
    created = datetime.now(timezone.utc).isoformat()
    predictions, exclusions, sources = load(created)
    metrics, groups = summarize(predictions)
    data = {'prediction':predictions,'exclusion':exclusions,'metrics':metrics,'group_metrics':groups}
    digest = semantic_hash(data)
    files = sorted((ROOT/'src/baseline').glob('*.py')) + [Path(__file__), ROOT/'src/audit/phase5_baseline_v1_audit.py',
        ROOT/'src/data_v1/source_io.py',ROOT/'src/data_v1/hashing.py',ROOT/'src/data_v1/schema.py']
    data['manifest'] = [dict(baseline_version=VERSION,contract_json=canonical(CONTRACT),source_sha256_json=canonical(sources),
        source_phase4_semantic_sha256=PHASE4_SEMANTIC,semantic_sha256=digest,build_status='VALIDATED',created_at_utc=created,
        implementation_sha256_json=canonical({str(p.resolve().relative_to(ROOT)).replace('\\','/'):sha256_file(p) for p in files}))]
    return data, (predictions, exclusions, sources)

def populate(conn, data, expected, fail_hook=None):
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('BEGIN IMMEDIATE')
    try:
        for sql in statements():
            conn.execute(sql)
        for table in ('metrics','prediction','group_metrics','exclusion','manifest'):
            insert(conn, table, data[table])
        if fail_hook:
            fail_hook()
        result = audit_connection(conn, expected)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise

def build(commit=False, output=OUTPUT, expected_semantic=None):
    output = Path(output).resolve()
    require(output not in {p.resolve() for p,_ in SOURCES.values()}, 'Source database is READ ONLY')
    require(not output.exists(), 'REFUSE OVERWRITE: Phase5 database already exists')
    data, expected = prepare()
    digest = data['manifest'][0]['semantic_sha256']
    require(expected_semantic is None or digest==expected_semantic, 'Dry-run/commit semantic mismatch')
    if not commit:
        conn = sqlite3.connect(':memory:')
        try:
            result = populate(conn,data,expected)
        finally:
            conn.close()
        require(fingerprints()==expected[2], 'Source modified during dry-run')
        result['mode'] = 'DRY_RUN'
        return result
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=output.name+'.',suffix='.building',dir=output.parent)
    os.close(fd)
    staging = Path(name)
    try:
        conn = sqlite3.connect(staging)
        try:
            populate(conn,data,expected)
        finally:
            conn.close()
        result = audit(staging)
        require(result['phase5_semantic_sha256']==digest, 'Staging semantic mismatch')
        require(fingerprints()==expected[2], 'Source changed before publication')
        # Windows FlushFileBuffers requires a writable handle; only our staging
        # artifact is opened this way, never any frozen source or final file.
        with staging.open('r+b') as handle:
            os.fsync(handle.fileno())
        # Same-volume hard link creates destination atomically and fails if it exists.
        # No replace/rename-overwrite of a preexisting formal artifact is possible.
        os.link(staging,output)
    finally:
        # Only the unique temporary file created by this invocation is removed.
        staging.unlink(missing_ok=True)
    result = audit(output)
    result['mode'] = 'COMMIT'
    return result

def export_reports(result, output=OUTPUT):
    conn = open_snapshot(output, result['phase5_physical_sha256'])
    try:
        data = read(conn)
    finally:
        conn.close()
    reports = ROOT/'reports/phase5'
    docs = ROOT/'docs/phase5'
    reports.mkdir(parents=True,exist_ok=True)
    docs.mkdir(parents=True,exist_ok=True)
    for table, filename in (('prediction','predictions'),('metrics','metrics'),('group_metrics','group_metrics')):
        with (reports/f'phase5_baseline_{filename}.csv').open('w',encoding='utf-8',newline='') as handle:
            writer = csv.DictWriter(handle,fieldnames=list(data[table][0]))
            writer.writeheader()
            writer.writerows(data[table])
    summary = {**result,'contract':CONTRACT,'authoritative_artifact':str(Path(output).resolve())}
    (reports/'phase5_baseline_summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    (docs/'PHASE5_FINAL_AUDIT.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    (docs/'PHASE5_SCHEMA.sql').write_text(';\n\n'.join(statements())+';\n',encoding='utf-8')

def terminal(result):
    print('PHASE 5 ECMWF RAW BASELINE V1')
    print('Source Phase4 semantic SHA:',PHASE4_SEMANTIC)
    print('Phase5 semantic SHA:',result['phase5_semantic_sha256'])
    print('T0:',CONTRACT['T0'])
    print('T1/T2:',CONTRACT['T1_T2'])
    for row in result['metrics']:
        print(row['horizon'], 'N=',row['n'])
        for key in ('bias_c','mae_c','rmse_c','median_ae_c','p90_ae_c'):
            print(f'  {key}: {row[key]:.9f}')
        for label in ('0_5','1','2'):
            print(f'  AE<={label.replace("_",".")}: {row[f"ae_le_{label}_count"]} / {row["n"]} = {row[f"ae_le_{label}_rate"]:.9f}')
    print('Known excluded sample: 2025-08-07 / T2, 14/24')
    print('Cross-vintage violations:',result['cross_vintage_violations'])
    print('Leakage violations:',result['leakage_violations'])
    print('Source DB modified: NO')
    print('PHASE 5 FINAL AUDIT: PASS')
    print('STATUS:', 'DRY RUN VALIDATED' if result.get('mode')=='DRY_RUN' else 'READY FOR INDEPENDENT ACCEPTANCE')

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--dry-run',action='store_true')
    modes.add_argument('--commit',action='store_true')
    parser.add_argument('--expect-semantic-sha')
    args = parser.parse_args()
    try:
        outcome = build(args.commit, expected_semantic=args.expect_semantic_sha)
        if args.commit:
            export_reports(outcome)
        terminal(outcome)
    except Exception as exc:
        print(f'PHASE 5 FINAL AUDIT: FAIL: {exc}')
        raise SystemExit(1)
