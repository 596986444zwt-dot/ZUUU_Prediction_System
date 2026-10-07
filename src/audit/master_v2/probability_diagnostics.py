"""Independent score/interval/reliability/aggregate reconstruction from all PMFs."""
import json,math
from collections import defaultdict
import numpy as np
from .common import *
FIELDS=('Brier','LogLoss','CRPS','assigned_probability','top1_hit','top2_hit','top3_hit','entropy','effective_spread','top1_probability','within1_probability')
def details(p,actual):
    k=np.arange(-80,81);cdf=np.cumsum(p);rank=np.argsort(-p,kind='stable');i=int(actual)+80;mu=float(np.dot(k,p))
    v=scores(p,actual);out=dict(Brier=v['brier'],LogLoss=v['logloss'],CRPS=v['crps'],assigned_probability=v['actual_probability'],top1_hit=v['top1'],top2_hit=v['top2'],top3_hit=v['top3'],entropy=float(-np.dot(p,np.log(p))),effective_spread=float(np.sqrt(np.dot(p,(k-mu)**2))),top1_probability=float(p[rank[0]]),within1_probability=float(sum(p[abs(k-k[rank[0]])<=1])),top1_integer=int(k[rank[0]]))
    for level in (50,80,90):
        lo=int(k[min(160,np.searchsorted(cdf,(1-level/100)/2))]);hi=int(k[min(160,np.searchsorted(cdf,1-(1-level/100)/2))])
        out.update({f'interval{level}_lower':lo,f'interval{level}_upper':hi,f'interval{level}_width':hi-lo,f'interval{level}_hit':int(lo<=actual<=hi)})
    return out
def audit(s,p):
    c=s.get('phase9_probability_v1.db');computed={};rowbad=0
    for rid,mass in p.mass.items():
        r=p.pred[rid];d=details(mass['pmf'],r['actual']);rowbad+=sum(not close(v,r.get(k),1e-9) for k,v in d.items());computed[rid]=dict(r,**d)
    def load(table):return [json.loads(r['payload_json']) for r in c.execute('SELECT * FROM phase9_'+table)]
    by={(r['horizon'],r['origin'],r['target_business_date']):r for r in computed.values()}
    common={h:sorted(r['target_business_date'] for r in computed.values() if r['horizon']==h and r['origin']=='ENGINE' and r['probability_source_level']=='ML' and (h,'RAW_SELECTED',r['target_business_date']) in by and (h,'MOS_SELECTED',r['target_business_date']) in by) for h in ('T1','T2')}
    def subset(h,origin):return [by[h,origin,d] for d in common[h]]
    def aggregate(rows):return dict(N=len(rows),**{k:float(np.mean([r[k] for r in rows])) for k in FIELDS})
    agg=[];agg_bad=0
    for r in load('probability_metric'):
        rows=subset(r['horizon'],r['origin']) if r['subset']=='SAME_COMMON_DATES' else [x for x in computed.values() if x['horizon']==r['horizon'] and x['origin']==r['origin'] and x['method']==r['method']]
        values=aggregate(rows);bad=sum(not close(v,r.get(k),1e-9) for k,v in values.items())
        if r['subset']=='SAME_COMMON_DATES':bad+=int(r['common_dates']!=common[r['horizon']])
        agg_bad+=bad;agg.append(dict(record_id=r['record_id'],**values,mismatches=bad))
    slices=[]
    for r in load('slice_metric'):
        rows=subset(r['horizon'],r['origin']);kind=r['slice_type'];group=r['slice_value']
        chosen=[x for i,x in enumerate(rows) if (x['target_business_date'][:4] if kind=='year' else season(x['target_business_date']) if kind=='season' else 'early' if i<len(rows)//2 else 'late')==group]
        values=aggregate(chosen);bad=sum(not close(v,r.get(k),1e-9) for k,v in values.items());agg_bad+=bad;slices.append(dict(record_id=r['record_id'],**values,mismatches=bad))
    intervals=[];bins=[];binbad=intervalbad=0
    for r in load('interval_metric'):
        rows=subset(r['horizon'],r['origin']);level=int(round(r['nominal']*100));coverage=float(np.mean([x[f'interval{level}_hit'] for x in rows]));width=float(np.mean([x[f'interval{level}_width'] for x in rows]));bad=int(r['N']!=len(rows) or not close(coverage,r['actual_coverage']) or not close(width,r['average_width']));intervalbad+=bad;intervals.append(dict(record_id=r['record_id'],N=len(rows),nominal=r['nominal'],coverage=coverage,width=width,mismatches=bad))
    for r in load('calibration_bin'):
        rows=subset(r['horizon'],r['origin']);idx=int(round(r['bin_lower']*10));values=[];hits=[]
        for x in rows:
            mass=p.mass[x['record_id']]['pmf'];probabilities=mass if r['kind']=='ALL_CLASS' else [x['top1_probability']];events=(np.arange(-80,81)==x['actual']).astype(int) if r['kind']=='ALL_CLASS' else [x['top1_hit']]
            for probability,event in zip(probabilities,events):
                if min(int(probability*10),9)==idx:values.append(float(probability));hits.append(int(event))
        mean=float(np.mean(values)) if values else None;frequency=float(np.mean(hits)) if hits else None
        bad=int(len(values)!=r['N'] or not close(mean,r['mean_predicted']) or not close(frequency,r['actual_frequency']) or r['warning']!=('LOW_SAMPLE' if len(values)<30 else None));binbad+=bad;bins.append(dict(record_id=r['record_id'],N=len(values),mean_predicted=mean,actual_frequency=frequency,mismatches=bad))
    confidence=[];catastrophic=[];summaries=[]
    for h in ('T1','T2'):
        rows=subset(h,'ENGINE');summaries.append(dict(horizon=h,**aggregate(rows)))
        for threshold in (.5,.6,.7,.8):
            selected=[x for x in rows if x['top1_probability']>=threshold];n=len(selected)
            confidence.append(dict(horizon=h,threshold=threshold,N=n,mean_predicted=float(np.mean([x['top1_probability'] for x in selected])) if n else None,actual_frequency=float(np.mean([x['top1_hit'] for x in selected])) if n else None,status='LOW_SAMPLE' if n<30 else 'DESCRIPTIVE'))
        catastrophic.extend(dict(horizon=h,date=x['target_business_date'],assigned=x['assigned_probability'],forecast=x['continuous_prediction'],actual=x['actual']) for x in rows if x['assigned_probability']<.05)
    s.counts['PROBABILITY_SCORE_MISMATCH_COUNT']+=rowbad+agg_bad+binbad+intervalbad
    output('PHASE9_AGGREGATE_DIAGNOSTICS_V2.json',dict(status='PASS' if not(rowbad or agg_bad or binbad or intervalbad) else 'FAIL',all_saved_score_field_mismatches=rowbad,aggregate_mismatches=agg_bad,interval_mismatches=intervalbad,calibration_bin_mismatches=binbad,common_dates=common,engine_summary=summaries,all_method_metrics=agg,year_season_half=slices,intervals=intervals,calibration_bins=bins,confidence=confidence,catastrophic_lt5=catastrophic))
