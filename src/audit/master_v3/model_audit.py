import json
import math
import pickle
from collections import defaultdict
import numpy as np
from .common import *

def independent_transform(x,p):
    a=np.asarray(x,float)[p['indices']].copy()
    if p['family']!='RIDGE':return a
    missing=np.isnan(a);a[missing]=np.asarray(p['medians'])[missing]
    if p['indicator_indices']:a=np.r_[a,missing[p['indicator_indices']].astype(float)]
    return (a-np.array(p['means']))/np.array(p['scales'])

def tree_value(node,x):
    while 'leaf_value' not in node:
        v=x[node['split_feature']];missing=node.get('missing_type','None')
        if math.isnan(v):
            if missing=='NaN':left=node['default_left']
            else:v=0.;left=node['default_left'] if missing=='Zero' else v<=float(node['threshold'])
        elif missing=='Zero' and abs(v)<=1e-35:left=node['default_left']
        elif node['decision_type']=='<=':left=v<=float(node['threshold'])
        else:left=int(v) in set(map(int,node['threshold'].split('||')))
        node=node['left_child'] if left else node['right_child']
    return node['leaf_value']

class Models:
    def __init__(self,s,features):
        self.s=s;self.f=features;c=s.get('phase8_machine_learning_v1.db')
        self.samples={r['sample_id']:r for r in records(c,'phase8_sample')};self.predictions=records(c,'phase8_prediction')
        self.states={r['record_id']:r for r in records(c,'phase8_state')};self.splits={r['record_id']:r for r in records(c,'phase8_split')};self.preps=records(c,'phase8_preprocessing');self.loaded={};self.reproduced={}
        self.formal={(r['input_sample_id']):r for r in self.predictions if r['model_family']==('RIDGE' if r['horizon']=='T1' else 'LIGHTGBM')}
        self.X={sid:np.array([features.values[sid][n] for n in features.names],float) for sid in features.samples}

    def load(self,stateid):
        if stateid not in self.loaded:
            st=self.states[stateid];p=ROOT/st['artifact_path']
            if not p.resolve().is_relative_to((ROOT/'docs/phase8/model_states').resolve()) or digest(p)!=st['artifact_sha256']:raise ValueError('UNTRUSTED_FROZEN_MODEL_ASSET')
            # Same workstation libraries, inference only; no untrusted network pickle.
            import sklearn,lightgbm,xgboost,catboost
            artifact=pickle.loads(p.read_bytes());dump=artifact['estimator'].booster_.dump_model() if st['model_family']=='LIGHTGBM' else None
            self.loaded[stateid]=(artifact,dump)
        return self.loaded[stateid]

    def calculate(self,stateid,values):
        artifact,dump=self.load(stateid);st=self.states[stateid];x=np.array([values[n] for n in self.f.names],float);z=independent_transform(x,artifact['preprocessor']);raw=values['ecmwf_tmax_c']
        if st['model_family']=='RIDGE':pred=float(raw+artifact['estimator'].intercept_+np.dot(artifact['estimator'].coef_,z));native=None
        else:
            pred=float(raw+math.fsum(tree_value(t['tree_structure'],z) for t in dump['tree_info']));native=float(raw+artifact['estimator'].booster_.predict(z[None,:],num_threads=1)[0])
        return pred,native

    def audit(self):
        split_out=[];ppbad=0;hypbad=0;modelselect=0
        for sid,r in self.splits.items():
            tr=json.loads(r['training_ids_json']);va=json.loads(r['validation_ids_json']);cut=stamp(r['training_cutoff'])
            bad=sum(self.samples[i]['horizon']!=r['horizon'] or stamp(self.samples[i]['label_eligibility_time'])>cut or stamp(self.samples[i]['issue_time_utc'])>=cut or self.samples[i]['target_business_date']>=r['outer_target_date'] for i in tr)
            if va:bad+=int(set(tr)&set(va)!=set() or max(self.samples[i]['target_business_date'] for i in tr)>=min(self.samples[i]['target_business_date'] for i in va))
            split_out.append(dict(split_id=sid,stage=r['stage'],training_n=len(tr),validation_n=len(va),cutoff=r['training_cutoff'],violations=bad));ppbad+=bad
        for prep in self.preps:
            p=json.loads(prep['preprocessing_json']);tr=json.loads(self.splits[prep['split_id']]['training_ids_json']);a=np.array([self.X[i] for i in tr]);names=self.f.names;kept=[];dropped={}
            for j,n in enumerate(names):
                finite=a[:,j][np.isfinite(a[:,j])]
                if len(finite)==0:dropped[n]='ALL_NULL_TRAINING_FEATURE';continue
                other=next((k for k in kept if np.array_equal(a[:,j],a[:,k],equal_nan=True)),None)
                if other is not None:dropped[n]='EXACT_DUPLICATE_OF_'+names[other];continue
                if np.var(finite)<=1e-12:dropped[n]='CONSTANT_TRAINING_VARIANCE';continue
                kept.append(j)
            b=int(p['indices']!=kept or p['training_rows']!=len(tr) or p['dropped']!=dropped)
            b+=int(p['valid_counts']!=np.isfinite(a).sum(axis=0).tolist() or p['null_counts']!=np.isnan(a).sum(axis=0).tolist())
            if p['family']=='RIDGE':
                z=a[:,kept];med=np.nanmedian(z,axis=0);indicator=np.flatnonzero(np.isnan(z).any(axis=0));filled=np.where(np.isnan(z),med,z)
                if len(indicator):filled=np.column_stack((filled,np.isnan(z[:,indicator]).astype(float)))
                scale=filled.std(axis=0);scale[scale<=1e-12]=1
                b+=int(not np.allclose(med,p['medians'],atol=1e-10,rtol=0) or indicator.tolist()!=p['indicator_indices'] or not np.allclose(filled.mean(axis=0),p['means'],atol=1e-10,rtol=0) or not np.allclose(scale,p['scales'],atol=1e-10,rtol=0))
            ppbad+=b
        c=self.s.get('phase8_machine_learning_v1.db');inner=records(c,'phase8_inner_prediction');groups=defaultdict(list)
        for r in inner:groups[r['state_id'],r['candidate_id']].append(r)
        hyper=records(c,'phase8_hyperparameter');hg=defaultdict(list)
        for r in hyper:
            g=groups[r['state_id'],r['candidate_id']];mae=np.mean([abs(x['continuous_prediction']-self.samples[x['sample_id']]['actual_target']) for x in g]);hypbad+=not close(mae,r['inner_mae']);hg[r['state_id']].append(r)
        for stid,g in hg.items():
            choice=min(g,key=lambda r:(r['inner_mae'],r['candidate_id']));hypbad+=int(choice['candidate_id']!=self.states[stid]['selected_candidate_id'] or sum(r['selected'] for r in g)!=1 or not choice['selected'])
        output('MASTER_MODEL_TIME_AUDIT.csv',split_out)
        out=[]
        for sid,p in sorted(self.formal.items()):
            if p['ml_prediction'] is None:continue
            st=self.states[p['state_id']];bad=stamp(st['training_cutoff'])>stamp(p['prediction_issue_time']) or st['horizon']!=p['horizon'] or p['feature_version']!='FEATURE_V1'
            value,native=self.calculate(p['state_id'],self.f.independent[sid]);self.reproduced[sid]=value
            mismatch=not close(value,p['ml_prediction'],1e-7) or native is not None and not close(value,native,1e-7)
            out.append(dict(sample_id=sid,horizon=p['horizon'],state=p['state_id'],training_cutoff=st['training_cutoff'],stored=p['ml_prediction'],independent=value,native=native,difference=value-p['ml_prediction'],independent_method='RIDGE_INTERCEPT_DOT_FROZEN_PREPROCESSOR' if p['horizon']=='T1' else 'DUMPED_TREE_TRAVERSAL',mismatch=int(mismatch),causality_violations=int(bad)))
            ppbad+=bad
        for h in ('T1','T2'):
            rows=[r for r in out if r['horizon']==h];self.s.counts['MODEL_'+h+'_REPRO_CHECK_COUNT']=len(rows);self.s.counts['MODEL_'+h+'_REPRO_MISMATCH_COUNT']=sum(r['mismatch'] for r in rows)
        self.s.counts['PREPROCESSING_LEAKAGE_COUNT']=ppbad;self.s.counts['HYPERPARAMETER_SELECTION_LEAKAGE_COUNT']=hypbad
        manifest=records(c,'phase8_manifest')[0];modelselect+=manifest['t1_candidate']!='RIDGE' or manifest['t2_candidate']!='LIGHTGBM'
        self.s.counts['MODEL_SELECTION_LEAKAGE_COUNT']=int(modelselect)
        self.s.evidence['phase8']=dict(candidate_T1=manifest['t1_candidate'],candidate_T2=manifest['t2_candidate'],all_state_count=len(self.states),preprocessor_check_count=len(self.preps),split_count=len(self.splits),independent_reproduction='ALL formal non-null OOS; no fitting performed')
        output('MASTER_MODEL_REPRODUCTION.csv',out)

