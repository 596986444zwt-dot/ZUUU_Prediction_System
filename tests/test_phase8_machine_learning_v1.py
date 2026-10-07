"""Causal boundary and frozen-result invariants for Phase8."""
import json
from pathlib import Path
import numpy as np
import pytest
from src.models.phase8.contracts import CONTRACT,ROOT,SEED,MIN_HISTORY,GRIDS,FAMILIES
from src.models.phase8.data import eligible,load,fingerprints
from src.models.phase8.pipeline import fit_preprocessor,transform,estimator
from src.models.phase8.metrics import compute
from src.data_v1.source_io import open_snapshot,sha256_file

def test_label_eligibility_midnight_boundary():
 z=dict(samples=[dict(issue_time_utc='2025-01-01T13:00:00+00:00',target_business_date='2025-01-02')],eligibility=['2025-01-04T00:00:00+08:00'])
 assert not len(eligible(z,'2025-01-03T15:59:59+00:00','2025-01-05'))
 assert eligible(z,'2025-01-03T16:00:00+00:00','2025-01-05').tolist()==[0]
def test_current_target_never_eligible():
 z=dict(samples=[dict(issue_time_utc='2025-01-01T13:00:00+00:00',target_business_date='2025-01-02')],eligibility=['2025-01-04T00:00:00+08:00'])
 assert not len(eligible(z,'2025-01-05T13:00:00+00:00','2025-01-02'))
@pytest.mark.parametrize('family',FAMILIES)
def test_all_null_is_excluded_without_fill(family):
 X=np.array([[1,np.nan],[2,np.nan],[3,np.nan]],float);p=fit_preprocessor(X,['useful','ecmwf_tmax_revision_24h_c'],family)
 assert p['dropped']['ecmwf_tmax_revision_24h_c']=='ALL_NULL_TRAINING_FEATURE'
 assert p['indices']==[0]
def test_train_median_scaling_does_not_consume_test():
 X=np.array([[1,1],[3,np.nan],[5,5]],float);p=fit_preprocessor(X,['a','b'],'RIDGE');q=json.dumps(p)
 out=transform(np.array([[1000,np.nan]],float),p)
 assert p['medians']==[3,3] and json.dumps(p)==q and np.isfinite(out).all()
 assert p['indicator_indices']==[1]
@pytest.mark.parametrize('family',FAMILIES[1:])
def test_tree_nan_is_not_imputed(family):
 X=np.array([[1,1],[2,np.nan],[3,3]],float);p=fit_preprocessor(X,['a','b'],family)
 assert np.isnan(transform(X,p)[1,1]) and not p['medians']
def test_duplicate_rule_has_no_y_input():
 X=np.array([[1,1,4],[2,2,4],[3,3,4]],float);p=fit_preprocessor(X,['hist_expanding_bias_c','hist_horizon_bias_c','run_age_hours'],'RIDGE')
 assert p['indices']==[0] and p['dropped']['hist_horizon_bias_c'].startswith('EXACT_DUPLICATE') and p['dropped']['run_age_hours']=='CONSTANT_TRAINING_VARIANCE'
def test_zero_is_not_missing():
 X=np.array([[0,np.nan],[1,0],[2,1]],float);p=fit_preprocessor(X,['a','b'],'RIDGE')
 assert p['valid_counts']==[3,2] and p['null_counts']==[0,1] and p['medians'][1]==.5
def test_continuous_error_sign_and_percentiles():
 m=compute([2.4,5.2],[3,4]);assert m['N']==2 and m['Bias']==pytest.approx(.3) and m['MAE']==pytest.approx(.9)
@pytest.mark.parametrize('family',FAMILIES)
def test_fixed_seed_prediction_reproducible(family):
 X=np.arange(90,dtype=float).reshape(30,3);X[0,1]=np.nan;p=fit_preprocessor(X,['a','b','c'],family);A=transform(X,p);y=np.sin(np.arange(30))
 a=estimator(family,GRIDS[family][0]);b=estimator(family,GRIDS[family][0]);a.fit(A,y);b.fit(A,y)
 assert np.allclose(a.predict(A),b.predict(A),rtol=1e-10,atol=1e-10)
def test_lightgbm_tree_restoration_matches_native():
 from src.models.phase8.restoration import evaluate_lightgbm_dump
 from lightgbm import LGBMRegressor
 X=np.arange(180,dtype=float).reshape(60,3);X[::7,1]=np.nan;y=np.sin(np.arange(60))
 e=LGBMRegressor(n_estimators=20,num_leaves=7,min_child_samples=5,verbosity=-1,n_jobs=1,random_state=SEED);e.fit(X,y)
 assert np.allclose(e.predict(X),evaluate_lightgbm_dump(e.booster_.dump_model(),X),rtol=1e-10,atol=1e-10)
def test_contract_predeclared_frozen():
 assert json.loads((ROOT/'docs/phase8/PHASE8_CONTRACT.json').read_text(encoding='utf-8'))==CONTRACT
 assert MIN_HISTORY==180 and CONTRACT['horizons']==['T1','T2'] and 'D+2' in CONTRACT['eligibility']
 assert CONTRACT['outer_refit_target_calendar_days']==14 and 'HISTORICAL_ZUUU_INGEST_NOT_OBSERVED' in CONTRACT['T0']

@pytest.fixture(scope='module')
def saved():
 path=ROOT/'database/phase8_machine_learning_v1.db'
 if not path.exists():pytest.skip('Saved-result gate runs after official build')
 c=open_snapshot(path)
 data={t:[dict(x) for x in c.execute('SELECT * FROM phase8_'+t)] for t in ('sample','split','preprocessing','hyperparameter','state','prediction','metric','comparison','manifest')};c.close();return data
def test_frozen_sources_unchanged(saved):
 before=json.loads(saved['manifest'][0]['source_sha_before_json']);assert all(sha256_file(ROOT/p)==h for p,h in before.items())
@pytest.mark.parametrize('horizon,n',[('T1',729),('T2',728)])
def test_base_universe_and_all_model_records(saved,horizon,n):
 assert sum(s['horizon']==horizon for s in saved['sample'])==n
 for f in FAMILIES:assert sum(p['horizon']==horizon and p['model_family']==f for p in saved['prediction'])==n
def test_gap_t0_and_probability_absent(saved):
 assert all(s['record_id']!='2025-08-07/T2' for s in saved['sample'])
 assert all(p['horizon'] in ('T1','T2') and p['feature_version']=='FEATURE_V1' for p in saved['prediction'])
 assert all('probability' not in p and p['model_version']!='MODEL_T0_V1' for p in saved['prediction'])
def test_inner_disjoint_and_horizon_separate(saved):
 for s in saved['split']:
  tr=json.loads(s['training_ids_json']);va=json.loads(s['validation_ids_json']);assert not set(tr)&set(va)
  assert all(x.endswith('/'+s['horizon']) for x in tr+va)
def test_prediction_unique_lineage_complete(saved):
 pp=saved['prediction'];assert len(pp)==len({p['prediction_id'] for p in pp})
 states={s['record_id']:s for s in saved['state']}
 for p in pp:
  if p['ml_prediction'] is None:assert p['status']=='INSUFFICIENT_TRAINING_HISTORY' and p['eligible_history_at_issue']<180
  else:assert p['state_id'] in states and p['training_sample_count']>=180 and p['training_cutoff']<=p['prediction_issue_time']
def test_t2_all_null_never_used(saved):
 for r in saved['preprocessing']:
  p=json.loads(r['preprocessing_json'])
  if r['horizon']=='T2':assert p['dropped']['ecmwf_tmax_revision_24h_c']=='ALL_NULL_TRAINING_FEATURE'
def test_saved_native_lightgbm_restore_matches_prediction(saved):
 from src.models.phase8.restoration import load_fitted_state
 st=next(s for s in saved['state'] if s['model_family']=='LIGHTGBM');a=load_fitted_state(ROOT/st['artifact_path'])
 p=next(p for p in saved['prediction'] if p['state_id']==st['record_id']);z=load()[st['horizon']];i=z['ids'].index(p['input_sample_id'])
 prediction=float(a['estimator'].predict(transform(z['X'][i:i+1],a['preprocessor']))[0]+z['raw'][i])
 assert prediction==pytest.approx(p['ml_prediction'],abs=1e-8)
def test_same_subset_counts(saved):
 for r in saved['comparison']:
  nn=[m['N'] for m in saved['metric'] if m['horizon']==r['HORIZON'] and m['comparison_model']==r['MODEL']]
  assert len(nn)==3 and set(nn)=={r['N']}
def test_inner_selection_is_actual_minimum(saved):
 for st in saved['state']:
  hh=[r for r in saved['hyperparameter'] if r['state_id']==st['record_id']];best=min(hh,key=lambda x:(x['inner_mae'],x['candidate_id']))
  assert st['selected_candidate_id']==best['candidate_id'] and best['selected']==1
def test_independent_final_gate_if_present():
 p=ROOT/'docs/phase8/PHASE8_FINAL_AUDIT.json'
 if not p.exists():pytest.skip('Independent audit runs after build')
 r=json.loads(p.read_text(encoding='utf-8'));assert r['PHASE8_ACCEPTANCE'] in ('PASS','PASS_WITH_WARNINGS') and not r['violations']
 assert r['training_reproduction_count']==30

@pytest.mark.parametrize('field',['TARGET_LEAKAGE_COUNT','FUTURE_LABEL_COUNT','SAME_DAY_UNSETTLED_COUNT','FUTURE_ECMWF_COUNT','PREPROCESSING_LEAKAGE_COUNT','HYPERPARAMETER_SELECTION_LEAKAGE_COUNT','SILENT_SAMPLE_DROP_COUNT','SILENT_IMPUTATION_COUNT','CROSS_HORIZON_CONTAMINATION_COUNT'])
def test_independent_full_scan_zero(field):
 path=ROOT/'docs/phase8/PHASE8_FINAL_AUDIT.json'
 if not path.exists():pytest.skip('Independent result required')
 assert json.loads(path.read_text(encoding='utf-8'))[field]==0
