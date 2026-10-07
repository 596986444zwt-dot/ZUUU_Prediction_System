"""Bias sees only verified settled historical errors, never evaluation labels."""
import math
from datetime import timedelta
from src.data_v1.contracts import BJT, utc
from .contracts import CONTRACT

def eligible_history(sample, history):
    issue = utc(sample['issue_time_utc'])
    return sorted((r for r in history
        if r['horizon'] == sample['horizon']
        and r.get('settlement_verified') is True
        and r.get('settled_at_utc') is not None
        and utc(r['settled_at_utc']) < issue
        and utc(r['day_end_utc']) <= utc(r['settled_at_utc'])
        and utc(r['day_end_utc']) < issue
        and utc(r['issue_time_utc']) < issue), key=lambda r: r['business_date_bjt'])

def select_bias(sample, history, model):
    past = eligible_history(sample, history)
    minimum = CONTRACT['minimum_history']
    if len(past) < minimum:
        return None, [], 'INSUFFICIENT_VERIFIED_SETTLED_HISTORY', []
    selected = past
    path = model
    components = []
    if model in ('M2', 'M3'):
        days = 7 if model == 'M2' else 30
        anchor = utc(sample['issue_time_utc']).astimezone(BJT).date() - timedelta(days=1)
        lower = (anchor - timedelta(days=days-1)).isoformat()
        selected = [r for r in past if lower <= r['business_date_bjt'] <= anchor.isoformat()]
        if len(selected) < CONTRACT['minimum_group_history']:
            selected, path = past, model + '>M1'
    elif model == 'M4':
        selected = [r for r in past if r['season'] == sample['season']]
        if len(selected) < CONTRACT['minimum_group_history']:
            selected, path = past, 'M4>M1'
    elif model == 'M5':
        path = 'M5>HORIZON_EXPANDING_IDENTICAL_TO_M1'
    elif model == 'M6':
        selected_by_key = {}
        for child in ('M1', 'M3', 'M4'):
            value, rows, fallback, _ = select_bias(sample, past, child)
            components.append({'model': child, 'bias': value, 'fallback': fallback,
                               'dates': [r['business_date_bjt'] for r in rows]})
            selected_by_key.update({r['business_date_bjt']: r for r in rows})
        selected = [selected_by_key[k] for k in sorted(selected_by_key)]
        return math.fsum(c['bias'] for c in components)/3, selected, 'M6_FIXED_EQUAL_WEIGHTS', components
    elif model != 'M1':
        raise ValueError('Unknown statistical MOS model')
    return math.fsum(r['raw_error'] for r in selected)/len(selected), selected, path, components
