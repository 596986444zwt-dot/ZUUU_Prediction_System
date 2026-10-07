"""Scores and descriptive diagnostics; do not change frozen protocol or upstream model."""
import numpy as np
from collections import Counter,defaultdict
from .contracts import *

SCORE_FIELDS=('Brier','LogLoss','CRPS','assigned_probability','top1_hit','top2_hit','top3_hit','entropy','effective_spread','top1_probability','within1_probability')

def aggregate(rows):
 return dict(N=len(rows),**{k:float(np.mean([r[k] for r in rows])) for k in SCORE_FIELDS})

def evaluate(data):
 pp=data['probability_prediction'];mass={r['record_id']:np.frombuffer(r['pmf'],dtype='<f8') for r in data['probability_mass']}
 metrics=[];slices=[];bins=[];intervals=[];confidence=[];catastrophic=[];comparison=[];decision=[];bootstrap=[];extreme=[]
 for h in MODELS:
  by={(p['origin'],p['target_business_date']):p for p in pp if p['horizon']==h and p['Brier'] is not None}
  common=sorted(d for (o,d),p in by.items() if o=='ENGINE' and p['probability_source_level']=='ML' and ('RAW_SELECTED',d) in by and ('MOS_SELECTED',d) in by)
  groups=defaultdict(list)
  for p in pp:
   if p['horizon']!=h or p['Brier'] is None:continue
   groups[(p['origin'],p['method'])].append(p)
  for (o,m),rows in groups.items():metrics.append(dict(record_id=h+'/'+o+'/'+str(m),horizon=h,origin=o,method=m,subset='OWN_AVAILABLE; do not compare different subsets',**aggregate(rows)))
  commonmetrics={}
  for origin in ('ENGINE','RAW_SELECTED','MOS_SELECTED'):
   rows=[by[(origin,d)] for d in common];mm=dict(record_id=h+'/'+origin+'/COMMON',horizon=h,origin=origin,method='PAST_ONLY_SELECTED',subset='SAME_COMMON_DATES',common_dates=common,**aggregate(rows));metrics.append(mm);commonmetrics[origin]=mm
   for typ in ('year','season','half'):
    keys=sorted({r['target_business_date'][:4] if typ=='year' else r['season'] if typ=='season' else 'early' if j<len(rows)//2 else 'late' for j,r in enumerate(rows)})
    for key in keys:
     chosen=[r for j,r in enumerate(rows) if (r['target_business_date'][:4] if typ=='year' else r['season'] if typ=='season' else 'early' if j<len(rows)//2 else 'late')==key]
     slices.append(dict(record_id=h+'/'+origin+'/'+typ+'/'+key,horizon=h,origin=origin,slice_type=typ,slice_value=key,**aggregate(chosen)))
   for level in (50,80,90):
    intervals.append(dict(record_id=h+'/'+origin+'/'+str(level),horizon=h,origin=origin,N=len(rows),nominal=level/100,actual_coverage=float(np.mean([r[f'interval{level}_hit'] for r in rows])),average_width=float(np.mean([r[f'interval{level}_width'] for r in rows]))))
   # Exact-class reliability includes every class-event, with sparse-bin warnings.
   for kind in ('ALL_CLASS','TOP1'):
    sums=np.zeros((10,3))
    for r in rows:
     p=mass[r['record_id']]
     probabilities=p if kind=='ALL_CLASS' else np.array([r['top1_probability']])
     hits=(np.asarray(SUPPORT)==r['actual']).astype(int) if kind=='ALL_CLASS' else np.array([r['top1_hit']])
     for v,hit in zip(probabilities,hits):
      b=min(int(v*10),9);sums[b]+=np.array([1,v,hit])
    for b,(n,s,t) in enumerate(sums):bins.append(dict(record_id=h+'/'+origin+'/'+kind+'/'+str(b),horizon=h,origin=origin,kind=kind,bin_lower=b/10,bin_upper=(b+1)/10,N=int(n),mean_predicted=s/n if n else None,actual_frequency=t/n if n else None,warning='LOW_SAMPLE' if n<30 else None))
   for threshold in (.5,.6,.7,.8):
    selected=[r for r in rows if r['top1_probability']>=threshold];n=len(selected);pred=float(np.mean([r['top1_probability'] for r in selected])) if n else None;freq=float(np.mean([r['top1_hit'] for r in selected])) if n else None
    confidence.append(dict(horizon=h,origin=origin,threshold=threshold,N=n,mean_predicted=pred,actual_frequency=freq,status='LOW_SAMPLE' if n<30 else 'OVERCONFIDENT' if pred-freq>.10 else 'UNDERCONFIDENT' if freq-pred>.10 else 'NO_CLEAR_MISCONFIDENCE'))
   for r in rows:
    if r['assigned_probability']<.05:catastrophic.append(dict(horizon=h,origin=origin,date=r['target_business_date'],forecast=r['continuous_prediction'],actual=r['actual'],assigned_probability=r['assigned_probability'],below_1pct=r['assigned_probability']<.01,below_2pct=r['assigned_probability']<.02,top1=r['top1_integer'],top1_probability=r['top1_probability'],method=r['method']))
   for label,predicate in (('HIGH_GE35',lambda r:r['actual']>=35),('LOW_LE5',lambda r:r['actual']<=5)):
    selected=[r for r in rows if predicate(r)]
    extreme.append(dict(horizon=h,origin=origin,group=label,N=len(selected),mean_assigned=float(np.mean([r['assigned_probability'] for r in selected])) if selected else None,top1_hit=float(np.mean([r['top1_hit'] for r in selected])) if selected else None,max_integer_miss=max((abs(r['top1_integer']-r['actual']) for r in selected),default=None),status='INSUFFICIENT_EXTREME_SAMPLE' if len(selected)<30 else 'DESCRIPTIVE',dates=[r['target_business_date'] for r in selected]))
  comparison.append(dict(horizon=h,N_COMMON=len(common),**{origin+'_'+k:commonmetrics[origin][k] for origin in commonmetrics for k in ('Brier','LogLoss','CRPS','top1_hit','top2_hit','top3_hit')}))
  reasons=[];ml=commonmetrics['ENGINE']
  if len(common)<250:reasons.append('COMMON_N_LT250')
  for origin in ('RAW_SELECTED','MOS_SELECTED'):
   for k,tolerance in (('LogLoss',.02),('Brier',.01),('CRPS',.02)):
    if ml[k]>commonmetrics[origin][k]+tolerance:reasons.append(origin+'_'+k+'_GATE')
  for v in [v for v in intervals if v['horizon']==h and v['origin']=='ENGINE' and v['nominal'] in (.8,.9)]:
   if abs(v['actual_coverage']-v['nominal'])>.10:reasons.append('INTERVAL_COVERAGE_'+str(v['nominal']))
  for s in [s for s in slices if s['horizon']==h and s['origin']=='ENGINE' and s['slice_type'] in ('year','season') and s['N']>=30]:
   for b in [b for b in slices if b['horizon']==h and b['origin']!='ENGINE' and b['slice_type']==s['slice_type'] and b['slice_value']==s['slice_value']]:
    if s['LogLoss']>b['LogLoss']+.30:reasons.append('SLICE_NLL_'+s['slice_type']+'_'+s['slice_value']+'_'+b['origin'])
  if any(c['horizon']==h and c['origin']=='ENGINE' and c['status']=='OVERCONFIDENT' for c in confidence):reasons.append('RELIABLE_BIN_OVERCONFIDENCE')
  decision.append(dict(horizon=h,candidate='NONE' if reasons else 'PAST_ONLY_SELECTED_CDF_ODDS_V1',reasons=reasons,interpretation='descriptive forward-use protocol candidate; no post-hoc replacement of continuous candidate'))
  # Consecutive-day moving block bootstrap, separate at known archive gaps.
  blocks=[]
  for start in range(len(common)-13):
   dates=[date.fromisoformat(d) for d in common[start:start+14]]
   if all((dates[j+1]-dates[j]).days==1 for j in range(13)):blocks.append(list(range(start,start+14)))
  rng=np.random.default_rng(SEED)
  for origin in ('RAW_SELECTED','MOS_SELECTED'):
   dif=np.array([by[('ENGINE',d)]['LogLoss']-by[(origin,d)]['LogLoss'] for d in common]);means=[]
   for _ in range(500):
    ids=[]
    while len(ids)<len(common):ids.extend(blocks[int(rng.integers(len(blocks)))])
    means.append(float(dif[ids[:len(common)]].mean()))
   bootstrap.append(dict(horizon=h,comparison='ENGINE minus '+origin,mean_paired_NLL=float(dif.mean()),block_days=14,draws=500,seed=SEED,CI025=float(np.quantile(means,.025)),CI975=float(np.quantile(means,.975)),interpretation='diagnostic moving contiguous block bootstrap; negative favors ML; not selection'))
 data.update(probability_metric=metrics,slice_metric=slices,calibration_bin=bins,interval_metric=intervals)
 return dict(comparison=comparison,candidates=decision,confidence=confidence,catastrophic=catastrophic,extreme=extreme,bootstrap=bootstrap)

from datetime import date
