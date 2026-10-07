import math
from .contracts import CONTRACT

def calculate(rows):
    rows = sorted(rows, key=lambda r: (r['business_date_bjt'], r['horizon']))
    n = len(rows)
    result = dict.fromkeys(('bias_c','mae_c','rmse_c','median_ae_c','p90_ae_c','max_ae_c',
        'mean_forecast_tmax_c','mean_observed_tmax_c','error_std_c','pearson_r','r2'))
    result['n'] = n
    for label, threshold in (('0_5', .5), ('1', 1), ('2', 2)):
        count = sum(abs(r['error_c']) <= threshold for r in rows)
        result[f'ae_le_{label}_count'] = count
        result[f'ae_le_{label}_rate'] = count/n if n else None
    if not n:
        return result
    e = [r['error_c'] for r in rows]
    a = sorted(map(abs, e))
    x = [r['ecmwf_raw_tmax_c'] for r in rows]
    y = [r['target_tmax_c'] for r in rows]
    mean = lambda v: math.fsum(v)/n
    def quantile(p):
        k = (n-1)*p
        lo = math.floor(k)
        return a[lo] + (a[math.ceil(k)]-a[lo])*(k-lo)
    mx, my, me = mean(x), mean(y), mean(e)
    sx = math.fsum((v-mx)**2 for v in x)
    sy = math.fsum((v-my)**2 for v in y)
    se = math.fsum(v*v for v in e)
    result.update(bias_c=me, mae_c=mean(a), rmse_c=math.sqrt(se/n), median_ae_c=quantile(.5),
        p90_ae_c=quantile(.9), max_ae_c=a[-1], mean_forecast_tmax_c=mx, mean_observed_tmax_c=my,
        error_std_c=math.sqrt(mean([(v-me)**2 for v in e])),
        pearson_r=math.fsum((u-mx)*(v-my) for u,v in zip(x,y))/math.sqrt(sx*sy) if n>1 and sx and sy else None,
        r2=1-se/sy if n>1 and sy else None)
    return result

def summarize(predictions):
    metrics, groups = [], []
    categories = {'month': list(map(str, range(1,13))), 'season': ['spring','summer','autumn','winter'],
                  'target_bin': CONTRACT['bins'], 'year': ['2024','2025','2026']}
    for horizon in ('T0','T1','T2'):
        rows = [r for r in predictions if r['horizon']==horizon]
        metrics.append({'horizon': horizon, **calculate(rows)})
        for kind, values in categories.items():
            for value in values:
                groups.append({'horizon': horizon, 'group_type': kind, 'group_value': value,
                    **calculate([r for r in rows if str(r[kind])==value])})
    return metrics, groups
