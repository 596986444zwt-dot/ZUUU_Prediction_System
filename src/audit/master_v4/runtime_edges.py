import pathlib,sys,json,math,calendar
from datetime import datetime,timedelta,date
from collections import defaultdict,Counter
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from src.audit.master_v4.verify import db,rows,payloads,stamp,BJT,eligible,save,mismatch,ec_feature
c=db('phase10_realtime_v1.db');data={t:payloads(c,'realtime_'+t) for t in ['prediction_snapshots','feature_snapshots','ecmwf_raw_runs','ecmwf_hourly','probability_state','probability_predictions']};c.close()
c=db('phase7_feature_v1.db');reg={r['feature_name']:r for r in rows(c,'phase7_feature_registry')};c.close()
c=db('phase6_statistical_mos_v1_review.db');history=rows(c,'phase6_mos_sample');c.close()
errors=[];checks=Counter();edges=0;api_counts=Counter();runselection=[];peers={}
for snap in data['prediction_snapshots'].values():
    f=data['feature_snapshots'][snap['feature_id']];issue=stamp(snap['prediction_issue_time']);day=snap['target_business_date'];h=snap['horizon'];cur=data['ecmwf_raw_runs'][f['selected_version']]
    curves=defaultdict(dict)
    for r in data['ecmwf_hourly'].values():
        if stamp(r['actual_ingest_time'])<=issue:curves[r['run_id']][r['target_time_utc']]=r
    legal={}
    for r in data['ecmwf_raw_runs'].values():
        if stamp(r['actual_ingest_time'])>issue or stamp(r['run_time'])>issue:continue
        if r['run_time'] not in legal or stamp(r['actual_ingest_time'])>stamp(legal[r['run_time']]['actual_ingest_time']):legal[r['run_time']]=r
    start=datetime.fromisoformat(day+'T00:00:00+08:00');keys=[(start+timedelta(hours=i)).astimezone(stamp(cur['run_time']).tzinfo).isoformat() for i in range(24)]
    choice=next((r for r in sorted(legal.values(),key=lambda r:stamp(r['run_time']),reverse=True) if all(k in curves[r['_id']] and curves[r['_id']][k]['temperature_2m_c'] is not None for k in keys)),None)
    if not choice or choice['_id']!=cur['_id']:errors.append({'snapshot':snap['_id'],'kind':'RUN_CHOICE'})
    g=[curves[cur['_id']][k] for k in keys];runselection.append({'snapshot':snap['_id'],'selected':cur['_id'],'expected':choice['_id'] if choice else None});api_counts[cur['api_model']]+=1
    def season(d):return ('DJF','MAM','JJA','SON')[(int(d[5:7])%12)//3]
    allowed=[r for r in history if r['horizon']==h and r['business_date_bjt']<day and eligible(r['business_date_bjt'])<=issue and stamp(r['issue_time_utc'])<issue];bias={};pools={}
    for model in ('M1','M2','M3','M4','M5'):
        used=allowed if len(allowed)>=7 else []
        if model in ('M2','M3') and used:
            end=issue.astimezone(BJT).date()-timedelta(days=2);lo=end-timedelta(days=(7 if model=='M2' else 30)-1);subset=[r for r in used if lo.isoformat()<=r['business_date_bjt']<=end.isoformat()];used=subset if len(subset)>=7 else used
        if model=='M4' and used:
            subset=[r for r in used if season(r['business_date_bjt'])==season(day)];used=subset if len(subset)>=7 else used
        pools[model]=used;bias[model]=sum(r['raw_error'] for r in used)/len(used) if used else None
    bias['M6']=(bias['M1']+bias['M3']+bias['M4'])/3 if bias['M1'] is not None else None
    for name,value in f['values'].items():
        d=reg[name];src=d['source'];checks[src]+=1
        if src=='ECMWF':expected=ec_feature(d,g)
        elif src=='PHASE6':
            expected=bias[d['operation']]
            if name=='m6_corrected_temperature_c' and expected is not None:expected=max(r['temperature_2m_c'] for r in g)-expected
            for r in f['lineage'][name]['input_ids']:
                edges+=1
                if r['horizon']!=h or stamp(r['eligibility'])>issue or r['date']>=day:errors.append({'snapshot':snap['_id'],'feature':name,'kind':'BIAS_TIME'})
        elif src=='REVISION':
            op=d['operation'];run=stamp(cur['run_time']);wanted=run-timedelta(hours=int(op[:-1])) if op in ('6h','12h','24h') else None
            old=legal.get(wanted.isoformat()) if wanted else max((r for r in legal.values() if stamp(r['run_time'])<run and r['model']==cur['model']),key=lambda r:stamp(r['run_time']),default=None)
            og=[curves[old['_id']].get(k) for k in keys] if old else []
            T=[r['temperature_2m_c'] for r in g];P=[r['temperature_2m_c'] for r in og] if og and all(og) else []
            if len(P)!=24 or any(x is None for x in P):expected=None
            elif op in ('prev_run','6h','12h','24h'):expected=max(T)-max(P)
            elif op=='curve_revision_prev_mean_c':expected=sum(a-b for a,b in zip(T,P))/24
            elif op=='curve_revision_prev_max_abs_c':expected=max(abs(a-b) for a,b in zip(T,P))
            elif op=='peak_hour_revision_prev_h':expected=T.index(max(T))-P.index(max(P))
            else:expected=T[14]-P[14]
        elif src=='CALENDAR':
            dt=date.fromisoformat(day);doy=dt.timetuple().tm_yday;peak=max(range(24),key=lambda i:g[i]['temperature_2m_c']);expected={'calendar_month':dt.month,'calendar_season_code':(dt.month%12)//3,'calendar_day_of_year':doy,'calendar_doy_sin':math.sin(2*math.pi*(doy-1)/(366 if calendar.isleap(dt.year) else 365)),'calendar_doy_cos':math.cos(2*math.pi*(doy-1)/(366 if calendar.isleap(dt.year) else 365)),'run_age_hours':(issue-stamp(cur['run_time'])).total_seconds()/3600,'target_start_lead_hours':g[0]['lead_hours'],'target_peak_lead_hours':g[peak]['lead_hours']}[name]
        else:continue  # Frozen full Solar math separately recomputed; runtime Solar values explicitly uncounted here.
        if mismatch(expected,value):errors.append({'snapshot':snap['_id'],'feature':name,'expected':expected,'actual':value})
    state=data['probability_state'][snap['probability_state_version']]
    if state.get('record_type')=='LINEAGE_ALIAS':state=data['probability_state'][state['canonical_state_id']]
    cases={r['record_id']:r for r in state['cases']};p=data['probability_predictions'][snap['probability_id']]
    ids=list(p.get('calibration_ids',[]))
    for opts in p.get('selection',{}).values():
        for method,score,selids in opts['options']:ids.extend(selids)
    for rid in ids:
        r=cases[rid];edges+=1
        if r['horizon']!=h or stamp(r['eligibility'])>issue or stamp(r['issue'])>=issue or r['target_business_date']>=day:errors.append({'snapshot':snap['_id'],'case':rid,'kind':'PROBABILITY_CASE_TIME'})
save('runtime_all_feature_edges.json',{'evidence_status':'FINAL','snapshots':len(data['prediction_snapshots']),'recomputed_cells':sum(v for k,v in checks.items() if k!='SOLAR'),'source_cells':dict(checks),'runtime_solar_unrecomputed_cells':checks['SOLAR'],'history_case_edges':edges,'mismatch_count':len(errors),'errors':errors,'api_products':dict(api_counts),'run_selection':runselection})
print('runtime feature/case edges',edges,'mismatches',len(errors))
