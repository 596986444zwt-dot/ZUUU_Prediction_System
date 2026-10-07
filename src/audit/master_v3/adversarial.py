"""New V2 risk registry, alias oracle, controlled anchor probes and causal replay."""
import copy,json,math,random,calendar
from collections import defaultdict
from unittest.mock import patch
import numpy as np
from .common import *

def clean(row):return {k:v for k,v in row.items() if k not in ('_id','_created')}
def ident(row):return hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def resolve(states,reference,validated_hashes=None):
    validated_hashes={} if validated_hashes is None else validated_hashes
    seen=[]
    while True:
        if reference in seen:raise ValueError('ALIAS_CYCLE')
        seen.append(reference)
        if reference not in states:raise ValueError('MISSING_CANONICAL')
        row=states[reference]
        if row.get('state_id')!=reference:raise ValueError('STATE_ID_MISMATCH')
        if row.get('record_type')!='LINEAGE_ALIAS':return reference,row,seen
        canonical=row.get('canonical_state_id')
        if canonical not in states:raise ValueError('MISSING_CANONICAL')
        if states[canonical].get('record_type')!='LINEAGE_ALIAS':
            if canonical not in validated_hashes:validated_hashes[canonical]=ident(states[canonical])
            if validated_hashes[canonical]!=row.get('canonical_payload_sha256'):raise ValueError('CANONICAL_HASH_MISMATCH')
        reference=canonical

def risk_samples(s,f,m,p):
    reasons=defaultdict(set)
    def add(day,reason,h=None):
        for horizon in (h,) if h else ('T1','T2'):reasons[day+'/'+horizon].add(reason)
    dates=sorted(s.truth);add(dates[0],'EARLIEST');add(dates[-1],'LATEST')
    for year in sorted({d[:4] for d in dates}):
        for season_name in ('DJF','MAM','JJA','SON'):
            ds=[d for d in dates if d[:4]==year and season(d)==season_name]
            if ds:
                for d in (ds[0],ds[-1]):add(d,'YEAR_SEASON_BOUNDARY')
    for d in sorted(dates,key=lambda d:s.truth[d]['daily_tmax_c'])[:3]:add(d,'COLDEST')
    for d in sorted(dates,key=lambda d:s.truth[d]['daily_tmax_c'])[-3:]:add(d,'HOTTEST')
    for day in ('2025-09-24','2025-07-17','2025-08-07'):
        for offset in (-1,0,1):add((datetime.fromisoformat(day)+timedelta(days=offset)).date().isoformat(),'RECOVERY_COR_KNOWN_GAP_NEIGHBOR')
    for h in ('T1','T2'):
        group=[r for r in m.formal.values() if r['horizon']==h]
        for key,label in [('raw_ecmwf_prediction','RAW_MAX_ERROR'),('mos_prediction','MOS_MAX_ERROR'),('ml_prediction','ML_MAX_ERROR')]:
            g=[r for r in group if r[key] is not None]
            if g:
                for row in (max(g,key=lambda r:abs(r[key]-r['actual_target'])),max(g,key=lambda r:r[key]-r['actual_target']),min(g,key=lambda r:r[key]-r['actual_target'])):add(row['target_business_date'],label,h)
        legal=sorted([r for r in group if r['ml_prediction'] is not None],key=lambda r:r['target_business_date'])
        for row in legal[:3]:add(row['target_business_date'],'ML_COLD_START_EDGE',h)
        distinct=[]
        for row in legal:
            if not distinct or row['state_id']!=distinct[-1]['state_id']:distinct.append(row)
        for row in (distinct[0],distinct[len(distinct)//2],distinct[-1]):
            for offset in (-1,0,1):add((datetime.fromisoformat(row['target_business_date'])+timedelta(days=offset)).date().isoformat(),'REFIT_BOUNDARY',h)
        engines=[r for r in p.engine.values() if r['horizon']==h and r['record_id'] in p.mass]
        for row in sorted(engines,key=lambda r:r.get('LogLoss',0),reverse=True)[:5]:add(row['target_business_date'],'WORST_LOGLOSS_LOW_ACTUAL_P',h)
        sequence=sorted(engines,key=lambda r:r['target_business_date'])
        switches=[b for a,b in zip(sequence,sequence[1:]) if a['method']!=b['method'] or a['status']!=b['status']]
        for row in switches[:6]+switches[-3:]:add(row['target_business_date'],'CALIBRATION_METHOD_FALLBACK_SWITCH',h)
        for row in sequence:
            d=datetime.fromisoformat(row['target_business_date'])
            if d.day==calendar.monthrange(d.year,d.month)[1] and (d.month in (1,6,12)):add(row['target_business_date'],'MONTH_YEAR_END',h)
        # T2 structural NULL and arbitrary NULL coverage are represented by every date.
        add(legal[len(legal)//2]['target_business_date'],'NULL_FEATURE_STRUCTURAL_T2_REVISION',h)
        for row in engines:
            q=float(p.mass[row['record_id']]['pmf'].max())
            if .3<=q<.4:
                add(row['target_business_date'],'TOP1_30_40_BIN',h)
            assigned=float(p.mass[row['record_id']]['pmf'][int(row['actual'])+80])
            for threshold in (.01,.02,.05):
                if assigned<threshold:add(row['target_business_date'],'ACTUAL_PROBABILITY_LT_'+str(threshold),h)
    for row in s.samples4:
        chosen=s.choose(row['business_date_bjt'],row['issue_time_utc'])
        available=[r for r in s.runs_available if stamp(r['source_available_time_utc'])<=stamp(row['issue_time_utc'])]
        if row['horizon'] in ('T1','T2') and chosen and available and chosen['run_time_utc']!=available[-1]['run_time_utc']:add(row['business_date_bjt'],'ECMWF_COMPLETE_RUN_FALLBACK',row['horizon'])
    for r in s.runs:
        if r['canonical_status']!='AVAILABLE':
            day=stamp(r['run_time_utc']).astimezone(BJT).date()
            for offset in (0,1,2):add((day+timedelta(days=offset)).isoformat(),'UNAVAILABLE_RUN_NEIGHBOR')
    c=s.get('phase10_realtime_v1.db')
    for row in payloads(c,'realtime_prediction_snapshots'):add(row['target_business_date'],'LIVE_ANCHOR' if row['daily_anchor'] else 'LIVE_OFF_ANCHOR',row['horizon'])
    for row in s.silver:
        if row['message_class']=='SPECI':add(row['business_date_bjt'],'SPECI_DATE')
    registry=[];s.v3_parity_samples={}
    old=json.loads((ROOT/'docs/master_audit_v2/MASTER_SAMPLE_MANIFEST.json').read_text(encoding='utf-8'))['sample_ids']
    rng=random.Random(SEED)
    for h in ('T1','T2'):
        pop=sorted([r for r in m.formal.values() if r['horizon']==h and r['ml_prediction'] is not None and r['input_sample_id'] in p.engine and p.engine[r['input_sample_id']]['record_id'] in p.mass],key=lambda r:r['target_business_date'])
        mandatory=[r for r in pop if r['input_sample_id'] in reasons]
        picked={r['input_sample_id']:r for r in mandatory}
        groups=defaultdict(list)
        for i,r in enumerate(pop):groups[season(r['target_business_date']),min(2,i*3//len(pop))].append(r)
        for key,g in sorted(groups.items()):
            fresh=[r for r in g if r['input_sample_id'] not in old and r['input_sample_id'] not in picked];rng.shuffle(fresh)
            for row in fresh[:3]:picked[row['input_sample_id']]=row;reasons[row['input_sample_id']].add('NEW_SEED_SEASON_TIME_STRATUM')
        remaining=[r for r in pop if r['input_sample_id'] not in picked and r['input_sample_id'] not in old];rng.shuffle(remaining)
        for row in remaining:
            if len(picked)>=110:break
            picked[row['input_sample_id']]=row;reasons[row['input_sample_id']].add('NEW_SEED_ADDITIONAL')
        s.v3_parity_samples[h]=sorted(picked.values(),key=lambda r:r['target_business_date'])
        for row in pop:
            sid=row['input_sample_id']
            if sid in reasons:registry.append(dict(sample_id=sid,horizon=h,date=row['target_business_date'],reasons=sorted(reasons[sid]),parity_selected=sid in picked,was_v2_sample=sid in old,overlap_reason='Mandatory risk evidence must not be omitted' if sid in old and sid in picked else None))
    covered={r['sample_id'] for r in registry}
    registry.extend(dict(sample_id=sid,horizon=sid.split('/')[-1],date=sid.split('/')[0],reasons=sorted(why),parity_selected=False,scope='EXHAUSTIVE_SOURCE_OR_COLD_START_OR_LIVE_ONLY') for sid,why in reasons.items() if sid not in covered)
    output('ADVERSARIAL_SAMPLE_REGISTRY.json',dict(seed=SEED,method='mandatory source/model/probability risks plus new-seed season/time strata; frozen before parity outcomes',samples=registry,parity_sample_ids={h:[r['input_sample_id'] for r in v] for h,v in s.v3_parity_samples.items()}))

def aliases(s):
    c=s.get('phase10_realtime_v1.db');states={r['_id']:clean(r) for r in payloads(c,'realtime_probability_state')}
    snaps=payloads(c,'realtime_prediction_snapshots');probs={r['_id']:r for r in payloads(c,'realtime_probability_predictions')}
    out=[];cycles=missing=bad=0;fingerprints={};canonical_hashes={}
    alias_rows={k:v for k,v in states.items() if v.get('record_type')=='LINEAGE_ALIAS'}
    for rid,alias in alias_rows.items():
        try:
            canonical,state,chain=resolve(states,rid,canonical_hashes)
            if canonical not in fingerprints:
                base={k:v for k,v in state.items() if k!='created_at'}
                fingerprints[canonical]=(ident(base),ident({k:v for k,v in base.items() if k!='namespace'}))
            mismatch=rid not in fingerprints[canonical]
            bad+=mismatch;out.append(dict(alias=rid,canonical=canonical,chain=chain,legacy_fingerprint_correct=not mismatch,status='FAIL' if mismatch else 'PASS'))
        except ValueError as exc:
            bad+=1;cycles+=str(exc)=='ALIAS_CYCLE';missing+=str(exc)=='MISSING_CANONICAL';out.append(dict(alias=rid,status='FAIL',error=str(exc)))
    edges=[];unresolved=0
    for snap in snaps:
        pr=probs[snap['probability_id']];ref=pr['probability_state_version']
        try:
            canonical,state,chain=resolve(states,ref,canonical_hashes);mismatch=canonical!=snap['probability_state_version'];bad+=mismatch
            edges.append(dict(prediction_id=snap['_id'],reference=ref,canonical=canonical,path=chain,status='FAIL' if mismatch else 'PASS'))
        except ValueError as exc:unresolved+=1;edges.append(dict(prediction_id=snap['_id'],reference=ref,status='FAIL',error=str(exc)))
    # Read-only production methods as subjects: do not invoke Archive constructor.
    from src.realtime.archive import Archive
    a=Archive.__new__(Archive);a.c=c;a.namespace='PRODUCTION'
    latest=a.latest('probability_state');active_bad=int(latest is not None and latest.get('record_type')=='LINEAGE_ALIAS')
    canonical=[v for v in states.values() if v.get('record_type')!='LINEAGE_ALIAS']
    residual_bad=sum(r.get('record_id') in alias_rows or r.get('record_type')=='LINEAGE_ALIAS' for state in canonical for r in state['residuals'])
    calibration_bad=sum(r.get('record_id') in alias_rows or r.get('record_type')=='LINEAGE_ALIAS' for state in canonical for r in state['cases'])
    backup=snapshot(ROOT/'docs/phase10/ma_fixes_v1/phase10_before_repair.db');changed=[];added=[]
    try:
        for row in backup.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            table=row[0];old={r[0]:tuple(r) for r in backup.execute('SELECT * FROM "'+table+'"')};new={r[0]:tuple(r) for r in c.execute('SELECT * FROM "'+table+'"')}
            changed.extend(table+'/'+str(k) for k,v in old.items() if new.get(k)!=v)
            added.extend(table+'/'+str(k) for k in new if k not in old)
    finally:backup.close()
    s.counts.update(ALIAS_COUNT=len(alias_rows),ALIAS_RESOLUTION_CHECK_COUNT=len(edges)+len(out),ALIAS_RESOLUTION_MISMATCH_COUNT=bad,ALIAS_CYCLE_COUNT=cycles,ALIAS_MISSING_CANONICAL_COUNT=missing,UNRESOLVED_PROBABILITY_STATE_REFERENCE_COUNT=unresolved,ALIAS_USED_AS_ACTIVE_STATE_COUNT=active_bad,ALIAS_USED_AS_LATEST_STATE_COUNT=active_bad,ALIAS_USED_AS_RESIDUAL_COUNT=residual_bad,ALIAS_USED_AS_CALIBRATION_COUNT=calibration_bad,REPAIR_ORIGINAL_ROW_CHANGED_COUNT=len(changed))
    result=dict(aliases=out,prediction_edges=edges,canonical_state_count=len(canonical),state_row_count=len(states),original_changed_rows=changed,added_rows=added,status='PASS' if not (bad or unresolved or changed) and all(x.startswith('realtime_probability_state/') for x in added) else 'FAIL')
    output('MA001_ALIAS_INDEPENDENT_AUDIT.json',result)
    text={str(p.relative_to(ROOT)):p.read_text(encoding='utf-8-sig') for p in (ROOT/'src/realtime').glob('*.py') if 'probability_state' in p.read_text(encoding='utf-8-sig')}
    output('MA001_STATE_CONTAMINATION_AUDIT.json',dict(status='PASS' if not(active_bad or residual_bad or calibration_bad) else 'FAIL',latest_canonical_state=latest['_id'] if latest else None,canonical_state_count=len(canonical),alias_count=len(alias_rows),reader_source_evidence=text,counts={k:s.counts[k] for k in s.counts if k.startswith('ALIAS_USED_')}))

def anchor_expected(issue,h,target,seen,success=True):
    key=(h,target);anchor=success and stamp(issue).astimezone(BJT).hour>=21 and key not in seen
    if anchor:seen.add(key)
    return anchor

def anchor_probes(s):
    from src.realtime.archive import Archive
    from src.realtime.engine import Engine
    cfg=json.loads((ROOT/'config/phase10_realtime_v1.json').read_text(encoding='utf-8'));results=[]
    feature_names=[r[0] for r in s.get('phase7_feature_v1.db').execute('SELECT feature_name FROM phase7_feature_registry ORDER BY feature_name')]
    fixed_features=dict(s.get('phase7_feature_v1.db').execute('SELECT * FROM training_feature_view ORDER BY sample_id LIMIT 1').fetchone());fixed_features.pop('sample_id')
    fixed_features.update(ecmwf_tmax_c=23.,m6_corrected_temperature_c=22.)
    scenarios=[]
    for time in ('20:30','21:00','21:15','21:30','21:45','22:00'):scenarios.append((time+' first',[(time,())],False))
    for first,later in [('21:00','21:15'),('21:00','21:45'),('21:30','21:45'),('21:30','22:00'),('21:45','22:00'),('20:30','21:00')]:scenarios.append((first+' -> '+later,[(first,()),(later,())],False))
    scenarios.extend([('failed attempts then success',[('21:00',('T1','T2')),('21:15',('T1','T2')),('21:30',()),('21:45',())],False),('T1 succeeds T2 fails independently',[('21:00',('T2',)),('21:15',()),('21:45',())],False),('T2 succeeds T1 fails independently',[('21:00',('T1',)),('21:15',()),('21:45',())],False),('degraded true anchor',[('21:30',())],True)])
    for n,(name,events,degraded) in enumerate(scenarios):
        path=TEMP/('anchor_'+str(n)+'.db')
        if path.exists():raise RuntimeError('FRESH_ANCHOR_FIXTURE_REQUIRED')
        a=Archive(path,'SIMULATION',create=True)
        try:
            residuals=[]
            for h in ('T1','T2'):
                for j in range(90):
                    day=(datetime(2026,1,1)+timedelta(days=j)).date().isoformat()
                    for origin in ('ML','RAW','MOS'):residuals.append(dict(record_id=day+'/'+h+'/'+origin,horizon=h,target_business_date=day,issue=(datetime.fromisoformat(day).replace(tzinfo=BJT)-timedelta(days=int(h[1:]))).replace(hour=21).isoformat(),eligibility=eligible(day).isoformat(),origin=origin,residual=float(j%3-1)))
            state=dict(state_id='AUDITOR_CANONICAL',cutoff='2026-09-01T00:00:00+00:00',residuals=residuals,cases=[])
            with a.c:a.insert('probability_state',state,state['state_id'])
            class Dummy:
                def predict(self,h,values,issue):
                    assert sorted(values)==feature_names
                    return 22.375,dict(model_version='AUDIT_DUMMY',record_id='AUDIT_DUMMY',artifact_sha256='AUDIT_DUMMY',training_cutoff='2026-01-01T00:00:00+00:00')
            e=Engine.__new__(Engine);e.db=a;e.cfg=cfg;e.models=Dummy();e.history=[];e.state=state
            failures={};expect={};seen=set()
            def construct(day,h,issue,*args):
                if h in failures.get(stamp(issue).astimezone(BJT).strftime('%H:%M'),()):raise ValueError('ISOLATED_CONSTRUCT_FAILURE')
                return dict(values=dict(fixed_features),selected_run='2026-10-03T06:00:00+00:00',selected_version='AUDIT_RUN',fallback_depth=0)
            with patch('src.realtime.engine.runtime_context',return_value=({},{})),patch('src.realtime.engine.construct',side_effect=construct),patch('src.realtime.engine.source_gate',return_value=(degraded,{})),patch('src.realtime.engine.system_gate',return_value=('ENGINE_DEGRADED' if degraded else 'ENGINE_HEALTHY',10**12)):
                for i,(time,failed) in enumerate(events):
                    failures[time]=failed;issue='2026-10-03T'+time+':00+08:00'
                    for h in ('T1','T2'):
                        if h not in failed:expect[stamp(issue).isoformat(),h]=anchor_expected(issue,h,('2026-10-04' if h=='T1' else '2026-10-05'),seen)
                    e.predict_event(dict(event_id='case'+str(i),event_time=issue,status='PENDING'))
            snaps=a.rows('prediction_snapshots');probs={r['_id']:r for r in a.rows('probability_predictions')};cont={r['_id']:r for r in a.rows('continuous_predictions')};bad=len(snaps)!=len(expect);mathbad=0
            for row in snaps:
                expected=expect[row['prediction_issue_time'],row['horizon']]
                bad+=row['daily_anchor']!=expected or row['status']!=('OK' if expected and not degraded else 'DEGRADED') or not expected and row.get('reason_code')!='OFF_FIXED_ISSUE_REGIME'
                mathbad+=cont[row['continuous_id']]['continuous_prediction_c']!=22.375
            for h in ('T1','T2'):
                masses=[probs[r['probability_id']]['pmf'] for r in snaps if r['horizon']==h]
                mathbad+=sum(not np.array_equal(masses[0],mass) for mass in masses[1:])
            mathbad+=sum(row['values']!=fixed_features for row in a.rows('feature_snapshots'))
            results.append(dict(scenario=name,status='FAIL' if bad or mathbad else 'PASS',status_mismatches=int(bad),numeric_mismatches=int(mathbad),snapshots=[{k:r.get(k) for k in ('horizon','target_business_date','prediction_issue_time','daily_anchor','status','reason_code')} for r in snaps],expected_count=len(expect),saved_count=len(snaps),namespace='ISOLATED_SIMULATION_FIXED_INFERENCE_NOT_ACCURACY_TEST'))
        finally:a.close()
    failures=sum(r['status']=='FAIL' for r in results)
    s.counts['ANCHOR_STATE_MACHINE_CHECK_COUNT']=len(results)
    s.counts['ANCHOR_STATE_MACHINE_MISMATCH_COUNT']=failures;s.counts['ANCHOR_NUMERIC_MISMATCH_COUNT']=sum(r['numeric_mismatches'] for r in results)
    output('MA002_ANCHOR_STATE_MACHINE_AUDIT.json',dict(status='PASS' if not failures else 'FAIL',definition='first successful legal BJT >=21:00 per horizon/target; failed attempts do not consume anchor',scenarios=results))

def forward_replay(s,p):
    from src.realtime.archive import Archive
    from src.realtime.handoff import bootstrap,forward
    from src.realtime.settlement import advance,evaluate
    formal=sorted([r for r in p.engine.values() if r['record_id'] in p.mass and r.get('probability_source_level')=='ML'],key=lambda r:r['issue'])
    start=stamp(formal[0]['issue'])+timedelta(days=45)
    state=bootstrap(start);history=[]
    expected_res={r['record_id']:r for r in p.residuals if stamp(r['eligibility'])<=start and stamp(r['issue'])<start}
    eligible_cases=[r for r in p.pred.values() if r['origin'] in ('ML','RAW','MOS') and r['record_id'] in p.mass and stamp(r['eligibility'])<=start and stamp(r['issue'])<start]
    bootbad=int({r['record_id']:r for r in state['residuals']}!=expected_res)+int(set(r['record_id'] for r in state['cases'])!=set(r['record_id'] for r in eligible_cases))
    path=TEMP/'forward_120days_chronological_gap_preserved.db'
    if path.exists():raise RuntimeError('ISOLATED_REPLAY_ALREADY_EXISTS')
    a=Archive(path,'SIMULATION',create=True);state['state_id']=ident(state)
    with a.c:a.insert('probability_state',state,state['state_id'],created=start)
    rows=[];gaps=[];violations=duplicates=off=transitions=evalbad=0
    try:
        for index in range(120):
            issue=start+timedelta(days=index);local=issue.astimezone(BJT);settled_day=(local.date()-timedelta(days=2)).isoformat()
            with a.c:a.insert('daily_ground_truth',dict(s.truth[settled_day],settlement_status='FINAL',settlement_time=eligible(settled_day).isoformat(),label_eligibility_time=eligible(settled_day).isoformat()),'TRUTH/'+settled_day,created=issue)
            evaluate(a,issue)
            old_res={r['record_id']:copy.deepcopy(r) for r in state['residuals']};old_id=state['state_id']
            changed=advance(a,state,history,issue);repeat=advance(a,state,history,issue);transitions+=int(changed)
            new={r['record_id']:r for r in state['residuals']}
            bad=sum(new.get(k)!=v for k,v in old_res.items())+sum(stamp(r['eligibility'])>issue or stamp(r['issue'])>=issue for r in state['residuals']+state['cases'])
            bad+=int(stamp(state['cutoff'])>issue or changed and state.get('previous_state_id')!=old_id)
            duplicates+=int(repeat)+len(state['residuals'])-len(new)
            off+=sum('/REFRESH' in r.get('snapshot_id','') for r in state['residuals'])
            pmf_bad=0
            for h in ('T1','T2'):
                target=(local.date()+timedelta(days=int(h[1:]))).isoformat();sid=target+'/'+h
                if sid not in p.samples:
                    assert sid=='2025-08-07/T2' and s.choose(target,issue) is None
                    gaps.append(dict(sample_id=sid,issue=issue.isoformat(),status='NO_FORECAST',reason='KNOWN_FROZEN_T2_INCOMPLETE_ECMWF_GAP',snapshot_created=False))
                    continue
                forecasts={origin:p.samples[sid][origin] for origin in ('ML','RAW','MOS')}
                live,variants=forward(state,h,target,issue,forecasts);live['variants']=variants;oracle=p.forward_independent(state,h,target,issue,forecasts)
                pmf_bad+=int(oracle is None or max(abs(np.array(live['pmf'])-oracle))>1e-9)
                for refresh in (False,True):
                    rid=local.date().isoformat()+'/'+h+('/REFRESH' if refresh else '/ANCHOR')
                    at=issue+timedelta(minutes=15 if refresh else 0)
                    a.snapshot(rid,{},dict(continuous_prediction_c=forecasts['ML'],raw_ecmwf_prediction=forecasts['RAW'],mos_prediction=forecasts['MOS']),dict(live,probability_state_version=state['state_id']),dict(horizon=h,target_business_date=target,prediction_issue_time=at.isoformat(),daily_anchor=not refresh,probability_state_version=state['state_id'],prediction_status='DEGRADED' if refresh else 'OK',reason_codes=['OFF_FIXED_ISSUE_REGIME'] if refresh else []))
            bad+=pmf_bad;violations+=bad
            rows.append(dict(day=local.date().isoformat(),issue=issue.isoformat(),admitted_label_day=settled_day,state_id=state['state_id'],previous_state=old_id,new_residual_ids=sorted(set(new)-set(old_res)),residual_n_before=len(old_res),residual_n=len(state['residuals']),calibration_n=len(state['cases']),selected_legal_runs={h:(s.choose((local.date()+timedelta(days=int(h[1:]))).isoformat(),issue) or {}).get('run_time_utc') for h in ('T1','T2')},changed=changed,second_advance_changed=repeat,violations=bad,pmf_mismatches=pmf_bad,evaluation_n=len(a.rows('daily_evaluation'))))
        snaps={r['_id']:r for r in a.rows('prediction_snapshots')};probs={r['_id']:r for r in a.rows('probability_predictions')};cont={r['_id']:r for r in a.rows('continuous_predictions')}
        evals=a.rows('daily_evaluation');expected_eval={rid for rid,row in snaps.items() if eligible(row['target_business_date'])<=issue};evalbad+=int({r['snapshot_id'] for r in evals}!=expected_eval)
        for row in evals:
            snap=snaps[row['snapshot_id']];actual=s.truth[snap['target_business_date']]['daily_tmax_c'];oracle=scores(probs[snap['probability_id']]['pmf'],actual)
            evalbad+=sum(not close(oracle[k],row[v],1e-9) for k,v in [('brier','Brier'),('logloss','LogLoss'),('crps','CRPS'),('top1','top1_hit'),('top2','top2_hit'),('top3','top3_hit'),('actual_probability','assigned_probability')])
            error=cont[snap['continuous_id']]['continuous_prediction_c']-actual
            evalbad+=int(not close(error,row['error']) or not close(abs(error),row['absolute_error']) or not close(error*error,row['squared_error']))
        state_by_id={r['_id']:r for r in a.rows('probability_state')}
        violations+=sum(stamp(state_by_id[row['probability_state_version']]['cutoff'])>stamp(row['prediction_issue_time']) for row in snaps.values())
    finally:a.close()
    s.counts['FORWARD_STATE_TRANSITION_COUNT']+=transitions;s.counts['FORWARD_STATE_CAUSALITY_VIOLATION_COUNT']+=violations+bootbad
    s.counts['FORWARD_STATE_DAY_COUNT']=len(rows)
    s.counts['DUPLICATE_RESIDUAL_UPDATE_COUNT']=duplicates;s.counts['OFF_ANCHOR_RESIDUAL_UPDATE_COUNT']=off;s.counts['SETTLEMENT_EVALUATION_MISMATCH_COUNT']+=evalbad
    output('FORWARD_STATE_AUDIT_V3.json',dict(status='PASS' if not(violations or bootbad or duplicates or off or evalbad) else 'FAIL',namespace='ISOLATED_120_CONSECUTIVE_DAYS_CHRONOLOGICAL_REPLAY',bootstrap_cutoff=start.isoformat(),bootstrap_residual_n=len(expected_res),bootstrap_calibration_n=len(eligible_cases),bootstrap_mismatches=bootbad,transitions=transitions,duplicate_updates=duplicates,off_anchor_updates=off,evaluation_count=len(evals),pending_unsettled_snapshot_count=len(snaps)-len(evals),evaluation_mismatches=evalbad,preserved_no_forecast_gaps=gaps,days=rows))
def supplemental(s,f,m,p,r):
    import re
    expected={'temperature_2m':'°C','dew_point_2m':'°C','relative_humidity_2m':'%','surface_pressure':'hPa','pressure_msl':'hPa','cloud_cover':'%','cloud_cover_low':'%','cloud_cover_mid':'%','cloud_cover_high':'%','wind_speed_10m':'km/h','wind_direction_10m':'°','wind_gusts_10m':'km/h','shortwave_radiation':'W/m²','direct_radiation':'W/m²','diffuse_radiation':'W/m²','precipitation':'mm','rain':'mm','cape':'J/kg'}
    units=[];unitbad=0
    for rid,u in s.rawunits.items():
        bad=[field for field,unit in expected.items() if field in u and u[field]!=unit];unitbad+=len(bad)
        units.append(dict(raw_run_id=rid,units=u,mismatched_fields=bad,status='FAIL' if bad else 'PASS'))
    output('UNIT_AUDIT_V3.json',dict(source_units=units,wind_conversion='km/h divided by3.6 -> m/s independently checked in every feature cell',radiation='previous-hour interval mean W/m²; energy sum*3600/1e6 MJ/m²',precipitation='previous-hour interval amount mm, sum without differencing',mismatch_count=unitbad))
    sources={d['source'] for d in f.registry}
    s.counts['METEOSTAT_FORMAL_TRAINING_USAGE_COUNT']=sum('meteostat' in str(d).lower() for d in f.registry)
    s.counts['WU_FORMAL_TRAINING_USAGE_COUNT']=sum('weather underground' in str(d).lower() for d in f.registry)
    s.counts['UNPROVEN_INTRADAY_ZUUU_USAGE_COUNT']=sum(d['source'] not in ('ECMWF','PHASE6','SOLAR','CALENDAR','REVISION') for d in f.registry)
    eligible4={row['business_date_bjt']+'/'+row['horizon'] for row in s.samples4 if row['horizon'] in ('T1','T2') and row['sample_status']=='ELIGIBLE'}
    # Derive source eligibility from real non-null complete sample curves, not status spelling.
    eligible4={row['business_date_bjt']+'/'+row['horizon'] for row in s.samples4 if row['horizon'] in ('T1','T2') and row['trajectory_valid_hours']==24 and row['sample_status']!='EXCLUDED_INCOMPLETE_ECMWF'}
    sample_sets=dict(phase4=eligible4,phase7=set(f.samples),phase8=set(m.samples),phase9=set(p.samples))
    dropped={name:sorted(eligible4-value) for name,value in sample_sets.items()};extra={name:sorted(value-eligible4) for name,value in sample_sets.items()}
    s.counts['SILENT_SAMPLE_DROP_COUNT']=sum(len(v) for v in dropped.values())+sum(len(v) for v in extra.values())
    output('SAMPLE_UNIVERSE_V3.json',dict(base_counts={h:sum(sid.endswith('/'+h) for sid in eligible4) for h in ('T1','T2')},counts={k:len(v) for k,v in sample_sets.items()},missing=dropped,unexpected=extra,known_gap_preserved='2025-08-07/T2' not in eligible4 and '2025-08-07/T2' not in f.samples))
    s.counts['UNIT_MISMATCH_COUNT']=unitbad
    rawtimes=[]
    for row in s.silver:
        raw=s.bronze[row['bronze_raw_id']]['raw_metar'];token=re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b',raw);t=stamp(row['observation_time_utc'])
        bad=token is None or tuple(map(int,token.groups()))!=(t.day,t.hour,t.minute)
        if bad:rawtimes.append(dict(silver_id=row['id'],raw=raw,observation_time=row['observation_time_utc']))
    s.counts['RAW_OBSERVATION_TIME_MISMATCH_COUNT']=len(rawtimes)
    specials=[]
    for day in ('2025-09-24','2025-07-17'):
        observations=[dict(row,raw=s.bronze[row['bronze_raw_id']]['raw_metar']) for row in s.silver if row['business_date_bjt']==day]
        specials.append(dict(day=day,target=s.truth[day],observations=observations))
    output('PHASE1_SPECIAL_ADVERSARIAL_V3.json',dict(raw_time_check_count=len(s.silver),raw_time_mismatches=rawtimes,days=specials))
    phase5=records(s.get('phase5_ecmwf_raw_baseline_v1.db'),'phase5_baseline_v1_prediction');diagnostics=[]
    for h in ('T0','T1','T2'):
        group=[row for row in phase5 if row['horizon']==h];errors=[row['ecmwf_raw_tmax_c']-row['target_tmax_c'] for row in group]
        diagnostics.append(dict(horizon=h,scope='HISTORICAL_FIXED_REFERENCE' if h=='T0' else 'FORMAL_BASELINE',**metrics(errors),ae_le_05=sum(abs(e)<=.5 for e in errors)/len(errors),ae_le_1=sum(abs(e)<=1 for e in errors)/len(errors),rounded_exact_diagnostic=sum(math.floor(row['ecmwf_raw_tmax_c']+.5)==row['target_tmax_c'] for row in group)/len(group),diagnostic_policy='frozen primary hit rates use continuous AE; rounded Exact separately marked DIAGNOSTIC ONLY'))
    output('PHASE5_BASELINE_REPRODUCTION_V3.json',dict(status='PASS' if not s.counts['PHASE5_METRIC_MISMATCH_COUNT'] else 'FAIL',metrics=diagnostics))
    output('FROZEN_CONTRACT_READS_V3.json',dict(feature_sources=sorted(sources),phase8=json.loads((ROOT/'docs/phase8/PHASE8_CONTRACT.json').read_text(encoding='utf-8')),phase10_protocol=(ROOT/'docs/phase10/PHASE10_PROTOCOL_V1.md').read_text(encoding='utf-8'),phase10_handoff=(ROOT/'docs/phase10/PHASE10_PHASE9_HANDOFF.md').read_text(encoding='utf-8'),phase10_settlement=(ROOT/'docs/phase10/PHASE10_SETTLEMENT_CONTRACT.md').read_text(encoding='utf-8'),intraday=(ROOT/'docs/intraday/INTRADAY_DISCOVERY_V1_REPORT.md').read_text(encoding='utf-8'),phase9_protocol=(ROOT/'docs/phase9/PHASE9_PROBABILITY_PROTOCOL_V1.md').read_text(encoding='utf-8')))



