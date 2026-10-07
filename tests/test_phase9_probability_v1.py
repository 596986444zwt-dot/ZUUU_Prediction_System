"""Probability conservation, causal edge cases and saved-result acceptance gates."""
import json
from datetime import datetime,timedelta
import numpy as np
import pytest
from src.probability.contracts import *
from src.probability.distribution import distribution,calibrate,score
from src.probability.data import eligible
from src.data_v1.source_io import open_snapshot,sha256_file

@pytest.mark.parametrize('method',BASE_METHODS)
def test_mass_cdf_survival_and_finite_score(method):
 p,_=distribution(34.63,np.linspace(-4,4,100),method);f=np.cumsum(p);s=1-np.r_[0,f[:-1]]
 assert p.min()>0 and p.max()<=1 and p.sum()==pytest.approx(1,abs=1e-13)
 assert np.all(np.diff(f)>=0) and np.all(np.diff(s)<=0) and f[-1]==pytest.approx(1)
 m=score(p,35);assert all(np.isfinite(m[k]) for k in ('Brier','LogLoss','CRPS'))

@pytest.mark.parametrize('alpha',ALPHAS)
def test_monotone_calibration_preserves_mass(alpha):
 p,_=distribution(12.3,np.arange(-5,6),'GAUSSIAN_EXPANDING');q,_=calibrate(p,alpha)
 assert q.min()>=FLOOR and q.sum()==pytest.approx(1) and np.all(np.diff(np.cumsum(q))>=0)

def test_actual_is_only_used_for_scoring():
 p,_=distribution(20,np.arange(-3,4),'GAUSSIAN_EXPANDING');old=p.copy()
 assert score(p,20)['LogLoss']<score(p,30)['LogLoss'];assert np.array_equal(old,p)

def test_empirical_half_open_reporting_edges():
 p,info=distribution(10.5,[0,0],'EMPIRICAL_EXPANDING')
 assert np.argmax(p)+SUPPORT[0]==11 and info['left_tail_mass']==0

def test_residual_sign_is_actual_minus_prediction():
 p,_=distribution(10,[2,2],'GAUSSIAN_EXPANDING');assert np.argmax(p)+SUPPORT[0]==12

def test_tail_fold_is_declared():
 p,i=distribution(100,[0,1],'GAUSSIAN_EXPANDING');assert i['right_tail_mass']>.99 and p[-1]>.99

def test_floor_not_zero_and_logloss_finite_at_remote_actual():
 p,_=distribution(30,[0,0],'EMPIRICAL_EXPANDING');assert p[0]==FLOOR and np.isfinite(score(p,-80)['LogLoss'])

def test_brier_and_crps_definition_independent():
 p,_=distribution(3,[0,1,2],'EMPIRICAL_EXPANDING');m=score(p,4)
 y=np.zeros(len(p));y[4-SUPPORT[0]]=1
 assert m['Brier']==pytest.approx(np.sum((p-y)**2))
 assert m['CRPS']==pytest.approx(np.sum((np.cumsum(p)[:-1]-np.cumsum(y)[:-1])**2))

def test_eligibility_at_dplus2_midnight_only():
 hist=dict(target_business_date='2025-01-01',issue='2024-12-31T13:00:00+00:00',eligibility='2025-01-03T00:00:00+08:00',ML=20)
 current=dict(target_business_date='2025-01-04',issue='2025-01-02T15:59:59+00:00')
 assert not eligible([hist],current);current['issue']='2025-01-02T16:00:00+00:00';assert len(eligible([hist],current))==1

def test_current_and_future_labels_excluded():
 r=dict(target_business_date='2025-01-05',issue='2025-01-01T13:00:00+00:00',eligibility='2025-01-07T00:00:00+08:00',ML=20)
 assert not eligible([r],dict(target_business_date='2025-01-05',issue='2025-01-08T13:00:00+00:00'))

def test_oos_cold_start_not_in_formal_pool():
 r=dict(target_business_date='2025-01-01',issue='2024-12-31T13:00:00+00:00',eligibility='2025-01-03T00:00:00+08:00',ML=None)
 c=dict(target_business_date='2025-01-08',issue='2025-01-07T13:00:00+00:00')
 assert not eligible([r],c);assert len(eligible([r],c,False))==1

def test_protocol_physically_frozen():
 p=DOCS/'PHASE9_PROBABILITY_PROTOCOL_V1.md'
 assert sha256_file(p)==json.loads((DOCS/'PHASE9_PROTOCOL_SHA256.json').read_text())['sha256']
 assert json.loads(p.read_text(encoding='utf-8').split('```json\n')[1].split('\n```')[0])==PROTOCOL
 assert MODELS=={'T1':'RIDGE','T2':'LIGHTGBM'} and MIN_RESIDUAL==60 and MIN_CALIBRATION==90

@pytest.fixture(scope='module')
def result():
 path=DOCS/'PHASE9_FINAL_AUDIT.json'
 if not path.exists():pytest.skip('Saved-result gates after independent acceptance')
 return json.loads(path.read_text(encoding='utf-8'))

@pytest.mark.parametrize('key',['TARGET_LEAKAGE_COUNT','FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','SAME_DAY_UNSETTLED_COUNT','PREPROCESSING_LEAKAGE_COUNT','CALIBRATION_LEAKAGE_COUNT','CROSS_HORIZON_CONTAMINATION_COUNT','MARKET_DATA_USAGE_COUNT','UNPROVEN_INTRADAY_ZUUU_USAGE_COUNT','negative_probability_count','probability_above_one_count','probability_sum_error_count','cdf_non_monotonic_count','survival_non_monotonic_count','actual_outside_support_count','infinite_logloss_count','nan_score_count','SILENT_SAMPLE_DROP_COUNT','SILENT_RESIDUAL_DROP_COUNT','SCORE_MISMATCH_COUNT','PROBABILITY_REPRODUCTION_MISMATCH_COUNT','BIN_MISMATCH_COUNT','INTERVAL_MISMATCH_COUNT','METHOD_SELECTION_MISMATCH_COUNT','CALIBRATION_PARAMETER_MISMATCH_COUNT'])
def test_full_independent_saved_gates(result,key):assert result[key]==0

def test_source_guardian_and_frozen_models(result):
 assert result['SOURCE_GUARDIAN']=='PASS'
 before=json.loads((DOCS/'SOURCE_SHA_BEFORE.json').read_text(encoding='utf-8'))
 assert all(sha256_file(ROOT/p)==h for p,h in before.items())
 assert result['T1_CONTINUOUS_MODEL']=='RIDGE' and result['T2_CONTINUOUS_MODEL']=='LIGHTGBM'

def test_universe_gap_and_trajectory_not_faked(result):
 assert result['T1_BASE_SAMPLE_COUNT']==729 and result['T2_BASE_SAMPLE_COUNT']==728
 assert result['KNOWN_T2_GAP_PRESERVED'] and result['CORRELATED_TRAJECTORY_STATUS']=='BLOCKED_FOR_DATA'
 assert result['TMAX_TIME_PROBABILITY_STATUS']=='BLOCKED' and result['FRAMEWORK_DEVIATION'] is False

def test_probability_snapshot_is_readonly_and_repeatable(result):
 c=open_snapshot(OUTPUT);rows=c.execute('select record_id,pmf from phase9_probability_mass order by record_id limit 6').fetchall();c.close()
 d=open_snapshot(OUTPUT);other=d.execute('select record_id,pmf from phase9_probability_mass order by record_id limit 6').fetchall();d.close()
 assert [tuple(r) for r in rows]==[tuple(r) for r in other]

def test_no_model_retraining_or_market_imports():
 for p in (ROOT/'src/probability').glob('*.py'):
  text=p.read_text(encoding='utf-8')
  assert '.fit(' not in text and 'requests.' not in text and 'train_test_split(' not in text

@pytest.mark.parametrize('method',BASE_METHODS)
def test_deterministic_distribution_from_same_inputs(method):
 a,i=distribution(27.37,np.linspace(-6,5,93),method);b,j=distribution(27.37,np.linspace(-6,5,93),method)
 assert np.array_equal(a,b) and i==j

def test_no_continuous_forecast_rounding():
 a,_=distribution(28.1,np.linspace(-3,3,90),'GAUSSIAN_EXPANDING');b,_=distribution(28.4,np.linspace(-3,3,90),'GAUSSIAN_EXPANDING')
 assert not np.array_equal(a,b) and np.sum(a>1e-4)>3

def test_top1_within1_probability_is_pmf_sum():
 p,_=distribution(30.2,np.linspace(-4,4,60),'GAUSSIAN_EXPANDING');s=score(p,30)
 assert s['within1_probability']==pytest.approx(p[np.abs(np.asarray(SUPPORT)-s['top1_integer'])<=1].sum())

def test_future_outcomes_cannot_change_earlier_probabilities_or_selection():
 from src.probability.walk_forward import run
 import copy
 rows=[]
 for i in range(180):
  d=datetime.fromisoformat('2025-01-01T00:00:00+08:00')+timedelta(days=i);day=d.date().isoformat();pred=20+np.sin(i/10)
  rows.append(dict(sample_id=day+'/T1',horizon='T1',target_business_date=day,issue=(d-timedelta(hours=3)).isoformat(),eligibility=eligible_time(day),actual=int(20+3*np.sin(i/7)),ML=float(pred),RAW=float(pred-.5),MOS=float(pred-.2),phase8_prediction_id=day+'/T1/RIDGE',phase8_state='synthetic_only',selected_run='not_used',run_available_time=(d-timedelta(hours=4)).isoformat(),season='DJF'))
 altered=copy.deepcopy(rows)
 for r in altered[170:]:r['actual']+=5
 a=run({'T1':rows});b=run({'T1':altered});limit=rows[170]['target_business_date']
 pa={r['record_id']:r for r in a['probability_prediction']};pb={r['record_id']:r for r in b['probability_prediction']}
 ma={r['record_id']:r['pmf'] for r in a['probability_mass']};mb={r['record_id']:r['pmf'] for r in b['probability_mass']}
 for rid,p in pa.items():
  if p['target_business_date']<=limit:
   assert p.get('method')==pb[rid].get('method') and p.get('calibration_alpha')==pb[rid].get('calibration_alpha')
   if rid in ma:assert ma[rid]==mb[rid]

def test_minimum_history_and_temporal_inner_selection_contract():
 assert PROTOCOL['minimum_residual_history']==60 and PROTOCOL['minimum_calibration_history']==90
 assert 'last 60 eligible' in PROTOCOL['method_selection'] and 'PREQUENTIAL' in PROTOCOL['calibration_selection']
 assert PROTOCOL['trajectory_count']==0 and PROTOCOL['random_seed']==SEED
