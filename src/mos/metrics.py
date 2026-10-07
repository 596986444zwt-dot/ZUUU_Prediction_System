import math
import statistics
from .contracts import MODELS

def calculate(errors):
    n = len(errors)
    result = dict(n=n, bias=None, mae=None, rmse=None, median_ae=None, p90_ae=None, max_ae=None)
    if not n:
        return result
    ae = sorted(map(abs, errors))
    q = (n-1)*.9
    result.update(bias=math.fsum(errors)/n, mae=math.fsum(ae)/n,
        rmse=math.sqrt(math.fsum(e*e for e in errors)/n), median_ae=statistics.median(ae),
        p90_ae=ae[math.floor(q)]+(ae[math.ceil(q)]-ae[math.floor(q)])*(q-math.floor(q)), max_ae=ae[-1])
    return result

def compare(rows, model, horizon, kind='ALL', value='ALL'):
    valid = [r for r in rows if r['status'] == 'PREDICTED']
    raw, mos = calculate([r['raw_error'] for r in valid]), calculate([r['mos_error'] for r in valid])
    out = dict(model=model, horizon=horizon, slice_type=kind, slice_value=str(value), attempted_n=len(rows), n=len(valid))
    out.update({'raw_'+k: v for k, v in raw.items() if k != 'n'})
    out.update({'mos_'+k: v for k, v in mos.items() if k != 'n'})
    out.update(mae_improvement=raw['mae']-mos['mae'] if valid else None,
        mae_improvement_pct=100*(raw['mae']-mos['mae'])/raw['mae'] if valid and raw['mae'] else None,
        rmse_improvement=raw['rmse']-mos['rmse'] if valid else None,
        bias_change=mos['bias']-raw['bias'] if valid else None, stability='NOT_ESTIMABLE')
    if valid:
        d = out['mae_improvement']
        out['stability'] = 'IMPROVED' if d > 1e-12 else 'WORSENED' if d < -1e-12 else 'UNCHANGED'
    for label, tolerance in [('exact', 0), ('within1', 1), ('within2', 2)]:
        for method, key in [('raw', 'raw_ecmwf_tmax'), ('mos', 'mos_continuous_tmax')]:
            out[f'{method}_integer_{label}_diagnostic'] = sum(abs(math.floor(r[key]+.5)-r['observed_tmax']) <= tolerance for r in valid)/len(valid) if valid else None
    return out

def summarize(predictions):
    overall, slices = [], []
    for model in MODELS:
        for horizon in ('T0', 'T1', 'T2'):
            rows = [r for r in predictions if r['model'] == model and r['horizon'] == horizon]
            overall.append(compare(rows, model, horizon))
            for kind, values in [('season', ['DJF', 'MAM', 'JJA', 'SON']), ('month', range(1, 13)), ('year', [2024, 2025, 2026])]:
                for value in values:
                    slices.append(compare([r for r in rows if r[kind] == value], model, horizon, kind, value))
    return overall, slices
