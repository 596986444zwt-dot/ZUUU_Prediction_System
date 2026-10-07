"""Two explicitly separate diagnostics; never change frozen issue semantics."""
from collections import Counter, defaultdict
from datetime import date, timedelta
import math
from src.audit.ecmwf_issue_rule_final_audit import choose_latest_complete
from .contracts import HOURS, WEATHER, utc, issue_time, require, distribution

def legal_candidates(archive, cutoff):
    cutoff = utc(cutoff)
    return sorted([r for r in archive if r['canonical_status']=='AVAILABLE'
        and r['archive_status']=='FROZEN' and r['_available']<=cutoff and r['_run']<=cutoff],
        key=lambda r:r['_run'],reverse=True)

def inspect_trajectory(run, hourly, day, cutoff):
    start=issue_time(day,0)
    expected=[start+timedelta(hours=h) for h in range(24)]
    rows=hourly.get(run['canonical_raw_run_id'],{})
    selected=[rows[t] for t in expected if t in rows]
    require(run['_run']<=run['_available']<=cutoff, 'Future ECMWF vintage/availability')
    for r in selected:
        require(r['raw_run_id']==run['canonical_raw_run_id'] and utc(r['run_time_utc'])==run['_run'], 'Cross-vintage')
        require(utc(r['source_available_time_utc'])==run['_available'], 'Availability mismatch')
        require(utc(r['target_time_utc'])==run['_run']+timedelta(hours=r['lead_hours']), 'Invalid forecast lead')
    valid=lambda r,k:r[k] is not None and math.isfinite(r[k])
    remaining=[r for r in selected if utc(r['target_time_utc'])>=cutoff]
    future=[r for r in selected if utc(r['target_time_utc'])>cutoff]
    return {'present':len(selected),'valid':sum(valid(r,'temperature_2m_c') for r in selected),
        'remaining_valid':sum(valid(r,'temperature_2m_c') for r in remaining),'remaining_expected':sum(t>=cutoff for t in expected),
        'future_valid':sum(valid(r,'temperature_2m_c') for r in future),
        'lead_hours':[r['lead_hours'] for r in selected],
        'variables':{v:sum(valid(r,v) for r in selected) for v in WEATHER},
        'remaining_variables':{v:sum(valid(r,v) for r in remaining) for v in WEATHER}}

def analyze(conn, dates):
    archive=[dict(r) for r in conn.execute("SELECT * FROM ecmwf_archive_v1 WHERE archive_status='FROZEN' ORDER BY run_time_utc")]
    require(len(archive)==3411, 'Archive count mismatch')
    for run in archive:
        run['_run']=utc(run['run_time_utc'])
        run['_available']=utc(run['source_available_time_utc'])
        require(run['_run']<=run['_available'], 'Archive time ordering error')
    require(Counter(r['canonical_status'] for r in archive)=={'AVAILABLE':3404,'SOURCE_TEMPERATURE_UNAVAILABLE':7}, 'Canonical status mismatch')
    columns=('raw_run_id','run_time_utc','source_available_time_utc','target_time_utc','lead_hours',*WEATHER)
    hourly=defaultdict(dict)
    temperatures=defaultdict(dict)
    for row in conn.execute('SELECT '+','.join('h.'+f for f in columns)+' FROM ecmwf_hourly_forecasts h JOIN ecmwf_archive_v1 a ON a.canonical_raw_run_id=h.raw_run_id ORDER BY h.raw_run_id,h.target_time_utc'):
        r=dict(row)
        key=utc(r['target_time_utc'])
        require(key not in hourly[r['raw_run_id']], 'Duplicate hourly target')
        hourly[r['raw_run_id']][key]=r
        temperatures[r['raw_run_id']][key]=r['temperature_2m_c']
    require(sum(v is None for r in temperatures.values() for v in r.values())==504, 'Frozen 504 NULL signature changed')
    rule=dict(conn.execute('SELECT * FROM ecmwf_issue_rule_v1').fetchone())
    details=[]
    summaries=[]
    feature_counts=[]
    for hour in HOURS:
        per_policy=defaultdict(list)
        variable_counts=defaultdict(Counter)
        all_leads=defaultdict(list)
        for day in dates:
            cutoff=issue_time(day,hour)
            legal=legal_candidates(archive,cutoff)
            # NEWEST_LEGAL is diagnostic even when partial. FROZEN_NEWEST_COMPLETE
            # reuses the accepted pure selector; the two results must not be conflated.
            latest=legal[0] if legal else None
            selected=choose_latest_complete(legal,temperatures,date.fromisoformat(day),cutoff)
            for policy,run in [('NEWEST_LEGAL',latest),('FROZEN_NEWEST_COMPLETE',selected[0] if selected else None)]:
                row={'business_date_bjt':day,'snapshot_bjt':f'{hour:02}:00','policy':policy,'issue_time_utc':cutoff.isoformat(),
                    'selected_run_time_utc':None,'source_available_time_utc':None,'availability_semantics':None,
                    'present_hours':0,'valid_temperature_hours':0,'remaining_valid_hours':0,'remaining_expected_hours':24-hour,
                    'future_valid_hours':0,'status':'NO_LEGAL_RUN','run_cycle_utc':None,'run_day_offset_utc':None,
                    'run_age_at_issue_hours':None,'lead_min':None,'lead_max':None,'fallback_from_latest':False}
                if run is not None:
                    check=inspect_trajectory(run,hourly,day,cutoff)
                    all_leads[policy].extend(check['lead_hours'])
                    row.update(selected_run_time_utc=run['run_time_utc'],source_available_time_utc=run['source_available_time_utc'],
                        availability_semantics=run['availability_semantics'],present_hours=check['present'],valid_temperature_hours=check['valid'],
                        remaining_valid_hours=check['remaining_valid'],remaining_expected_hours=check['remaining_expected'],future_valid_hours=check['future_valid'],
                        status='COMPLETE' if check['valid']==24 else 'PARTIAL' if check['valid'] else 'NO_TARGET_TEMPERATURE',
                        run_cycle_utc=run['_run'].hour,run_day_offset_utc=(run['_run'].date()-date.fromisoformat(day)).days,
                        run_age_at_issue_hours=(cutoff-run['_run']).total_seconds()/3600,
                        lead_min=min(check['lead_hours']) if check['lead_hours'] else None,lead_max=max(check['lead_hours']) if check['lead_hours'] else None,
                        fallback_from_latest=bool(latest and latest['canonical_raw_run_id']!=run['canonical_raw_run_id']))
                    for var in WEATHER:
                        vc=variable_counts[policy,var]
                        vc['valid_hourly_values']+=check['variables'][var]
                        vc['samples_with_any']+=int(check['variables'][var]>0)
                        vc['samples_with_24_valid']+=int(check['variables'][var]==24)
                        vc['remaining_valid_values']+=check['remaining_variables'][var]
                        vc['samples_remaining_complete']+=int(check['remaining_variables'][var]==check['remaining_expected'])
                per_policy[policy].append(row)
                details.append(row)
        for policy,rows in per_policy.items():
            summaries.append({'snapshot_bjt':f'{hour:02}:00','policy':policy,'days':len(rows),
                'legal_run_days':sum(r['selected_run_time_utc'] is not None for r in rows),
                'no_legal_run_days':sum(r['selected_run_time_utc'] is None for r in rows),
                'target_trajectory_any_days':sum(r['valid_temperature_hours']>0 for r in rows),
                'complete_24h_days':sum(r['status']=='COMPLETE' for r in rows),'partial_days':sum(r['status']=='PARTIAL' for r in rows),
                'completely_unavailable_days':sum(r['valid_temperature_hours']==0 for r in rows),
                'remaining_complete_days':sum(r['remaining_valid_hours']==r['remaining_expected_hours'] for r in rows),
                'fallback_days':sum(r['fallback_from_latest'] for r in rows),
                'run_cycle_day_offset_distribution':dict(Counter(f"D{r['run_day_offset_utc']:+d}/{r['run_cycle_utc']:02d}Z" for r in rows if r['selected_run_time_utc'])),
                'valid_hours_distribution':dict(Counter(r['valid_temperature_hours'] for r in rows)),
                'run_age_hours':distribution([r['run_age_at_issue_hours'] for r in rows if r['run_age_at_issue_hours'] is not None]),
                'lead_min_distribution':distribution([r['lead_min'] for r in rows if r['lead_min'] is not None]),
                'lead_max_distribution':distribution([r['lead_max'] for r in rows if r['lead_max'] is not None]),
                'forecast_lead_hours_distribution':distribution(all_leads[policy]),
                'forecast_lead_hours_histogram':dict(sorted(Counter(all_leads[policy]).items())),
                'availability_semantics_distribution':dict(Counter(r['availability_semantics'] for r in rows if r['availability_semantics']))})
        for (policy,var),counts in sorted(variable_counts.items()):
            feature_counts.append({'snapshot_bjt':f'{hour:02}:00','policy':policy,'variable':var,**dict(counts)})
    return {'coverage':summaries,'selections':details,'variable_availability':feature_counts,
        'frozen_issue_rule':rule,'canonical_status_counts':dict(Counter(r['canonical_status'] for r in archive)),
        'source_temperature_null_count':504,'leakage_violations':0,'cross_vintage_violations':0,
        'remaining_day_boundary':'Candidate audit only: target_time >= issue_time. Strict future uses >. No interpolation; arbitrary-minute endpoint semantics not frozen.'}
