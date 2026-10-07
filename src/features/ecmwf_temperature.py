"""Pure frozen-trajectory operators; no observed temperature input."""
import math
from .contracts import require, CONTRACT

def complete_values(rows,field,hours):
    values=[rows[h].get(field) for h in hours]
    if any(v is None for v in values):return None,'MISSING_SOURCE_INPUT'
    if any(not math.isfinite(v) for v in values):return None,'NONFINITE_SOURCE_INPUT'
    return values,None

def compute(definition, rows):
    fields=__import__('json').loads(definition['source_fields'])
    hs=__import__('json').loads(definition['hours_json'])
    t=[rows[h]['temperature_2m_c'] for h in range(24)];peak=t.index(max(t))
    op=definition['operation']
    if op in ('at_peak','sin_peak','cos_peak'):hs=[peak]
    if hs is None:hs=list(range(24))
    arrays=[]
    for field in fields:
        vals,reason=complete_values(rows,field,hs)
        if reason:return None,reason
        arrays.append(vals)
    v=arrays[0]
    if fields[0].endswith('_kmh'):v=[x/3.6 for x in v]
    if op=='max':return max(v),None
    if op=='min':return min(v),None
    if op=='mean':return math.fsum(v)/len(v),None
    if op=='range':return max(v)-min(v),None
    if op=='argmax':return hs[v.index(max(v))],None
    if op=='argmin':return hs[v.index(min(v))],None
    if op in ('at','at_peak'):return v[0],None
    if op=='delta':return v[1]-v[0],None
    if op=='sum':return math.fsum(v),None
    if op=='energy':return math.fsum(v)*.0036,None
    if op=='clear':return sum(x<=10 for x in v),None
    if op=='positive_count':return sum(x>0 for x in v),None
    if op=='any_positive':return int(any(x>0 for x in v)),None
    if op=='sin_peak':return math.sin(math.radians(v[0])),None
    if op=='cos_peak':return math.cos(math.radians(v[0])),None
    if op in ('spread_min_c','spread_mean_c'):
        spreads=[a-b for a,b in zip(*arrays)]
        return (min(spreads) if op=='spread_min_c' else math.fsum(spreads)/24),None
    if op in ('period_delta','rh_change_pct','morning_to_afternoon_delta_c'):return math.fsum(v[12:18])/6-math.fsum(v[6:12])/6,None
    change=[t[h]-t[h-1] for h in range(1,24)]
    if op=='max_hourly_warming_c':return max(change),None
    if op=='max_hourly_cooling_c':return min(change),None
    if op=='strongest_warming_hour_bjt':return 1+change.index(max(change)),None
    if op=='strongest_cooling_hour_bjt':return 1+change.index(min(change)),None
    if op=='peak_sharpness_c':return (None,'PEAK_AT_ENDPOINT') if peak in (0,23) else (t[peak]-(t[peak-1]+t[peak+1])/2,None)
    if op=='hours_within_05c_of_max':return sum(x>=max(t)-.5 for x in t),None
    if op=='hours_within_10c_of_max':return sum(x>=max(t)-1 for x in t),None
    if op=='pre_peak_slope_c_per_h':return (None,'PEAK_NOT_AFTER_06') if peak<=6 else ((t[peak]-t[6])/(peak-6),None)
    if op=='post_peak_slope_c_per_h':return (None,'PEAK_NOT_BEFORE_21') if peak>=21 else ((t[21]-t[peak])/(21-peak),None)
    if op=='morning_to_peak_warming_c':return max(t)-t[6],None
    if op=='peak_to_evening_cooling_c':return max(t)-t[21],None
    raise ValueError('Unknown operator: '+op)
