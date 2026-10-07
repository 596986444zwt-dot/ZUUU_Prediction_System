"""Source Guardian and strict training-admission evidence. No source mutations."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from src.data_v1.source_io import open_snapshot, sha256_file, sidecar_state
from src.data_v1.contracts import BJT, require, utc
from src.baseline.source import load
from src.baseline.storage import read, semantic_hash, TYPES, PREFIX
from src.audit.phase5_baseline_v1_audit import audit_connection
from src.audit.zuuu_target_v1_final_audit import compute_target_sha256
from src.audit.ecmwf_issue_rule_final_audit import validate_frozen_objects
from src.builders.ecmwf_archive_v1_freezer import semantic_hash as archive_hash
from .contracts import SOURCES, PHASE5_SEMANTIC, season

def fingerprints():
    out = {}
    for name, (path, expected) in SOURCES.items():
        actual = sha256_file(path)
        require(actual == expected, f'SOURCE_SHA_MISMATCH: {name}')
        out[name] = {'sha256': actual, 'sidecars': {k:list(v) for k,v in sidecar_state(path).items()}}
    return out

def guardian():
    before = fingerprints()
    checks, label_evidence = {}, {}
    for name, (path, expected) in SOURCES.items():
        conn = open_snapshot(path, expected)
        try:
            integrity = [r[0] for r in conn.execute('PRAGMA integrity_check')]
            fk = conn.execute('PRAGMA foreign_key_check').fetchall()
            require(integrity == ['ok'] and not fk, name + ': integrity/FK failure')
            schema = [dict(r) for r in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
            tables = [r['name'] for r in schema if r['type'] == 'table']
            counts = {t: conn.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0] for t in tables}
            manifests = {t: [dict(r) for r in conn.execute('SELECT * FROM "'+t+'"')] for t in tables if 'manifest' in t}
            semantic = {}
            if name == 'production':
                validate_frozen_objects(conn)
                targets = conn.execute('SELECT * FROM zuuu_target_v1 ORDER BY business_date_bjt').fetchall()
                semantic['TARGET_V1'] = compute_target_sha256(targets)
                require(semantic['TARGET_V1'] == 'b8548609e64d10b787fc09d9fddb28e20acd1808b165f47e62b574580cc67f3e', 'Target semantic mismatch')
                archive = [dict(r) for r in conn.execute('SELECT * FROM ecmwf_archive_v1 ORDER BY run_time_utc')]
                semantic['ECMWF_ARCHIVE'] = archive_hash(archive)
                require(semantic['ECMWF_ARCHIVE'] == 'd98fe65fc17948b8418c69247c2d714b255f815e36958645076d61403a282ac7', 'Archive semantic mismatch')
                rule = dict(conn.execute('SELECT * FROM ecmwf_issue_rule_v1').fetchone())
                semantic['ECMWF_ISSUE_RULE'] = hashlib.sha256(rule['rule_payload_json'].encode()).hexdigest()
                require(semantic['ECMWF_ISSUE_RULE'] == '7fdbf14f0f105870f06e9e4d29753718d8cfebfe98d7608c9f916ea441455504', 'Issue rule mismatch')
                require(counts['zuuu_target_v1'] == 729 and counts['ecmwf_archive_v1'] == 3411 and counts['ecmwf_issue_rule_v1'] == 1, 'Production count mismatch')
                target_columns = [r[1] for r in conn.execute('PRAGMA table_info(zuuu_target_v1)')]
                bronze_columns = [r[1] for r in conn.execute('PRAGMA table_info(zuuu_raw_metar)')]
                raw = [dict(r) for r in conn.execute('SELECT id,observation_time_utc,ingest_time_utc,created_at_utc FROM zuuu_raw_metar')]
                dates = {r['business_date_bjt'] for r in targets}
                window = [r for r in raw if utc(r['observation_time_utc']).astimezone(BJT).date().isoformat() in dates]
                raw_by_id = {r['id']: r for r in raw}
                lineage = []
                for t in targets:
                    ids = json.loads(t['all_bronze_raw_ids'])
                    require(all(i in raw_by_id for i in ids), 'Missing Bronze label lineage')
                    ingests = [raw_by_id[i]['ingest_time_utc'] for i in ids]
                    lineage.append({'business_date_bjt': t['business_date_bjt'], 'final_tmax': t['daily_tmax_c'],
                        'candidate_created_at': t['source_candidate_created_at_utc'], 'frozen_at': t['frozen_at_utc'],
                        'first_lineage_import': min(ingests), 'last_lineage_import': max(ingests),
                        'historical_settled_at': None, 'settlement_verified': False,
                        'availability_status': 'FINAL_LABEL_HISTORICAL_SETTLEMENT_NOT_PROVEN'})
                label_evidence = {'target_columns': target_columns, 'bronze_columns': bronze_columns,
                    'historical_bronze_rows': len(window), 'ingest_day_counts': dict(Counter(utc(r['ingest_time_utc']).date().isoformat() for r in window)),
                    'historical_ingest_equals_created_rows': sum(r['ingest_time_utc'] == r['created_at_utc'] for r in window),
                    'target_candidate_created_range': [min(r['source_candidate_created_at_utc'] for r in targets), max(r['source_candidate_created_at_utc'] for r in targets)],
                    'target_frozen_range': [min(r['frozen_at_utc'] for r in targets), max(r['frozen_at_utc'] for r in targets)],
                    'verified_historical_settlement_days': 0, 'daily_lineage': lineage,
                    'reason': 'Day end is an event boundary, not proof of receipt/final correction settlement. Import/candidate/freeze timestamps postdate every backtest issue. No evidenced historical settlement rule exists in frozen inputs.',
                    'prior_discovery': 'docs/intraday/INTRADAY_AVAILABILITY_AUDIT.json'}
                manifests['ecmwf_issue_rule_v1'] = [rule]
            elif name == 'auxiliary':
                require(counts['aux_v1_solar_time'] == 17496 and counts['aux_v1_meteostat_hourly'] == 227097, 'Auxiliary row count mismatch')
                # Existing audited schema and manifest checks without its disk connector.
                from src.audit.phase3_final_audit import audit_aux_database, audit_solar, audit_meteostat, audit_triggers
                errors = []
                factory = conn.row_factory
                conn.row_factory = None  # Legacy auxiliary auditor expects tuples.
                audit_aux_database(conn, errors)
                audit_solar(conn, errors)
                audit_meteostat(conn, errors)
                audit_triggers(conn, errors)
                conn.row_factory = factory
                require(not errors, 'Auxiliary audit failure: '+str(errors))
                semantic['status'] = 'No frozen auxiliary semantic SHA supplied; physical SHA and existing manifest audit pinned'
            elif name == 'phase4':
                from src.audit.phase4_data_v1_audit import audit_connection as audit4
                result = audit4(conn, reconstruct=False)
                semantic['DATA_V1'] = result['dataset_semantic_sha256']
                require(semantic['DATA_V1'] == '4eea080168f102f30314b0b8a64123af533923fd7f4b7e7c6e9312b5582835dd', 'Phase4 semantic mismatch')
            elif name == 'phase5':
                for table, columns in TYPES.items():
                    require(set(r[1] for r in conn.execute('PRAGMA table_info('+PREFIX+table+')')) == set(columns), 'Phase5 schema mismatch')
                data = read(conn)
                semantic['BASELINE'] = semantic_hash(data)
                require(semantic['BASELINE'] == PHASE5_SEMANTIC, 'Phase5 semantic mismatch')
            checks[name] = {'physical_sha256': expected, 'integrity_check': integrity, 'foreign_key_check': len(fk),
                            'schema': schema, 'row_counts': counts, 'manifests': manifests, 'semantic': semantic}
        finally:
            conn.close()
    require(fingerprints() == before, 'SOURCE_MUTATION')
    return before, checks, label_evidence

def load_samples():
    reconstructed, exclusions, _ = load('PHASE6_SOURCE_AUDIT')
    conn = open_snapshot(*SOURCES['phase5'])
    try:
        data = read(conn)
        recorded = json.loads(data['manifest'][0]['source_sha256_json'])
        actual = {k:v for k,v in fingerprints().items() if k != 'phase5'}
        require(set(recorded) == set(actual) and all(recorded[k]['sha256'] == actual[k]['sha256'] for k in actual), 'Phase5 source physical manifest mismatch')
        # The frozen Phase5 manifest records its build-time sidecar inventory.
        # Current empty WAL / SHM can differ without changing the pinned main DB.
        # Guardian pins current sidecars before/after; never rewrite old manifest.
        audit_connection(conn, (reconstructed, exclusions, recorded))
        frozen = data['prediction']
    finally:
        conn.close()
    # Explicit Phase6 projection contains no prohibited weather/observation features.
    samples = []
    for r in frozen:
        start = datetime.fromisoformat(r['business_date_bjt']).replace(tzinfo=BJT)
        first = start.astimezone(timezone.utc)
        run = utc(r['selected_ecmwf_run_time_utc'])
        samples.append(dict(business_date_bjt=r['business_date_bjt'], horizon=r['horizon'],
            issue_time_utc=r['issue_time_utc'], selected_ecmwf_run_time_utc=r['selected_ecmwf_run_time_utc'],
            selected_ecmwf_source_available_time_utc=r['selected_ecmwf_source_available_time_utc'],
            ecmwf_availability_semantics=r['ecmwf_availability_semantics'], raw_ecmwf_tmax=r['ecmwf_raw_tmax_c'],
            observed_tmax=r['target_tmax_c'], raw_error=r['ecmwf_raw_tmax_c']-r['target_tmax_c'],
            month=r['month'], year=r['year'], season=season(r['month']),
            lead_start_hours=(first-run).total_seconds()/3600, lead_end_hours=(first-run).total_seconds()/3600+23,
            day_end_utc=(start+timedelta(days=1)).astimezone(timezone.utc).isoformat(),
            settled_at_utc=None, settlement_verified=False))
    return samples, exclusions
