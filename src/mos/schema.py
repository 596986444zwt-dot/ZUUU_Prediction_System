"""New isolated Phase6 artifact; append-only attempts include explicit abstentions."""
import sqlite3

PREFIX = 'phase6_mos_'
KEYS = {'sample': ('business_date_bjt', 'horizon'),
        'bias_state': ('target_business_date', 'horizon', 'model'),
        'prediction': ('target_business_date', 'horizon', 'model'),
        'metric': ('model', 'horizon', 'slice_type', 'slice_value'),
        'slice_metric': ('model', 'horizon', 'slice_type', 'slice_value'),
        'manifest': ('model_version',)}

REAL_NAMES = {'bias_value', 'mos_continuous_tmax', 'mos_error', 'absolute_mos_error',
              'raw_ecmwf_tmax', 'observed_tmax', 'raw_error', 'absolute_raw_error'}

def statements(data):
    sql = []
    for table, keys in KEYS.items():
        row = data[table][0]
        columns = []
        for name, value in row.items():
            dtype = 'REAL' if name in REAL_NAMES or isinstance(value, float) or (table in ('metric','slice_metric') and value is None) else 'INTEGER' if isinstance(value, (int, bool)) else 'TEXT'
            columns.append(f'{name} {dtype}' + (' NOT NULL' if name in keys else ''))
        columns.append('PRIMARY KEY('+','.join(keys)+')')
        if table in ('sample', 'prediction', 'bias_state'):
            columns.append("CHECK(horizon IN ('T0','T1','T2'))")
        if table in ('prediction', 'bias_state'):
            columns.append("CHECK(model IN ('M1','M2','M3','M4','M5','M6'))")
            columns.append('CHECK(training_n>=0)')
            columns.append("CHECK((status='PREDICTED' AND bias_value IS NOT NULL AND training_n>=7) OR (status IN ('BLOCKED_LABEL_AVAILABILITY','INSUFFICIENT_HISTORY') AND bias_value IS NULL AND training_n=0))")
        if table == 'bias_state':
            columns.append(f'FOREIGN KEY(target_business_date,horizon) REFERENCES {PREFIX}sample(business_date_bjt,horizon)')
        if table == 'prediction':
            columns.append(f'FOREIGN KEY(target_business_date,horizon,model) REFERENCES {PREFIX}bias_state(target_business_date,horizon,model)')
            columns.append("CHECK((status='PREDICTED' AND mos_continuous_tmax IS NOT NULL AND mos_error IS NOT NULL) OR (status!='PREDICTED' AND mos_continuous_tmax IS NULL AND mos_error IS NULL))")
        sql.append(f'CREATE TABLE {PREFIX}{table} ('+','.join(columns)+')')
    for table in KEYS:
        for action in ('UPDATE', 'DELETE'):
            sql.append(f"CREATE TRIGGER {PREFIX}{table}_{action.lower()} BEFORE {action} ON {PREFIX}{table} BEGIN SELECT RAISE(ABORT,'Phase6 immutable artifact'); END")
        sql.append(f"CREATE TRIGGER {PREFIX}{table}_seal BEFORE INSERT ON {PREFIX}{table} WHEN EXISTS(SELECT 1 FROM {PREFIX}manifest) BEGIN SELECT RAISE(ABORT,'Phase6 artifact sealed'); END")
    return sql

def populate(conn, data):
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('BEGIN IMMEDIATE')
    try:
        for sql in statements(data):
            conn.execute(sql)
        for table in KEYS:
            rows = data[table]
            columns = list(rows[0])
            conn.executemany(f'INSERT INTO {PREFIX}{table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                             [tuple(r[c] for c in columns) for r in rows])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise

def read(conn):
    return {t: [dict(r) for r in conn.execute(f'SELECT * FROM {PREFIX}{t} ORDER BY '+','.join(keys))] for t, keys in KEYS.items()}
