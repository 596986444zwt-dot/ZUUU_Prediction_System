import json
from src.mos.data import guardian, fingerprints, load_samples
from src.data_v1.source_io import sha256_file
from src.data_v1.contracts import require
from .contracts import ROOT, PRESERVED, RULE, LAG_HOURS
from .eligibility import eligibility_time

def preserved_fingerprints():
    expected = json.loads(PRESERVED.read_text(encoding='utf-8-sig'))
    actual = {p:sha256_file(p) for p in expected}
    require(actual == expected, 'Original BLOCKED assets changed')
    return actual

def load():
    rows, exclusions = load_samples()
    projected = []
    for row in rows:
        # Remove settlement assertions from old adapter; keep original values/lineage.
        r = {k:v for k,v in row.items() if k not in ('settled_at_utc','settlement_verified')}
        r.update(label_business_date=r['business_date_bjt'], label_eligibility_time_bjt=eligibility_time(r['business_date_bjt']).isoformat(),
                 eligibility_rule=RULE, eligibility_lag=LAG_HOURS,
                 label_eligibility_semantics='CAUSAL_DAILY_LABEL_ELIGIBILITY_NOT_OBSERVED_RECEIPT')
        projected.append(r)
    return projected, exclusions
