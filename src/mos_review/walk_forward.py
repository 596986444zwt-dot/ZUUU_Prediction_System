"""Deterministic chronological bias correction under calendar eligibility."""
import json
import math
from datetime import timedelta
from src.data_v1.contracts import BJT, utc
from .contracts import MODELS, CONTRACT, VERSION, RULE, LAG_HOURS
from .eligibility import check_label, latest_eligible_date

def select_states(sample, past):
    allowed = sorted((r for r in past if r['horizon'] == sample['horizon'] and check_label(r, sample)['is_label_eligible']),
                     key=lambda r:r['business_date_bjt'])
    if len(allowed) < CONTRACT['minimum_history']:
        return {m:(None, [], 'INSUFFICIENT_HISTORY', []) for m in MODELS}
    result = {}
    anchor = latest_eligible_date(sample['issue_time_utc'])
    for model in ('M1','M2','M3','M4','M5'):
        used, path = allowed, model
        if model in ('M2','M3'):
            lower = (anchor-timedelta(days=(7 if model == 'M2' else 30)-1)).isoformat()
            used = [r for r in allowed if lower <= r['business_date_bjt'] <= anchor.isoformat()]
        elif model == 'M4':
            used = [r for r in allowed if r['season'] == sample['season']]
        elif model == 'M5':
            path = 'M5>HORIZON_EXPANDING_IDENTICAL_TO_M1'
        if len(used) < CONTRACT['minimum_group_history']:
            used, path = allowed, model+'>M1'
        result[model] = (math.fsum(r['raw_error'] for r in used)/len(used), used, path, [])
    components = [{'model':m, 'bias':result[m][0], 'fallback':result[m][2],
                   'dates':[r['business_date_bjt'] for r in result[m][1]]} for m in ('M1','M3','M4')]
    union = {r['business_date_bjt']:r for m in ('M1','M3','M4') for r in result[m][1]}
    result['M6'] = (math.fsum(c['bias'] for c in components)/3, [union[k] for k in sorted(union)], 'M6_FIXED_EQUAL_WEIGHTS', components)
    return result

def evaluate(samples, created='AUDIT'):
    predictions, states = [], []
    history = {h:[] for h in ('T0','T1','T2')}
    for sample in sorted(samples, key=lambda r:(utc(r['issue_time_utc']),r['business_date_bjt'],r['horizon'])):
        choices = select_states(sample, history[sample['horizon']])
        for model in MODELS:
            bias, used, path, components = choices[model]
            status = 'PREDICTED' if bias is not None else 'INSUFFICIENT_HISTORY'
            state = dict(target_business_date=sample['business_date_bjt'], horizon=sample['horizon'], model=model,
                bias_value=bias, bias_window={'M2':'7_CALENDAR_DAYS','M3':'30_CALENDAR_DAYS'}.get(model,'EXPANDING'),
                training_cutoff=sample['issue_time_utc'], training_start=used[0]['business_date_bjt'] if used else None,
                training_end=used[-1]['business_date_bjt'] if used else None, training_n=len(used),
                latest_label_eligibility_time_bjt=used[-1]['label_eligibility_time_bjt'] if used else None,
                eligibility_rule=RULE, eligibility_lag=LAG_HOURS,
                training_dates_json=json.dumps([r['business_date_bjt'] for r in used],separators=(',',':')),
                fallback_path=path, components_json=json.dumps(components,sort_keys=True,separators=(',',':')), status=status)
            states.append(state)
            # Continuous prediction is fixed before evaluation label is attached.
            forecast = sample['raw_ecmwf_tmax']-bias if bias is not None else None
            error = forecast-sample['observed_tmax'] if forecast is not None else None
            predictions.append(dict(target_business_date=sample['business_date_bjt'], horizon=sample['horizon'], model=model,
                issue_time=sample['issue_time_utc'], selected_ecmwf_run=sample['selected_ecmwf_run_time_utc'],
                available_time=sample['selected_ecmwf_source_available_time_utc'], availability_semantics=sample['ecmwf_availability_semantics'],
                raw_ecmwf_tmax=sample['raw_ecmwf_tmax'], mos_continuous_tmax=forecast, observed_tmax=sample['observed_tmax'],
                raw_error=sample['raw_error'], mos_error=error, absolute_raw_error=abs(sample['raw_error']),
                absolute_mos_error=abs(error) if error is not None else None, bias_method=model, bias_value=bias,
                bias_window=state['bias_window'], season=sample['season'], month=sample['month'], year=sample['year'],
                lead_start_hours=sample['lead_start_hours'], lead_end_hours=sample['lead_end_hours'],
                training_cutoff=state['training_cutoff'], training_start=state['training_start'], training_end=state['training_end'],
                training_n=state['training_n'], fallback_path=path, eligibility_rule=RULE, eligibility_lag=LAG_HOURS,
                status=status, model_spec=CONTRACT[model], model_version=VERSION, created_at=created))
        history[sample['horizon']].append(sample)
    return predictions, states
