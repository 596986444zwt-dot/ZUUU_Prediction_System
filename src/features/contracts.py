from pathlib import Path
from datetime import datetime, timezone, timedelta
from src.mos.contracts import SOURCES as BASE_SOURCES

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'FEATURE_V1'
OUTPUT = ROOT / 'database/phase7_feature_v1.db'
REPORTS = ROOT / 'docs/phase7'
SOURCES = dict(BASE_SOURCES)
SOURCES['phase6'] = (ROOT/'database/phase6_statistical_mos_v1_review.db', '47dcdde2b585b3edcc48fbbb9473bbadf0fa50c9f8b133ac41bd4d85a160077a')
PHASE6_SEMANTIC = '43ea07f9579a617c3117efbf8d93ab7ee5fe57d5e2ebf443ea12068171834415'
BJT = timezone(timedelta(hours=8))
EPOCH = '1970-01-01T00:00:00+00:00'
CONTRACT = dict(version=VERSION, formal_horizons=['T1','T2'], T0='LEGACY_T0_REFERENCE not materialized; production Intraday DEFERRED / BLOCKED_FOR_HISTORICAL_REPLAY',
    intraday='HISTORICAL_ZUUU_INGEST_NOT_OBSERVED', meteostat='TRAINING BLOCKED',
    eligibility='GROUND_TRUTH_ELIGIBILITY_RULE_V1; D+2 00:00 BJT; conservative eligibility, not observed receipt',
    missing='NULL; no imputation, interpolation or sample deletion; per-feature mask and reason',
    season={'DJF':[12,1,2],'MAM':[3,4,5],'JJA':[6,7,8],'SON':[9,10,11]},
    periods={'morning':list(range(6,12)),'afternoon':list(range(12,18)),'evening':list(range(18,24))},
    tie='earliest BJT hour for tied extrema/changes', radiation='source hourly backward-interval mean W/m2; sum*3600/1e6 MJ/m2; source target-hour labels preserved',
    precipitation='source hourly preceding-interval mm; sum across target-day hour labels; not a new interval alignment',
    availability='ECMWF estimated dissemination inherited; max of input availability; deterministic EPOCH means computable, not receipt',
    selection='physical definitions and source quality only; no target correlation or OOS feature selection',
    phase6='M6 benchmark-derived feature only; PHASE6_MOS_CANDIDATE remains NONE',
    revisions='exact run offsets 6/12/24h; prev latest earlier canonical same-model AVAILABLE run with availability<=issue; no search forward, no splice; full target-day curve required')

def utc(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('Timezone required')
    return t.astimezone(timezone.utc)

def require(condition, message):
    if not condition: raise ValueError(message)
