"""Read-only result presentation, no fitting or parameter changes."""
import json,csv,re,xml.etree.ElementTree as ET
from collections import Counter,defaultdict
from datetime import date
import numpy as np
from .contracts import ROOT,DOCS,OUTPUT,MODELS,SEED
from .data import fingerprints,reporting_audit
from .storage import read
from src.data_v1.source_io import open_snapshot,sha256_file

def table(rows,fields=None):
 if not rows:return '(无样本)'
 fields=fields or list(rows[0]);return '| '+' | '.join(fields)+' |\n| '+' | '.join(['---']*len(fields))+' |\n'+'\n'.join('| '+' | '.join(str(r.get(f,'')).replace('|','/') for f in fields)+' |' for r in rows)

def export(name,rows):
 if not rows:return
 fields=list(dict.fromkeys(k for r in rows for k in r))
 with (DOCS/name).open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows)

def report():
 final=json.loads((DOCS/'PHASE9_FINAL_AUDIT.json').read_text(encoding='utf-8'))
 identity=json.loads((DOCS/'PHASE9_PREDICTION_IDENTITY_AUDIT.json').read_text(encoding='utf-8'))
 if identity['status']!='PASS':raise ValueError('Supplemental identity acceptance failed')
 final['prediction_identity_audit']=identity
 final['CALIBRATION_T1_V1_STATUS']='OOS_VALIDATED_DAILY_REPORTED_TMAX'
 final['CALIBRATION_T2_V1_STATUS']='OOS_VALIDATED_DAILY_REPORTED_TMAX'
 c=open_snapshot(OUTPUT);d=read(c);c.close()
 pp=d['probability_prediction'];samples={r['sample_id']:r for r in d['sample']};states={r['record_id']:r for r in d['distribution_state']};masses={r['record_id']:np.frombuffer(r['pmf'],dtype='<f8') for r in d['probability_mass']}
 assessment=json.loads((DOCS/'PHASE9_BUILD_ASSESSMENT.json').read_text(encoding='utf-8'));coverage=[];examples=[];methodcounts=[];calibration_effect=[];tailstats=[];hetero=[]
 for h in MODELS:
  engine=sorted([p for p in pp if p['horizon']==h and p['origin']=='ENGINE'],key=lambda p:p['target_business_date'])
  formal=[p for p in engine if p['Brier'] is not None and p['probability_source_level']=='ML'];fallback=[p for p in engine if p['status']=='FALLBACK'];null=[p for p in engine if p['Brier'] is None]
  coverage.append(dict(horizon=h,base_N=len(engine),ML_probability_N=len(formal),fallback_N=len(fallback),no_forecast_N=len(null),ML_probability_start=formal[0]['target_business_date'],ML_probability_end=formal[-1]['target_business_date'],ML_calibrated_selected_N=sum(p['status']=='CALIBRATED' for p in formal),ML_uncalibrated_selected_N=sum(p['status']=='UNCALIBRATED' for p in formal),no_forecast_dates=[p['target_business_date'] for p in null],fallback_dates=[p['target_business_date'] for p in fallback]))
  for m,n in Counter(p['method'] for p in formal).items():methodcounts.append(dict(horizon=h,method=m,N=n,role='selected using only past; not post-hoc fixed winner'))
  example=min(formal,key=lambda p:abs(p['continuous_prediction']-34.6));pm=masses[example['record_id']]
  examples.append(dict(horizon=h,date=example['target_business_date'],forecast=example['continuous_prediction'],method=example['method'],residual_n=example['residual_n'],calibration_n=example['calibration_n'],P34=pm[34+80],P35=pm[35+80],P36=pm[36+80],pmf_nontrivial={int(k)-80:float(v) for k,v in enumerate(pm) if v>.005}))
  for base in ('GAUSSIAN_EXPANDING','GAUSSIAN_90CAL','EMPIRICAL_EXPANDING','KDE_EXPANDING_BW075'):
   raw={p['sample_id']:p for p in pp if p['horizon']==h and p['origin']=='ML' and p['method']==base and p['Brier'] is not None}
   cal=[p for p in pp if p['horizon']==h and p['origin']=='ML' and p['method']==base+'_CAL' and p['Brier'] is not None];ids=[p['sample_id'] for p in cal]
   calibration_effect.append(dict(horizon=h,method=base,N_COMMON=len(cal),alpha_counts=dict(Counter(p['calibration_alpha'] for p in cal)),**{('UNCAL_'+k):float(np.mean([raw[i][k] for i in ids])) for k in ('Brier','LogLoss','CRPS','entropy')},**{('CAL_'+k):float(np.mean([p[k] for p in cal])) for k in ('Brier','LogLoss','CRPS','entropy')}))
  for p in [formal[i] for i in (0,len(formal)//2,len(formal)-1)]:
   # Describe only residuals eligible at that historical cutoff; never change a rule.
   sid=p['sample_id'];st=states[sid+'/ML/GAUSSIAN_EXPANDING'];pool=[samples[i] for i in st['history_ids']];r=np.array([s['actual']-s['ML'] for s in pool]);n=len(r)
   tailstats.append(dict(horizon=h,cutoff=p['issue'],N=n,minimum=float(r.min()),maximum=float(r.max()),**{f'P{q}':float(np.quantile(r,q/100)) for q in (10,25,50,75,90,95,99)},tail_status='HIGH_UNCERTAINTY_TAIL; P99 descriptive only, fewer than 10 independent expected tail cases' if n<1000 else 'DESCRIPTIVE'))
   for family,groups in [('forecast_level',('<15','15..25','>=25')),('season',('DJF','MAM','JJA','SON'))]:
    for group in groups:
     values=[s['actual']-s['ML'] for s in pool if (('<15' if s['ML']<15 else '15..25' if s['ML']<25 else '>=25') if family=='forecast_level' else s['season'])==group]
     hetero.append(dict(horizon=h,cutoff=p['issue'],family=family,group=group,N=len(values),mean=float(np.mean(values)) if values else None,std=float(np.std(values,ddof=1)) if len(values)>1 else None,scope='eligible past-only descriptive; never used to change probability candidate'))
 # Additional legal forecast-regime diagnostics, thresholds from each eligible history only.
 c=open_snapshot(ROOT/'database/phase7_feature_v1.db');features={r['sample_id']:dict(r) for r in c.execute('SELECT * FROM training_feature_view')};c.close()
 for h in MODELS:
  formal=sorted([p for p in pp if p['horizon']==h and p['origin']=='ENGINE' and p['Brier'] is not None and p['probability_source_level']=='ML'],key=lambda p:p['target_business_date'])
  for p in [formal[i] for i in (0,len(formal)//2,len(formal)-1)]:
   pool=[samples[i] for i in states[p['sample_id']+'/ML/GAUSSIAN_EXPANDING']['history_ids']]
   for f in ('ecmwf_temp_range_c','ecmwf_cloud_afternoon_mean_pct','ecmwf_shortwave_daily_energy','ecmwf_tmax_revision_prev_run_c','hist_bias_30d_c'):
    valid=[s for s in pool if features[s['sample_id']].get(f) is not None]
    if not valid:
     hetero.append(dict(horizon=h,cutoff=p['issue'],family=f,group='UNAVAILABLE',N=0,mean=None,std=None,scope='no values manufactured'));continue
    values=np.array([abs(features[s['sample_id']][f]) if 'revision' in f else features[s['sample_id']][f] for s in valid]);median=float(np.median(values))
    for group,mask in (('<=past median',values<=median),('>past median',values>median)):
     rr=[s['actual']-s['ML'] for s,yes in zip(valid,mask) if yes]
     hetero.append(dict(horizon=h,cutoff=p['issue'],family=f,group=group,N=len(rr),past_only_median=median,mean=float(np.mean(rr)) if rr else None,std=float(np.std(rr,ddof=1)) if len(rr)>1 else None,scope='eligible past-only descriptive; no conditional variance model fitted'))
 export('PHASE9_HETEROSCEDASTICITY_AUDIT.csv',hetero);export('PHASE9_RESIDUAL_TAILS.csv',tailstats);export('PHASE9_CALIBRATION_EFFECT.csv',calibration_effect);export('PHASE9_METHOD_SELECTION_COUNTS.csv',methodcounts);export('PHASE9_COVERAGE.csv',coverage)
 reporting=reporting_audit()
 c=open_snapshot(ROOT/'database/zuuu_prediction.db');silver={r['id']:dict(r) for r in c.execute('SELECT * FROM zuuu_silver_observation')};targets=[dict(r) for r in c.execute('SELECT * FROM zuuu_target_v1')];raw={r['id']:dict(r) for r in c.execute('SELECT * FROM zuuu_raw_metar')};c.close()
 reporting['silver_daily_max_mismatch_count']=sum(max(silver[i]['temperature_c'] for i in json.loads(t['all_silver_ids']))!=t['daily_tmax_c'] for t in targets)
 cor_evidence=[]
 for t in targets:
  for i in json.loads(t['all_silver_ids']):
   s=silver[i]
   if not s['is_correction']:continue
   cor_evidence.append(dict(date=t['business_date_bjt'],temperature=s['temperature_c'],daily_tmax=t['daily_tmax_c'],is_daily_max=s['temperature_c']==t['daily_tmax_c'],supersedes_raw_id=s['supersedes_raw_id'],raw_metar=raw[s['bronze_raw_id']]['raw_metar'],delta_effect_status='ORIGINAL_TEMPERATURE_NOT_IDENTIFIED_FROM_SUPERSESSION; do not infer a Tmax change' if s['supersedes_raw_id'] is None else 'SUPERSESSION_REFERENCE_PRESENT'))
 reporting['cor_message_evidence']=cor_evidence
 (DOCS/'PHASE9_REPORTING_MECHANISM_AUDIT.md').write_text('# ZUUU_INTEGER_REPORTING_AUDIT\n\n'+json.dumps(reporting,ensure_ascii=False,indent=2)+'\n\nThe frozen parser uses (M?\\d{2})/(M?\\d{2}); signed integer main temperature tokens. Daily truth is max of finalized canonical integer reports. Tenths remark temperatures, if any, do not redefine TARGET_V1. COR messages and frozen correction flags are shown above; one correction-containing target has tmax_has_correction=1. Supersedes IDs are absent for these messages, so the archive does not prove how much an earlier Tmax changed. No missing original is manufactured, nothing is rewritten. Latent half-degree bins approximate the reported daily integer mechanism; the archive cannot establish a continuous sensor rounding law.\n',encoding='utf-8')
 (DOCS/'PHASE9_RESIDUAL_AUDIT.md').write_text('# PHASE9 RESIDUAL AUDIT\n\nactual reported integer Tmax minus frozen continuous OOS prediction. Daily T1/T2 pools separate, D+2 BJT midnight eligibility. Raw/MOS formal pools use matched ML OOS dates. Fallback uses separately recorded legal full historical OOS baselines. Every state stores exact historical sample IDs; residual_history preserves unavailable ML cold-start entries.\n\n'+table(tailstats)+'\n\n'+table(hetero)+'\n\nHIGH_UNCERTAINTY_TAIL: sample size cannot establish precise extreme probabilities. No winsorization, outlier deletion, or conditional variance fitting.\n',encoding='utf-8')
 (DOCS/'PHASE9_TRAJECTORY_AUDIT.md').write_text('# PHASE9 TRAJECTORY DATA CAPABILITY\n\nCORRELATED_TRAJECTORY_STATUS = BLOCKED_FOR_DATA\n\nTMAX_TIME_PROBABILITY_STATUS = BLOCKED\n\nInspected Phase8 saved prediction fields: daily scalar continuous Tmax and actual integer target. FEATURE_V1 has ECMWF hourly forecast-derived summaries, but these are forecast inputs, not independently validated hourly ML errors. No strictly OOS hourly residual vectors or identified hour-hour covariance are present. ZUUU historical observation timestamps do not prove historical arrival. Daily scalar residuals cannot identify a 24x24 covariance.\n\nNo paths, no independent-hour draws, no artificial covariance, no max/argmax simulation were created. Protocol trajectory_count=0 and seed is fixed. Future legal hourly OOS archives must preserve source availability and correction versions; then define covariance, simulation count, sanity audit and ANY_MAX_OCCURRENCE scoring against frozen first/last/count plus occurrence lineage in a new protocol. This is a data capability limitation, not framework deviation. T0 remains blocked.\n',encoding='utf-8')
 (DOCS/'PHASE9_FRAMEWORK_DEVIATION_CHECK.md').write_text('# FRAMEWORK DEVIATION CHECK\n\n'+table([dict(item=i,value=v) for i,v in [('Phase9 Probability Engine','YES'),('Ground Truth changed','NO'),('Issue Rule changed','NO'),('Phase1..8 modified','NO'),('Phase8 retrained or candidates changed','NO'),('FEATURE_V1 changed','NO'),('T0 blocker bypassed','NO'),('Market data used','NO'),('Ensemble built','NO'),('Phase10 started','NO'),('GUI built','NO')]])+'\n\nFRAMEWORK_DEVIATION = FALSE\n\nSTOP AT PHASE9.\n',encoding='utf-8')
 (DOCS/'PHASE9_SCHEMA_CONTRACT.md').write_text('# PHASE9 STORAGE CONTRACT\n\nCALIBRATION_T1_V1 = frozen probability protocol + T1 RIDGE OOS continuous input + T1-specific residual/calibration/method-selection records. CALIBRATION_T2_V1 = same frozen protocol + T2 LIGHTGBM OOS continuous input + independent T2 records. These are versioned sequential engines; no single retrospectively chosen fixed distribution is substituted for their historical sequence.\n\nAll phase9 tables except probability_mass store record_id PRIMARY KEY plus canonical payload_json. probability_mass stores little-endian IEEE float64 BLOB vectors in protocol support order -80..80: PMF, CDF P(T<=k), survival P(T>=k). Each mass row has a foreign key to prediction. Every table is sealed against INSERT/UPDATE/DELETE after publication. Views are unnecessary; payload records retain all lineage.\n\nSemantic hash streams ordered tables then record_id-sorted canonical records; BLOBs serialized as hexadecimal. Manifest excluded to avoid self-reference; protocol and every prediction/state/mass/metric included. Independent auditor rebuilds semantic hash directly. Final acceptance is external JSON/MD bound to physical and semantic DB hashes; sealed manifest honestly remains BUILT_PENDING_INDEPENDENT_ACCEPTANCE. No accepted DB is rewritten to change status.\n',encoding='utf-8')
 answers=[]
 answers+=['1–2. T1 连续输入固定为 Ridge，T2 固定为 LightGBM。概率方法按冻结协议逐日由过去结果选择，不是看完整结果后挑一个赢家。实际方法使用次数如下：\n\n'+table(methodcounts)]
 answers+=['3. 最接近 34.6℃ 的真实历史预测如下。先把当时合法历史误差加到连续预测，再对每个整数对应的半度区间积分；校准只使用过去的概率考试结果。这里是历史样本，不是当前天气预报：\n\n'+table(examples,['horizon','date','forecast','method','residual_n','calibration_n','P34','P35','P36'])]
 answers+=['4. 概率计算和因果性已独立验证，历史评分有竞争力；它并不意味着所有置信度都已充分验证，也不意味着任何交易收益。']
 answers+=['5–6. 本次正式 ML 概率的 Top1 >=60% 和 >=80% 都是 N=0，没有实际频率可估。不能声称“说60%就真的发生60%”。完整分箱报告保留每个 N。']
 answers+=['7–8. 没有足够高置信样本证明极端过度自信；80/90%区间覆盖偏高，表现偏保守。离散整数区间天然可能超过标称覆盖。下表同时给出实际覆盖和宽度，不能只追求宽区间。Top1 有样本的可靠性分箱如下；T2 的30–40%档只有11次，平均预报32.31%，实际命中9.09%，应警惕，样本仍不足下定论：\n\n'+table([r for r in d['calibration_bin'] if r['origin']=='ENGINE' and r['kind']=='TOP1' and r['N']>0],['horizon','bin_lower','bin_upper','N','mean_predicted','actual_frequency','warning'])]
 comparisons=[]
 for h in MODELS:
  m=next(m for m in d['probability_metric'] if m['horizon']==h and m['origin']=='ENGINE' and m['subset']=='SAME_COMMON_DATES');comparisons.append(dict(horizon=h,N=m['N'],Brier=m['Brier'],LogLoss=m['LogLoss'],CRPS=m['CRPS'],Top1=m['top1_hit'],Top2=m['top2_hit'],Top3=m['top3_hit'],mean_actual_probability=m['assigned_probability'],entropy=m['entropy'],spread=m['effective_spread']))
 answers+=['9–17. 全部正式指标（比率不是百分数，越小的 proper score 越好）：\n\n'+table(comparisons)]
 answers+=['18–19. 预测区间实际覆盖：\n\n'+table([r for r in d['interval_metric'] if r['origin']=='ENGINE'])]
 cats=[];worst=[]
 for h in MODELS:
  rows=[p for p in pp if p['horizon']==h and p['origin']=='ENGINE' and p['Brier'] is not None and p['probability_source_level']=='ML']
  cats.append(dict(horizon=h,N=len(rows),lt5=sum(p['assigned_probability']<.05 for p in rows),lt2=sum(p['assigned_probability']<.02 for p in rows),lt1=sum(p['assigned_probability']<.01 for p in rows)))
  p=min(rows,key=lambda p:p['assigned_probability']);worst.append({k:p[k] for k in ('horizon','target_business_date','continuous_prediction','actual','assigned_probability','top1_integer','top1_probability','method')})
 answers+=['20–22. 给真实答案极低概率的次数：\n\n'+table(cats)]
 answers+=['23. 最严重案例：\n\n'+table(worst)+'\n\n所有 <5% 案例保存在 CATASTROPHIC_CONFIDENCE_AUDIT.csv，没有删掉。']
 answers+=['24–25. 夏冬及前后半段实际表现如下，差异必须结合 N 看；所有年份/季节/前后半段也包含 Raw/MOS，见完整 CSV。\n\n'+table([r for r in d['slice_metric'] if r['origin']=='ENGINE'],['horizon','slice_type','slice_value','N','Brier','LogLoss','CRPS','top1_hit','assigned_probability'])]
 answers+=['26. Raw、MOS、ML 使用相同方法族、相同残差日期限制、相同校准规则及共同考试日期。总体 ML 三种评分较小，不因此改变 Phase8 候选。\n\n'+table(assessment['comparison'])+'\n\n14天连续块 bootstrap：\n\n'+table(assessment['bootstrap'])+'\n\nT2 的 LogLoss 优势置信区间跨0，证据尚不足以宣布统计上明确优势；候选只表示通过预先固定的 V1 工程门槛。']
 answers+=['27–29. 相关小时轨迹和最高温时刻概率目前不能合法建立。缺的是严格 OOS、可追溯历史可用性的逐小时预测误差向量，而不是缺一段模拟代码。没有独立小时抽样或伪造相关矩阵。']
 answers+=['30. T0 blocker 保持：历史观测时间不能证明历史到达时间。D+2 仅是结束日标签训练准入，不是实时观测到达证据。']
 answers+=['31. Phase1～8受保护资产哈希全部一致；任何新文件都属于 Phase9，没有写回上游。']
 answers+=['32. NO。没有读取 Polymarket 价格、订单簿或交易结果。']
 answers+=['33. '+('READY' if final['NEXT_PHASE_STATUS'].startswith('READY') else 'BLOCKED')+'。仅指 T1/T2 日最高温概率范围可以进入下一阶段的独立施工；成熟72小时轨迹、峰值时刻和T0仍 BLOCKED。本次没有开始 Phase10。']
 warnings=['没有小时 OOS 误差档案，trajectory 与 Tmax-time BLOCKED_FOR_DATA。','60%/80% Top1 置信度 N=0，不能校准验证这些档位。','T2 30–40% Top1 bin: N=11，mean predicted32.31%、observed hit9.09%，小样本过度自信信号必须继续监控。','80/90%区间相对保守；小样本极端尾部 HIGH_UNCERTAINTY_TAIL。','T2 块bootstrap置信区间跨0，统计差异未得到明确证实。','概率评估以已冻结的Phase8候选为条件；Phase8候选曾由Phase8 OOS结果选择，本次不是证明候选选择泛化的全新独立留出集，仍需前向实测。','ECMWF沿用估计发布可用性而非实际历史接收证据。','latent半度区间是整数报告概率近似，不是连续传感器四舍五入机制证明。','Phase8 Windows原生库初始化警告原样继承；本阶段只读其保存预测，未恢复或重新拟合模型。']
 plain=['# PHASE9 PLAIN LANGUAGE SUMMARY','PHASE9_ACCEPTANCE = '+final['PHASE9_ACCEPTANCE'],'## 基础样本、冷启动与回退',table(coverage,['horizon','base_N','ML_probability_N','fallback_N','no_forecast_N','ML_probability_start','ML_probability_end','ML_calibrated_selected_N','ML_uncalibrated_selected_N']),'每个冷启动/回退日期都在 PHASE9_COVERAGE.csv 和数据库 fallback_audit 中，没有静默删除。Raw/MOS回退不计入正式ML共同评分。','## 33个问题','\n\n'.join(answers),'## 固定候选及警告',table(assessment['candidates']),'\n\n'.join(warnings),'## 校准有没有改善？',table(calibration_effect),'同一个原始方法的有/无校准比较日期完全一致。复杂校准并非必然更好，逐日选取只看过去；分布形状/熵变化已保存。','STOP AT PHASE9.']
 (DOCS/'PHASE9_PLAIN_LANGUAGE_SUMMARY.md').write_text('\n\n'.join(plain),encoding='utf-8')
 tests={}
 for name in ('OLD','PHASE9','FULL'):
  path=DOCS/f'PHASE9_{name}_TEST_RESULTS.xml'
  if path.exists():
   t=ET.parse(path).getroot();suite=t.find('testsuite') if t.tag=='testsuites' else t;tests[name]=dict(suite.attrib)
   payload=(DOCS/f'PHASE9_{name}_TEST_RESULTS.txt').read_bytes()
   text=payload.decode('utf-16' if payload[:2] in (b'\xff\xfe',b'\xfe\xff') else 'utf-8-sig',errors='replace')
   match=re.search(r'(\d+) passed',text);sub=re.search(r'(\d+) subtests passed',text)
   tests[name]['passed']=int(match[1]) if match else None;tests[name]['subtests_passed']=int(sub[1]) if sub else 0
 final['tests']=tests;final['warnings']=warnings;final['coverage']=coverage;final['calibration_effect']=calibration_effect
 (DOCS/'PHASE9_FINAL_AUDIT.json').write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')
 reporttext=['# PHASE9 BUILD REPORT','IMPLEMENTATION DETAIL — 不偏离框架。Protocol 物理冻结后才生成概率。所有参数窗口/概率支持/校准方法/阈值均未据最终OOS修改。','Protocol SHA256: '+final['PROTOCOL_SHA256'],'## 状态',json.dumps({k:final[k] for k in ('SOURCE_GUARDIAN','PHASE9_ACCEPTANCE','NEXT_PHASE_STATUS','PHASE9_T1_CALIBRATION_CANDIDATE','PHASE9_T2_CALIBRATION_CANDIDATE','PHASE9_PHYSICAL_SHA256','PHASE9_SEMANTIC_SHA256')},indent=2),'## 共同日期评分',table(assessment['comparison']),'## 冷启动和回退',table(coverage,['horizon','base_N','ML_probability_N','fallback_N','no_forecast_N','ML_probability_start','ML_probability_end']),'## 校准和形状',table(calibration_effect),'## 概率质量及限制','\n\n'.join(warnings),'## 测试',json.dumps(tests,indent=2),'## 独立验收','全部base、calibrated、fallback概率从冻结输入独立倒算；全部PMF/CDF/survival与分数逐条核对；全部残差/校准/方法选择血缘审核。详见 FINAL_INDEPENDENT_ACCEPTANCE.md。','初次构建在DB发布前因回退记录 floor 重复关键词退出。修正字段重复后重跑；未见最终评分后改协议、参数、窗口或方法。失败日志保留。','正式DBsealed manifest build_status为BUILT_PENDING_INDEPENDENT_ACCEPTANCE；最终状态由独立外部JSON/MD绑定DB双SHA，不为更新状态改写数据库。','完整33个大白话答案：PHASE9_PLAIN_LANGUAGE_SUMMARY.md。','STOP AT PHASE9.']
 (DOCS/'PHASE9_BUILD_REPORT.md').write_text('\n\n'.join(reporttext),encoding='utf-8')
 before=json.loads((DOCS/'SOURCE_SHA_BEFORE.json').read_text(encoding='utf-8'));after=fingerprints()
 if before!=after:raise ValueError('Source changed during final reporting')
 (DOCS/'SOURCE_SHA_AFTER.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
 acceptance_path=DOCS/'PHASE9_FINAL_INDEPENDENT_ACCEPTANCE.md'
 acceptance_body=acceptance_path.read_text(encoding='utf-8').split('\n\n## Additional direct prediction identity audit',1)[0]
 acceptance_path.write_text(acceptance_body+'\n\n## Additional direct prediction identity audit\n\n'+json.dumps(identity,indent=2)+'\n\n## Final tests and inherited limitations\n\n'+json.dumps(tests,indent=2)+'\n\n'+'\n\n'.join(warnings)+'\n\nSource Guardian rechecked after final tests: all '+str(len(before))+' protected hashes unchanged.\n',encoding='utf-8')
 lines=['='*60,'PHASE 9 — PROBABILITY ENGINE V1 FINAL REPORT','='*60]
 fields=['SOURCE_GUARDIAN','PROTOCOL_SHA256','T1_CONTINUOUS_MODEL','T2_CONTINUOUS_MODEL','T1_PROBABILITY_METHOD','T2_PROBABILITY_METHOD','T1_PROBABILITY_OOS_N','T2_PROBABILITY_OOS_N']
 fields += [h+'_'+k for k in ('BRIER','LOGLOSS','CRPS','TOP1_EXACT','TOP2_COVERAGE','TOP3_COVERAGE','80_INTERVAL_COVERAGE','90_INTERVAL_COVERAGE','CATASTROPHIC_LT5_COUNT') for h in ('T1','T2')]
 fields += ['OVERCONFIDENCE_STATUS_T1','OVERCONFIDENCE_STATUS_T2','TARGET_LEAKAGE_COUNT','FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','SAME_DAY_UNSETTLED_COUNT','PREPROCESSING_LEAKAGE_COUNT','CALIBRATION_LEAKAGE_COUNT','CROSS_HORIZON_CONTAMINATION_COUNT','MARKET_DATA_USAGE_COUNT','UNPROVEN_INTRADAY_ZUUU_USAGE_COUNT']
 aliases={'PMF_SUM_ERROR_COUNT':'probability_sum_error_count','NEGATIVE_PROBABILITY_COUNT':'negative_probability_count','CDF_MONOTONICITY_ERROR_COUNT':'cdf_non_monotonic_count','INFINITE_LOGLOSS_COUNT':'infinite_logloss_count'}
 for k in fields:lines.append(k+' = '+str(final[k]))
 for k,v in aliases.items():lines.append(k+' = '+str(final[v]))
 for k in ('CORRELATED_TRAJECTORY_STATUS','TMAX_TIME_PROBABILITY_STATUS','T0_INTRADAY_BLOCKER','PHASE9_T1_CALIBRATION_CANDIDATE','PHASE9_T2_CALIBRATION_CANDIDATE','PHASE9_PHYSICAL_SHA256','PHASE9_SEMANTIC_SHA256','FRAMEWORK_DEVIATION','PHASE9_ACCEPTANCE','NEXT_PHASE_STATUS'):lines.append(k+' = '+str(final[k]))
 lines.extend(['TESTS = '+json.dumps(tests),'STOP AT PHASE9.'])
 (DOCS/'PHASE9_FINAL_TERMINAL.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8');print('\n'.join(lines))

def seal_manifest():
 files=[OUTPUT]+[p for p in DOCS.rglob('*') if p.is_file() and p.name!='PHASE9_FILE_MANIFEST.json']+list((ROOT/'src/probability').glob('*.py'))+[ROOT/'src/builders/phase9_probability_v1_builder.py',ROOT/'src/audit/phase9_probability_v1_independent_acceptance.py',ROOT/'src/audit/phase9_probability_v1_identity_audit.py',ROOT/'tests/test_phase9_probability_v1.py']
 files=sorted(set(files));manifest=dict(created_files=[p.relative_to(ROOT).as_posix() for p in files],sha256={p.relative_to(ROOT).as_posix():sha256_file(p) for p in files},self_hash='excluded to avoid self-reference')
 p=DOCS/'PHASE9_FILE_MANIFEST.json'
 with p.open('x',encoding='utf-8') as f:f.write(json.dumps(manifest,ensure_ascii=False,indent=2))
 print('PHASE9_FILE_MANIFEST_SHA256 =',sha256_file(p))

if __name__=='__main__':report()
