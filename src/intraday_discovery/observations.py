import json
from collections import Counter, defaultdict
from .contracts import BJT, HOURS, issue_time, peak_relation, utc, require, distribution

def grouped_latency(rows, field, group_fields):
    groups = defaultdict(list)
    for r in rows:
        key = tuple(str(utc(r['observation_time_utc']).astimezone(BJT).year) if f=='year_bjt' else str(r.get(f)) for f in group_fields)
        groups[key].append((utc(r[field])-utc(r['observation_time_utc'])).total_seconds())
    return [{**dict(zip(group_fields,key)), 'delay_seconds':distribution(values)} for key,values in sorted(groups.items())]

def cadence(rows, dates):
    grouped = defaultdict(list)
    for r in rows:
        grouped[utc(r['observation_time_utc']).astimezone(BJT).date().isoformat()].append(r)
    daily = []
    time_versions = defaultdict(list)
    for r in rows:
        time_versions[r['observation_time_utc']].append(r)
    for day in dates:
        observations = grouped[day]
        times = [utc(r['observation_time_utc']).astimezone(BJT) for r in observations]
        daily.append({'business_date_bjt':day,'observation_rows':len(observations),'unique_observation_times':len(set(times)),
            'hourly_slots':len({t.hour for t in times}), 'exact_hour_slots':len({t.hour for t in times if t.minute==t.second==t.microsecond==0}),
            'non_hourly_rows':sum(bool(t.minute or t.second or t.microsecond) for t in times),
            'cor_rows':sum(r['message_class']=='COR' for r in observations)})
    counter = Counter(r['message_class'] for r in rows)
    return {
        'rows':len(rows),'daily_count_distribution':distribution([r['observation_rows'] for r in daily]),
        'daily_count_histogram':dict(sorted(Counter(r['observation_rows'] for r in daily).items())),
        'exact_hour_coverage':sum(r['exact_hour_slots'] for r in daily), 'expected_hour_slots':len(dates)*24,
        'hourly_coverage_rate':sum(r['exact_hour_slots'] for r in daily)/(len(dates)*24),
        'non_hourly_rows':sum(r['non_hourly_rows'] for r in daily),
        'message_class_counts':{k:counter[k] for k in ('METAR','SPECI','COR','AMD','PREFIXLESS','OTHER')},
        'source_query_class_counts':dict(Counter(str(r.get('source_query_class')) for r in rows)),
        'duplicate_time_groups':sum(len(v)>1 for v in time_versions.values()),
        'duplicate_time_extra_rows':sum(len(v)-1 for v in time_versions.values()),
        'multiple_raw_text_versions':sum(len({r.get('raw_metar') for r in v})>1 for v in time_versions.values()),
        'duplicate_details':[{ 'observation_time_utc':t,'raw_ids':[r.get('id') for r in v],
            'classes':[r['message_class'] for r in v]} for t,v in time_versions.items() if len(v)>1],
        'hour_of_day_coverage':[{'hour_bjt':h,'days':sum(any(utc(r['observation_time_utc']).astimezone(BJT).hour==h for r in grouped[d]) for d in dates)} for h in range(24)],
    },daily

def analyze(conn):
    targets = [dict(r) for r in conn.execute('SELECT * FROM zuuu_target_v1 ORDER BY business_date_bjt')]
    require(len(targets)==729 and all(r['target_status']=='FROZEN' and r['target_version']=='ZUUU_TARGET_V1' for r in targets), 'Target freeze mismatch')
    dates = [r['business_date_bjt'] for r in targets]
    from src.data_v1.contracts import DATES
    require(tuple(dates)==DATES, 'Target calendar mismatch')
    require(all(float(r['daily_tmax_c']).is_integer() for r in targets), 'Ground Truth integer contract mismatch')
    raw_all = [dict(r) for r in conn.execute('SELECT * FROM zuuu_raw_metar ORDER BY observation_time_utc,id')]
    raw = [r for r in raw_all if utc(r['observation_time_utc']).astimezone(BJT).date().isoformat() in set(dates)]
    silver = [dict(r) for r in conn.execute('SELECT s.*,b.ingest_time_utc,b.raw_metar FROM zuuu_silver_observation s JOIN zuuu_raw_metar b ON b.id=s.bronze_raw_id ORDER BY s.id')]
    silver_window = [r for r in silver if r['business_date_bjt'] in set(dates)]
    for r in silver_window:
        require(utc(r['observation_time_bjt'])==utc(r['observation_time_utc']), 'Silver timezone mismatch')
    by_id = {r['id']:r for r in silver_window}
    # Validate frozen lineage, not recompute or replace the Ground Truth definition.
    for t in targets:
        peak_rows = [by_id[i] for i in json.loads(t['tmax_silver_ids'])]
        require(len(peak_rows)==t['tmax_occurrence_count'], 'Frozen occurrence lineage mismatch')
        require(all(r['temperature_c']==t['daily_tmax_c'] and r['business_date_bjt']==t['business_date_bjt'] for r in peak_rows), 'Frozen peak lineage mismatch')
        require(min(utc(r['observation_time_bjt']) for r in peak_rows)==utc(t['first_tmax_time_bjt']) and max(utc(r['observation_time_bjt']) for r in peak_rows)==utc(t['last_tmax_time_bjt']), 'Frozen peak timestamp mismatch')
        require(sorted(r['bronze_raw_id'] for r in peak_rows)==sorted(json.loads(t['tmax_bronze_raw_ids'])), 'Peak Bronze lineage mismatch')
    latency = {}
    for name, rows, field in [('bronze_all',raw_all,'ingest_time_utc'),('bronze_729_days',raw,'ingest_time_utc'),('silver_processing_729_days',silver_window,'created_at_utc')]:
        latency[name] = {'overall_seconds':distribution([(utc(r[field])-utc(r['observation_time_utc'])).total_seconds() for r in rows]),
            'by_source':grouped_latency(rows,field,('source',)), 'by_message_class':grouped_latency(rows,field,('message_class',)),
            'by_year_bjt':grouped_latency(rows,field,('year_bjt',)),
            'by_source_class_year':grouped_latency(rows,field,('source','message_class','year_bjt')),
            'observation_min':min(r['observation_time_utc'] for r in rows),'observation_max':max(r['observation_time_utc'] for r in rows),
            'timestamp_min':min(r[field] for r in rows),'timestamp_max':max(r[field] for r in rows),
            'timestamp_date_counts_utc':dict(Counter(utc(r[field]).date().isoformat() for r in rows))}
    legacy = [dict(r) for r in conn.execute('SELECT * FROM zuuu_raw_reports ORDER BY id')]
    legacy_evidence = []
    for r in legacy:
        payload = json.loads(r['raw_json'])
        payload = payload[0] if isinstance(payload,list) else payload
        require(payload.get('receiptTime')==r['receipt_time_utc'], 'AWC receipt provenance mismatch')
        legacy_evidence.append({k:r[k] for k in ('id','observation_time_utc','report_time_utc','receipt_time_utc','ingest_time_utc','bronze_file_path')})
    local_files = []
    from .contracts import ROOT
    for path in sorted((ROOT/'data/bronze/zuuu').glob('*.json')):
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
        for r in payload if isinstance(payload,list) else [payload]:
            local_files.append({'path':str(path.relative_to(ROOT)),'obsTime':r.get('obsTime'),'receiptTime':r.get('receiptTime'),'reportTime':r.get('reportTime')})
    raw_cadence,daily = cadence(raw,dates)
    silver_cadence,_ = cadence(silver_window,dates)
    first_minutes = [(utc(r['first_tmax_time_bjt']).astimezone(BJT)-issue_time(r['business_date_bjt'],0).astimezone(BJT)).total_seconds()/60 for r in targets]
    histogram = []
    cumulative=0
    for h in range(24):
        count=sum(int(m//60)==h for m in first_minutes)
        cumulative+=count
        histogram.append({'hour_bjt':h,'days':count,'percentage':100*count/729,'cumulative_percentage':100*cumulative/729})
    snapshots=[]
    by_day=defaultdict(list)
    for r in silver_window: by_day[r['business_date_bjt']].append(r)
    for hour in HOURS:
        relations=Counter(peak_relation(issue_time(r['business_date_bjt'],hour),r['first_tmax_time_bjt']) for r in targets)
        counts=[]
        event_available=Counter()
        for day in dates:
            cutoff=issue_time(day,hour)
            available=[r for r in by_day[day] if utc(r['observation_time_utc'])<=cutoff]
            counts.append(len(available))
            if available:
                event_available['latest_tmax_tmin_count']+=1
                latest=max(utc(r['observation_time_utc']) for r in available)
                from datetime import timedelta
                for lag in (1,2,3):
                    event_available[f'trend_{lag}h']+=int(any(utc(r['observation_time_utc'])==latest-timedelta(hours=lag) for r in available))
        leads=[(utc(t['first_tmax_time_bjt'])-issue_time(t['business_date_bjt'],hour)).total_seconds()/3600 for t in targets if peak_relation(issue_time(t['business_date_bjt'],hour),t['first_tmax_time_bjt'])=='PRE_PEAK']
        snapshots.append({'snapshot_bjt':f'{hour:02}:00','days':729,**{k:relations[k] for k in ('PRE_PEAK','EQUAL_PEAK','POST_PEAK')},
            'pre_peak_pct':100*relations['PRE_PEAK']/729,'pre_peak_median_lead_hours':distribution(leads)['median'],
            'event_time_only_observation_count_min':min(counts),'event_time_only_observation_count_median':distribution(counts)['median'],
            **{f'event_time_only_{k}_days':v for k,v in event_available.items()},
            'strict_observation_asof_proven_days':0,'observation_availability_status':'NOT_PROVEN_BULK_INGEST'})
    return {'targets':targets,'latency':latency,'bronze_cadence':raw_cadence,'silver_cadence':silver_cadence,'daily_cadence':daily,
        'historical_ingest_equals_created_rows':sum(r['ingest_time_utc']==r['created_at_utc'] for r in raw_all),
        'silver_corrections_without_supersedes':sum(r['is_correction']==1 and r['supersedes_raw_id'] is None for r in silver_window),
        'correction_records':[{k:r[k] for k in ('id','bronze_raw_id','observation_time_utc','message_class','source_query_class','supersedes_raw_id')} for r in silver_window if r['is_correction']],
        'legacy_awc_records':legacy_evidence,'legacy_awc_records_in_729_days':sum(utc(r['observation_time_utc']).astimezone(BJT).date().isoformat() in dates for r in legacy),
        'local_bronze_json_evidence':local_files,'peak_distribution':histogram,'first_peak_minutes_bjt':distribution(first_minutes),
        'last_peak_minutes_bjt':distribution([(utc(r['last_tmax_time_bjt']).astimezone(BJT).hour*60+utc(r['last_tmax_time_bjt']).astimezone(BJT).minute) for r in targets]),
        'occurrence_count_distribution':distribution([r['tmax_occurrence_count'] for r in targets]),
        'target_integer_temperature_distribution':distribution([r['daily_tmax_c'] for r in targets]),'snapshots':snapshots}
