from pathlib import Path
from datetime import datetime, timedelta
import json

ROOT=Path(__file__).resolve().parents[2]
DOCS=ROOT/'docs/phase9'
OUTPUT=ROOT/'database/phase9_probability_v1.db'
MODELS={'T1':'RIDGE','T2':'LIGHTGBM'}
SEED=20261002
MIN_RESIDUAL=60
MIN_CALIBRATION=90
MIN_SELECTION=60
SUPPORT=list(range(-80,81))
FLOOR=1e-12
BASE_METHODS=('GAUSSIAN_EXPANDING','GAUSSIAN_90CAL','EMPIRICAL_EXPANDING','KDE_EXPANDING_BW075')
METHODS=BASE_METHODS+tuple(x+'_CAL' for x in BASE_METHODS)
ALPHAS=(1.0,0.8,1.2)
PROTOCOL=dict(version='PROBABILITY_PROTOCOL_V1',horizons=MODELS,
 residual='actual reported integer Tmax minus frozen continuous OOS prediction; positive means actual warmer',
 eligibility='GROUND_TRUTH_ELIGIBILITY_RULE_V1: D+2 00:00 Asia/Shanghai <= issue; residual target date < current target; historical issue < current issue',
 minimum_residual_history=MIN_RESIDUAL,minimum_calibration_history=MIN_CALIBRATION,
 residual_methods=list(BASE_METHODS),window='EXPANDING or 90 calendar days ending issue BJT date minus 2; 90CAL lower boundary inclusive end minus 89; minimum 60 eligible observations; no centered windows',
 gaussian='historical mean and sample std ddof=1; sigma=max(std,0.5 C), explicit deterministic lower bound',
 empirical='equal weight latent points forecast + each eligible OOS residual; half-open integer bins [k-.5,k+.5); no smoothing except declared floor',
 kde='equal-weight normal kernels centered on eligible residuals; bandwidth fixed 0.75 C; no bandwidth optimization',
 integer_mapping='approximation to REPORTED_INTEGER_TMAX from latent continuous distribution; METAR integer reports maximized daily, not proven continuous rounding mechanism',
 support=SUPPORT,tail='latent mass below -80.5 folded into -80; above 80.5 into 80; both masses recorded; fixed support never chosen from current actual',
 probability_floor=FLOOR,normalization='after tail folding raw/sum(raw); then (1-K*epsilon)*p+epsilon; record pre-normalization mass and L1 floor adjustment; no hidden clipping',
 calibration='global monotone CDF log-odds slope: G_a(u)=u^a/(u^a+(1-u)^a); a in [1,.8,1.2]; preserve endpoint 0/1; difference transformed CDF; apply declared PMF floor again; no per-class independent fit',
 calibration_selection='last 90 eligible past PREQUENTIAL uncalibrated probability forecasts, each originally generated from its own past; choose slope minimizing mean NLL, grid order ties; none until 90 cases',
 method_selection='at each issue, score last 60 eligible past OOS forecasts of each currently available variant; min mean NLL, differences <=.01 prefer protocol order; candidates need 60 past scored forecasts; otherwise GAUSSIAN_EXPANDING. No current actual access',
 baseline='RAW and M6 use same methods/calibration/selection rules; formal residual dates restricted to same dates as available ML OOS; never in-sample ML residuals',
 fallback='ENGINE: eligible ML selected method; else M6 GAUSSIAN_EXPANDING full eligible historical OOS >=60; else RAW same; else NO_FORECAST. Fallback scored separately, excluded from formal ML/common comparison',
 metrics='multiclass Brier=sum_k(p_k-1[y=k])^2, unnormalized; NLL=-ln(p_actual); discrete CRPS=sum_{k=-80}^{79}(F(k)-1[y<=k])^2, C steps 1',
 calibration_bins=[i/10 for i in range(11)],minimum_bin_warning=30,
 intervals='central equal-tail integer quantiles 50%,80%,90%; discrete support may exceed nominal coverage; widths upper-lower C',
 diagnostic='top ties choose smaller integer; top2/top3 ranked stable probability descending; +/-1 sum around top1; entropy and distribution std',
 extremes='actual >=35 C or <=5 C, descriptive only; no exclusion; warn extreme group N<30; catastrophic p_actual<.01,.02,.05',
 confidence='top1 >=.5,.6,.7,.8; small groups N<30 cannot establish calibration; >=30 with confidence minus hit>.10 flag overconfidence; hit minus confidence>.10 underconfidence',
 candidate='descriptive protocol-level PAST_ONLY_SELECTED candidate, never retroactive single winning method: ML common N>=250; NLL <= each RAW/M6+.02; Brier <= each+.01; CRPS <= each+.02; 80/90 interval coverage within .10 of nominal; year/season N>=30 NLL <= either baseline+.30; no reliable-group overconfidence >.10; otherwise NONE',
 acceptance='all mathematical and causal counts zero; source unchanged; independent scores reproduce; no hidden extremes; model quality limitations mean WARNING and NONE, not fabricated success; failed integrity/math/leakage => FAIL; insufficient legal outputs => BLOCKED',
 trajectory='BLOCKED_FOR_DATA unless strict hourly OOS errors plus legally timestamped hourly truth exist. Daily scalar errors cannot identify covariance; no independent-hour simulation',
 trajectory_count=0,random_seed=SEED,tmax_time='BLOCKED; future scoring ANY_MAX_OCCURRENCE from frozen first/last/count plus occurrence lineage; no present timing probability',
 warnings=['Phase8 14 calendar-day expanding blocks fixed within block','minimum ML history180; inherited cold start','Ridge training-only medians; tree native missing','Windows native dependency initialization order sklearn then LightGBM then XGBoost then CatBoost','previous-hour radiation/precipitation unchanged','FEATURE_V1 missingness/redundancy retained','ECMWF estimated dissemination not observed arrival','HISTORICAL_ZUUU_INGEST_NOT_OBSERVED; T0 BLOCKED'],
 heteroscedasticity='descriptive eligible past residual spread by forecast level (<15,15..25,>=25), season, forecast cloud/radiation/revision median regimes where supported; no additional feature/model selection',
 uncertainty='paired NLL differences; moving contiguous target-date block bootstrap length14, draws500, fixed seed; diagnostic only; observed gap splits contiguous blocks')

def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def stamp(x):
 t=datetime.fromisoformat(x.replace('Z','+00:00'))
 if t.tzinfo is None:raise ValueError('Timezone required')
 return t
def eligible_time(day):return (datetime.fromisoformat(day+'T00:00:00+08:00')+timedelta(days=2)).isoformat()
def require(x,message):
 if not x:raise ValueError(message)
