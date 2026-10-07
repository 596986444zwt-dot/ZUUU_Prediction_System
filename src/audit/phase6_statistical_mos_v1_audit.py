"""Read-only Phase6 verification; BLOCKED experiments cannot become PASS."""
import argparse
import json
import math
from src.mos.contracts import ROOT, OUTPUT, CONTRACT, VERSION
from src.mos.data import fingerprints, load_samples
from src.mos.walk_forward import evaluate
from src.mos.metrics import summarize
from src.mos.schema import read, statements
from src.mos.semantic_hash import semantic_hash, canonical
from src.data_v1.source_io import open_snapshot, sha256_file
from src.data_v1.contracts import require, utc

def audit_connection(conn, samples=None):
    require([r[0] for r in conn.execute('PRAGMA integrity_check')] == ['ok'], 'Phase6 integrity failure')
    require(not conn.execute('PRAGMA foreign_key_check').fetchall(), 'Phase6 foreign key failure')
    data = read(conn)
    require(len(data['manifest']) == 1, 'Phase6 manifest count')
    manifest = data['manifest'][0]
    require(manifest['model_version'] == VERSION and manifest['contract_json'] == canonical(CONTRACT), 'Phase6 contract mismatch')
    require(json.loads(manifest['source_sha_before_json']) == fingerprints(), 'Phase6 source fingerprint mismatch')
    implementation = json.loads(manifest['implementation_sha256_json'])
    required_files = {p.relative_to(ROOT).as_posix() for p in (ROOT/'src/mos').glob('*.py')}
    required_files.update(('src/builders/phase6_statistical_mos_v1_builder.py','src/audit/phase6_statistical_mos_v1_audit.py','tests/test_phase6_statistical_mos_v1.py'))
    require(set(implementation) == required_files and all(sha256_file(ROOT/p) == h for p,h in implementation.items()), 'Phase6 implementation provenance mismatch')
    if samples is None:
        samples, _ = load_samples()
    def project(rows):
        return sorted((canonical({k:int(v) if isinstance(v,bool) else v for k,v in r.items() if k != 'created_at'}) for r in rows))
    require(project(samples) == project(data['sample']), 'Phase6 sample projection differs from frozen sources')
    predictions, states = evaluate(samples, 'INDEPENDENT_REBUILD')
    require(project(predictions) == project(data['prediction']), 'Phase6 prediction replay mismatch')
    require(project(states) == project(data['bias_state']), 'Phase6 state replay mismatch')
    metrics, slices = summarize(predictions)
    require(data['metric'] == sorted(metrics, key=lambda r: (r['model'],r['horizon'],r['slice_type'],r['slice_value'])), 'Metric reproduction failure')
    require(data['slice_metric'] == sorted(slices, key=lambda r: (r['model'],r['horizon'],r['slice_type'],r['slice_value'])), 'Slice reproduction failure')
    # Independent lineage arithmetic and time checks, separate from bias selector.
    by_key = {(r['business_date_bjt'],r['horizon']):r for r in samples}
    for state in data['bias_state']:
        lineage = json.loads(state['training_lineage_json'])
        require(len(lineage) == state['training_n'], 'Training lineage count')
        for r in lineage:
            past = by_key[r['date'],r['horizon']]
            require(past['settlement_verified'] and utc(past['settled_at_utc']) < utc(state['training_cutoff']), 'Unsettled/future label leakage')
            require(utc(past['day_end_utc']) <= utc(past['settled_at_utc']) and utc(past['issue_time_utc']) < utc(state['training_cutoff']), 'Invalid training chronology')
            require(r['horizon'] == state['horizon'] and r['date'] != state['target_business_date'], 'Horizon/current-target leakage')
        if state['bias_value'] is not None and state['model'] != 'M6':
            require(math.isclose(state['bias_value'], math.fsum(r['raw_error'] for r in lineage)/len(lineage), abs_tol=1e-12), 'Bias arithmetic failure')
    if not any(r['settlement_verified'] for r in samples):
        require(all(r['status'] == 'BLOCKED_LABEL_AVAILABILITY' and r['mos_continuous_tmax'] is None for r in predictions), 'Missing availability gate')
        require(manifest['build_status'] == 'BLOCKED' and manifest['candidate'] == 'NONE', 'Unsupported PASS/candidate')
    digest = semantic_hash(data)
    require(digest == manifest['semantic_sha256'], 'Phase6 semantic hash mismatch')
    # Verify actual schema/immutability declarations, not just table contents.
    for expected in statements(data):
        require(conn.execute('SELECT 1 FROM sqlite_master WHERE sql=?', (expected,)).fetchone() is not None, 'Phase6 schema/trigger mismatch')
    return {'artifact_audit': 'PASS', 'PHASE6_BUILD_STATUS': manifest['build_status'],
        'PHASE6_MOS_CANDIDATE': manifest['candidate'],
        'NEXT_PHASE_STATUS': 'BLOCKED_FOR_PHASE7_FEATURE_ENGINEERING' if manifest['build_status'] == 'BLOCKED' else 'READY_FOR_PHASE7_FEATURE_ENGINEERING',
        'phase6_semantic_sha256': digest, 'integrity_check': 'ok', 'foreign_key_check': 0,
        'sample_rows': len(samples), 'prediction_attempts': len(predictions),
        'valid_mos_oos_predictions': sum(r['status'] == 'PREDICTED' for r in predictions),
        'metrics': metrics, 'leakage_violations': 0, 'source_mutation': False}

def audit(path=OUTPUT):
    before = fingerprints()
    physical = sha256_file(path)
    conn = open_snapshot(path, physical)
    try:
        report = audit_connection(conn)
    finally:
        conn.close()
    require(sha256_file(path) == physical and before == fingerprints(), 'Artifact/source mutated during audit')
    return dict(report, phase6_physical_sha256=physical, SOURCE_SHA_BEFORE=before, SOURCE_SHA_AFTER=fingerprints())

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default=str(OUTPUT))
    args = parser.parse_args()
    report = audit(args.database)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['PHASE6_BUILD_STATUS'] == 'PASS' else 2)
