import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from src.data_v1.source_io import open_snapshot, sha256_file, sidecar_state
from src.data_v1.schema import read_rows
from src.data_v1.hashing import dataset_hash
from src.data_v1.contracts import require, utc, DATES
from .contracts import SOURCES, PHASE4, PHASE4_SHA, PHASE4_SEMANTIC, temperature_bin

BJT = ZoneInfo('Asia/Shanghai')

def fingerprints():
    result = {}
    for name, (path, expected) in SOURCES.items():
        digest = sha256_file(path)
        require(digest == expected, f'{name}: frozen physical SHA mismatch')
        result[name] = {'sha256': digest, 'sidecars': sidecar_state(path)}
    return result

def prediction(sample, hours, target, solar, created):
    day, horizon = sample['business_date_bjt'], sample['horizon']
    require(sample['sample_status']=='ELIGIBLE', 'Only eligible samples can become predictions')
    require(len(hours)==24, 'Expected 24 hours')
    start = datetime.fromisoformat(day).replace(tzinfo=BJT)
    expected = {start.astimezone(timezone.utc)+timedelta(hours=i) for i in range(24)}
    times = [utc(h['target_time_utc']) for h in hours]
    require(len(set(times))==24 and set(times)==expected, 'Duplicate or misaligned target hour')
    run = utc(sample['selected_ecmwf_run_time_utc'])
    available = utc(sample['selected_ecmwf_source_available_time_utc'])
    issue = utc(sample['issue_time_utc'])
    require(run <= available <= issue, 'Future leakage or invalid availability ordering')
    require(issue == (start+timedelta(hours=21, days=-int(horizon[1]))).astimezone(timezone.utc), 'Frozen issue time mismatch')
    require(sample['target_tmax_c']==target['daily_tmax_c'], 'Frozen target mismatch')
    require(sample['trajectory_valid_hours']==sample['trajectory_present_hours']==24, 'Source completeness mismatch')
    require(sample['meteostat_training_admission']=='BLOCKED', 'Meteostat admission violation')
    for h in hours:
        require((h['business_date_bjt'],h['horizon'])==(day,horizon), 'Wrong sample lineage')
        require(h['selected_ecmwf_run_time_utc']==sample['selected_ecmwf_run_time_utc'] and h['source_raw_run_id']==sample['source_raw_run_id'], 'Cross-vintage trajectory')
        require(h['source_available_time_utc']==sample['selected_ecmwf_source_available_time_utc'], 'Hourly availability mismatch')
        require(h['temperature_2m_c'] is not None and math.isfinite(h['temperature_2m_c']), 'NULL/nonfinite temperature')
        require(utc(h['target_time_bjt'])==utc(h['target_time_utc']) and h['target_time_bjt'].endswith('+08:00'), 'BJT mismatch')
        require((utc(h['target_time_utc'])-run).total_seconds()==h['lead_hours']*3600, 'Lead mismatch')
        z = solar[h['solar_target_time']]
        require(h['solar_target_time']==h['target_time_utc'] and z['business_date_bjt']==day, 'Solar alignment mismatch')
        require(z['availability_basis']=='DETERMINISTIC_NOT_APPLICABLE', 'Solar availability mismatch')
    seasons = {solar[h['solar_target_time']]['season'] for h in hours}
    require(len(seasons)==1, 'Inconsistent frozen season')
    maximum = max(h['temperature_2m_c'] for h in hours)
    peaks = sorted(h['target_time_utc'] for h in hours if h['temperature_2m_c']==maximum)
    error = maximum-target['daily_tmax_c']
    return dict(business_date_bjt=day, horizon=horizon, issue_time_utc=sample['issue_time_utc'],
        selected_ecmwf_run_time_utc=sample['selected_ecmwf_run_time_utc'],
        selected_ecmwf_source_available_time_utc=sample['selected_ecmwf_source_available_time_utc'],
        hour_count=24, ecmwf_raw_tmax_c=maximum, target_tmax_c=target['daily_tmax_c'],
        error_c=error, absolute_error_c=abs(error), squared_error_c=error**2,
        ecmwf_tmax_first_time_utc=peaks[0], ecmwf_tmax_last_time_utc=peaks[-1], ecmwf_tmax_occurrence_count=len(peaks),
        status='ELIGIBLE', source_data_version='DATA_V1', source_phase4_semantic_sha256=PHASE4_SEMANTIC,
        ecmwf_availability_semantics=sample['ecmwf_availability_semantics'],
        month=start.month, year=start.year, season=next(iter(seasons)), target_bin=temperature_bin(target['daily_tmax_c']), created_at_utc=created)

def load(created):
    before = fingerprints()
    conn = open_snapshot(PHASE4, PHASE4_SHA)
    try:
        require(conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok', 'Phase4 integrity failure')
        require(not conn.execute('PRAGMA foreign_key_check').fetchall(), 'Phase4 FK failure')
        data = read_rows(conn)
        manifests = conn.execute('SELECT * FROM phase4_data_v1_manifest').fetchall()
        require(len(manifests)==1 and manifests[0]['build_status']=='ACCEPTED', 'Source manifest failure')
        require(manifests[0]['dataset_semantic_sha256']==dataset_hash(data)==PHASE4_SEMANTIC, 'Phase4 semantic hash failure')
    finally:
        conn.close()
    target = {r['business_date_bjt']:r for r in data['target']}
    require(tuple(sorted(target))==DATES and len(data['target'])==729, 'Target calendar mismatch')
    require(all(r['target_status']=='FROZEN' and r['target_version']=='ZUUU_TARGET_V1' for r in target.values()), 'Target contract mismatch')
    require(len(data['sample'])==2187, 'Source sample count mismatch')
    solar = {r['target_time']:r for r in data['solar']}
    require(len(solar)==len(data['solar'])==17496, 'Solar coverage mismatch')
    hours = defaultdict(list)
    for row in data['ecmwf_hourly']:
        hours[row['business_date_bjt'],row['horizon']].append(row)
    predictions, exclusions = [], []
    seen = set()
    for s in data['sample']:
        key = s['business_date_bjt'],s['horizon']
        require(key not in seen, 'Duplicate sample')
        seen.add(key)
        if s['sample_status']=='ELIGIBLE':
            predictions.append(prediction(s, hours[key], target[key[0]], solar, created))
        else:
            require(key==('2025-08-07','T2') and s['sample_status']=='EXCLUDED_INCOMPLETE_ECMWF', 'Unexpected exclusion')
            require(s['trajectory_valid_hours']==14 and len(hours[key])==14 and sum(h['temperature_2m_c'] is not None for h in hours[key])==14, 'Gap signature changed')
            require(utc(s['selected_ecmwf_run_time_utc'])==datetime(2025,8,4,6,tzinfo=timezone.utc), 'Gap vintage changed')
            exclusions.append({k:s[k] for k in ('business_date_bjt','horizon','sample_status','exclusion_reason','trajectory_valid_hours','trajectory_expected_hours','selected_ecmwf_run_time_utc')})
    require(seen=={(d,h) for d in DATES for h in ('T0','T1','T2')}, 'Source sample calendar mismatch')
    require(len(exclusions)==1 and len(predictions)==2186 and len(data['ecmwf_hourly'])==52478, 'Source counts mismatch')
    require([sum(r['horizon']==h for r in predictions) for h in ('T0','T1','T2')]==[729,729,728], 'Horizon counts mismatch')
    require(fingerprints()==before, 'Source modified during read')
    return predictions, exclusions, before
