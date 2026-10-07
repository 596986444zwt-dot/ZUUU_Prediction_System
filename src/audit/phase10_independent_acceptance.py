"""Direct frozen-output comparisons plus independent runtime DB math checks."""
import csv
import json
import hashlib
import math
import sqlite3
import tempfile
from pathlib import Path
import numpy as np
from src.data_v1.source_io import open_snapshot,sha256_file
from src.realtime.contracts import ROOT,now,utc,identity,eligibility

DOCS=ROOT/'docs/phase10'

def csv_write(name,rows):
    if not rows:return
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with (DOCS/name).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

def independent_scores(p,actual):
    support=np.arange(-80,81);one=(support==actual).astype(float)
    cdf=np.cumsum(p)
    return {'Brier':float(np.sum((p-one)**2)),'LogLoss':float(-math.log(p[int(actual)+80])),
            'CRPS':float(np.sum((cdf[:-1]-(support[:-1]>=actual))**2))}

def golden():
    """Frozen expected values are direct SELECTs; runtime is the subject under test."""
    from src.features.data import load
    from src.realtime.features import construct
    from src.realtime.handoff import Models,bootstrap,forward
    source=load()
    c=open_snapshot(ROOT/'database/phase7_feature_v1.db')
    expected={r['sample_id']:dict(r) for r in c.execute('SELECT * FROM training_feature_view')};c.close()
    c=open_snapshot(ROOT/'database/phase8_machine_learning_v1.db')
    ml={(r['target_business_date'],r['horizon']):dict(r) for r in c.execute("SELECT * FROM phase8_prediction WHERE (horizon='T1' AND model_family='RIDGE') OR (horizon='T2' AND model_family='LIGHTGBM')")};c.close()
    c=open_snapshot(ROOT/'database/phase9_probability_v1.db')
    pp={json.loads(r['payload_json'])['record_id']:json.loads(r['payload_json']) for r in c.execute('SELECT payload_json FROM phase9_probability_prediction')}
    masses={r['record_id']:np.frombuffer(r['pmf'],dtype='<f8').copy() for r in c.execute('SELECT * FROM phase9_probability_mass')};c.close()
    models=Models(now());results=[];edges=[]
    for h in ('T1','T2'):
        available=[r for r in source['samples'] if r['horizon']==h and ml[r['business_date_bjt'],h]['ml_prediction'] is not None]
        indices=sorted(set([0,59,150,len(available)//2,len(available)-60,len(available)-1]))
        for i in indices:
            s=available[i];day=s['business_date_bjt'];issue=s['issue_time_utc'];pred=ml[day,h]
            # Full frozen archive is filtered by runtime selector and causal bias operator.
            feature=construct(day,h,issue,source['runs'],source['trajectories_full'] if 'trajectories_full' in source else _full_trajectories(source),list(source['history'].values()))
            diffs=[]
            for name,value in feature['values'].items():
                wanted=expected[day+'/'+h][name]
                diffs.append(0. if value is None and wanted is None else math.inf if value is None or wanted is None else abs(value-wanted))
            value,state=models.predict(h,feature['values'],issue,pred['state_id'])
            boot=bootstrap(issue)
            prob,variants=forward(boot,h,day,issue,{'ML':value,'RAW':pred['raw_ecmwf_prediction'],'MOS':pred['mos_prediction']})
            eid=day+'/'+h+'/ENGINE';saved=pp[eid];pdiff=float(np.max(np.abs(np.array(prob['pmf'])-masses[eid])))
            violation=sum(utc(r['eligibility'])>utc(issue) for r in boot['residuals'])+sum(utc(r['eligibility'])>utc(issue) for r in boot['cases'])
            row=dict(horizon=h,target_business_date=day,issue=issue,feature_max_difference=max(diffs),continuous_stored=pred['ml_prediction'],
                continuous_runtime=value,continuous_difference=abs(value-pred['ml_prediction']),method_stored=saved['method'],method_runtime=prob['method'],
                pmf_max_difference=pdiff,status=prob['status'],state_residual_n=len(boot['residuals']),state_cases_n=len(boot['cases']),causality_violations=violation,
                comparison='DIRECT_FROZEN_PHASE7_8_9_SELECTS')
            row['PASS']=row['feature_max_difference']<1e-9 and row['continuous_difference']<1e-8 and pdiff<1e-9 and row['method_stored']==row['method_runtime'] and violation==0
            results.append(row)
            edges.append(dict(horizon=h,cutoff=issue,residual_n=len(boot['residuals']),calibration_case_n=len(boot['cases']),future_eligible_count=violation,
                              method=prob['method'],residual_history_used=prob['residual_n'],calibration_n=prob['calibration_n']))
            print('GOLDEN',h,day,row['PASS'],max(diffs),pdiff,flush=True)
    csv_write('PHASE10_GOLDEN_REPRODUCTION.csv',results)
    csv_write('PHASE10_FORWARD_STATE_AUDIT.csv',edges)
    return results

def _full_trajectories(source):
    """All original weather rows for the selected vintage, no value substitution."""
    if 'trajectories_full' in source:return source['trajectories_full']
    trajectories={rid:dict(rows) for rid,rows in source['trajectories'].items()}
    for rows in source['hours'].values():
        for r in rows:
            rid=r['source_raw_run_id'] if 'source_raw_run_id' in r else r['raw_run_id']
            trajectories.setdefault(rid,{})[r['target_time_utc']]=dict(r)
    source['trajectories_full']=trajectories
    return trajectories

def database_audit(path):
    c=sqlite3.connect('file:'+Path(path).as_posix()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    def rows(t):return {r['record_id']:json.loads(r['payload_json']) for r in c.execute('SELECT * FROM realtime_'+t)}
    x=rows('feature_snapshots');ml=rows('continuous_predictions');prob=rows('probability_predictions');snap=rows('prediction_snapshots')
    counts={'LEAKAGE_VIOLATION_COUNT':0,'HALF_SNAPSHOT_COUNT':0,'PMF_SUM_ERROR_COUNT':0,'CROSS_RUN_SPLICE_COUNT':0,'SILENT_IMPUTATION_COUNT':0,'EVALUATION_SCORE_MISMATCH_COUNT':0,'FUTURE_ECMWF_COUNT':0,'RAW_RUN_HASH_MISMATCH_COUNT':0}
    raw=rows('ecmwf_raw_runs')
    for rid,r in raw.items():
        counts['RAW_RUN_HASH_MISMATCH_COUNT']+=int(identity({'run':r['run_time'],'payload':r['payload'],'model':'IFS_HRES'})!=rid)
    for rid,s in snap.items():
        if not all(s[key] in store for key,store in [('feature_id',x),('continuous_id',ml),('probability_id',prob)]):counts['HALF_SNAPSHOT_COUNT']+=1;continue
        feature=x[s['feature_id']];p=prob[s['probability_id']];issue=utc(s['prediction_issue_time'])
        selected=raw.get(s['selected_version'])
        counts['FUTURE_ECMWF_COUNT']+=int(selected is None or utc(selected['run_time'])>issue or utc(selected['actual_ingest_time'])>issue)
        for f in feature['lineage'].values():
            counts['LEAKAGE_VIOLATION_COUNT']+=int(utc(f['feature_available_time'])>issue)
            if f['source']=='PHASE6':
                for label in f['input_ids']:
                    counts['LEAKAGE_VIOLATION_COUNT']+=int(utc(label['eligibility'])>issue or label['date']>=s['target_business_date'] or label['horizon']!=s['horizon'])
            if f['source']=='ECMWF':counts['CROSS_RUN_SPLICE_COUNT']+=int(len(set(f['input_ids']))!=1)
        mass=np.array(p['pmf']);counts['PMF_SUM_ERROR_COUNT']+=int(not np.isfinite(mass).all() or np.min(mass)<0 or np.max(mass)>1 or abs(mass.sum()-1)>1e-10 or np.max(np.abs(np.cumsum(mass)-p['cdf']))>1e-10 or np.max(np.abs((1-np.r_[0,np.cumsum(mass)[:-1]])-p['survival']))>1e-10)
        if feature['horizon']=='T2' and feature['values']['ecmwf_tmax_revision_24h_c'] is not None:
            # Full older run could legitimately cover a different issue context; check lineage, never blanket assume NULL.
            counts['CROSS_RUN_SPLICE_COUNT']+=int(len(feature['lineage']['ecmwf_tmax_revision_24h_c']['input_ids'])!=2)
    for r in rows('daily_evaluation').values():
        p=np.array(prob[snap[r['snapshot_id']]['probability_id']]['pmf']);maths=independent_scores(p,r['actual'])
        counts['EVALUATION_SCORE_MISMATCH_COUNT']+=int(any(abs(maths[k]-r[k])>1e-10 for k in maths))
    counts['FUTURE_RESIDUAL_COUNT']=0;counts['FUTURE_CALIBRATION_LABEL_COUNT']=0;counts['CROSS_HORIZON_CONTAMINATION_COUNT']=0
    states=rows('probability_state')
    counts['UNRESOLVED_PROBABILITY_STATE_REFERENCE_COUNT']=sum(p.get('probability_state_version') not in states for p in prob.values())
    for state in states.values():
        if state.get('record_type')=='LINEAGE_ALIAS':
            canonical=states.get(state['canonical_state_id'])
            if canonical is None or canonical.get('record_type')=='LINEAGE_ALIAS' or identity(canonical)!=state['canonical_payload_sha256']:
                raise ValueError('INVALID_PROBABILITY_STATE_ALIAS')
            continue
        cutoff=utc(state['cutoff'])
        counts['FUTURE_RESIDUAL_COUNT']+=sum(utc(r['eligibility'])>cutoff or utc(r['issue'])>=cutoff for r in state['residuals'])
        counts['FUTURE_CALIBRATION_LABEL_COUNT']+=sum(utc(r['eligibility'])>cutoff or utc(r['issue'])>=cutoff for r in state['cases'])
        counts['CROSS_HORIZON_CONTAMINATION_COUNT']+=sum(r['horizon'] not in ('T1','T2') for r in state['residuals']+state['cases'])
    counts['integrity']=[r[0] for r in c.execute('PRAGMA integrity_check')];counts['foreign_keys']=len(c.execute('PRAGMA foreign_key_check').fetchall())
    counts['snapshot_count']=len(snap);c.close();return counts

def forward_replay():
    """Separate truth oracle; outcomes enter state only at virtual eligibility."""
    from src.realtime.handoff import bootstrap,forward
    from src.probability.distribution import score
    c=open_snapshot(ROOT/'database/phase9_probability_v1.db')
    all_res=[json.loads(r[0]) for r in c.execute('SELECT payload_json FROM phase9_residual_history')]
    samples=[json.loads(r[0]) for r in c.execute('SELECT payload_json FROM phase9_sample')]
    cases=[]
    for raw,mass in c.execute('SELECT p.payload_json,m.pmf FROM phase9_probability_prediction p JOIN phase9_probability_mass m USING(record_id)'):
        r=json.loads(raw)
        if r['origin'] in ('ML','RAW','MOS'):
            r['pmf']=np.frombuffer(mass,dtype='<f8').tolist();cases.append(r)
    wanted={}
    for raw,mass in c.execute("SELECT p.payload_json,m.pmf FROM phase9_probability_prediction p JOIN phase9_probability_mass m USING(record_id)"):
        r=json.loads(raw)
        if r['origin']=='ENGINE':wanted[r['record_id']]=np.frombuffer(mass,dtype='<f8').copy()
    c.close();output=[]
    for h in ('T1','T2'):
        days=sorted((r for r in samples if r['horizon']==h and '2026-08-19'<=r['target_business_date']<='2026-09-01'),key=lambda r:r['issue'])
        first=days[0]['issue'];state=bootstrap(first)
        delayed_res=[r for r in all_res if r['horizon']==h and utc(r['issue'])<utc(first) and utc(r['eligibility'])>utc(first)]
        delayed_cases=[r for r in cases if r['horizon']==h and utc(r['issue'])<utc(first) and utc(r['eligibility'])>utc(first)]
        pending=[]
        for sample in days:
            issue=sample['issue'];target=sample['target_business_date']
            # Oracle is accessed here only after virtual settlement eligibility.
            for r in list(delayed_res):
                if utc(r['eligibility'])<=utc(issue):state['residuals'].append(r);delayed_res.remove(r)
            for r in list(delayed_cases):
                if utc(r['eligibility'])<=utc(issue):state['cases'].append(r);delayed_cases.remove(r)
            for entry in list(pending):
                if utc(entry['eligibility'])<=utc(issue):
                    for origin in ('ML','RAW','MOS'):
                        if entry[origin] is not None:state['residuals'].append(dict(record_id=entry['sample_id']+'/'+origin,horizon=h,target_business_date=entry['target_business_date'],
                            issue=entry['issue'],eligibility=entry['eligibility'],origin=origin,residual=entry['actual']-entry[origin]))
                    for variant in entry['variants']:
                        state['cases'].append(dict(variant,record_id=entry['sample_id']+'/'+variant['origin']+'/'+variant['method'],horizon=h,
                            target_business_date=entry['target_business_date'],issue=entry['issue'],eligibility=entry['eligibility'],actual=entry['actual'],**score(np.array(variant['pmf']),entry['actual'])))
                    pending.remove(entry)
            p,variants=forward(state,h,target,issue,{k:sample[k] for k in ('ML','RAW','MOS')})
            difference=float(np.max(np.abs(np.array(p['pmf'])-wanted[sample['sample_id']+'/ENGINE'])))
            violations=sum(utc(r['eligibility'])>utc(issue) for r in state['residuals']+state['cases'])
            output.append(dict(horizon=h,cutoff=issue,target=target,pmf_max_difference=difference,future_eligible_count=violations,
                               residual_n=p['residual_n'],calibration_n=p['calibration_n'],method=p['method'],PASS=difference<1e-9 and violations==0))
            pending.append(dict(sample,variants=variants))
            state['cutoff']=issue
            print('FORWARD_REPLAY',h,target,difference,violations,flush=True)
    csv_write('PHASE10_FORWARD_STATE_SEQUENTIAL_REPLAY.csv',output)
    return output

if __name__=='__main__':
    r=golden();print(json.dumps({'GOLDEN_REPRODUCTION_MISMATCH_COUNT':sum(not row['PASS'] for row in r)},indent=2))
