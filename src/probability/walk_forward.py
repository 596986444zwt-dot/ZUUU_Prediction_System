"""Daily causal residual, calibration and method selection, all upstream immutable."""
from datetime import timedelta,date
import numpy as np
from .contracts import *
from .data import eligible
from .distribution import distribution,calibrate,score

def past_cases(cases,current):
 return [p for p in cases if p['target_business_date']<current['target_business_date'] and stamp(p['eligibility'])<=stamp(current['issue']) and stamp(p['issue'])<stamp(current['issue'])]

def run(source):
 data={t:[] for t in ('sample','residual_history','probability_prediction','probability_mass','distribution_state','calibration_registry','candidate_selection','fallback_audit')}
 for h,rows in source.items():
  histories={(origin,method):[] for origin in ('ML','RAW','MOS') for method in METHODS}
  for r in rows:
   data['sample'].append(dict(record_id=r['sample_id'],**r))
   for origin in ('ML','RAW','MOS'):
    data['residual_history'].append(dict(record_id=r['sample_id']+'/'+origin,sample_id=r['sample_id'],horizon=h,origin=origin,continuous_prediction=r[origin],actual=r['actual'],residual=None if r[origin] is None else r['actual']-r[origin],eligibility=r['eligibility'],target_business_date=r['target_business_date'],issue=r['issue'],status='OOS' if r[origin] is not None else 'RESIDUAL_HISTORY_UNAVAILABLE',phase8_prediction_id=r['phase8_prediction_id'] if origin=='ML' else None))
  for i,r in enumerate(rows):
   past=eligible(rows,r,True);current={};past_ids={x['sample_id'] for x in past}
   for origin in ('ML','RAW','MOS'):
    for base in BASE_METHODS:
     pool=[x for x in past if x[origin] is not None]
     if base=='GAUSSIAN_90CAL':
      end=stamp(r['issue']).astimezone(stamp(r['eligibility']).tzinfo).date()-timedelta(days=2);start=end-timedelta(days=89)
      pool=[x for x in pool if start<=date.fromisoformat(x['target_business_date'])<=end]
     rid=r['sample_id']+'/'+origin+'/'+base
     state=dict(record_id=rid,horizon=h,origin=origin,method=base,cutoff=r['issue'],history_ids=[x['sample_id'] for x in pool],history_n=len(pool),history_start=pool[0]['target_business_date'] if pool else None,history_end=pool[-1]['target_business_date'] if pool else None)
     data['distribution_state'].append(state)
     if r[origin] is None or len(pool)<MIN_RESIDUAL:
      p=None;info={};reason='CONTINUOUS_UNAVAILABLE' if r[origin] is None else 'INSUFFICIENT_RESIDUAL_HISTORY'
     else:p,info=distribution(r[origin],[x['actual']-x[origin] for x in pool],base);reason=None
     for method in (base,base+'_CAL'):
      alpha=1.;calids=[];cal_scores=[];prob=p;norm=info
      status='UNCALIBRATED' if prob is not None else 'NO_FORECAST';missing=reason
      if method.endswith('_CAL'):
       available=past_cases(histories[(origin,base)],r)
       if prob is None or len(available)<MIN_CALIBRATION:prob=None;status='NO_FORECAST';missing=reason or 'INSUFFICIENT_CALIBRATION_HISTORY'
       else:
        selected=available[-MIN_CALIBRATION:];calids=[x['record_id'] for x in selected]
        cal_scores=[float(np.mean([-np.log(calibrate(x['_p'],a)[0][x['actual']-SUPPORT[0]]) for x in selected])) for a in ALPHAS]
        alpha=ALPHAS[min(range(len(ALPHAS)),key=lambda j:(cal_scores[j],j))];prob,norm=calibrate(p,alpha);status='CALIBRATED'
       data['calibration_registry'].append(dict(record_id=r['sample_id']+'/'+origin+'/'+method,horizon=h,origin=origin,method=method,cutoff=r['issue'],calibration_ids=calids,calibration_n=len(calids),alpha=alpha,alpha_grid=list(ALPHAS),selection_scores=cal_scores,status=status))
      out=dict(record_id=r['sample_id']+'/'+origin+'/'+method,sample_id=r['sample_id'],horizon=h,target_business_date=r['target_business_date'],issue=r['issue'],eligibility=r['eligibility'],origin=origin,method=method,continuous_prediction=r[origin],continuous_model=MODELS[h] if origin=='ML' else origin,model_version='MODEL_'+h+'_V1' if origin=='ML' else ('PHASE5' if origin=='RAW' else 'PHASE6_M6'),phase8_prediction_id=r['phase8_prediction_id'] if origin=='ML' else None,distribution_state=rid,residual_n=len(pool),residual_history_start=state['history_start'],residual_history_end=state['history_end'],calibration_alpha=alpha,calibration_n=len(calids),calibration_cutoff=r['issue'] if calids else None,support_min=SUPPORT[0],support_max=SUPPORT[-1],floor=FLOOR,random_seed=SEED,status=status,missing_reason=missing,probability_source_level='ML' if origin=='ML' else origin,actual=r['actual'],season=r['season'],left_tail_mass=info.get('left_tail_mass'),right_tail_mass=info.get('right_tail_mass'),pre_normalization_mass=norm.get('pre_normalization_mass'),floor_l1_adjustment=norm.get('floor_l1_adjustment'),Brier=None,LogLoss=None,CRPS=None)
      if prob is not None:
       out.update(score(prob,r['actual']));data['probability_mass'].append(dict(record_id=out['record_id'],pmf=prob.astype('<f8').tobytes(),cdf=np.cumsum(prob).astype('<f8').tobytes(),survival=(1-np.r_[0,np.cumsum(prob)[:-1]]).astype('<f8').tobytes()))
       case=dict(out,_p=prob);histories[(origin,method)].append(case);current[(origin,method)]=case
      data['probability_prediction'].append(out)
   chosen={}
   for origin in ('ML','RAW','MOS'):
    options=[]
    for method in METHODS:
     if (origin,method) not in current:continue
     old=past_cases(histories[(origin,method)],r)[-MIN_SELECTION:]
     if len(old)==MIN_SELECTION:options.append((method,float(np.mean([x['LogLoss'] for x in old])),[x['record_id'] for x in old]))
    if options:
     best=min(x[1] for x in options);name=next(m for m in METHODS if any(x[0]==m and x[1]<=best+.01 for x in options))
    else:name='GAUSSIAN_EXPANDING'
    selected=current.get((origin,name));chosen[origin]=selected
    data['candidate_selection'].append(dict(record_id=r['sample_id']+'/'+origin,horizon=h,origin=origin,cutoff=r['issue'],options=[dict(method=m,LogLoss=s,validation_ids=ids) for m,s,ids in options],selected=name if selected else None,rule='PAST_ONLY_NLL_LAST60_TIE001; otherwise fixed Gaussian expanding'))
   for origin,label in (('RAW','RAW_SELECTED'),('MOS','MOS_SELECTED'),('ML','ENGINE')):
    pick=chosen[origin];level=origin;status=pick['status'] if pick else 'NO_FORECAST';fallback=None
    if origin=='ML' and pick is None:
     fullpast=eligible(rows,r,False)
     for fallback_origin in ('MOS','RAW'):
      pool=[x for x in fullpast if x[fallback_origin] is not None]
      if r[fallback_origin] is not None and len(pool)>=MIN_RESIDUAL:
       prob,info=distribution(r[fallback_origin],[x['actual']-x[fallback_origin] for x in pool],'GAUSSIAN_EXPANDING')
       fid=r['sample_id']+'/FALLBACK/'+fallback_origin
       data['distribution_state'].append(dict(record_id=fid,horizon=h,origin=fallback_origin,method='GAUSSIAN_EXPANDING',cutoff=r['issue'],history_ids=[x['sample_id'] for x in pool],history_n=len(pool),history_start=pool[0]['target_business_date'],history_end=pool[-1]['target_business_date']))
       pick=dict(record_id=fid,sample_id=r['sample_id'],horizon=h,target_business_date=r['target_business_date'],issue=r['issue'],eligibility=r['eligibility'],origin=fallback_origin,method='GAUSSIAN_EXPANDING',continuous_prediction=r[fallback_origin],continuous_model=fallback_origin,model_version='PHASE6_M6' if fallback_origin=='MOS' else 'PHASE5',distribution_state=fid,residual_n=len(pool),residual_history_start=pool[0]['target_business_date'],residual_history_end=pool[-1]['target_business_date'],calibration_alpha=1.,calibration_n=0,calibration_cutoff=None,support_min=SUPPORT[0],support_max=SUPPORT[-1],random_seed=SEED,actual=r['actual'],season=r['season'],**info,**score(prob,r['actual']),_p=prob,phase8_prediction_id=None)
       level=fallback_origin;fallback='ML unavailable or residual history insufficient';status='FALLBACK';break
    out={k:v for k,v in pick.items() if not k.startswith('_')} if pick else dict(sample_id=r['sample_id'],horizon=h,target_business_date=r['target_business_date'],issue=r['issue'],actual=r['actual'],season=r['season'],Brier=None,LogLoss=None,CRPS=None,method=None,residual_n=0)
    out.update(record_id=r['sample_id']+'/'+label,origin=label,status=status,probability_source_level=level,missing_reason=None if pick else 'NO_LEGAL_PROBABILITY_HISTORY',selected_probability_id=pick['record_id'] if pick and status!='FALLBACK' else None)
    data['probability_prediction'].append(out)
    if pick:data['probability_mass'].append(dict(record_id=out['record_id'],pmf=pick['_p'].astype('<f8').tobytes(),cdf=np.cumsum(pick['_p']).astype('<f8').tobytes(),survival=(1-np.r_[0,np.cumsum(pick['_p'])[:-1]]).astype('<f8').tobytes()))
    if label=='ENGINE':data['fallback_audit'].append(dict(record_id=out['record_id'],horizon=h,target_business_date=r['target_business_date'],issue=r['issue'],status=status,level=level,reason=fallback or out.get('missing_reason'),ml_available=r['ML'] is not None,residual_n=out['residual_n']))
   if i%100==0:print('Probability walk-forward',h,r['target_business_date'], 'ML residual history',len(past),flush=True)
 return data
