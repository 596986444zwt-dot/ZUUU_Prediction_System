"""Isolated artifact with normalized, queryable training-label eligibility lineage."""
import hashlib
import sqlite3
from src.mos.schema import KEYS, PREFIX, statements as base_statements, populate as base_populate, read
from src.mos.semantic_hash import canonical
from .contracts import CONTRACT

ELIGIBILITY_VIEW = '''CREATE VIEW phase6_mos_label_eligibility_audit AS
SELECT b.target_business_date, b.horizon, b.model,
       l.business_date_bjt AS label_business_date,
       l.label_eligibility_time_bjt,
       b.training_cutoff AS prediction_issue_time,
       b.eligibility_rule, b.eligibility_lag,
       (julianday(l.label_eligibility_time_bjt)<=julianday(b.training_cutoff)
        AND julianday(l.day_end_utc)<julianday(b.training_cutoff)
        AND l.business_date_bjt<date(b.training_cutoff,'+8 hours')
        AND l.business_date_bjt<b.target_business_date
        AND julianday(l.issue_time_utc)<julianday(b.training_cutoff)) AS is_label_eligible,
       l.raw_error
FROM phase6_mos_bias_state b, json_each(b.training_dates_json) j
JOIN phase6_mos_sample l ON l.business_date_bjt=j.value AND l.horizon=b.horizon'''

def statements(data):
    return base_statements(data) + [ELIGIBILITY_VIEW]

def populate(conn, data):
    base_populate(conn, data)
    conn.execute(ELIGIBILITY_VIEW)
    conn.commit()

def semantic_hash(data):
    digest = hashlib.sha256()
    digest.update(canonical(CONTRACT).encode()+b'\n')
    for table, keys in KEYS.items():
        if table == 'manifest':
            continue
        digest.update(table.encode()+b'\n')
        for row in sorted(data[table], key=lambda r:tuple(r[k] for k in keys)):
            digest.update(canonical({k:v for k,v in row.items() if k != 'created_at'}).encode()+b'\n')
    return digest.hexdigest()
