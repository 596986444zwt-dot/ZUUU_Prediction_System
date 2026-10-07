"""Independent saved-result verifier; never invokes a builder or training helper.

Reproduction directly constructs library estimators from saved parameters and
independently computes training-only transformations. Full causal/metric scans
operate on saved rows and frozen input snapshots, not builder conclusions.
"""
import ast
import collections
import csv
import hashlib
import json
import math
import pickle
import warnings
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
# Match the documented estimator/library startup order before any unpickling.
# In this Windows SDK, loading CatBoost native dependencies first makes later
# LightGBM native calls raise access violations; controlled startup is verified.
import sklearn.linear_model
import sklearn.ensemble
import lightgbm
import xgboost
import catboost
from src.data_v1.source_io import open_snapshot,sha256_file

ROOT=Path(__file__).resolve().parents[2]
DOCS=ROOT/'docs/phase8'
def stamp(v):return datetime.fromisoformat(v.replace('Z','+00:00'))
def near(a,b):return (a is None and b is None) or (a is not None and b is not None and math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-8))
def dump(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)

def independent_prep(X,names,family):
 kept=[];dropped={}
 for j,name in enumerate(names):
  a=X[:,j];v=a[~np.isnan(a)]
  if len(v)==0:dropped[name]='ALL_NULL_TRAINING_FEATURE';continue
  if name=='hist_horizon_bias_c' and np.array_equal(a,X[:,names.index('hist_expanding_bias_c')],equal_nan=True):dropped[name]='EXACT_DUPLICATE_OF_hist_expanding_bias_c';continue
  equivalent=[k for k in kept if np.array_equal(a,X[:,k],equal_nan=True)]
  if equivalent:dropped[name]='EXACT_DUPLICATE_OF_'+names[equivalent[0]];continue
  if np.var(v)<=1e-12:dropped[name]='CONSTANT_TRAINING_VARIANCE';continue
  kept.append(j)
 A=X[:,kept];med=[];ind=[];means=[];scales=[]
 if family=='RIDGE':
  med=[float(np.median(a[~np.isnan(a)])) for a in A.T];ind=[i for i,a in enumerate(A.T) if np.isnan(a).any()]
  B=np.where(np.isnan(A),med,A)
  if ind:B=np.column_stack((B,np.isnan(A[:,ind]).astype(float)))
  means=B.mean(axis=0).tolist();scales=B.std(axis=0);scales[scales<=1e-12]=1;scales=scales.tolist()
 return dict(indices=kept,dropped=dropped,medians=med,indicator_indices=ind,means=means,scales=scales)
def independent_transform(X,p):
 A=X[:,p['indices']]
 if not p['medians']:return A.copy()
 B=np.where(np.isnan(A),p['medians'],A)
 if p['indicator_indices']:B=np.column_stack((B,np.isnan(A[:,p['indicator_indices']]).astype(float)))
 return (B-p['means'])/p['scales']
def direct_estimator(f,params,seed):
 if f=='RIDGE':
  from sklearn.linear_model import Ridge
  return Ridge(**params,solver='svd')
 if f=='RANDOM_FOREST':
  from sklearn.ensemble import RandomForestRegressor
  return RandomForestRegressor(**params,random_state=seed,n_jobs=1)
 if f=='LIGHTGBM':
  from lightgbm import LGBMRegressor
  return LGBMRegressor(**params,random_state=seed,n_jobs=1,deterministic=True,force_col_wise=True,verbosity=-1)
 if f=='XGBOOST':
  from xgboost import XGBRegressor
  return XGBRegressor(**params,random_state=seed,n_jobs=1,tree_method='hist',objective='reg:squarederror')
 from catboost import CatBoostRegressor
 return CatBoostRegressor(**params,random_seed=seed,thread_count=1,verbose=False,allow_writing_files=False,nan_mode='Min',bootstrap_type='No')
def independently_metric(pred,actual):
 e=[float(p-a) for p,a in zip(pred,actual)];a=sorted(abs(v) for v in e);n=len(e)
 def q(t):
  x=(n-1)*t;j=int(x);return a[j]+(a[min(j+1,n-1)]-a[j])*(x-j)
 rounded=[abs(math.floor(p+.5)-y) for p,y in zip(pred,actual)]
 return dict(N=n,Bias=math.fsum(e)/n,MAE=math.fsum(a)/n,RMSE=math.sqrt(math.fsum(v*v for v in e)/n),MedianAE=q(.5),P90AE=q(.9),MaxAE=a[-1],DiagnosticExact=sum(x==0 for x in rounded)/n,DiagnosticWithin1=sum(x<=1 for x in rounded)/n,DiagnosticWithin2=sum(x<=2 for x in rounded)/n)
def independently_walk_lightgbm_trees(model,A):
 result=[]
 for row in A:
  leaves=[]
  for tree in model['tree_info']:
   node=tree['tree_structure']
   while 'leaf_value' not in node:
    if node['decision_type']!='<=':raise ValueError('Unsupported split')
    value=float(row[node['split_feature']]);kind=node['missing_type']
    if (kind=='NaN' and math.isnan(value)) or (kind=='Zero' and (math.isnan(value) or abs(value)<=1e-35)):left=node['default_left']
    else:left=(0.0 if math.isnan(value) else value)<=node['threshold']
    node=node['left_child'] if left else node['right_child']
   leaves.append(node['leaf_value'])
  score=sum(leaves);result.append(score/len(leaves) if model['average_output'] else score)
 return np.array(result)
def audit(reproduce=True):
 counts=collections.Counter();violations=[]
 def check(name,condition,detail):
  if not condition:counts[name]+=1;violations.append(dict(check=name,detail=detail))
 c=open_snapshot(ROOT/'database/phase8_machine_learning_v1.db')
 check('DATABASE_INTEGRITY_VIOLATION_COUNT',c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','Phase8')
 tables={r[0].replace('phase8_',''):[] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'phase8_%'")}
 for t in tables:tables[t]=[dict(r) for r in c.execute('SELECT * FROM phase8_'+t+' ORDER BY record_id')]
 c.close();manifest=tables['manifest'][0];contract=json.loads(manifest['contract_json']);before=json.loads(manifest['source_sha_before_json'])
 for path,h in before.items():check('SOURCE_MUTATION_COUNT',(ROOT/path).exists() and sha256_file(ROOT/path)==h,path)
 check('CONTRACT_MUTATION_COUNT',sha256_file(DOCS/'PHASE8_CONTRACT.json')==manifest['contract_sha256'],'Contract')
 for path,h in json.loads(manifest['implementation_sha_json']).items():check('IMPLEMENTATION_MUTATION_COUNT',sha256_file(ROOT/path)==h,path)
 phase7=open_snapshot(ROOT/'database/phase7_feature_v1.db')
 names=sorted(r[0] for r in phase7.execute('SELECT feature_name FROM phase7_feature_registry'));xx={r['sample_id']:dict(r) for r in phase7.execute('SELECT * FROM training_feature_view')};yy={r['sample_id']:dict(r) for r in phase7.execute('SELECT * FROM phase7_feature_label')};ss={r['sample_id']:dict(r) for r in phase7.execute('SELECT * FROM phase7_feature_sample')}
 X={sid:np.array([np.nan if x[n] is None else x[n] for n in names]) for sid,x in xx.items()}
 input_missing={}
 for row in phase7.execute('SELECT v.*,s.issue_time_utc FROM phase7_feature_value v JOIN phase7_feature_sample s USING(sample_id)'):
  check('FUTURE_ECMWF_COUNT',stamp(row['feature_available_time'])<=stamp(row['issue_time_utc']),row['sample_id']+'/'+row['feature_name'])
  if row['value'] is None:input_missing[(row['sample_id'],row['feature_name'])]=row['missing_reason']
 phase7.close()
 rawdb=open_snapshot(ROOT/'database/phase5_ecmwf_raw_baseline_v1.db');raw={(r['business_date_bjt'],r['horizon']):dict(r) for r in rawdb.execute('SELECT * FROM phase5_baseline_v1_prediction')};rawdb.close()
 mosdb=open_snapshot(ROOT/'database/phase6_statistical_mos_v1_review.db');mos={(r['target_business_date'],r['horizon']):dict(r) for r in mosdb.execute("SELECT * FROM phase6_mos_prediction WHERE model='M6'")};mosdb.close()
 splits={r['record_id']:r for r in tables['split']};states={r['record_id']:r for r in tables['state']};preps={r['record_id']:r for r in tables['preprocessing']}
 for sp in splits.values():
  ids=json.loads(sp['training_ids_json']);val=json.loads(sp['validation_ids_json']);cut=stamp(sp['training_cutoff'])
  check('SPLIT_COUNT_MISMATCH',len(ids)==sp['training_sample_count'] and len(ids)==len(set(ids)),sp['record_id'])
  for sid in ids:
   check('CROSS_HORIZON_CONTAMINATION_COUNT',ss[sid]['horizon']==sp['horizon'],sp['record_id']+'/'+sid)
   check('FUTURE_LABEL_COUNT',stamp(yy[sid]['label_eligibility_time_bjt'])<=cut,sp['record_id']+'/'+sid)
   check('SAME_DAY_UNSETTLED_COUNT',ss[sid]['target_business_date']<cut.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),sp['record_id']+'/'+sid)
   check('TRAINING_TIME_VIOLATION_COUNT',stamp(ss[sid]['issue_time_utc'])<cut,sp['record_id']+'/'+sid)
  expected=[sid for sid,s in ss.items() if s['horizon']==sp['horizon'] and stamp(yy[sid]['label_eligibility_time_bjt'])<=cut and stamp(s['issue_time_utc'])<cut and s['target_business_date']<sp['outer_target_date']]
  check('TRAINING_UNIVERSE_MISMATCH',set(expected)==set(ids),sp['record_id'])
  check('INNER_TIME_VIOLATION_COUNT',not set(ids).intersection(val) and all(stamp(ss[sid]['issue_time_utc'])>=cut for sid in val),sp['record_id'])
 for r in preps.values():
  sp=splits[r['split_id']];ids=json.loads(sp['training_ids_json']);A=np.stack([X[sid] for sid in ids]);reference=independent_prep(A,names,r['model_family']);saved=json.loads(r['preprocessing_json'])
  check('PREPROCESSING_LEAKAGE_COUNT',saved['training_rows']==len(ids) and saved['indices']==reference['indices'] and saved['dropped']==reference['dropped'],r['record_id'])
  for k in ('medians','indicator_indices','means','scales'):check('PREPROCESSING_LEAKAGE_COUNT',len(saved[k])==len(reference[k]) and np.allclose(saved[k],reference[k],rtol=1e-10,atol=1e-10),r['record_id']+'/'+k)
  check('PREPROCESSING_LEAKAGE_COUNT',saved['valid_counts']==np.sum(np.isfinite(A),axis=0).tolist() and saved['null_counts']==np.sum(np.isnan(A),axis=0).tolist(),r['record_id']+'/counts')
  check('T2_REVISION_IMPUTATION_COUNT',r['horizon']!='T2' or 'ecmwf_tmax_revision_24h_c' in saved['dropped'],r['record_id'])
  if r['model_family']!='RIDGE':check('SILENT_IMPUTATION_COUNT',not saved['medians'] and saved['imputation_policy']=='NATIVE_NAN_NO_IMPUTATION',r['record_id'])
 for state in states.values():
  hh=[r for r in tables['hyperparameter'] if r['state_id']==state['record_id']];f=state['model_family'];grid=contract['grids'][f]
  check('HYPERPARAMETER_SELECTION_LEAKAGE_COUNT',len(hh)==len(grid),state['record_id'])
  for hyp in hh:
   check('HYPERPARAMETER_SELECTION_LEAKAGE_COUNT',json.loads(hyp['parameters_json'])==grid[hyp['candidate_id']],hyp['record_id'])
   ip=[r for r in tables['inner_prediction'] if r['state_id']==state['record_id'] and r['candidate_id']==hyp['candidate_id']]
   score=math.fsum(abs(r['continuous_prediction']-r['observed']) for r in ip)/len(ip)
   check('HYPERPARAMETER_SELECTION_LEAKAGE_COUNT',near(score,hyp['inner_mae']) and len(ip)==28,hyp['record_id'])
   for r in ip:
    check('HYPERPARAMETER_SELECTION_LEAKAGE_COUNT',stamp(yy[r['sample_id']]['label_eligibility_time_bjt'])<=stamp(state['training_cutoff']) and r['sample_id'] in json.loads(splits[r['split_id']]['validation_ids_json']) and near(r['observed'],yy[r['sample_id']]['label_tmax_c']),r['record_id'])
  win=min(hh,key=lambda x:(x['inner_mae'],x['candidate_id']))
  check('HYPERPARAMETER_SELECTION_LEAKAGE_COUNT',state['selected_candidate_id']==win['candidate_id'] and sum(r['selected'] for r in hh)==1 and win['selected']==1,state['record_id'])
  check('ARTIFACT_HASH_MISMATCH_COUNT',sha256_file(ROOT/state['artifact_path'])==state['artifact_sha256'],state['record_id'])
 predictions=tables['prediction']
 for p in predictions:
  sid=p['input_sample_id'];s=ss[sid];key=(p['target_business_date'],p['horizon'])
  check('BENCHMARK_MISMATCH_COUNT',near(p['raw_ecmwf_prediction'],raw[key]['ecmwf_raw_tmax_c']) and near(p['mos_prediction'],mos[key]['mos_continuous_tmax']),p['record_id'])
  check('TARGET_LEAKAGE_COUNT',near(p['actual_target'],yy[sid]['label_tmax_c']) and p['feature_version']=='FEATURE_V1',p['record_id'])
  check('FUTURE_ECMWF_COUNT',stamp(p['run_available_time'])<=stamp(p['prediction_issue_time']) and p['selected_run']==s['selected_run'],p['record_id'])
  eligible=[v for v,z in ss.items() if z['horizon']==p['horizon'] and stamp(yy[v]['label_eligibility_time_bjt'])<=stamp(p['prediction_issue_time']) and stamp(z['issue_time_utc'])<stamp(p['prediction_issue_time']) and z['target_business_date']<p['target_business_date']]
  nulls=[n for n,v in zip(names,X[sid]) if np.isnan(v)]
  check('MISSING_MASK_MISMATCH_COUNT',json.loads(p['input_null_features_json'])==nulls and json.loads(p['missing_reasons_json'])=={n:input_missing[(sid,n)] for n in nulls},p['record_id'])
  check('SILENT_SAMPLE_DROP_COUNT',p['eligible_history_at_issue']==len(eligible),p['record_id'])
  if p['ml_prediction'] is None:check('SILENT_SAMPLE_DROP_COUNT',len(eligible)<contract['minimum_training_history'] and p['status']=='INSUFFICIENT_TRAINING_HISTORY',p['record_id']);continue
  st=states[p['state_id']];prep=json.loads(preps[st['preprocessing_id']]['preprocessing_json'])
  check('PREDICTION_LINEAGE_VIOLATION_COUNT',p['training_cutoff']==st['training_cutoff'] and p['training_sample_count']==st['training_sample_count'] and p['input_feature_count']==prep['input_feature_count'] and p['hyperparameters']==st['parameters_json'],p['record_id'])
  check('FUTURE_LABEL_COUNT',stamp(st['training_cutoff'])<=stamp(p['prediction_issue_time']),p['record_id'])
  check('CROSS_HORIZON_CONTAMINATION_COUNT',st['horizon']==p['horizon'] and st['model_family']==p['model_family'],p['record_id'])
  check('METRIC_MISMATCH_COUNT',near(p['error'],p['ml_prediction']-p['actual_target']) and near(p['absolute_error'],abs(p['error'])),p['record_id'])
 for h,n in (('T1',729),('T2',728)):
  for f in contract['grids']:
   ps=[p for p in predictions if p['horizon']==h and p['model_family']==f];check('SILENT_SAMPLE_DROP_COUNT',len(ps)==n and {p['input_sample_id'] for p in ps}=={sid for sid,s in ss.items() if s['horizon']==h},h+'/'+f)
 check('KNOWN_GAP_VIOLATION_COUNT','2025-08-07/T2' not in ss,'Known gap')
 for r in tables['metric']+tables['slice_metric']:
  ps=[p for p in predictions if p['horizon']==r['horizon'] and p['model_family']==r['comparison_model'] and p['ml_prediction'] is not None]
  if 'slice_type' in r:
   def group(p):
    day=p['target_business_date'];month=int(day[5:7]);return day[:4] if r['slice_type']=='year' else day[5:7] if r['slice_type']=='month' else 'DJF' if month in (12,1,2) else 'MAM' if month in (3,4,5) else 'JJA' if month in (6,7,8) else 'SON'
   ps=[p for p in ps if group(p)==r['slice_value']]
  key='raw_ecmwf_prediction' if r['model']=='RAW' else 'mos_prediction' if r['model']=='M6' else 'ml_prediction'
  mm=independently_metric([p[key] for p in ps],[p['actual_target'] for p in ps])
  check('METRIC_MISMATCH_COUNT',all(near(mm[k],r[k]) for k in mm),r['record_id'])
 comparisons=tables['comparison']
 for r in comparisons:
  ms={m['model']:m for m in tables['metric'] if m['horizon']==r['HORIZON'] and m['comparison_model']==r['MODEL']};m=ms[r['MODEL']];a=ms['RAW'];b=ms['M6']
  check('SAME_SUBSET_MISMATCH_COUNT',r['N']==m['N']==a['N']==b['N'] and near(r['DELTA_VS_RAW'],a['MAE']-m['MAE']) and near(r['DELTA_VS_MOS'],b['MAE']-m['MAE']),r['record_id'])
 # Check every saved outer prediction using its hash-verified fitted artifact.
 warnings.filterwarnings('ignore',message='X does not have valid feature names, but LGBMRegressor was fitted with feature names')
 for st in states.values():
  artifact=pickle.loads((ROOT/st['artifact_path']).read_bytes())
  ps=[p for p in predictions if p['state_id']==st['record_id']]
  A=np.stack([X[p['input_sample_id']] for p in ps]);prep=json.loads(preps[st['preprocessing_id']]['preprocessing_json'])
  B=independent_transform(A,prep)
  correction=artifact['estimator'].predict(B)
  if st['model_family']=='LIGHTGBM':
   learned=artifact['estimator'].booster_.dump_model()
   check('LIGHTGBM_TREE_EVALUATION_MISMATCH_COUNT',np.allclose(correction,independently_walk_lightgbm_trees(learned,B),rtol=1e-10,atol=1e-10),st['record_id'])
  calc=correction+np.array([p['raw_ecmwf_prediction'] for p in ps])
  check('SAVED_PREDICTION_MISMATCH_COUNT',all(near(v,p['ml_prediction']) for v,p in zip(calc,ps)),st['record_id'])
 # Independently apply the already-frozen candidate gates; no retraining.
 inferred={}
 for h in ('T1','T2'):
  qualified=[]
  for r in comparisons:
   if r['HORIZON']!=h:continue
   family=r['MODEL'];ok=r['N']>=365 and r['DELTA_VS_RAW']>=.05 and r['DELTA_VS_MOS']>=.05 and r['MODEL_RMSE']<=min(r['RAW_RMSE'],r['MOS_RMSE']) and abs(r['MODEL_BIAS'])<=abs(r['RAW_BIAS'])+.10
   for s in tables['slice_metric']:
    if s['horizon']!=h or s['comparison_model']!=family or s['model']!=family or s['slice_type'] not in ('year','season') or s['N']<30:continue
    peers=[t for t in tables['slice_metric'] if t['horizon']==h and t['comparison_model']==family and t['slice_type']==s['slice_type'] and t['slice_value']==s['slice_value'] and t['model'] in ('RAW','M6')]
    if any(s['MAE']-t['MAE']>.15 for t in peers):ok=False
   if ok:qualified.append(r)
  if qualified:
   best=min(r['MODEL_MAE'] for r in qualified);nearby=[r for r in qualified if r['MODEL_MAE']<=best+.02];inferred[h]=min(nearby,key=lambda r:('RIDGE','RANDOM_FOREST','LIGHTGBM','XGBOOST','CATBOOST').index(r['MODEL']))['MODEL']
  else:inferred[h]='NONE'
  check('CANDIDATE_DECISION_MISMATCH_COUNT',inferred[h]==manifest['t1_candidate' if h=='T1' else 't2_candidate'],h)
 reproduction=[]
 if reproduce:
  warnings.filterwarnings('ignore',message='X does not have valid feature names, but LGBMRegressor was fitted with feature names')
  for h in ('T1','T2'):
   for f in contract['grids']:
    candidates=sorted((x for x in states.values() if x['horizon']==h and x['model_family']==f),key=lambda x:x['training_cutoff'])
    for st in (candidates[0],candidates[len(candidates)//2],candidates[-1]):
     sp=splits[st['outer_split_id']];ids=json.loads(sp['training_ids_json']);A=np.stack([X[sid] for sid in ids]);prep=independent_prep(A,names,f)
     y=np.array([yy[sid]['label_tmax_c']-raw[(ss[sid]['target_business_date'],h)]['ecmwf_raw_tmax_c'] for sid in ids]);est=direct_estimator(f,json.loads(st['parameters_json']),st['random_seed']);est.fit(independent_transform(A,prep),y)
     ps=[p for p in predictions if p['state_id']==st['record_id']]
     B=np.stack([X[p['input_sample_id']] for p in ps]);native_correction=est.predict(independent_transform(B,prep));recalc=native_correction+np.array([p['raw_ecmwf_prediction'] for p in ps]);diff=max(abs(v-p['ml_prediction']) for v,p in zip(recalc,ps))
     if f=='LIGHTGBM':check('LIGHTGBM_TREE_EVALUATION_MISMATCH_COUNT',np.allclose(native_correction,independently_walk_lightgbm_trees(est.booster_.dump_model(),independent_transform(B,prep)),rtol=1e-10,atol=1e-10),st['record_id'])
     check('REPRODUCTION_MISMATCH_COUNT',diff<=1e-7,st['record_id'])
     reproduction.append(dict(horizon=h,model=f,state_id=st['record_id'],training_n=len(ids),prediction_n=len(ps),max_absolute_prediction_difference=float(diff)))
     print('Independent reproduction',st['record_id'],'max difference',diff,flush=True)
 scan=[]
 for fp in list((ROOT/'src/models/phase8').glob('*.py'))+[ROOT/'src/builders/phase8_machine_learning_v1_builder.py']:
  txt=fp.read_text(encoding='utf-8');tree=ast.parse(txt)
  for n in ast.walk(tree):
   if isinstance(n,ast.Call):
    call=ast.unparse(n.func)
    if any(x in call for x in ('train_test_split','KFold','GridSearchCV','RandomizedSearchCV','fit_transform','predict_proba')):scan.append(dict(file=fp.relative_to(ROOT).as_posix(),line=n.lineno,call=call))
 check('STATIC_TIME_SPLIT_VIOLATION_COUNT',not scan,str(scan))
 # Independently serialize result tables in the frozen digest format.
 dig=hashlib.sha256()
 order=('registry','sample','split','preprocessing','hyperparameter','inner_prediction','state','prediction','metric','slice_metric','comparison','candidate')
 for t in order:
  dig.update((t+'\n').encode())
  for r in tables[t]:
   if t=='candidate':r=dict(r,qualified=bool(r['qualified']))
   if t=='registry':pass
   dig.update((dump({k:v for k,v in r.items() if k not in ('fit_seconds','artifact_sha256')})+'\n').encode())
 check('SEMANTIC_HASH_MISMATCH_COUNT',dig.hexdigest()==manifest['semantic_sha256'],'Phase8')
 for path,h in before.items():check('SOURCE_MUTATION_COUNT',sha256_file(ROOT/path)==h,path+' AFTER')
 fields=('TARGET_LEAKAGE_COUNT','FUTURE_LABEL_COUNT','SAME_DAY_UNSETTLED_COUNT','FUTURE_ECMWF_COUNT','PREPROCESSING_LEAKAGE_COUNT','HYPERPARAMETER_SELECTION_LEAKAGE_COUNT','SILENT_SAMPLE_DROP_COUNT','SILENT_IMPUTATION_COUNT','CROSS_HORIZON_CONTAMINATION_COUNT')
 result={k:counts[k] for k in fields};result.update(all_violation_counts=dict(counts),violations=violations,SOURCE_GUARDIAN_STATUS='PASS' if counts['SOURCE_MUTATION_COUNT']==0 else 'FAIL',T1_BASE_SAMPLE_COUNT=sum(s['horizon']=='T1' for s in ss.values()),T2_BASE_SAMPLE_COUNT=sum(s['horizon']=='T2' for s in ss.values()),FEATURE_VERSION='FEATURE_V1',FEATURE_COUNT=len(names),OUTER_WALK_FORWARD_STATUS='PASS' if not counts['TRAINING_TIME_VIOLATION_COUNT'] else 'FAIL',INNER_TIME_VALIDATION_STATUS='PASS' if not counts['INNER_TIME_VIOLATION_COUNT'] else 'FAIL',PHASE8_PHYSICAL_SHA256=sha256_file(ROOT/'database/phase8_machine_learning_v1.db'),PHASE8_SEMANTIC_SHA256=dig.hexdigest(),T0_INTRADAY_BLOCKER=contract['T0'],PHASE8_T1_MODEL_CANDIDATE=manifest['t1_candidate'],PHASE8_T2_MODEL_CANDIDATE=manifest['t2_candidate'],MODEL_T1_V1_STATUS='OOS_VALIDATED' if not violations else 'FAIL',MODEL_T2_V1_STATUS='OOS_VALIDATED' if not violations else 'FAIL',PHASE8_ACCEPTANCE='PASS_WITH_WARNINGS' if not violations else 'FAIL',NEXT_PHASE_STATUS='READY_FOR_PHASE9' if not violations else 'BLOCKED_FOR_PHASE9',comparison=comparisons,reproduction=reproduction,static_scan=scan,training_reproduction_count=len(reproduction))
 (DOCS/'PHASE8_FINAL_AUDIT.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 lines=['# PHASE8 FINAL INDEPENDENT ACCEPTANCE','This audit did not call the official builder, feature builder or any official fit/training/preprocessing function. Frozen DB bytes were deserialized into RAM. All splits, preprocessing statistics and parameter selections were checked against independently read FEATURE_V1; all metrics and year/season/month slices were recalculated. Thirty early/middle/late fitted model states were independently rebuilt using library classes and saved parameters.','## Acceptance matrix',json.dumps({k:v for k,v in result.items() if k not in ('comparison','reproduction')},indent=2),'## Fair same-subset results',json.dumps(comparisons,indent=2),'## Independent reproduction',json.dumps(reproduction,indent=2),'## Candidate status','Candidate decisions describe future-phase model candidates, not a retroactively selected prediction series or system champion. The predeclared contract is physically pinned before training. Aggregate model results remain model-specific nested OOS regardless of whether a candidate exists.','## Warnings','Expanding refit is every 14 target calendar days, not daily; fitted state remains fixed within each block. Two short recent inner validation periods can yield unstable parameter choices with seasonal drift. Minimum history 180 excludes cold start but all base rows remain. Ridge uses explicit training medians; other libraries use native missing values. No probability calibration, ensemble, T0 training, real-time engine or GUI. Missingness, redundancy and backward-hour radiation/precipitation semantics are inherited.','## STOP','PHASE8_ACCEPTANCE = '+result['PHASE8_ACCEPTANCE'],'STOP AT PHASE8.']
 (DOCS/'PHASE8_FINAL_INDEPENDENT_ACCEPTANCE.md').write_text('\n\n'.join(lines),encoding='utf-8')
 print(json.dumps({k:v for k,v in result.items() if k not in ('reproduction','violations')},indent=2),flush=True)
 return result
if __name__=='__main__':audit()
