"""FEATURE_V1 operators with a label-free runtime input adapter."""
from datetime import datetime, timedelta
import math
from src.features.registry import REGISTRY
from src.features.ecmwf_temperature import compute as curve_compute
from src.features.calendar_solar import compute as time_compute
from src.features.forecast_revision import compute as revision_compute
from src.auxiliary.solar.features import features as solar_features
from src.mos_review.walk_forward import select_states
from src.data_v1.source_io import open_snapshot
from .contracts import ROOT,BJT,utc,iso,require

def season(day):
    month=int(day[5:7])
    return 'DJF' if month in (12,1,2) else 'MAM' if month in (3,4,5) else 'JJA' if month in (6,7,8) else 'SON'

def historical_bias_history():
    c=open_snapshot(ROOT/'database/phase6_statistical_mos_v1_review.db')
    rows=[dict(r) for r in c.execute("SELECT * FROM phase6_mos_sample WHERE horizon IN ('T1','T2')")]
    c.close();return rows

def selection(runs, trajectories, day, issue):
    start=datetime.fromisoformat(day+'T00:00:00+08:00')
    keys=[iso(start+timedelta(hours=i)) for i in range(24)]
    legal=sorted((r for r in runs.values() if utc(r['run_time_utc'])<=utc(issue) and utc(r['source_available_time_utc'])<=utc(issue)),
                 key=lambda r:utc(r['run_time_utc']),reverse=True)
    for depth,r in enumerate(legal):
        trajectory=trajectories.get(r['canonical_raw_run_id'],{})
        if all(k in trajectory and trajectory[k].get('temperature_2m_c') is not None and math.isfinite(trajectory[k]['temperature_2m_c']) for k in keys):
            return r,[trajectory[k] for k in keys],dict(fallback_depth=depth,latest_run_complete=depth==0,selection_reason='LATEST_LEGAL_COMPLETE' if depth==0 else 'OLDER_COMPLETE_FALLBACK')
    raise ValueError('NO_LEGAL_ECMWF_RUN')

def runtime_context(db, issue):
    runs={};trajectories={}
    # For multiple payload versions select the latest version actually received by issue.
    for row in db.rows('ecmwf_raw_runs'):
        trajectories.setdefault(row['_id'],{})  # Quarantined/partial vintage remains explicitly incomplete.
        seen=row.get('replay_available_time',row['actual_ingest_time']) if db.namespace=='SIMULATION' else row['actual_ingest_time']
        if utc(seen)>utc(issue):continue
        available=iso(seen);r=dict(row,run_time_utc=row['run_time'],source_available_time_utc=available,canonical_raw_run_id=row['_id'])
        prev=runs.get(row['run_time'])
        if prev is None or utc(available)>utc(prev['source_available_time_utc']):runs[row['run_time']]=r
    for row in db.rows('ecmwf_hourly'):
        seen=row.get('replay_available_time',row['actual_ingest_time']) if db.namespace=='SIMULATION' else row['actual_ingest_time']
        if utc(seen)<=utc(issue):
            trajectories.setdefault(row['run_id'],{})[row['target_time_utc']]=row
    return runs,trajectories

def construct(day,horizon,issue,runs,trajectories,history):
    require(horizon in ('T1','T2'),'T0_FORMAL_MODEL_BLOCKED')
    run,hours,choice=selection(runs,trajectories,day,issue)
    run_time=run['run_time_utc']; available=run['source_available_time_utc']
    sample=dict(business_date_bjt=day,horizon=horizon,season=season(day),issue_time_utc=iso(issue),selected_ecmwf_run_time_utc=run_time)
    biases=select_states(sample,[r for r in history if not r.get('settlement_time') or utc(r['settlement_time'])<=utc(issue)])
    solar=[solar_features(row['target_time_utc'],ingest_time=issue) for row in hours]
    values={};lineage={};missing={}
    for definition in REGISTRY:
        name=definition['feature_name'];source=definition['source']; used=[]; missing_reason=None; at='1970-01-01T00:00:00+00:00'
        if source=='ECMWF':
            value,missing_reason=curve_compute(definition,hours);at=available
            used=[run['canonical_raw_run_id']]
        elif source in ('CALENDAR','SOLAR'):
            value,missing_reason=time_compute(definition,sample,hours,solar)
            if source=='CALENDAR' and name in ('run_age_hours','target_start_lead_hours','target_peak_lead_hours'):
                at=available;used=[run['canonical_raw_run_id']]
        elif source=='REVISION':
            operation=definition['operation']
            slot=operation if operation in ('prev_run','6h','12h','24h') else 'prev_run'
            value,missing_reason,old,_=revision_compute(sample,hours,runs,trajectories,slot,operation)
            at=max([available]+([old['source_available_time_utc']] if old else []),key=utc)
            used=[run['canonical_raw_run_id']]+([old['canonical_raw_run_id']] if old else [])
        else:
            bias,labels,path,components=biases[definition['operation']]
            value=max(row['temperature_2m_c'] for row in hours)-bias if name=='m6_corrected_temperature_c' and bias is not None else bias
            missing_reason='INSUFFICIENT_HISTORY' if bias is None else None
            used=[dict(date=r['business_date_bjt'],horizon=r['horizon'],eligibility=r['label_eligibility_time_bjt'],raw_error=r['raw_error']) for r in labels]
            times=[r['label_eligibility_time_bjt'] for r in labels]+[r.get('settlement_time',r['label_eligibility_time_bjt']) for r in labels]
            if times:at=max(times,key=utc)
            if name=='m6_corrected_temperature_c':at=max(at,available,key=utc)
        require(utc(at)<=utc(issue),'FEATURE_AVAILABILITY_VIOLATION')
        if value is not None: require(math.isfinite(value),'FEATURE_NONFINITE')
        values[name]=value
        missing[name]=missing_reason
        lineage[name]=dict(source=source,input_ids=used,feature_available_time=iso(at),prediction_issue_time=iso(issue),missing_reason=missing_reason)
        if source=='PHASE6':lineage[name].update(fallback_path=path,training_n=len(labels),components=components)
    require(len(values)==102,'FEATURE_CONTRACT_ERROR')
    return dict(values=values,missing_reasons=missing,lineage=lineage,feature_version='FEATURE_V1',selected_run=run_time,
                selected_version=run['canonical_raw_run_id'],run_available_time=available,target_business_date=day,horizon=horizon,
                prediction_issue_time=iso(issue),**choice)
