"""Flags only: neither source rewriting nor outcome-driven outlier removal."""
import math
import statistics

def percentile(values,p):
    if not values:return None
    a=sorted(values);q=(len(a)-1)*p;lo=math.floor(q);hi=math.ceil(q)
    return a[lo]+(a[hi]-a[lo])*(q-lo)

def distribution(values):
    a=[x for x in values if x is not None]
    return dict(min=min(a) if a else None,max=max(a) if a else None,mean=math.fsum(a)/len(a) if a else None,
        median=statistics.median(a) if a else None,std=statistics.pstdev(a) if a else None,
        p01=percentile(a,.01),p05=percentile(a,.05),p95=percentile(a,.95),p99=percentile(a,.99))

def flags(feature,value):
    if value is None:return []
    if not math.isfinite(value):return ['NONFINITE']
    n=feature['feature_name'];u=feature['unit'];f=[]
    if u=='%' and 'change' not in n and not 0<=value<=100:f.append('PERCENT_RANGE')
    if u=='m/s' and value<0:f.append('NEGATIVE_WIND')
    if u in ('W/m2','MJ/m2','J/kg','mm') and value<0:f.append('NEGATIVE_PHYSICAL_QUANTITY')
    if u=='hPa' and 'change' not in n and not 800<=value<=1100:f.append('PRESSURE_RANGE')
    if u=='C' and 'temp' in n and not -60<=value<=60:f.append('TEMPERATURE_RANGE')
    return f
