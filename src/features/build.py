"""FEATURE_V1 pure dataset assembly; no fitting, target correlation or selection."""
import json
import math
from .contracts import VERSION,CONTRACT,EPOCH,utc,require
from .registry import REGISTRY,DEFERRED
from . import ecmwf_temperature,forecast_revision,calendar_solar,historical_bias
from .availability import latest,check
from .qc import flags

def assemble(source):
    data={t:[] for t in ('feature_registry','feature_sample','feature_label','feature_source_lineage','feature_history_label','feature_history_state','feature_value','feature_qc','manifest')}
    data['feature_registry']=[dict(r) for r in REGISTRY]
    bundles={};history=source['history']
    for l in history.values():
        data['feature_history_label'].append(dict(label_business_date=l['business_date_bjt'],horizon=l['horizon'],observed_tmax=l['observed_tmax'],raw_ecmwf_tmax=l['raw_ecmwf_tmax'],raw_error=l['raw_error'],label_eligibility_time_bjt=l['label_eligibility_time_bjt'],historical_issue_time=l['issue_time_utc'],day_end_utc=l['day_end_utc']))
    for sample in source['samples']:
        day,h=sample['business_date_bjt'],sample['horizon'];sid=day+'/'+h
        hrs=source['hours'][(day,h)];sol=source['solar'][day]
        require(len(hrs)==len(sol)==24,'Incomplete base trajectory/solar')
        for hour,row in enumerate(hrs):
            require(int(row['target_time_bjt'][11:13])==hour and row['selected_ecmwf_run_time_utc']==sample['selected_ecmwf_run_time_utc'] and row['source_raw_run_id']==sample['source_raw_run_id'],'Cross-run/hour mismatch')
            require(row['source_available_time_utc']==sample['selected_ecmwf_source_available_time_utc'],'Hourly availability mismatch')
            require((utc(row['target_time_utc'])-utc(row['selected_ecmwf_run_time_utc'])).total_seconds()/3600==row['lead_hours'],'Lead mismatch')
        require(all(r['latitude']==30.576 and r['longitude']==103.950 and r['availability_basis']=='DETERMINISTIC_NOT_APPLICABLE' for r in sol),'Solar contract')
        data['feature_sample'].append(dict(sample_id=sid,target_business_date=day,horizon=h,issue_time_utc=sample['issue_time_utc'],selected_run=sample['selected_ecmwf_run_time_utc'],run_available_time=sample['selected_ecmwf_source_available_time_utc'],availability_semantics=sample['ecmwf_availability_semantics'],model=hrs[0]['model'],source_raw_run_id=sample['source_raw_run_id'],sample_role='FORMAL_T1_T2',feature_version=VERSION))
        data['feature_label'].append(dict(sample_id=sid,label_tmax_c=source['labels'][day],label_source='ZUUU_TARGET_V1',label_eligibility_time_bjt=history[(day,h)]['label_eligibility_time_bjt']))
        for m in ('M1','M2','M3','M4','M5','M6'):
            b=source['states'][(day,h,m)]
            data['feature_history_state'].append(dict(sample_id=sid,model=m,**{k:b[k] for k in ('bias_value','training_cutoff','training_start','training_end','training_n','training_dates_json','fallback_path','components_json','latest_label_eligibility_time_bjt','eligibility_rule','eligibility_lag','status')}))
        current=sample['selected_ecmwf_run_time_utc'];available=sample['selected_ecmwf_source_available_time_utc']
        comparison_lookup={slot:forecast_revision.comparison_run(sample,source['runs'],slot) for slot in ('prev_run','6h','12h','24h')}
        for definition in REGISTRY:
            src=definition['source'];op=definition['operation'];run=current;comp=None;labels=[];state=None;comparison=[]
            if src=='ECMWF':value,reason=ecmwf_temperature.compute(definition,hrs);av=available;bundle=sid+'/ECMWF'
            elif src in ('CALENDAR','SOLAR'):
                value,reason=calendar_solar.compute(definition,sample,hrs,sol)
                av=available if op in ('run_age_hours','target_start_lead_hours','target_peak_lead_hours') else EPOCH
                bundle=sid+('/PEAK_LEAD' if op=='target_peak_lead_hours' else '/RUN_TIME' if op in ('run_age_hours','target_start_lead_hours') else '/'+src)
            elif src=='REVISION':
                slot=op if op in ('prev_run','6h','12h','24h') else 'prev_run'
                value,reason,comp,comparison=forecast_revision.compute(sample,hrs,source['runs'],source['trajectories'],slot,op,comparison_lookup)
                av=latest(available,comp['source_available_time_utc']) if comp else available;bundle=sid+'/REVISION/'+slot
            elif src=='PHASE6':
                value,reason,state,labels=historical_bias.compute(definition,sample,source['states'],source['predictions'],history)
                av=latest(available if op=='M6' else EPOCH,*[l['label_eligibility_time_bjt'] for l in labels],*[l['selected_ecmwf_source_available_time_utc'] for l in labels]);bundle=sid+'/PHASE6/'+op
            else:raise ValueError(src)
            check(av,sample['issue_time_utc'])
            require(value is None or math.isfinite(value),'Nonfinite feature')
            if bundle not in bundles:
                bundles[bundle]=dict(bundle_id=bundle,sample_id=sid,source=src,source_table=definition['source_table'],current_run=current if src in ('ECMWF','REVISION') or op in ('run_age_hours','target_start_lead_hours','target_peak_lead_hours','M6') else None,
                    current_run_available_time=available if src in ('ECMWF','REVISION') or op in ('run_age_hours','target_start_lead_hours','target_peak_lead_hours','M6') else None,
                    comparison_run=comp['run_time_utc'] if comp else None,comparison_raw_run_id=comp['canonical_raw_run_id'] if comp else None,
                    comparison_available_time=comp['source_available_time_utc'] if comp else None,source_raw_run_id=sample['source_raw_run_id'] if src in ('ECMWF','REVISION') else None,
                    model=hrs[0]['model'] if src in ('ECMWF','REVISION') else None,issue_time=sample['issue_time_utc'],feature_available_time=av,
                    target_times_json=json.dumps([r['target_time_utc'] for r in hrs] if src in ('ECMWF','REVISION') or op=='target_peak_lead_hours' else [r['target_time'] for r in sol] if src=='SOLAR' else []),
                    lead_hours_json=json.dumps([r['lead_hours'] for r in hrs] if src in ('ECMWF','REVISION') or op=='target_peak_lead_hours' else []),
                    source_hourly_ids_json=json.dumps([r['source_hourly_id'] for r in hrs] if src in ('ECMWF','REVISION') else []),
                    comparison_hourly_ids_json=json.dumps([r['id'] for r in comparison]),
                    history_model=op if src=='PHASE6' else None,availability_semantics=sample['ecmwf_availability_semantics'] if src in ('ECMWF','REVISION') else 'CONSERVATIVE_CAUSAL_LABEL_ELIGIBILITY_NOT_OBSERVED_RECEIPT' if src=='PHASE6' else 'DETERMINISTIC_COMPUTABILITY_NOT_RECEIPT',
                    solar_latitude=30.576 if src=='SOLAR' else None,solar_longitude=103.950 if src=='SOLAR' else None)
            else:require(bundles[bundle]['feature_available_time']==av,'Shared bundle availability mismatch')
            data['feature_value'].append(dict(sample_id=sid,feature_name=definition['feature_name'],value=float(value) if value is not None else None,is_available=int(value is not None),missing_reason=reason,feature_available_time=av,bundle_id=bundle))
            for flag in flags(definition,value):data['feature_qc'].append(dict(sample_id=sid,feature_name=definition['feature_name'],flag=flag))
        # Cross-variable QC annotates, never overwrites source or features.
        if any(r['dew_point_2m_c'] is not None and r['dew_point_2m_c']>r['temperature_2m_c']+.2 for r in hrs):data['feature_qc'].append(dict(sample_id=sid,feature_name='ecmwf_dewpoint_mean_c',flag='DEWPOINT_ABOVE_TEMPERATURE'))
        if any(r['shortwave_radiation_wm2'] is not None and r['shortwave_radiation_wm2']>5 and sol[i]['solar_elevation']<-12 for i,r in enumerate(hrs)):data['feature_qc'].append(dict(sample_id=sid,feature_name='ecmwf_shortwave_daily_energy',flag='NIGHT_RADIATION_CHECK_BACKWARD_INTERVAL'))
    data['feature_source_lineage']=list(bundles.values())
    return data
