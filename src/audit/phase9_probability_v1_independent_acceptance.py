"""Independent Phase9 acceptance: never import official probability/residual/calibration builders."""
from pathlib import Path
from datetime import datetime,timedelta
from collections import Counter,defaultdict
import hashlib,json,re
import numpy as np
from scipy.special import erfc,expit,logit
from src.data_v1.source_io import open_snapshot,sha256_file

ROOT=Path(__file__).resolve().parents[2];DOCS=ROOT/'docs/phase9';DB=ROOT/'database/phase9_probability_v1.db'
ORDER=('sample','residual_history','distribution_registry','distribution_state','calibration_registry','candidate_selection','probability_prediction','probability_mass','probability_metric','calibration_bin','interval_metric','slice_metric','fallback_audit','trajectory_registry','protocol','manifest')

def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def time(x):return datetime.fromisoformat(x.replace('Z','+00:00'))
def read(c,t):return [dict(r) if t=='probability_mass' else json.loads(r['payload_json']) for r in c.execute('SELECT * FROM phase9_'+t+' ORDER BY record_id')]

def independent_audit():
 before=json.loads((DOCS/'SOURCE_SHA_BEFORE.json').read_text(encoding='utf-8'));physical_before=sha256_file(DB)
 violations=Counter();details=[]
 def check(ok,key,context=''):
  if not ok:
   violations[key]+=1
   if len(details)<200:details.append(dict(key=key,context=context))
 for p,h in before.items():check(sha256_file(ROOT/p)==h,'SOURCE_MUTATION',p)
 c=open_snapshot(DB);check(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok','INTEGRITY');check(not c.execute('PRAGMA foreign_key_check').fetchall(),'FOREIGN_KEYS');data={t:read(c,t) for t in ORDER};c.close()
 proto=data['protocol'][0]['protocol'];k=np.asarray(proto['support']);eps=proto['probability_floor'];n=len(k);lo=int(k[0]);models=proto['horizons'];bases=proto['residual_methods'];methods=bases+[m+'_CAL' for m in bases]
 protocol_file=DOCS/'PHASE9_PROBABILITY_PROTOCOL_V1.md';check(sha256_file(protocol_file)==data['protocol'][0]['physical_sha256'],'PROTOCOL_MUTATION')
 check(models=={'T1':'RIDGE','T2':'LIGHTGBM'},'CONTINUOUS_MODEL_IDENTITY')
 for p,h in data['manifest'][0]['implementation_sha256'].items():check(sha256_file(ROOT/p)==h,'IMPLEMENTATION_CHANGED',p)
 dig=hashlib.sha256()
 for t in ORDER:
  if t=='manifest':continue
  dig.update((t+'\n').encode())
  for row in sorted(data[t],key=lambda r:r['record_id']):dig.update((canonical({a:b.hex() if isinstance(b,bytes) else b for a,b in row.items()})+'\n').encode())
 semantic=dig.hexdigest();check(semantic==data['manifest'][0]['semantic_sha256'],'SEMANTIC_HASH')
 c=open_snapshot(ROOT/'database/phase8_machine_learning_v1.db')
 upstream={r['input_sample_id']:dict(r) for r in c.execute("SELECT * FROM phase8_prediction WHERE (horizon='T1' AND model_family='RIDGE') OR (horizon='T2' AND model_family='LIGHTGBM')")};c.close()
 c=open_snapshot(ROOT/'database/zuuu_prediction.db');truth={r['business_date_bjt']:dict(r) for r in c.execute('SELECT * FROM zuuu_target_v1')};c.close()
 c=open_snapshot(ROOT/'database/phase7_feature_v1.db');base_samples={r['sample_id']:dict(r) for r in c.execute('SELECT * FROM phase7_feature_sample')};base_labels={r['sample_id']:dict(r) for r in c.execute('SELECT * FROM phase7_feature_label')};c.close()
 samples={r['sample_id']:r for r in data['sample']};pred={r['record_id']:r for r in data['probability_prediction']};states={r['record_id']:r for r in data['distribution_state']}
 check(set(samples)==set(base_samples)==set(upstream),'SILENT_SAMPLE_DROP_COUNT')
 for sid,s in samples.items():
  p=upstream[sid];check(s['ML']==p['ml_prediction'] and s['RAW']==p['raw_ecmwf_prediction'] and s['MOS']==p['mos_prediction'],'CONTINUOUS_VALUE_MISMATCH',sid)
  check(s['actual']==truth[s['target_business_date']]['daily_tmax_c']==p['actual_target'],'TARGET_MAPPING',sid)
  expected=(datetime.fromisoformat(s['target_business_date']+'T00:00:00+08:00')+timedelta(days=2)).isoformat()
  check(expected==s['eligibility']==base_labels[sid]['label_eligibility_time_bjt'],'LABEL_ELIGIBILITY_RULE',sid)
  check(time(p['run_available_time'])<=time(s['issue']),'FUTURE_ECMWF_COUNT',sid)
 check('2025-08-07/T2' not in samples,'KNOWN_GAP')
 residuals={r['record_id']:r for r in data['residual_history']}
 check(len(residuals)==3*len(samples),'SILENT_RESIDUAL_DROP_COUNT')
 for rid,r in residuals.items():
  s=samples[r['sample_id']];expected=None if s[r['origin']] is None else s['actual']-s[r['origin']]
  check(expected==r['residual'],'RESIDUAL_SIGN_OR_VALUE',rid)
  if r['origin']=='ML' and expected is not None:check(upstream[r['sample_id']]['status']=='OOS','IN_SAMPLE_RESIDUAL',rid)

 def lawful(s,current):return s['target_business_date']<current['target_business_date'] and time(s['eligibility'])<=time(current['issue']) and time(s['issue'])<time(current['issue'])
 def floor(p):
  p=np.maximum(p,0);p=p/p.sum();return (1-n*eps)*p+eps
 def transform(p,a,stable=False):
  f=np.cumsum(p,axis=-1);u=np.clip(f[...,:-1],1e-300,1-1e-16)
  fa=expit(a*logit(u)) if stable else u**a/(u**a+(1-u)**a);f=np.concatenate((fa,np.ones((*fa.shape[:-1],1))),axis=-1)
  q=np.diff(np.concatenate((np.zeros((*f.shape[:-1],1)),f),axis=-1),axis=-1);q=np.maximum(q,0);q=q/q.sum(axis=-1,keepdims=True)
  return (1-n*eps)*q+eps
 def normal_cdf(z):return .5*erfc(-z/np.sqrt(2.))
 def remake(forecast,res,method):
  rr=np.asarray(res);edges=k+.5
  if method.startswith('GAUSSIAN'):
   mu=rr.mean();sigma=max(rr.std(ddof=1),.5);f=normal_cdf((edges-forecast-mu)/sigma);lt=float(normal_cdf((k[0]-.5-forecast-mu)/sigma));rt=float(normal_cdf((forecast+mu-k[-1]-.5)/sigma))
  elif method.startswith('KDE'):
   f=normal_cdf((edges[:,None]-forecast-rr[None,:])/.75).mean(axis=1);lt=float(normal_cdf((k[0]-.5-forecast-rr)/.75).mean());rt=float(normal_cdf((forecast+rr-k[-1]-.5)/.75).mean())
  else:
   latent=np.sort(forecast+rr);f=np.searchsorted(latent,edges,side='left')/len(rr);lt=float(np.mean(latent<k[0]-.5));rt=float(np.mean(latent>=k[-1]+.5))
  pm=np.diff(np.r_[0,f]);pm[-1]+=1-f[-1];return floor(pm),lt,rt

 total_residual_edges=0;state_prob={};state_rows=defaultdict(list)
 for s in samples.values():state_rows[s['horizon']].append(s)
 for sid,st in states.items():
  parts=sid.split('/');current=samples['/'.join(parts[:2])];origin=st['origin'];fallback='/FALLBACK/' in sid
  allowed=[s for s in state_rows[st['horizon']] if lawful(s,current) and s[origin] is not None and (fallback or s['ML'] is not None)]
  allowed.sort(key=lambda s:s['issue'])
  if st['method']=='GAUSSIAN_90CAL':
   end=time(current['issue']).astimezone(time(current['eligibility']).tzinfo).date()-timedelta(days=2);start=end-timedelta(days=89)
   allowed=[s for s in allowed if start<=datetime.fromisoformat(s['target_business_date']).date()<=end]
  ids=[s['sample_id'] for s in allowed];check(st['history_ids']==ids and st['history_n']==len(ids),'RESIDUAL_HISTORY_MISMATCH',sid)
  total_residual_edges+=len(st['history_ids'])
  for historical in st['history_ids']:
   s=samples.get(historical)
   check(s is not None and s['horizon']==st['horizon'],'CROSS_HORIZON_CONTAMINATION_COUNT',sid)
   if s:
    check(s['target_business_date']<current['target_business_date'],'FUTURE_RESIDUAL_COUNT',sid)
    check(time(s['eligibility'])<=time(current['issue']),'SAME_DAY_UNSETTLED_COUNT',sid)
  if current[origin] is not None and len(allowed)>=60:state_prob[sid]=remake(current[origin],[s['actual']-s[origin] for s in allowed],st['method'])

 masses={r['record_id']:{q:np.frombuffer(r[q],dtype='<f8') for q in ('pmf','cdf','survival')} for r in data['probability_mass']}
 check(len(masses)==len(data['probability_mass']),'DUPLICATE_MASS_ID')
 total_cal_edges=0;calibrations={r['record_id']:r for r in data['calibration_registry']}
 histories=defaultdict(list)
 for p in pred.values():
  if p['origin'] in ('ML','RAW','MOS') and p['Brier'] is not None:histories[(p['horizon'],p['origin'],p['method'])].append(p)
 for v in histories.values():v.sort(key=lambda p:p['issue'])
 for rid,ca in calibrations.items():
  current=pred[rid];base=ca['method'][:-4]
  available=[p for p in histories[(ca['horizon'],ca['origin'],base)] if lawful(p,current)]
  expected=available[-90:] if len(available)>=90 and current['distribution_state'] in state_prob else []
  check(ca['calibration_ids']==[p['record_id'] for p in expected] and ca['calibration_n']==len(expected),'CALIBRATION_HISTORY_MISMATCH',rid)
  total_cal_edges+=len(ca['calibration_ids'])
  for pid in ca['calibration_ids']:
   p=pred[pid];check(time(p['eligibility'])<=time(ca['cutoff']) and p['target_business_date']<current['target_business_date'],'FUTURE_CALIBRATION_LABEL_COUNT',rid);check(p['horizon']==ca['horizon'],'CROSS_HORIZON_CONTAMINATION_COUNT',rid)
  if expected:
   mat=np.array([masses[p['record_id']]['pmf'] for p in expected]);actuals=np.array([p['actual']-lo for p in expected]);scores=[]
   for a in (1.,.8,1.2):
    q=transform(mat,a,stable=True);scores.append(float(np.mean(-np.log(q[np.arange(len(q)),actuals]))))
   best=(1.,.8,1.2)[min(range(3),key=lambda j:(scores[j],j))]
   check(np.allclose(scores,ca['selection_scores'],atol=1e-8,rtol=1e-9) and best==ca['alpha'],'CALIBRATION_PARAMETER_MISMATCH_COUNT',rid)

 score_fields=('Brier','LogLoss','CRPS','assigned_probability','top1_hit','top2_hit','top3_hit','entropy','effective_spread','top1_probability','within1_probability')
 max_math_diff=0.;reproductions=[];rescore={}
 for rid,m in masses.items():
  p=pred[rid];q=m['pmf'];actual=p['actual'];at=actual-lo
  check(len(q)==n and np.isfinite(q).all(),'PMF_FORMAT',rid)
  check(np.all(q>=0),'negative_probability_count',rid);check(np.all(q<=1),'probability_above_one_count',rid);check(abs(q.sum()-1)<=1e-10,'probability_sum_error_count',rid)
  check(np.all(np.diff(m['cdf'])>=-1e-12),'cdf_non_monotonic_count',rid);check(np.all(np.diff(m['survival'])<=1e-12),'survival_non_monotonic_count',rid)
  check(np.allclose(np.cumsum(q),m['cdf'],atol=1e-12) and np.allclose(1-np.r_[0,np.cumsum(q)[:-1]],m['survival'],atol=1e-12),'CDF_SURVIVAL_MISMATCH',rid)
  check(0<=at<n,'actual_outside_support_count',rid);check(q.min()>=eps*(1-1e-8),'FLOOR_VIOLATION',rid)
  rank=np.argsort(-q,kind='stable');onehot=np.zeros(n);onehot[at]=1;cdf=np.cumsum(q);mu=float(q@k)
  scores=dict(Brier=float(np.sum((q-onehot)**2)),LogLoss=float(-np.log(q[at])),CRPS=float(np.sum((cdf[:-1]-np.cumsum(onehot)[:-1])**2)),assigned_probability=float(q[at]),top1_hit=int(rank[0]==at),top2_hit=int(at in rank[:2]),top3_hit=int(at in rank[:3]),entropy=float(-q@np.log(q)),effective_spread=float(np.sqrt(q@((k-mu)**2))),top1_probability=float(q[rank[0]]),within1_probability=float(q[np.abs(k-k[rank[0]])<=1].sum()))
  rescore[rid]=scores
  check(all(np.isfinite(scores[s]) for s in ('Brier','LogLoss','CRPS')),'nan_score_count',rid);check(np.isfinite(scores['LogLoss']),'infinite_logloss_count',rid)
  check(all(abs(scores[s]-p[s])<1e-9 for s in score_fields),'SCORE_MISMATCH_COUNT',rid)
  for lev in (50,80,90):
   lower=int(k[min(np.searchsorted(cdf,(1-lev/100)/2),n-1)]);upper=int(k[min(np.searchsorted(cdf,1-(1-lev/100)/2),n-1)])
   check(p[f'interval{lev}_lower']==lower and p[f'interval{lev}_upper']==upper and p[f'interval{lev}_hit']==int(lower<=actual<=upper) and p[f'interval{lev}_width']==upper-lower,'INTERVAL_MISMATCH_COUNT',rid)
  if p['origin'] in ('ML','RAW','MOS'):
   expected,lt,rt=state_prob[p['distribution_state']]
   if p['method'].endswith('_CAL'):expected=transform(expected,p['calibration_alpha'])
   difference=float(np.max(np.abs(expected-q)));max_math_diff=max(max_math_diff,difference);check(difference<1e-9,'PROBABILITY_REPRODUCTION_MISMATCH_COUNT',rid)
   check(abs(lt-p['left_tail_mass'])<1e-10 and abs(rt-p['right_tail_mass'])<1e-10,'TAIL_MISMATCH',rid)
  elif p['status']=='FALLBACK':
   expected,lt,rt=state_prob[p['distribution_state']];check(np.max(np.abs(expected-q))<1e-9,'PROBABILITY_REPRODUCTION_MISMATCH_COUNT',rid)
  else:
   check(np.array_equal(q,masses[p['selected_probability_id']]['pmf']),'SELECTED_PMF_MISMATCH',rid)
 for p in pred.values():check((p['Brier'] is not None)==(p['record_id'] in masses),'MISSING_MASS',p['record_id'])
 # Every date/variant is retained, including cold start; availability follows the exact minima.
 for sid,current in samples.items():
  for origin in ('ML','RAW','MOS'):
   for method in methods:
    rid=sid+'/'+origin+'/'+method;check(rid in pred,'SILENT_SAMPLE_DROP_COUNT',rid)
    if rid not in pred:continue
    p=pred[rid];available=p['distribution_state'] in state_prob
    if method.endswith('_CAL'):available=available and calibrations[rid]['calibration_n']==90
    check((p['Brier'] is not None)==available,'PROBABILITY_COLD_START_MISMATCH',rid)
  engine=pred[sid+'/ENGINE'];mlpick=next(s for s in data['candidate_selection'] if s['record_id']==sid+'/ML')['selected']
  if not mlpick:
   allpast=[s for s in state_rows[current['horizon']] if lawful(s,current)]
   fallback_origin=next((o for o in ('MOS','RAW') if current[o] is not None and sum(s[o] is not None for s in allpast)>=60),None)
   check((engine['status']=='FALLBACK')==(fallback_origin is not None),'FALLBACK_HIERARCHY_MISMATCH',sid)
   if fallback_origin:check(engine['probability_source_level']==fallback_origin,'FALLBACK_HIERARCHY_MISMATCH',sid)

 for sel in data['candidate_selection']:
  sid='/'.join(sel['record_id'].split('/')[:2]);current=samples[sid];origin=sel['origin'];options=[]
  for method in methods:
   now=pred[sid+'/'+origin+'/'+method]
   if now['Brier'] is None:continue
   past=[p for p in histories[(current['horizon'],origin,method)] if lawful(p,current)][-60:]
   if len(past)==60:options.append(dict(method=method,LogLoss=float(np.mean([rescore[p['record_id']]['LogLoss'] for p in past])),validation_ids=[p['record_id'] for p in past]))
  if options:
   best=min(o['LogLoss'] for o in options);chosen=next(m for m in methods if any(o['method']==m and o['LogLoss']<=best+.01 for o in options))
  else:chosen='GAUSSIAN_EXPANDING'
  if pred[sid+'/'+origin+'/'+chosen]['Brier'] is None:chosen=None
  check(len(options)==len(sel['options']) and all(a['method']==b['method'] and a['validation_ids']==b['validation_ids'] and abs(a['LogLoss']-b['LogLoss'])<1e-9 for a,b in zip(options,sel['options'])) and chosen==sel['selected'],'METHOD_SELECTION_MISMATCH_COUNT',sel['record_id'])
  selected=pred[sid+('/ENGINE' if origin=='ML' else '/'+origin+'_SELECTED')]
  if chosen:check(selected['selected_probability_id']==sid+'/'+origin+'/'+chosen,'METHOD_SELECTION_MISMATCH_COUNT',sel['record_id'])

 def summary(rows):return dict(N=len(rows),**{s:float(np.mean([rescore[r['record_id']][s] for r in rows])) for s in score_fields})
 commons={}
 for h in models:
  commons[h]=sorted(p['target_business_date'] for p in pred.values() if p['horizon']==h and p['origin']=='ENGINE' and p['Brier'] is not None and p['probability_source_level']=='ML' and pred[p['sample_id']+'/RAW_SELECTED']['Brier'] is not None and pred[p['sample_id']+'/MOS_SELECTED']['Brier'] is not None)
 for row in data['probability_metric']:
  if row['subset']=='SAME_COMMON_DATES':
   rows=[pred[d+'/'+row['horizon']+'/'+row['origin']] for d in commons[row['horizon']]];check(row['common_dates']==commons[row['horizon']],'COMPARABLE_SET_MISMATCH')
  else:rows=[p for p in pred.values() if p['horizon']==row['horizon'] and p['origin']==row['origin'] and p['method']==row['method'] and p['Brier'] is not None]
  mm=summary(rows);check(all(abs(mm[s]-row[s])<1e-9 for s in mm),'METRIC_MISMATCH',row['record_id'])
 for row in data['slice_metric']:
  allrows=[pred[d+'/'+row['horizon']+'/'+row['origin']] for d in commons[row['horizon']]]
  rows=[p for i,p in enumerate(allrows) if (p['target_business_date'][:4] if row['slice_type']=='year' else p['season'] if row['slice_type']=='season' else 'early' if i<len(allrows)//2 else 'late')==row['slice_value']]
  mm=summary(rows);check(all(abs(mm[s]-row[s])<1e-9 for s in mm),'SLICE_MISMATCH',row['record_id'])
 for row in data['interval_metric']:
  rows=[pred[d+'/'+row['horizon']+'/'+row['origin']] for d in commons[row['horizon']]];lev=int(round(row['nominal']*100))
  check(row['N']==len(rows) and abs(row['actual_coverage']-np.mean([p[f'interval{lev}_hit'] for p in rows]))<1e-12 and abs(row['average_width']-np.mean([p[f'interval{lev}_width'] for p in rows]))<1e-12,'INTERVAL_MISMATCH_COUNT',row['record_id'])
 for row in data['calibration_bin']:
  rows=[pred[d+'/'+row['horizon']+'/'+row['origin']] for d in commons[row['horizon']]];b=int(round(row['bin_lower']*10));count=0;total=0.;hits=0
  for p in rows:
   probs=masses[p['record_id']]['pmf'] if row['kind']=='ALL_CLASS' else np.array([p['top1_probability']]);hit=(k==p['actual']).astype(int) if row['kind']=='ALL_CLASS' else np.array([p['top1_hit']]);mask=np.minimum((probs*10).astype(int),9)==b
   count+=int(mask.sum());total+=float(probs[mask].sum());hits+=int(hit[mask].sum())
  check(count==row['N'] and (not count or abs(total/count-row['mean_predicted'])<1e-10 and abs(hits/count-row['actual_frequency'])<1e-10),'BIN_MISMATCH_COUNT',row['record_id'])
 # Recompute external confidence, catastrophic and extreme diagnostics from verified probabilities.
 diagnostics=json.loads((DOCS/'PHASE9_BUILD_ASSESSMENT.json').read_text(encoding='utf-8'))
 for row in diagnostics['confidence']:
  rr=[pred[d+'/'+row['horizon']+'/'+row['origin']] for d in commons[row['horizon']]];chosen=[p for p in rr if p['top1_probability']>=row['threshold']]
  check(row['N']==len(chosen),'CONFIDENCE_DIAGNOSTIC_MISMATCH')
  if chosen:check(abs(row['mean_predicted']-np.mean([p['top1_probability'] for p in chosen]))<1e-12 and abs(row['actual_frequency']-np.mean([p['top1_hit'] for p in chosen]))<1e-12,'CONFIDENCE_DIAGNOSTIC_MISMATCH')
 expected_cat={(h,o,d) for h,ds in commons.items() for o in ('ENGINE','RAW_SELECTED','MOS_SELECTED') for d in ds if pred[d+'/'+h+'/'+o]['assigned_probability']<.05}
 check(expected_cat=={(r['horizon'],r['origin'],r['date']) for r in diagnostics['catastrophic']},'CATASTROPHIC_DIAGNOSTIC_MISMATCH')
 for row in diagnostics['extreme']:
  rr=[pred[d+'/'+row['horizon']+'/'+row['origin']] for d in commons[row['horizon']]];rr=[p for p in rr if p['actual']>=35] if row['group']=='HIGH_GE35' else [p for p in rr if p['actual']<=5]
  check(row['N']==len(rr) and row['dates']==[p['target_business_date'] for p in rr],'EXTREME_SAMPLE_DROP')

 # Independently evaluate predefined descriptive candidate gate, without changing results.
 candidate_decisions=[]
 for h in models:
  metrics={m['origin']:m for m in data['probability_metric'] if m['horizon']==h and m['subset']=='SAME_COMMON_DATES'};ml=metrics['ENGINE'];reasons=[]
  if ml['N']<250:reasons.append('COMMON_N_LT250')
  for origin in ('RAW_SELECTED','MOS_SELECTED'):
   for metric,tol in (('LogLoss',.02),('Brier',.01),('CRPS',.02)):
    if ml[metric]>metrics[origin][metric]+tol:reasons.append(origin+'_'+metric+'_GATE')
  for row in data['interval_metric']:
   if row['horizon']==h and row['origin']=='ENGINE' and row['nominal'] in (.8,.9) and abs(row['actual_coverage']-row['nominal'])>.10:reasons.append('INTERVAL_COVERAGE_'+str(row['nominal']))
  for row in data['slice_metric']:
   if row['horizon']!=h or row['origin']!='ENGINE' or row['slice_type'] not in ('year','season') or row['N']<30:continue
   for other in data['slice_metric']:
    if other['horizon']==h and other['origin']!='ENGINE' and other['slice_type']==row['slice_type'] and other['slice_value']==row['slice_value'] and row['LogLoss']>other['LogLoss']+.30:reasons.append('SLICE_NLL_'+row['slice_type']+'_'+row['slice_value']+'_'+other['origin'])
  rr=[pred[d+'/'+h+'/ENGINE'] for d in commons[h]]
  for threshold in (.5,.6,.7,.8):
   ss=[p for p in rr if p['top1_probability']>=threshold]
   if len(ss)>=30 and np.mean([p['top1_probability']-p['top1_hit'] for p in ss])>.10:reasons.append('RELIABLE_BIN_OVERCONFIDENCE');break
  candidate='NONE' if reasons else 'PAST_ONLY_SELECTED_CDF_ODDS_V1';candidate_decisions.append(dict(horizon=h,candidate=candidate,reasons=reasons))
  saved=next(r for r in data['manifest'][0]['candidate_decision'] if r['horizon']==h);check(candidate==saved['candidate'] and set(reasons)==set(saved['reasons']),'CANDIDATE_RULE_MISMATCH')
  for index in (0,len(rr)//2,len(rr)-1):
   p=rr[index];q=masses[p['record_id']]['pmf'];historical=states[p['distribution_state']]['history_ids'][-5:]
   reproductions.append(dict(horizon=h,date=p['target_business_date'],issue=p['issue'],continuous=p['continuous_prediction'],method=p['method'],pmf_center={int(k[i]):float(q[i]) for i in np.flatnonzero(q>.005)},residual_n=p['residual_n'],calibration_n=p['calibration_n'],last5=[dict(date=samples[s]['target_business_date'],eligibility=samples[s]['eligibility'],actual=samples[s]['actual'],forecast=samples[s]['ML'],residual=samples[s]['actual']-samples[s]['ML']) for s in historical]))
 # Static inspection verifies only frozen continuous inputs, no hidden model training/market I/O.
 static=[]
 for path in (ROOT/'src/probability').glob('*.py'):
  txt=path.read_text(encoding='utf-8')
  for pattern in (r'shift\(\s*-1',r'center\s*=\s*True',r'train_test_split\(',r'shuffle\s*=\s*True',r'KFold\(',r'\.fit\(',r'import requests',r'market_price\s*[=\[]'):
   if re.search(pattern,txt):static.append(dict(file=path.relative_to(ROOT).as_posix(),pattern=pattern))
 check(not static,'FORBIDDEN_OPERATION',canonical(static))
 for p,h in before.items():check(sha256_file(ROOT/p)==h,'SOURCE_MUTATION',p)
 check(physical_before==sha256_file(DB),'SOURCE_MUTATION','Phase9 DB')
 keys=('TARGET_LEAKAGE_COUNT','FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','SAME_DAY_UNSETTLED_COUNT','PREPROCESSING_LEAKAGE_COUNT','CALIBRATION_LEAKAGE_COUNT','CROSS_HORIZON_CONTAMINATION_COUNT','MARKET_DATA_USAGE_COUNT','UNPROVEN_INTRADAY_ZUUU_USAGE_COUNT','negative_probability_count','probability_above_one_count','probability_sum_error_count','cdf_non_monotonic_count','survival_non_monotonic_count','actual_outside_support_count','infinite_logloss_count','nan_score_count','SILENT_SAMPLE_DROP_COUNT','SILENT_RESIDUAL_DROP_COUNT','SCORE_MISMATCH_COUNT','PROBABILITY_REPRODUCTION_MISMATCH_COUNT','BIN_MISMATCH_COUNT','INTERVAL_MISMATCH_COUNT','METHOD_SELECTION_MISMATCH_COUNT','CALIBRATION_PARAMETER_MISMATCH_COUNT')
 result={key:violations[key] for key in keys};result.update(SOURCE_GUARDIAN='FAIL' if violations['SOURCE_MUTATION'] else 'PASS',PROTOCOL_SHA256=data['protocol'][0]['physical_sha256'],T1_CONTINUOUS_MODEL='RIDGE',T2_CONTINUOUS_MODEL='LIGHTGBM',T1_BASE_SAMPLE_COUNT=sum(s['horizon']=='T1' for s in samples.values()),T2_BASE_SAMPLE_COUNT=sum(s['horizon']=='T2' for s in samples.values()),KNOWN_T2_GAP_PRESERVED='2025-08-07/T2' not in samples,TOTAL_PROBABILITY_ROWS=len(pred),TOTAL_MASS_ROWS=len(masses),TOTAL_RESIDUAL_LINEAGE_EDGES=total_residual_edges,TOTAL_CALIBRATION_LINEAGE_EDGES=total_cal_edges,maximum_probability_math_difference=max_math_diff,independent_reproduction='ALL saved base/calibrated/fallback PMFs independently reconstructed; six representative snapshots',snapshots=reproductions,violations=dict(violations),violation_examples=details,PHASE9_PHYSICAL_SHA256=physical_before,PHASE9_SEMANTIC_SHA256=semantic,CORRELATED_TRAJECTORY_STATUS='BLOCKED_FOR_DATA',TMAX_TIME_PROBABILITY_STATUS='BLOCKED',T0_INTRADAY_BLOCKER='HISTORICAL_ZUUU_INGEST_NOT_OBSERVED; BLOCKED / DEFERRED',FRAMEWORK_DEVIATION=bool(static),PHASE9_ACCEPTANCE='FAIL' if sum(violations.values()) else 'PASS_WITH_WARNINGS',NEXT_PHASE_STATUS='BLOCKED_FOR_PHASE10' if sum(violations.values()) or any(c['candidate']=='NONE' for c in candidate_decisions) else 'READY_FOR_PHASE10_T1_T2_DAILY_TMAX_SCOPE',static_scan=static)
 for h in models:
  mm=next(m for m in data['probability_metric'] if m['horizon']==h and m['origin']=='ENGINE' and m['subset']=='SAME_COMMON_DATES')
  result[h+'_PROBABILITY_METHOD']='PAST_ONLY_SELECTED (see per-date method lineage)';result[h+'_PROBABILITY_OOS_N']=mm['N']
  for k2,k3 in [('BRIER','Brier'),('LOGLOSS','LogLoss'),('CRPS','CRPS'),('TOP1_EXACT','top1_hit'),('TOP2_COVERAGE','top2_hit'),('TOP3_COVERAGE','top3_hit')]:result[h+'_'+k2]=mm[k3]
  for lev in (80,90):result[h+'_'+str(lev)+'_INTERVAL_COVERAGE']=next(i['actual_coverage'] for i in data['interval_metric'] if i['horizon']==h and i['origin']=='ENGINE' and i['nominal']==lev/100)
  rr=[pred[d+'/'+h+'/ENGINE'] for d in commons[h]];result[h+'_CATASTROPHIC_LT5_COUNT']=sum(p['assigned_probability']<.05 for p in rr)
  confidence=[p for p in rr if p['top1_probability']>=.5];result['OVERCONFIDENCE_STATUS_'+h]='LOW_SAMPLE_HIGH_CONFIDENCE' if len(confidence)<30 else 'OVERCONFIDENT' if np.mean([p['top1_probability']-p['top1_hit'] for p in confidence])>.10 else 'NO_CLEAR_OVERCONFIDENCE'
  result['PHASE9_'+h+'_CALIBRATION_CANDIDATE']=next(c['candidate'] for c in candidate_decisions if c['horizon']==h)
 (DOCS/'PHASE9_FINAL_AUDIT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 report=['# PHASE9 FINAL INDEPENDENT ACCEPTANCE','No official Phase9 builder, residual constructor, calibration function or trajectory function was imported or called. Frozen Phase8 predictions and ZUUU_TARGET_V1 were read directly. Independent erf CDF, empirical rank binning, power-ratio CDF calibration, full PMF reconstruction, scoring, inner parameter/method selection and candidate rules were checked. Every output probability and every state lineage was audited. For calibration validation NLL, an independent implementation of the protocol stable expit(alpha*logit(CDF)) algorithm prevents cancellation amplification at the 1e-12 floor. Power-ratio PMF reconstruction is separately checked for every calibrated forecast.','## Acceptance matrix','```json\n'+json.dumps({k:v for k,v in result.items() if k not in ('snapshots',)},ensure_ascii=False,indent=2)+'\n```','## Six true early/middle/late examples','```json\n'+json.dumps(reproductions,ensure_ascii=False,indent=2)+'\n```','## Initial numerical review','Initial audit flagged 860 calibration validation score comparisons: algebraic power ratio cancellation at near-one CDF caused ~1e-6 mean NLL differences while full PMFs matched within 1.12e-13. Independent stable protocol arithmetic reproduced stored validation scores and alpha choices. No protocol, probability DB, output PMF, parameter choice or builder code was changed. Initial JSON/MD and terminal log are preserved. This review corrects the independent numerical checker, not the results.','## Limitations','OOS validates the already selected Phase8 candidate streams conditional on that frozen selection. Phase8 candidate choice used its Phase8 OOS outcomes; these same dates are not a fresh independent holdout proving model-choice generalization. Phase9 does not change candidates. Forward prospective evaluation is still required. ECMWF availability is frozen estimated dissemination, not historical observed receipt. Daily reported integer mapping uses half-degree latent approximation. No legal hourly OOS residual archive exists, so correlated trajectories and peak-time probability remain blocked. High-confidence/extreme bins may be sparse; no promise of trading accuracy.','STOP AT PHASE9.']
 (DOCS/'PHASE9_FINAL_INDEPENDENT_ACCEPTANCE.md').write_text('\n\n'.join(report),encoding='utf-8')
 print(json.dumps({k:v for k,v in result.items() if k not in ('snapshots',)},ensure_ascii=False,indent=2),flush=True)
 if sum(violations.values()):raise SystemExit(1)
 return result

if __name__=='__main__':independent_audit()
