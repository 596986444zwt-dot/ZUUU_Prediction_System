"""Presentation only after saved-result independent acceptance; no fitting."""
import json
import csv
from collections import Counter,defaultdict
from .contracts import ROOT,DOCS,FAMILIES
from src.data_v1.source_io import open_snapshot,sha256_file

def table(rows,fields=None):
 if not rows:return '(none)'
 fields=fields or list(rows[0]);return '| '+' | '.join(fields)+' |\n| '+' | '.join(['---']*len(fields))+' |\n'+'\n'.join('| '+' | '.join(str(r.get(k,'')).replace('|','/') for k in fields)+' |' for r in rows)
def finalize():
 result=json.loads((DOCS/'PHASE8_FINAL_AUDIT.json').read_text(encoding='utf-8'))
 c=open_snapshot(ROOT/'database/phase8_machine_learning_v1.db')
 data={t:[dict(r) for r in c.execute('SELECT * FROM phase8_'+t)] for t in ('prediction','preprocessing','state','metric','slice_metric','comparison','candidate','manifest')};c.close()
 pp=data['prediction'];cold=[p for p in pp if p['ml_prediction'] is None]
 with (DOCS/'PHASE8_COLD_START_AUDIT.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(cold[0]));w.writeheader();w.writerows(cold)
 counts=[];features=[];degradation=[];importance=[]
 for h in ('T1','T2'):
  for family in FAMILIES:
   p=[x for x in pp if x['horizon']==h and x['model_family']==family];null=[x for x in p if x['ml_prediction'] is None];valid=[x for x in p if x['ml_prediction'] is not None]
   counts.append(dict(horizon=h,model=family,base_N=len(p),OOS_N=len(valid),cold_N=len(null),cold_start=min(x['target_business_date'] for x in null),cold_end=max(x['target_business_date'] for x in null),OOS_start=min(x['target_business_date'] for x in valid),OOS_end=max(x['target_business_date'] for x in valid)))
   rr=[r for r in data['preprocessing'] if r['horizon']==h and r['model_family']==family and r['stage']=='OUTER'];prep=[json.loads(r['preprocessing_json']) for r in rr]
   drops=defaultdict(Counter)
   for pr in prep:
    for n,why in pr['dropped'].items():drops[n][why]+=1
   features.append(dict(horizon=h,model=family,source_features=102,retained_min=min(len(pr['indices']) for pr in prep),retained_max=max(len(pr['indices']) for pr in prep),model_input_min=min(pr['input_feature_count'] for pr in prep),model_input_max=max(pr['input_feature_count'] for pr in prep),dropped_feature_reasons={n:dict(c) for n,c in drops.items()}))
   ss=[s for s in data['slice_metric'] if s['horizon']==h and s['comparison_model']==family and s['slice_type'] in ('year','season')]
   for s in [v for v in ss if v['model']==family]:
    baselines={b['model']:b for b in ss if b['slice_type']==s['slice_type'] and b['slice_value']==s['slice_value'] and b['model'] in ('RAW','M6')}
    degradation.append(dict(horizon=h,model=family,slice_type=s['slice_type'],slice=s['slice_value'],N=s['N'],MODEL_MAE=s['MAE'],RAW_MAE=baselines['RAW']['MAE'],MOS_MAE=baselines['M6']['MAE'],DELTA_VS_RAW=baselines['RAW']['MAE']-s['MAE'],DELTA_VS_MOS=baselines['M6']['MAE']-s['MAE']))
 for st in data['state']:
  pr=json.loads(next(r['preprocessing_json'] for r in data['preprocessing'] if r['record_id']==st['preprocessing_id']))
  names=pr['feature_names']+['missing_indicator_'+pr['feature_names'][i] for i in pr['indicator_indices']]
  for name,value in zip(names,json.loads(st['importance_json'])):importance.append(dict(state_id=st['record_id'],horizon=st['horizon'],model=st['model_family'],feature=name,value=value,interpretation='POST_HOC_DIAGNOSTIC_ONLY; no causal claim; no selection'))
 for filename,rows in [('PHASE8_FEATURE_IMPORTANCE.csv',importance),('PHASE8_STABILITY_COMPARISON.csv',degradation),('PHASE8_COVERAGE.csv',counts)]:
  with (DOCS/filename).open('w',encoding='utf-8',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 words=['# PHASE8 PLAIN LANGUAGE SUMMARY','## 模型实际 OOS 成绩',table(data['comparison'],['MODEL','HORIZON','N','RAW_MAE_SAME_SUBSET','MOS_MAE_SAME_SUBSET','MODEL_MAE','MODEL_RMSE','MODEL_BIAS','DELTA_VS_RAW','DELTA_VS_MOS']),'以上温度指标单位为 °C；DELTA 为基准 MAE 减模型 MAE，正数表示模型改善。连续预测没有提前取整。','## 哪些日期未训练、为什么',table(counts),'每个日期的 NULL 原因和可用历史样本数见 PHASE8_COLD_START_AUDIT.csv；没有静默删除。已知 2025-08-07/T2 源缺口仍排除。','## 实际输入及确定性排除',table(features),'102 是冻结源特征数；模型可能排除训练窗口全空、完全重复、常量列，Ridge 可能增加仅由训练缺失生成的指示器。树模型不填补，Ridge 中位数和标准化只读训练窗口。完整逐特征审计见 PHASE8_FEATURE_PREPROCESSING_AUDIT.csv。T2 24h revision 原值一直是 NULL，每个训练窗口均明确排除，没有变成 0。','## 年份与季节：所有改善及退化',table(degradation),'正 delta 改善，负 delta 退化。不能仅凭总体成绩宣称跨季节稳定；候选门槛在训练前冻结。全年/月份完整连续指标见 PHASE8_SLICE_METRICS.csv。','## 25 个问题的直接回答']
 for i,family in enumerate(FAMILIES,1):
  results=[r for r in data['comparison'] if r['MODEL']==family];words.append(f'{i}. {family} 的 T1/T2 MAE、RMSE、Bias 和同日期 Raw/MOS 成绩见第一张表。')
 answers={6:'真正 OOS 天数按模型和 horizon 在覆盖表逐项列出。',7:'冷启动的起止日期在覆盖表，所有日期在 COLD_START_AUDIT.csv，原因是已准入标签少于 180。',8:f'训练未来标签违规数 = {result["FUTURE_LABEL_COUNT"]}。',9:f'预处理泄漏数 = {result["PREPROCESSING_LEAKAGE_COUNT"]}；Ridge scaler/imputer 均只使用训练窗口。',10:f'调参泄漏数 = {result["HYPERPARAMETER_SELECTION_LEAKAGE_COUNT"]}，最终 OOS 不参与内层参数选择。',11:f'目标泄漏数 = {result["TARGET_LEAKAGE_COUNT"]}；没有根据答案筛选特征。',12:'每个模型与 Raw/M6 的比较日期完全相同，第一张表同时列出两种差值。',13:'T1 稳定性逐切片列在上表；按事先门槛候选为 '+result['PHASE8_T1_MODEL_CANDIDATE']+'。',14:'T2 稳定性逐切片列在上表；按事先门槛候选为 '+result['PHASE8_T2_MODEL_CANDIDATE']+'。',15:'是否总体改善而个别季节退化，直接看完整稳定性表中的负 delta；没有隐去不利切片。',16:'每个模型实际保留数量及最终输入数量范围在输入表。',17:'被排除的名字、理由和训练块出现次数在输入表及逐特征 CSV。',18:'T2 全空 24h revision 保持安全 NULL，在所有实际训练窗口中排除。',19:f'静默填补数 = {result["SILENT_IMPUTATION_COUNT"]}；Ridge 合法训练中位数填补是显式记录的。',20:f'静默样本删除数 = {result["SILENT_SAMPLE_DROP_COUNT"]}。',21:'Phase1～7 数据库、文件及架构 PDF 哈希均在训练前后及独立验收中逐一复查，Source Guardian = '+result['SOURCE_GUARDIAN_STATUS']+'。',22:'T0 Intraday blocker 保持：历史观测真实到达时间证据不足。D+2 是标签准入，不是观测到达时间。',23:'NO：没有进入 Phase9 或生成概率、Calibration。',24:'NO：没有正式 MODEL_T0_V1，也没有 Ensemble、Real-Time 或 GUI。',25:'下一阶段资格：'+('READY' if result['NEXT_PHASE_STATUS']=='READY_FOR_PHASE9' else 'BLOCKED')+'。本次停在 Phase8，不自动启动下一阶段。'}
 for i in range(6,26):words.append(str(i)+'. '+answers[i])
 words += ['## Final status',json.dumps({k:result[k] for k in ('PHASE8_ACCEPTANCE','MODEL_T1_V1_STATUS','MODEL_T2_V1_STATUS','PHASE8_T1_MODEL_CANDIDATE','PHASE8_T2_MODEL_CANDIDATE','NEXT_PHASE_STATUS')},indent=2),'所有系数及特征重要性仅为事后解释，不代表因果关系，没有据此删除特征、改变参数网格或重跑 OOS。','STOP AT PHASE8.']
 (DOCS/'PHASE8_PLAIN_LANGUAGE_SUMMARY.md').write_text('\n\n'.join(words),encoding='utf-8')
 with (DOCS/'PHASE8_BUILD_REPORT.md').open('a',encoding='utf-8') as f:f.write('\n\n## Final independent acceptance\n\n'+table([{k:result[k] for k in ('PHASE8_ACCEPTANCE','MODEL_T1_V1_STATUS','MODEL_T2_V1_STATUS','PHASE8_T1_MODEL_CANDIDATE','PHASE8_T2_MODEL_CANDIDATE','NEXT_PHASE_STATUS')}])+'\n\nComplete ordinary-language answers, model input policies and all unfavorable stability slices: PHASE8_PLAIN_LANGUAGE_SUMMARY.md.\n\nSTOP AT PHASE8.\n')
 files=[ROOT/'database/phase8_machine_learning_v1.db']+list(DOCS.rglob('*'))+list((ROOT/'src/models/phase8').glob('*.py'))+[ROOT/'src/builders/phase8_machine_learning_v1_builder.py',ROOT/'src/audit/phase8_machine_learning_v1_independent_acceptance.py',ROOT/'tests/test_phase8_machine_learning_v1.py']
 files=sorted({p for p in files if p.is_file() and '__pycache__' not in p.parts and p.name!='PHASE8_FILE_MANIFEST.json'})
 (DOCS/'PHASE8_FILE_MANIFEST.json').write_text(json.dumps(dict(created_files=[p.relative_to(ROOT).as_posix() for p in files],sha256={p.relative_to(ROOT).as_posix():sha256_file(p) for p in files},self_hash='excluded to avoid self-reference'),indent=2),encoding='utf-8')
 print('============================================================\nPHASE 8 — MACHINE LEARNING V1 FINAL REPORT\n============================================================')
 for k in ('SOURCE_GUARDIAN_STATUS','T1_BASE_SAMPLE_COUNT','T2_BASE_SAMPLE_COUNT','FEATURE_VERSION','FEATURE_COUNT','OUTER_WALK_FORWARD_STATUS','INNER_TIME_VALIDATION_STATUS','TARGET_LEAKAGE_COUNT','FUTURE_LABEL_COUNT','SAME_DAY_UNSETTLED_COUNT','FUTURE_ECMWF_COUNT','PREPROCESSING_LEAKAGE_COUNT','HYPERPARAMETER_SELECTION_LEAKAGE_COUNT','SILENT_IMPUTATION_COUNT','SILENT_SAMPLE_DROP_COUNT'):print(k,'=',result[k])
 for h in ('T1','T2'):
  for f in FAMILIES:
   mm=next(r for r in data['metric'] if r['horizon']==h and r['comparison_model']==f and r['model']==f);print(f+'_'+h,'=',json.dumps(mm))
  for f in ('RAW','M6'):
   mm=next(r for r in data['metric'] if r['horizon']==h and r['comparison_model']=='RIDGE' and r['model']==f);print(h+('_RAW_BASELINE' if f=='RAW' else '_MOS_BASELINE'),'=',json.dumps(mm))
 for k in ('PHASE8_T1_MODEL_CANDIDATE','PHASE8_T2_MODEL_CANDIDATE','T0_INTRADAY_BLOCKER','PHASE8_PHYSICAL_SHA256','PHASE8_SEMANTIC_SHA256','PHASE8_ACCEPTANCE','NEXT_PHASE_STATUS'):print(k,'=',result[k])
 print('STOP AT PHASE8.')
if __name__=='__main__':finalize()
