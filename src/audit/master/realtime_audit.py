import copy
import json
import math
import tempfile
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
import numpy as np
from .common import *
from .database_audit import Source,WEATHER,daily
from .feature_audit import Features

def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def ident(x):
    import hashlib
    return hashlib.sha256(canonical(x).encode()).hexdigest()

class ReadRows:
    namespace='PRODUCTION'
    def __init__(self,c):self.c=c
    def rows(self,table):return sorted(payloads(self.c,'realtime_'+table),key=lambda r:(r['_created'],r['_id']))

class Realtime:
    def __init__(self,s,f,m,p):
        self.s=s;self.f=f;self.m=m;self.p=p;c=s.get('phase10_realtime_v1.db');self.db=ReadRows(c)
        tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'realtime_%'")]
        self.tables={t.removeprefix('realtime_'):self.db.rows(t.removeprefix('realtime_')) for t in tables};self.probes_results=[];self.notes=[]

    def runtime_source(self,issue):
        q=copy.copy(self.s);q.runs_by_time={};q.runs_available=[];q.curves={}
        for r in self.tables['ecmwf_raw_runs']:
            if stamp(r['actual_ingest_time'])>stamp(issue):continue
            run=r['run_time'];old=q.runs_by_time.get(run)
            if old is None or old['source_available_time_utc']<r['actual_ingest_time']:
                q.runs_by_time[run]=dict(run_time_utc=run,canonical_raw_run_id=r['_id'],source_available_time_utc=r['actual_ingest_time'],model=r['model'])
            hourly=r['payload'].get('hourly',{});q.curves[r['_id']]={}
            for i,t in enumerate(hourly.get('time',[])):
                q.curves[r['_id']][stamp(t+'+00:00').isoformat()]={field:hourly.get(api,[None]*len(hourly['time']))[i] for field,api in WEATHER.items()}
        # Only QC-accepted hourly rows can enter actual selection.
        valid={r['run_id'] for r in self.tables['ecmwf_hourly']};q.runs_available=sorted(q.runs_by_time.values(),key=lambda r:r['run_time_utc'])
        for run in q.runs_available:
            if run['canonical_raw_run_id'] not in valid:q.curves[run['canonical_raw_run_id']]={}
        return q

    def audit(self):
        c=self.s.counts;out=[];feature={r['_id']:r for r in self.tables['feature_snapshots']};cont={r['_id']:r for r in self.tables['continuous_predictions']};prob={r['_id']:r for r in self.tables['probability_predictions']};states={r['state_id']:r for r in self.tables['probability_state']};raw={r['_id']:r for r in self.tables['ecmwf_raw_runs']};snap=self.tables['prediction_snapshots']
        half=0;lineage=[];app=[];used=defaultdict(set);runtimeformula=0;runtimeprob=0;runtime_model=0;timeviolations=0;alias=[];rawbad=0
        for r in self.tables['zuuu_raw']:
            rawbad+=int(ident(r['raw_payload'])!=r['raw_payload_hash']);stamp(r['actual_ingest_time'])
            rawbad+=int(r['actual_ingest_time'][:4]<'2026' or r['_created']!=r['actual_ingest_time'])
        zuuu={r['_id']:r for r in self.tables['zuuu_raw']}
        for r in self.tables['zuuu_normalized']:
            rawbad+=int(r['raw_id'] not in zuuu or stamp(r['actual_ingest_time'])<stamp(r['observation_time'])-timedelta(minutes=5) or r['raw_report']!=zuuu[r['raw_id']]['raw_message'])
        for rid,r in raw.items():
            rawbad+=int(ident(r['payload'])!=r['payload_hash'] or ident({'run':r['run_time'],'payload':r['payload'],'model':'IFS_HRES'})!=rid or stamp(r['actual_ingest_time'])<stamp(r['run_time']))
        for r in snap:
            rid=r['_id'];missing=[];issue=stamp(r['prediction_issue_time']);h=r['horizon'];day=r['target_business_date']
            for key,store in (('feature_id',feature),('continuous_id',cont),('probability_id',prob)):
                if r[key] not in store:missing.append(key)
                else:used[key].add(r[key])
            if r['probability_state_version'] not in states:missing.append('state')
            if r['selected_version'] not in raw:missing.append('raw_run')
            half+=bool(missing)
            if missing:lineage.append(dict(prediction_id=rid,missing=missing,status='FAIL'));continue
            x=feature[r['feature_id']];y=cont[r['continuous_id']];p=prob[r['probability_id']];state=states[r['probability_state_version']]
            rq=self.runtime_source(issue);selected=rq.choose(day,r['prediction_issue_time'])
            runbad=selected is None or selected['canonical_raw_run_id']!=r['selected_version'];c['FUTURE_ECMWF_RUN_COUNT']+=int(stamp(raw[r['selected_version']]['actual_ingest_time'])>issue or stamp(raw[r['selected_version']]['run_time'])>issue);c['ISSUE_RULE_SELECTION_MISMATCH_COUNT']+=int(runbad)
            # Independent arithmetic using runtime raw curves and the same eligible historical labels.
            rq.samples_by_key=dict(self.s.samples_by_key);rq.samples_by_key[day,h]=dict(business_date_bjt=day,horizon=h,source_raw_run_id=r['selected_version'],selected_ecmwf_run_time_utc=r['selected_run'],selected_ecmwf_source_available_time_utc=raw[r['selected_version']]['actual_ingest_time'],issue_time_utc=r['prediction_issue_time'])
            rq.independent_bias={};fq=copy.copy(self.f);fq.s=rq;fq.revision_evidence=[]
            expected,av=fq.compute(dict(target_business_date=day,horizon=h));fb=sum(not close(expected[n],x['values'][n]) for n in self.f.names);runtimeformula+=fb
            for n,l in x['lineage'].items():
                timeviolations+=stamp(l['feature_available_time'])>issue or stamp(av[n])>issue
                if l['source']=='ECMWF':c['CROSS_RUN_SPLICE_COUNT']+=int(len(set(l['input_ids']))!=1)
                if l['source']=='PHASE6':timeviolations+=sum(eligible(k['date'])>issue or k['date']>=day or k['horizon']!=h for k in l['input_ids'])
            val,native=self.m.calculate(y['model_state_id'],expected);mb=not close(val,y['continuous_prediction_c'],1e-7);runtime_model+=mb
            forecasts={'ML':val,'RAW':expected['ecmwf_tmax_c'],'MOS':expected['m6_corrected_temperature_c']};pmf=self.p.forward_independent(state,h,day,r['prediction_issue_time'],forecasts);pb=pmf is None or np.max(np.abs(pmf-np.array(p['pmf'])))>1e-9;runtimeprob+=pb
            mass=np.array(p['pmf']);c['PMF_SUM_MISMATCH_COUNT']+=int(abs(mass.sum()-1)>1e-10);c['PMF_NEGATIVE_COUNT']+=int(mass.min()<0 or mass.max()>1 or not np.isfinite(mass).all())
            state_alias=p.get('probability_state_version')!=r['probability_state_version'];alias.append(dict(snapshot=rid,snapshot_state=r['probability_state_version'],probability_state=p.get('probability_state_version'),probability_state_exists=p.get('probability_state_version') in states,different=state_alias))
            # Lineage can still resolve through immutable parent snapshot; record field conflict separately.
            lineage.append(dict(prediction_id=rid,horizon=h,model_state=y['model_state_id'],raw_version=r['selected_version'],state=r['probability_state_version'],missing=missing,parent_chain_status='PASS',probability_direct_state_reference=p.get('probability_state_version'),status='FAIL' if state_alias or p.get('probability_state_version') not in states else 'PASS'))
            exact_fixed=issue.astimezone(BJT).hour==21 and issue.astimezone(BJT).minute==0 and issue.astimezone(BJT).second==0
            app.append(dict(prediction_id=rid,issue=r['prediction_issue_time'],horizon=h,status=r['status'],fixed_21_exact=exact_fixed,daily_anchor=r['daily_anchor'],mislabelled=int(not exact_fixed and r['status']=='OK'),reason=r.get('reason_code')))
            out.append(dict(prediction_id=rid,horizon=h,target=day,issue=r['prediction_issue_time'],raw=r['selected_run'],feature_mismatches=fb,model_mismatch=int(mb),probability_mismatch=int(pb),run_selection_mismatch=int(runbad),status='FAIL' if fb or mb or pb or runbad else 'PASS'))
        orphans=sum(len(set(store)-used[k]) for k,store in [('feature_id',feature),('continuous_id',cont),('probability_id',prob)])
        c['HALF_SNAPSHOT_COUNT']=half;c['ORPHAN_RECORD_COUNT']=orphans;c['LINEAGE_CHECK_COUNT']=len(lineage);c['LINEAGE_BREAK_COUNT']=sum(r['status']=='FAIL' for r in lineage);c['PHASE10_FEATURE_MISMATCH_COUNT']=runtimeformula;c['PHASE10_MODEL_MISMATCH_COUNT']=runtime_model;c['PHASE10_PROBABILITY_MISMATCH_COUNT']=runtimeprob;c['PHASE10_TIME_VIOLATION_COUNT']=timeviolations;c['PHASE10_RAW_MISMATCH_COUNT']=rawbad
        output('MASTER_PHASE10_REALTIME_AUDIT.csv',out);output('MASTER_LINEAGE_AUDIT.csv',lineage);output('MASTER_REALTIME_APPLICABILITY_AUDIT.csv',app);output('MASTER_PROBABILITY_STATE_REFERENCE_AUDIT.csv',alias)
        self.notes.append(dict(component='probability_state_reference',different=sum(r['different'] for r in alias),unresolved_probability_reference=sum(not r['probability_state_exists'] for r in alias),examples=alias[:3]))
        stateout=[];previous=None;transbad=0
        for state in self.tables['probability_state']:
            cut=stamp(state['cutoff']);bad=sum(stamp(r['eligibility'])>cut or stamp(r['issue'])>=cut or r['horizon'] not in ('T1','T2') for r in state['residuals']+state['cases'])
            if previous:
                old={r['record_id']:r for r in previous['residuals']};new={r['record_id']:r for r in state['residuals']};bad+=int(cut<stamp(previous['cutoff']) or any(new.get(k)!=v for k,v in old.items()) or state.get('previous_state_id')!=previous['state_id'])
            transbad+=bad;stateout.append(dict(state_id=state['state_id'],cutoff=state['cutoff'],residual_n=len(state['residuals']),calibration_case_n=len(state['cases']),violations=bad,previous=state.get('previous_state_id')));previous=state
        c['FORWARD_STATE_TRANSITION_COUNT']=max(0,len(stateout)-1);c['FORWARD_STATE_CAUSALITY_VIOLATION_COUNT']=transbad
        output('MASTER_FORWARD_STATE_AUDIT.csv',stateout)
        self.s.evidence['phase10']=dict(raw_zuuu=len(zuuu),raw_ecmwf=len(raw),snapshots=len(snap),probability_state_count=len(states),real_evaluations=len(self.tables['daily_evaluation']),state_reference_notes=self.notes,live_applicability_mislabels=sum(r['mislabelled'] for r in app),operational='PENDING_SOAK')
        self.settlement_audit()

    def settlement_audit(self):
        rows=[];norm=self.tables['zuuu_normalized'];targets={r['_id']:r for r in self.tables['daily_ground_truth']};bad=0
        for t in targets.values():
            selected=[r for r in norm if r['business_date']==t['business_date_bjt'] and stamp(r['actual_ingest_time'])<=stamp(t['settlement_time'])];groups=defaultdict(list)
            for r in selected:groups[r['observation_time']].append(r)
            canonical_rows=[min(g,key=lambda r:r['actual_ingest_time']) for g in groups.values()];canonical_rows.sort(key=lambda r:r['observation_time']);inputs=[]
            for i,r in enumerate(canonical_rows,1):inputs.append(dict(id=i,bronze_raw_id=i,observation_time_utc=r['observation_time'],temperature_c=r['temperature_c'],is_correction=r['is_corrected'],message_class=r['report_type'],recovery_reason=None))
            rec=daily(inputs);mismatch=rec is None or any(rec[k]!=t[k] for k in ('daily_tmax_c','first_tmax_time_bjt','last_tmax_time_bjt','tmax_occurrence_count','hourly_coverage_count','observation_count'));bad+=mismatch
            rows.append(dict(kind='REAL_TARGET',id=t['_id'],day=t['business_date_bjt'],status=t['settlement_status'],mismatch=int(mismatch),recalculated=rec))
        for e in self.tables['daily_evaluation']:
            snap=next(r for r in self.tables['prediction_snapshots'] if r['_id']==e['snapshot_id']);pr=next(r for r in self.tables['probability_predictions'] if r['_id']==snap['probability_id']);ml=next(r for r in self.tables['continuous_predictions'] if r['_id']==snap['continuous_id']);a=e['actual'];sc=scores(pr['pmf'],a);mapping={'brier':'Brier','logloss':'LogLoss','crps':'CRPS','top1':'top1_hit','top2':'top2_hit','top3':'top3_hit','actual_probability':'assigned_probability'}
            b=any(not close(v,e[mapping[k]]) for k,v in sc.items()) or not close(ml['continuous_prediction_c']-a,e['error']);bad+=b;rows.append(dict(kind='REAL_EVALUATION',id=e['_id'],mismatch=int(b)))
        self.s.counts['SETTLEMENT_EVALUATION_MISMATCH_COUNT']=bad;output('MASTER_SETTLEMENT_EVALUATION_AUDIT.csv',rows)

    def parity(self):
        from src.realtime.features import construct
        from src.realtime.handoff import forward
        selected=[]
        for h in ('T1','T2'):
            pop=[r for r in self.m.formal.values() if r['horizon']==h and r['ml_prediction'] is not None and r['input_sample_id'] in self.p.engine and self.p.engine[r['input_sample_id']]['record_id'] in self.p.mass]
            selected.extend(select_stratified(pop,30,day='target_business_date'))
        manifest=dict(seed=SEED,population={h:sum(r['horizon']==h and r['ml_prediction'] is not None for r in self.m.formal.values()) for h in ('T1','T2')},method='season/time thirds plus extremes, fixed seed',sample_ids=[r['input_sample_id'] for r in selected]);output('MASTER_SAMPLE_MANIFEST.json',manifest)
        runs=self.s.runs_by_time;curves={};
        for r in self.s.runs_available:
            rid=r['canonical_raw_run_id'];curves[rid]={t:dict(v,target_time_utc=t,lead_hours=(stamp(t)-stamp(r['run_time_utc'])).total_seconds()/3600,id=i) for i,(t,v) in enumerate(self.s.curves[rid].items())}
        parity=[];end=[]
        for row in selected:
            sid=row['input_sample_id'];day=row['target_business_date'];h=row['horizon'];issue=row['prediction_issue_time'];expected=self.f.independent[sid]
            actual=construct(day,h,issue,runs,curves,self.s.samples6);fb=sum(not close(expected[n],actual['values'][n]) for n in self.f.names)
            model,native=self.m.calculate(row['state_id'],actual['values']);mb=not close(model,row['ml_prediction'],1e-7)
            state={'cutoff':issue,'residuals':[r for r in self.p.residuals if stamp(r['eligibility'])<=stamp(issue) and stamp(r['issue'])<stamp(issue)],'cases':[]}
            for r in self.p.pred.values():
                if r['origin'] in ('ML','RAW','MOS') and r['record_id'] in self.p.mass and eligible(r['target_business_date'])<=stamp(issue) and stamp(r['issue'])<stamp(issue):state['cases'].append(dict(r,pmf=self.p.mass[r['record_id']]['pmf'].tolist()))
            forecasts={'ML':model,'RAW':expected['ecmwf_tmax_c'],'MOS':expected['m6_corrected_temperature_c']};live,_=forward(state,h,day,issue,forecasts);independent=self.p.forward_independent(state,h,day,issue,forecasts);saved=self.p.mass[self.p.engine[sid]['record_id']]['pmf'];diff=float(np.max(np.abs(np.array(live['pmf'])-saved)));indiff=float(np.max(np.abs(independent-saved)));pb=diff>1e-9 or indiff>1e-9
            parity.append(dict(sample_id=sid,horizon=h,issue=issue,feature_cells=102,feature_mismatches=fb,continuous_difference=model-row['ml_prediction'],pmf_difference=diff,independent_pmf_difference=indiff,mismatch=int(fb or mb or pb),status='FAIL' if fb or mb or pb else 'PASS'))
            truth=self.s.truth[day]['daily_tmax_c'];chosen=self.s.choose(day,issue);sc=scores(independent,truth)
            end.append(dict(sample_id=sid,horizon=h,date=day,issue=issue,selected_raw_run=chosen['canonical_raw_run_id'] if chosen else None,run=chosen['run_time_utc'] if chosen else None,raw_tmax=expected['ecmwf_tmax_c'],feature_count=102,model_state=row['state_id'],continuous=model,probability_method=self.p.engine[sid]['method'],actual=truth,**sc,mismatch=int(fb or mb or pb),status='FAIL' if fb or mb or pb else 'PASS'))
        self.s.counts['HISTORICAL_REALTIME_PARITY_CHECK_COUNT']=len(parity);self.s.counts['HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT']=sum(r['mismatch'] for r in parity);self.s.counts['END_TO_END_CHECK_COUNT']=len(end);self.s.counts['END_TO_END_MISMATCH_COUNT']=sum(r['mismatch'] for r in end)
        output('MASTER_HISTORICAL_REALTIME_PARITY.csv',parity);output('MASTER_END_TO_END_REPRODUCTION.csv',end)
        historical_lineage=[]
        for row in end:
            sid=row['sample_id'];model=self.m.formal[sid];sample=self.f.samples[sid];prob=self.p.engine[sid]
            absent=[name for name,ok in [('feature',sid in self.f.values),('model_asset',(ROOT/self.m.states[model['state_id']]['artifact_path']).is_file()),('raw',row['selected_raw_run'] in self.s.rawruns),('target',row['date'] in self.s.truth),('probability',prob.get('selected_probability_id') in self.p.mass if prob.get('selected_probability_id') else prob['record_id'] in self.p.mass)] if not ok]
            historical_lineage.append(dict(prediction_id=prob['record_id'],horizon=row['horizon'],model_state=model['state_id'],raw_version=row['selected_raw_run'],missing=absent,status='FAIL' if absent else 'PASS',namespace='FROZEN_HISTORICAL'))
        output('MASTER_HISTORICAL_LINEAGE_AUDIT.csv',historical_lineage)
        self.s.counts['LINEAGE_CHECK_COUNT']+=len(historical_lineage);self.s.counts['LINEAGE_BREAK_COUNT']+=sum(r['status']=='FAIL' for r in historical_lineage)

    def probes(self):
        from .probes import run_probes
        self.probes_results=run_probes();output('MASTER_FAILURE_PROBES.csv',self.probes_results)
        self.s.counts['MASTER_FAILURE_PROBE_FAILURE_COUNT']=sum(r['status']=='FAIL' for r in self.probes_results)
