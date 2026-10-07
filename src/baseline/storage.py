"""New-artifact-only schema and deterministic semantic projection."""
import hashlib
import json
from .contracts import CONTRACT
from .metrics import calculate

PREFIX = 'phase5_baseline_v1_'
PREDICTION_TYPES = {
    **dict.fromkeys(('business_date_bjt','horizon','issue_time_utc','selected_ecmwf_run_time_utc',
      'selected_ecmwf_source_available_time_utc','ecmwf_tmax_first_time_utc','ecmwf_tmax_last_time_utc',
      'status','source_data_version','source_phase4_semantic_sha256','ecmwf_availability_semantics',
      'season','target_bin','created_at_utc'), 'TEXT NOT NULL'),
    **dict.fromkeys(('hour_count','ecmwf_tmax_occurrence_count','month','year'), 'INTEGER NOT NULL'),
    **dict.fromkeys(('ecmwf_raw_tmax_c','target_tmax_c','error_c','absolute_error_c','squared_error_c'), 'REAL NOT NULL'),
}
METRIC_TYPES = {k: ('INTEGER NOT NULL' if k=='n' or k.endswith('_count') else 'REAL') for k in calculate([])}
TYPES = {
    'prediction': PREDICTION_TYPES,
    'metrics': {'horizon':'TEXT NOT NULL', **METRIC_TYPES},
    'group_metrics': {'horizon':'TEXT NOT NULL','group_type':'TEXT NOT NULL','group_value':'TEXT NOT NULL', **METRIC_TYPES},
    'exclusion': {**dict.fromkeys(('business_date_bjt','horizon','sample_status','exclusion_reason','selected_ecmwf_run_time_utc'), 'TEXT NOT NULL'), 'trajectory_valid_hours':'INTEGER NOT NULL','trajectory_expected_hours':'INTEGER NOT NULL'},
    'manifest': dict.fromkeys(('baseline_version','contract_json','source_sha256_json','source_phase4_semantic_sha256',
        'semantic_sha256','build_status','created_at_utc','implementation_sha256_json'), 'TEXT NOT NULL'),
}
KEYS = {'prediction':('business_date_bjt','horizon'), 'metrics':('horizon',),
    'group_metrics':('horizon','group_type','group_value'), 'exclusion':('business_date_bjt','horizon'), 'manifest':('baseline_version',)}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def semantic_hash(data):
    payload = {'contract':CONTRACT}
    for table in ('prediction','metrics','group_metrics','exclusion'):
        columns = sorted(set(TYPES[table])-{'created_at_utc'})
        payload[table] = [{c:r[c] for c in columns} for r in sorted(data[table], key=lambda r:tuple(r[k] for k in KEYS[table]))]
    return hashlib.sha256(canonical(payload).encode()).hexdigest()

def statements():
    result = []
    for table, types in TYPES.items():
        fields = [f'{k} {v}' for k,v in types.items()]
        fields.append('PRIMARY KEY('+','.join(KEYS[table])+')')
        if table in ('prediction','metrics','group_metrics','exclusion'):
            fields.append("CHECK(horizon IN ('T0','T1','T2'))")
        if table in ('prediction','group_metrics'):
            fields.append(f'FOREIGN KEY(horizon) REFERENCES {PREFIX}metrics(horizon)')
        if table=='prediction':
            fields += ["CHECK(hour_count=24 AND status='ELIGIBLE')", 'CHECK(absolute_error_c>=0 AND squared_error_c>=0)', 'CHECK(ecmwf_tmax_occurrence_count BETWEEN 1 AND 24)']
        result.append(f'CREATE TABLE {PREFIX}{table} ('+','.join(fields)+')')
    for table in TYPES:
        for action in ('UPDATE','DELETE'):
            result.append(f"CREATE TRIGGER {PREFIX}{table}_{action.lower()} BEFORE {action} ON {PREFIX}{table} BEGIN SELECT RAISE(ABORT,'Frozen baseline: mutation prohibited'); END")
    return result

def insert(conn, table, rows):
    columns = list(TYPES[table])
    conn.executemany(f'INSERT INTO {PREFIX}{table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
        [tuple(r[c] for c in columns) for r in rows])

def read(conn):
    return {t:[dict(r) for r in conn.execute(f'SELECT * FROM {PREFIX}{t} ORDER BY '+','.join(KEYS[t]))] for t in TYPES}
