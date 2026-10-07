"""Nested chronological expanding-block evaluation with daily outer records."""
import pickle
import hashlib
import time
from datetime import date,timedelta
import numpy as np
from .contracts import *
from .data import eligible
from .pipeline import fit_preprocessor,transform,estimator

def folds(z,train):
 require(len(train)>=MIN_HISTORY,'Cold start')
 out=[]
 for k,val in enumerate((train[-2*INNER_DAYS:-INNER_DAYS],train[-INNER_DAYS:])):
  first=z['samples'][int(val[0])];tr=eligible(z,first['issue_time_utc'],first['target_business_date'])
  tr=np.intersect1d(tr,train)
  require(len(tr)>=INNER_MIN_HISTORY and not np.intersect1d(tr,val).size,'Inner chronology/history')
  out.append((k,tr,val,first['issue_time_utc']))
 return out

def run(source,artifact_dir):
 data={name:[] for name in ('sample','split','preprocessing','hyperparameter','inner_prediction','state','prediction')}
 artifact_dir.mkdir(parents=True,exist_ok=False)
 for h,z in source.items():
  for i,s in enumerate(z['samples']):data['sample'].append(dict(record_id=s['sample_id'],**s,label_eligibility_time=z['eligibility'][i],actual_target=float(z['y'][i]),raw_prediction=float(z['raw'][i]),mos_prediction=None if np.isnan(z['mos'][i]) else float(z['mos'][i])))
  active=None;expiry=None
  for i,s in enumerate(z['samples']):
   day=date.fromisoformat(s['target_business_date']);available=eligible(z,s['issue_time_utc'],s['target_business_date'])
   if len(available)>=MIN_HISTORY and (active is None or day>=expiry):
    active={};expiry=day+timedelta(days=BLOCK_DAYS);train=available
    outer_id=h+'/'+s['target_business_date'];fi=folds(z,train)
    def split(stage,tr,va,cutoff,suffix):
     rid=outer_id+'/'+stage+'/'+suffix
     data['split'].append(dict(record_id=rid,horizon=h,outer_target_date=s['target_business_date'],stage=stage,training_cutoff=cutoff,training_start=z['ids'][int(tr[0])],training_end=z['ids'][int(tr[-1])],training_sample_count=len(tr),training_ids_json=canonical([z['ids'][int(t)] for t in tr]),validation_ids_json=canonical([z['ids'][int(t)] for t in va]),validation_start=z['ids'][int(va[0])] if len(va) else None,validation_end=z['ids'][int(va[-1])] if len(va) else None))
     return rid
    outer_split=split('OUTER',train,[],s['issue_time_utc'],'refit')
    inner_splits={k:split('INNER',tr,val,cutoff,str(k)) for k,tr,val,cutoff in fi}
    for family in FAMILIES:
     start=time.perf_counter();outer_state_id=outer_id+'/'+family;scores=[];preps={}
     for k,tr,val,cutoff in fi:
      prep=fit_preprocessor(z['X'][tr],z['names'],family);preps[k]=prep
      prep_id=outer_state_id+'/INNER/'+str(k)
      data['preprocessing'].append(dict(record_id=prep_id,state_id=outer_state_id,split_id=inner_splits[k],horizon=h,model_family=family,stage='INNER',training_cutoff=cutoff,preprocessing_json=canonical(prep)))
     for candidate_id,params in enumerate(GRIDS[family]):
      errors=[]
      for k,tr,val,cutoff in fi:
       est=estimator(family,params);prep=preps[k]
       est.fit(transform(z['X'][tr],prep),z['y'][tr]-z['raw'][tr])
       pred=z['raw'][val]+est.predict(transform(z['X'][val],prep));errors.extend(np.abs(pred-z['y'][val]).tolist())
       for j,p in zip(val,pred):
        data['inner_prediction'].append(dict(record_id=f'{outer_state_id}/{candidate_id}/{k}/{z["ids"][int(j)]}',state_id=outer_state_id,horizon=h,model_family=family,candidate_id=candidate_id,fold=k,split_id=inner_splits[k],sample_id=z['ids'][int(j)],continuous_prediction=float(p),observed=float(z['y'][int(j)]),absolute_error=float(abs(p-z['y'][int(j)]))))
      score=float(np.mean(errors));scores.append(score)
      data['hyperparameter'].append(dict(record_id=outer_state_id+'/'+str(candidate_id),state_id=outer_state_id,horizon=h,model_family=family,candidate_id=candidate_id,parameters_json=canonical(params),selection_metric='POOLED_INNER_MAE',inner_mae=score,inner_split_ids_json=canonical(list(inner_splits.values())),validation_sample_count=len(errors),selected=0,random_seed=SEED))
     selected=min(range(len(scores)),key=lambda k:(scores[k],k));params=GRIDS[family][selected]
     for row in data['hyperparameter']:
      if row['state_id']==outer_state_id:row['selected']=int(row['candidate_id']==selected)
     prep=fit_preprocessor(z['X'][train],z['names'],family);prep_id=outer_state_id+'/OUTER'
     data['preprocessing'].append(dict(record_id=prep_id,state_id=outer_state_id,split_id=outer_split,horizon=h,model_family=family,stage='OUTER',training_cutoff=s['issue_time_utc'],preprocessing_json=canonical(prep)))
     est=estimator(family,params);est.fit(transform(z['X'][train],prep),z['y'][train]-z['raw'][train])
     path=artifact_dir/(outer_state_id.replace('/','_')+'.pkl')
     payload=pickle.dumps(dict(estimator=est,preprocessor=prep),protocol=5)
     with path.open('xb') as f:f.write(payload)
     importance=est.coef_.tolist() if family=='RIDGE' else est.feature_importances_.tolist()
     row=dict(record_id=outer_state_id,horizon=h,model_family=family,model_version='MODEL_'+h+'_V1',feature_version='FEATURE_V1',outer_split_id=outer_split,preprocessing_id=prep_id,training_cutoff=s['issue_time_utc'],training_sample_count=len(train),parameters_json=canonical(params),selected_candidate_id=selected,selection_metric='POOLED_INNER_MAE',selected_inner_mae=scores[selected],random_seed=SEED,input_feature_count=prep['input_feature_count'],artifact_path=path.relative_to(ROOT).as_posix(),artifact_sha256=hashlib.sha256(payload).hexdigest(),importance_json=canonical(importance),fit_seconds=time.perf_counter()-start)
     data['state'].append(row);active[family]=(row,est,prep)
    print('Completed expanding refit',h,s['target_business_date'],'eligible training N',len(train),flush=True)
   for family in FAMILIES:
    if active is None:p=None;row=None;prep=None;status='INSUFFICIENT_TRAINING_HISTORY'
    else:
     row,est,prep=active[family];p=float(z['raw'][i]+est.predict(transform(z['X'][i:i+1],prep))[0]);status='OOS'
     require(utc(row['training_cutoff'])<=utc(s['issue_time_utc']),'Future fitted state')
    data['prediction'].append(dict(record_id=s['sample_id']+'/'+family,prediction_id=s['sample_id']+'/'+family,horizon=h,target_business_date=s['target_business_date'],prediction_issue_time=s['issue_time_utc'],feature_version='FEATURE_V1',model_family=family,model_version='MODEL_'+h+'_V1',state_id=row['record_id'] if row else None,hyperparameters=row['parameters_json'] if row else None,training_cutoff=row['training_cutoff'] if row else None,training_sample_count=row['training_sample_count'] if row else len(available),eligible_history_at_issue=len(available),input_feature_count=prep['input_feature_count'] if prep else 0,raw_ecmwf_prediction=float(z['raw'][i]),mos_prediction=None if np.isnan(z['mos'][i]) else float(z['mos'][i]),ml_prediction=p,actual_target=float(z['y'][i]),error=None if p is None else float(p-z['y'][i]),absolute_error=None if p is None else float(abs(p-z['y'][i])),status=status,selected_run=s['selected_run'],run_available_time=s['run_available_time'],input_sample_id=s['sample_id'],input_null_features_json=canonical([n for n,v in zip(z['names'],z['X'][i]) if np.isnan(v)]),missing_reasons_json=canonical({n:z['missing_reasons'].get((s['sample_id'],n)) for n,v in zip(z['names'],z['X'][i]) if np.isnan(v)})))
 return data
