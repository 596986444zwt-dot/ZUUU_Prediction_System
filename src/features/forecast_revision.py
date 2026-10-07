import math
from datetime import timedelta
from .contracts import utc, require

def comparison_run(sample,runs,slot):
    current=utc(sample['selected_ecmwf_run_time_utc']);issue=utc(sample['issue_time_utc'])
    model=runs[sample['selected_ecmwf_run_time_utc']]['model']
    if slot=='prev_run':
        # Explicit max, independent of dictionary insertion order.
        candidates=[r for key,r in runs.items() if key<sample['selected_ecmwf_run_time_utc'] and r['model']==model and r['source_available_time_utc']<=sample['issue_time_utc']]
        return max(candidates,key=lambda r:utc(r['run_time_utc']),default=None)
    wanted=current-timedelta(hours=int(slot[:-1]))
    r=runs.get(wanted.isoformat())
    return r if r and r['model']==model and utc(r['source_available_time_utc'])<=issue else None

def compute(sample,current_hours,runs,trajectories,slot,operation,comparison_lookup=None):
    run=comparison_lookup[slot] if comparison_lookup is not None else comparison_run(sample,runs,slot)
    if run is None:return None,'COMPARISON_RUN_UNAVAILABLE',None,[]
    rows=trajectories[run['canonical_raw_run_id']]
    aligned=[rows.get(h['target_time_utc']) for h in current_hours]
    if any(r is None or r['temperature_2m_c'] is None for r in aligned):return None,'COMPARISON_TARGET_CURVE_INCOMPLETE',run,[r for r in aligned if r]
    old=[r['temperature_2m_c'] for r in aligned];cur=[r['temperature_2m_c'] for r in current_hours]
    if not all(math.isfinite(v) for v in old):return None,'NONFINITE_COMPARISON_INPUT',run,aligned
    if operation in ('prev_run','6h','12h','24h'):value=max(cur)-max(old)
    elif operation=='curve_revision_prev_mean_c':value=math.fsum(a-b for a,b in zip(cur,old))/24
    elif operation=='curve_revision_prev_max_abs_c':value=max(abs(a-b) for a,b in zip(cur,old))
    elif operation=='peak_hour_revision_prev_h':value=cur.index(max(cur))-old.index(max(old))
    elif operation=='temp_14_revision_prev_c':value=cur[14]-old[14]
    else:raise ValueError(operation)
    return value,None,run,aligned
