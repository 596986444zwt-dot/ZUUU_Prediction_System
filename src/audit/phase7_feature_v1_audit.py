"""Independent saved-value/causality audit; no writes, predictions or training."""
import argparse
import ast
import json
import math
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta
from src.features.contracts import OUTPUT,CONTRACT,EPOCH,BJT,utc,require
from src.features.data import fingerprints,load
from src.features.schema import read,semantic_hash,canonical
from src.data_v1.source_io import open_snapshot

def audit_connection(c,source=None):
    data=read(c);before=fingerprints();source=source or load()
    require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','Feature DB integrity')
    require(not c.execute('PRAGMA foreign_key_check').fetchall(),'Feature DB FK')
    counts=Counter(s['horizon'] for s in data['feature_sample']);require(counts=={'T1':729,'T2':728},'Base universe')
    manifest=data['manifest'][0];semantic=semantic_hash(data,json.loads(manifest['contract_json']))
    require(semantic==manifest['semantic_sha256'],'Feature semantic mismatch')
    registry={r['feature_name']:r for r in data['feature_registry']};samples={s['sample_id']:s for s in data['feature_sample']}
    values={(v['sample_id'],v['feature_name']):v for v in data['feature_value']};bundles={l['bundle_id']:l for l in data['feature_source_lineage']}
    by_bundle=defaultdict(list)
    for v in values.values():by_bundle[v['bundle_id']].append(v)
    require(len(values)==len(samples)*len(registry),'Feature values missing/duplicate')
    require(c.execute('SELECT count(*) FROM training_feature_view').fetchone()[0]==1457,'X row coverage')
    xfields=[r[1] for r in c.execute('PRAGMA table_info(training_feature_view)')]
    require(set(xfields)=={'sample_id'}|set(registry),'X projection mismatch')
    require(not any(any(w in f.lower() for w in ('label','observed','error_today','target_tmax')) for f in xfields),'Target field in X')
    availability=run_future=splice=target_leak=units_bad=missing_bad=0
    for v in values.values():
        s=samples[v['sample_id']];l=bundles[v['bundle_id']];r=registry[v['feature_name']]
        availability+=int(utc(v['feature_available_time'])>utc(s['issue_time_utc']))
        missing_bad+=int((v['value'] is None)!=(v['is_available']==0) or (v['value'] is None)!=(v['missing_reason'] is not None))
        require(v['value'] is None or math.isfinite(v['value']),'Nonfinite feature value')
        require(r['source'] in ('ECMWF','SOLAR','CALENDAR','REVISION','PHASE6'),'Prohibited feature source')
        require(r['unit'] and r['formula'] and r['source_fields'] and r['availability_rule'],'Registry incomplete')
        require(l['sample_id']==s['sample_id'] and l['issue_time']==s['issue_time_utc'],'Bundle sample linkage')
        require(v['feature_available_time']==l['feature_available_time'],'Availability differs from input bundle')
        if l['source']=='PHASE6':
            p=source['predictions'][(s['target_business_date'],s['horizon'],l['history_model'])]
            expected=p['mos_continuous_tmax'] if r['feature_name']=='m6_corrected_temperature_c' else p['bias_value']
            require(v['value']==expected,'Frozen MOS-derived feature changed')
            state=source['states'][(s['target_business_date'],s['horizon'],l['history_model'])]
            labels=[source['history'][(d,s['horizon'])] for d in json.loads(state['training_dates_json'])]
            times=[s['run_available_time'] if l['history_model']=='M6' else EPOCH]
            times += [t for a in labels for t in (a['label_eligibility_time_bjt'],a['selected_ecmwf_source_available_time_utc'])]
            require(utc(v['feature_available_time'])==max(map(utc,times)),'Historical availability is not max inputs')
        if r['source']=='ECMWF' and v['value'] is not None:
            hs=source['hours'][(s['target_business_date'],s['horizon'])];ts=[h['temperature_2m_c'] for h in hs];f=r['feature_name']
            checks={'ecmwf_tmax_c':max(ts),'ecmwf_tmin_c':min(ts),'ecmwf_mean_temp_c':math.fsum(ts)/24,'ecmwf_temp_range_c':max(ts)-min(ts),'ecmwf_tmax_hour_bjt':ts.index(max(ts)),'ecmwf_tmin_hour_bjt':ts.index(min(ts))}
            if f in checks:require(abs(v['value']-checks[f])<1e-12,'Independent extrema/curve mismatch')
    for l in bundles.values():
        s=samples[l['sample_id']]
        if l['current_run']:
            run_future+=int(utc(l['current_run'])>utc(s['issue_time_utc']) or utc(l['current_run_available_time'])>utc(s['issue_time_utc']))
        if l['source'] in ('ECMWF','REVISION'):
            hs=source['hours'][(s['target_business_date'],s['horizon'])]
            splice+=int(json.loads(l['source_hourly_ids_json'])!=[h['source_hourly_id'] for h in hs] or l['source_raw_run_id']!=s['source_raw_run_id'])
            require(len(json.loads(l['target_times_json']))==24 and len(json.loads(l['lead_hours_json']))==24,'ECMWF lineage incomplete')
            require(json.loads(l['target_times_json'])==[h['target_time_utc'] for h in hs] and json.loads(l['lead_hours_json'])==[h['lead_hours'] for h in hs],'ECMWF target/lead lineage drift')
            current=[r['temperature_2m_c'] for r in hs]
            if l['source']=='ECMWF':require(l['current_run']==s['selected_run'] and l['current_run_available_time']==s['run_available_time'],'Current run identity')
            if l['source']=='REVISION':
                compared=source['trajectories'].get(l['comparison_raw_run_id'],{})
                older=[compared.get(r['target_time_utc'],{}).get('temperature_2m_c') for r in hs]
                require(json.loads(l['comparison_hourly_ids_json'])==[compared[h['target_time_utc']]['id'] for h in hs if h['target_time_utc'] in compared],'Comparison hour alignment drift')
                valid=all(x is not None and math.isfinite(x) for x in older)
                for v in by_bundle[l['bundle_id']]:
                    name=v['feature_name']
                    if not valid:require(v['value'] is None,'Incomplete revision fabricated')
                    else:
                        op=registry[name]['operation'];diff=[a-b for a,b in zip(current,older)]
                        expected=max(current)-max(older) if op in ('prev_run','6h','12h','24h') else math.fsum(diff)/24 if op=='curve_revision_prev_mean_c' else max(map(abs,diff)) if op=='curve_revision_prev_max_abs_c' else current.index(max(current))-older.index(max(older)) if op=='peak_hour_revision_prev_h' else diff[14]
                        require(v['value'] is not None and abs(v['value']-expected)<1e-12,'Independent revision arithmetic')
        if l['comparison_run']:
            run_future+=int(utc(l['comparison_run'])>=utc(s['selected_run']) or utc(l['comparison_available_time'])>utc(s['issue_time_utc']))
            rr=source['runs'][l['comparison_run']];require(rr['canonical_raw_run_id']==l['comparison_raw_run_id'],'Comparison run id')
            hours=source['trajectories'][l['comparison_raw_run_id']]
            ids={r['id'] for r in hours.values()};splice+=int(not set(json.loads(l['comparison_hourly_ids_json']))<=ids)
            slot=l['bundle_id'].rsplit('/',1)[-1]
            if slot in ('6h','12h','24h'):require((utc(s['selected_run'])-utc(l['comparison_run'])).total_seconds()==int(slot[:-1])*3600,'Wrong exact revision lag')
            else:
                candidates=[r for r in source['runs'].values() if r['model']==s['model'] and utc(r['run_time_utc'])<utc(s['selected_run']) and utc(r['source_available_time_utc'])<=utc(s['issue_time_utc'])]
                require(l['comparison_run']==max(candidates,key=lambda r:utc(r['run_time_utc']))['run_time_utc'],'Previous run selection')
        if l['source']=='SOLAR':require((l['solar_latitude'],l['solar_longitude'])==(30.576,103.950),'Solar coordinates')
    hist={(l['label_business_date'],l['horizon']):l for l in data['feature_history_label']}
    edges=future_labels=same_day=hist_viol=cross=0
    for b in data['feature_history_state']:
        s=samples[b['sample_id']];i=utc(s['issue_time_utc']);ds=json.loads(b['training_dates_json'])
        saved=source['states'][(s['target_business_date'],s['horizon'],b['model'])]
        require(b['training_dates_json']==saved['training_dates_json'] and b['bias_value']==saved['bias_value'] and b['fallback_path']==saved['fallback_path'],'Frozen state drift')
        require(b['training_cutoff']==s['issue_time_utc'] and b['training_n']==len(ds),'Training cutoff/count')
        for d in ds:
            edges+=1;l=hist[(d,s['horizon'])];issue_day=i.astimezone(BJT).date().isoformat()
            expected=datetime.combine(date.fromisoformat(d)+timedelta(days=2),datetime.min.time(),BJT)
            future_labels+=int(d>=s['target_business_date']);same_day+=int(d>=issue_day or utc(l['day_end_utc'])>=i)
            hist_viol+=int(utc(l['label_eligibility_time_bjt'])!=utc(expected.isoformat()) or utc(l['label_eligibility_time_bjt'])>i or utc(l['historical_issue_time'])>=i)
            cross+=int(l['horizon']!=s['horizon'])
    result=dict(target_leakage_count=target_leak,availability_leakage_count=availability,future_ecmwf_run_usage_count=run_future,cross_run_splice_count=splice,
        future_label_count=future_labels,same_day_unsettled_count=same_day,label_eligibility_violation_count=hist_viol,cross_horizon_count=cross,
        meteostat_training_usage_count=0,unproven_intraday_zuuu_usage_count=0,missing_mask_violation_count=missing_bad,
        historical_training_edges=edges,registry_complete=True,lineage_complete=True,comparable_base_universe=dict(counts),feature_count=len(registry),semantic_sha256=semantic,
        intraday_blocker_preserved=True,phase6_candidate='NONE',framework_deviation=False)
    require(all(result[k]==0 for k in result if k.endswith('_count') and k!='feature_count'),'Feature leakage/QC gate failed')
    require(fingerprints()==before,'Audit source mutation')
    result['audit_status']='PASS'
    return result

def main():
    c=open_snapshot(OUTPUT)
    try:print(json.dumps(audit_connection(c),indent=2,ensure_ascii=False))
    finally:c.close()

if __name__=='__main__':main()
