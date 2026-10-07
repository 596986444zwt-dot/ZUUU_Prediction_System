"""Specifications frozen before any outer OOS predictions."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / 'docs/phase8'
OUTPUT = ROOT / 'database/phase8_machine_learning_v1.db'
VERSION = 'MACHINE_LEARNING_V1'
SEED = 20261002
MIN_HISTORY = 180
INNER_MIN_HISTORY = 120
BLOCK_DAYS = 14
INNER_DAYS = 14
FAMILIES = ('RIDGE','RANDOM_FOREST','LIGHTGBM','XGBOOST','CATBOOST')
GRIDS = {
 'RIDGE': [{'alpha': a} for a in (1.0,10.0,100.0)],
 'RANDOM_FOREST': [dict(n_estimators=80,max_depth=d,min_samples_leaf=l,max_features=.7) for d,l in ((4,20),(6,10))],
 'LIGHTGBM': [dict(n_estimators=80,num_leaves=n,max_depth=d,learning_rate=.05,min_child_samples=20,colsample_bytree=.8,reg_lambda=2.0) for n,d in ((7,3),(15,4))],
 'XGBOOST': [dict(n_estimators=80,max_depth=d,learning_rate=.05,min_child_weight=10,subsample=1.0,colsample_bytree=.8,reg_lambda=5.0) for d in (2,3)],
 'CATBOOST': [dict(iterations=80,depth=d,learning_rate=.05,l2_leaf_reg=5.0) for d in (3,4)]}
SOURCES = {
 'database/zuuu_prediction.db':'2e0149050ed11fe2d750b6f4798f7a51b313bf8f59bb671da5a076daeb124367',
 'database/phase3_auxiliary_v1.db':'ec8defe1778a66ed9ea20dc9e3825579de685c1818862a3eef113bba9a624f22',
 'database/phase4_data_v1.db':'9d1901aa0f6c9c3106450cb9d41d885797745f087a80c497cff082f67094a892',
 'database/phase5_ecmwf_raw_baseline_v1.db':'851122d90d2202f6259d26d6ca08e35960ef295ad624a9464d020c38e6c507f5',
 'database/phase6_statistical_mos_v1.db':'11b6e4212f1b93fc86ee887f7941ec2bbe6eb00512cf7ebe1e70b4928646130c',
 'database/phase6_statistical_mos_v1_review.db':'47dcdde2b585b3edcc48fbbb9473bbadf0fa50c9f8b133ac41bd4d85a160077a',
 'database/phase7_feature_v1.db':'2daba6de94b4d08863f95e19f089c5169ae9b51ed3f7994a5621835bfa1fe893'}
CONTRACT = dict(version=VERSION,feature_version='FEATURE_V1',horizons=['T1','T2'],minimum_training_history=MIN_HISTORY,
 inner_minimum_training_history=INNER_MIN_HISTORY,outer_refit_target_calendar_days=BLOCK_DAYS,
 outer='chronological expanding blocks; daily predictions; state frozen at block-first issue; no mid-block refit',
 inner='two latest disjoint 14-sample chronological validation blocks in eligible outer history; training cutoff is each validation block first issue; D+2 eligibility applies',
 target='observed Tmax minus raw ECMWF; predict raw + learned continuous residual; no integer rounding',
 eligibility='GROUND_TRUTH_ELIGIBILITY_RULE_V1: label D+2 00:00 Asia/Shanghai <= fit cutoff; historical issue < cutoff; target date < current target date',
 preprocessing='all-null exclusion, then exact duplicate exclusion, then constant variance <=1e-12; X_train only; lexicographic order; no correlation filter',
 duplicate='drop hist_horizon_bias_c only when training values equal hist_expanding_bias_c; remaining exact X_train duplicate keep lexicographically first; never inspect y',
 ridge='training-only median; indicators for retained columns with training NULL; training-only population mean/std scaling; SVD Ridge',
 trees='native NaN; no imputation/scaling; no categorical target encoding; deterministic seed and single CPU thread',
 parameter_selection='small grids fixed below; minimum pooled inner MAE; ties use grid order; no outer target access; no early stopping',
 grids=GRIDS,random_seed=SEED,
 candidate='descriptive future-phase candidate, not retroactive selected-model OOS or system champion: common-subset N>=365; MAE gain >=0.05 C over BOTH raw and M6; RMSE <= both; abs bias <= abs(raw bias)+0.10 C; year/season slices N>=30 may worsen <=0.15 C versus either benchmark; if qualified MAE differences <=0.02 C prefer family complexity order; NONE allowed',
 radiation='inherit preceding-hour means and amounts, target-hour labels 00..23; never realign or difference',
 prohibited=['MODEL_T0_V1','Probability','Ensemble','Realtime','GUI','Meteostat','unproven Intraday'],
 T0='HISTORICAL_ZUUU_INGEST_NOT_OBSERVED; BLOCKED / DEFERRED')
def utc(value):
 t=datetime.fromisoformat(value.replace('Z','+00:00'))
 if t.tzinfo is None:raise ValueError('Timezone missing')
 return t
def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def require(condition,message):
 if not condition:raise ValueError(message)
