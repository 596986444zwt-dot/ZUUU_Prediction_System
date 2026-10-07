import copy
from src.mos.contracts import ROOT, SOURCES, MODELS, CONTRACT as ORIGINAL_CONTRACT

VERSION = 'STATISTICAL_MOS_V1_CAUSAL_REVIEW_1'
RULE = 'GROUND_TRUTH_ELIGIBILITY_RULE_V1'
LAG_HOURS = 24
OUTPUT = ROOT / 'database/phase6_statistical_mos_v1_review.db'
REPORTS = ROOT / 'docs/phase6/blocker_review'
PRESERVED = REPORTS / 'PRESERVED_BLOCKED_ASSETS.json'
CONTRACT = copy.deepcopy(ORIGINAL_CONTRACT)
CONTRACT.update(version=VERSION, eligibility_rule=RULE, eligibility_lag_hours_after_day_end=LAG_HOURS,
    training_gate='same horizon; label day < issue BJT date and target date; day_end < issue; D+2 00:00 BJT <= issue; historical forecast issue < current issue',
    label_availability='CAUSAL_DAILY_LABEL_ELIGIBILITY_NOT_OBSERVED_RECEIPT; no exact historical settlement/receipt claim',
    trailing_anchor='issue BJT calendar date minus 2 days; seven/thirty calendar days ending at that anchor',
    M2='7 calendar days ending at latest causally eligible date; minimum 7 else expanding M1',
    M3='30 calendar days ending at latest causally eligible date; minimum 7 else expanding M1',
    Intraday='HISTORICAL_ZUUU_INGEST_NOT_OBSERVED; BLOCKED_FOR_STRICT_INTRADAY_REPLAY remains unchanged')
