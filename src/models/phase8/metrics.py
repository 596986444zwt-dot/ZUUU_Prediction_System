"""Continuous same-date metrics; integer hits are diagnostic only."""
import math
import numpy as np
def compute(predicted,observed):
 p=np.array(predicted,dtype=float);y=np.array(observed,dtype=float);e=p-y;a=np.abs(e)
 if not len(e):return dict(N=0,Bias=None,MAE=None,RMSE=None,MedianAE=None,P90AE=None,MaxAE=None)
 rounded=np.floor(p+.5);ae=np.abs(rounded-y)
 return dict(N=len(e),Bias=float(e.mean()),MAE=float(a.mean()),RMSE=float(np.sqrt((e*e).mean())),MedianAE=float(np.median(a)),P90AE=float(np.quantile(a,.9)),MaxAE=float(a.max()),DiagnosticExact=float((ae==0).mean()),DiagnosticWithin1=float((ae<=1).mean()),DiagnosticWithin2=float((ae<=2).mean()))
def season(day):
 month=int(day[5:7]);return 'DJF' if month in (12,1,2) else 'MAM' if month in (3,4,5) else 'JJA' if month in (6,7,8) else 'SON'
def summarize(predictions):
 metrics=[];slices=[];comparison=[]
 for h in ('T1','T2'):
  for family in ('RIDGE','RANDOM_FOREST','LIGHTGBM','XGBOOST','CATBOOST'):
   pp=[x for x in predictions if x['horizon']==h and x['model_family']==family and x['ml_prediction'] is not None]
   for model,key in ((family,'ml_prediction'),('RAW','raw_ecmwf_prediction'),('M6','mos_prediction')):
    mm=compute([x[key] for x in pp],[x['actual_target'] for x in pp]);metrics.append(dict(horizon=h,comparison_model=family,model=model,**mm))
   mm,raw,mos=metrics[-3:]
   comparison.append(dict(MODEL=family,HORIZON=h,N=mm['N'],RAW_MAE_SAME_SUBSET=raw['MAE'],MOS_MAE_SAME_SUBSET=mos['MAE'],MODEL_MAE=mm['MAE'],DELTA_VS_RAW=raw['MAE']-mm['MAE'],DELTA_VS_MOS=mos['MAE']-mm['MAE'],RAW_RMSE=raw['RMSE'],MOS_RMSE=mos['RMSE'],MODEL_RMSE=mm['RMSE'],RAW_BIAS=raw['Bias'],MOS_BIAS=mos['Bias'],MODEL_BIAS=mm['Bias']))
   for kind in ('year','season','month'):
    groups={str(x['target_business_date'][:4]) if kind=='year' else season(x['target_business_date']) if kind=='season' else x['target_business_date'][5:7] for x in pp}
    for group in sorted(groups):
     ss=[x for x in pp if (x['target_business_date'][:4] if kind=='year' else season(x['target_business_date']) if kind=='season' else x['target_business_date'][5:7])==group]
     for model,key in ((family,'ml_prediction'),('RAW','raw_ecmwf_prediction'),('M6','mos_prediction')):slices.append(dict(horizon=h,comparison_model=family,model=model,slice_type=kind,slice_value=group,**compute([x[key] for x in ss],[x['actual_target'] for x in ss])))
 return metrics,slices,comparison
def candidates(comparison,slices):
 from .contracts import FAMILIES
 result={};details=[]
 for h in ('T1','T2'):
  qualified=[]
  for x in [c for c in comparison if c['HORIZON']==h]:
   f=x['MODEL'];reasons=[]
   if x['N']<365:reasons.append('COVERAGE_LT_365')
   if min(x['DELTA_VS_RAW'],x['DELTA_VS_MOS'])<.05:reasons.append('MAE_GAIN_LT_0.05_VS_BOTH')
   if x['MODEL_RMSE']>min(x['RAW_RMSE'],x['MOS_RMSE']):reasons.append('RMSE_NOT_BETTER_THAN_BOTH')
   if abs(x['MODEL_BIAS'])>abs(x['RAW_BIAS'])+.10:reasons.append('BIAS_GATE')
   ss=[s for s in slices if s['horizon']==h and s['comparison_model']==f and s['slice_type'] in ('year','season')]
   for s in [a for a in ss if a['model']==f and a['N']>=30]:
    bs=[a for a in ss if a['slice_type']==s['slice_type'] and a['slice_value']==s['slice_value'] and a['model'] in ('RAW','M6')]
    if any(s['MAE']-a['MAE']>.15 for a in bs):reasons.append('SLICE_DEGRADATION_'+s['slice_type']+'_'+s['slice_value'])
   if not reasons:qualified.append(x)
   details.append(dict(horizon=h,model=f,qualified=not reasons,reasons=reasons))
  if not qualified:result[h]='NONE'
  else:
   best=min(x['MODEL_MAE'] for x in qualified);close=[x for x in qualified if x['MODEL_MAE']<=best+.02];result[h]=min(close,key=lambda x:FAMILIES.index(x['MODEL']))['MODEL']
 return result,details
