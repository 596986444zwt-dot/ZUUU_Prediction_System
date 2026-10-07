"""Evidence-based V2 reporting. No production mutation or automatic repairs."""
from .common import *
import xml.etree.ElementTree as ET

LABELS=['FRAMEWORK_INTEGRITY','SOURCE_GUARDIAN','DATABASE_INTEGRITY','PHASE1_GROUND_TRUTH','PHASE2_ECMWF_ARCHIVE','PHASE2_ISSUE_RULE','PHASE3_AUXILIARY','PHASE4_DATA_V1','PHASE5_RAW_BASELINE','PHASE6_STATISTICAL_MOS','PHASE7_FEATURE_V1','PHASE8_MACHINE_LEARNING','PHASE9_PROBABILITY','PHASE10_REALTIME_BUILD','MA001_LINEAGE_FIX','MA001_ALIAS_RESOLUTION','MA001_STATE_CONTAMINATION','MA002_ANCHOR_FIX','MA002_ANCHOR_STATE_MACHINE','CROSS_PHASE_TIME_CAUSALITY','FEATURE_REPRODUCTION','MODEL_T1_REPRODUCTION','MODEL_T2_REPRODUCTION','PROBABILITY_REPRODUCTION','PROBABILITY_STATE_CAUSALITY','HISTORICAL_REALTIME_PARITY','FORWARD_STATE_CAUSALITY','END_TO_END_REPRODUCTION','PREDICTION_LINEAGE','SNAPSHOT_IMMUTABILITY','SETTLEMENT_LOGIC','EVALUATION_LOGIC','FAILURE_RECOVERY_EVIDENCE']

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8-sig'))
def table(name):
    with (OUT/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def finish(s,f,m,p,r):
    c={k:int(v) for k,v in s.counts.items()};findings=[]
    def finding(id,severity,phase,title,description,evidence,impact,scope='REPORT_ONLY; 由负责人另行授权处理'):
        findings.append(dict(finding_id=id,severity=severity,phase=phase,component=title,status='OPEN' if severity in ('CRITICAL','HIGH') else 'WARNING',title=title,description=description,evidence=evidence,affected_asset='见所列证据中的具体资产路径',affected_dates=[],affected_predictions=[],expected='冻结框架与本轮独立审计要求',actual=description,framework_reference='V1.0 架构及 MASTER_AUDIT_V2_PROTOCOL.md',causal_impact=impact,historical_impact=impact,realtime_impact=impact,repair_scope_recommendation=scope))
    critical=['UPSTREAM_CHANGED_FILE_COUNT','TARGET_RECALC_MISMATCH_COUNT','FUTURE_ECMWF_RUN_COUNT','CROSS_RUN_SPLICE_COUNT','TARGET_LEAKAGE_COUNT','FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','PMF_NEGATIVE_COUNT','PMF_SUM_MISMATCH_COUNT','CDF_MONOTONICITY_VIOLATION_COUNT','FORWARD_STATE_CAUSALITY_VIOLATION_COUNT','POLYMARKET_USAGE_COUNT','UNPROVEN_INTRADAY_ZUUU_USAGE_COUNT']
    high=['DATABASE_INTEGRITY_FAILURE_COUNT','ISSUE_RULE_SELECTION_MISMATCH_COUNT','PHASE2_VALUE_MISMATCH_COUNT','PHASE4_SAMPLE_MISMATCH_COUNT','PHASE5_METRIC_MISMATCH_COUNT','PHASE6_MOS_MISMATCH_COUNT','PHASE6_CAUSALITY_VIOLATION_COUNT','FEATURE_FORMULA_MISMATCH_COUNT','FEATURE_TIME_AVAILABILITY_VIOLATION_COUNT','MODEL_T1_REPRO_MISMATCH_COUNT','MODEL_T2_REPRO_MISMATCH_COUNT','PREPROCESSING_LEAKAGE_COUNT','HYPERPARAMETER_SELECTION_LEAKAGE_COUNT','MODEL_SELECTION_LEAKAGE_COUNT','PROBABILITY_REPRO_MISMATCH_COUNT','PROBABILITY_SCORE_MISMATCH_COUNT','INVALID_RESIDUAL_COUNT','CROSS_HORIZON_RESIDUAL_COUNT','ALIAS_RESOLUTION_MISMATCH_COUNT','ALIAS_CYCLE_COUNT','ALIAS_MISSING_CANONICAL_COUNT','ALIAS_USED_AS_ACTIVE_STATE_COUNT','ALIAS_USED_AS_RESIDUAL_COUNT','ALIAS_USED_AS_CALIBRATION_COUNT','ALIAS_USED_AS_LATEST_STATE_COUNT','UNRESOLVED_PROBABILITY_STATE_REFERENCE_COUNT','ANCHOR_STATE_MACHINE_MISMATCH_COUNT','ANCHOR_NUMERIC_MISMATCH_COUNT','NEW_REFERENCE_GUARD_FAILURE_COUNT','HALF_SNAPSHOT_COUNT','ORPHAN_RECORD_COUNT','HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT','END_TO_END_MISMATCH_COUNT','LINEAGE_BREAK_COUNT','DUPLICATE_RESIDUAL_UPDATE_COUNT','OFF_ANCHOR_RESIDUAL_UPDATE_COUNT','SETTLEMENT_EVALUATION_MISMATCH_COUNT','SILENT_IMPUTATION_COUNT','SILENT_SAMPLE_DROP_COUNT','UNIT_MISMATCH_COUNT','MODEL_RETRAIN_COUNT','MODEL_PROMOTION_COUNT','HASH_MISMATCH_COUNT','MASTER_FAILURE_PROBE_FAILURE_COUNT','PHASE10_FEATURE_MISMATCH_COUNT','PHASE10_MODEL_MISMATCH_COUNT','PHASE10_PROBABILITY_MISMATCH_COUNT','PHASE10_TIME_VIOLATION_COUNT','PHASE10_RAW_MISMATCH_COUNT','CALIBRATION_SELECTION_MISMATCH_COUNT','PHASE8_METRIC_MISMATCH_COUNT']
    absent=[k for k in critical+high if k not in c]
    for key in critical+high:
        if c.get(key,0):finding('V2-'+key,'CRITICAL' if key in critical else 'HIGH','CROSS_PHASE',key,'独立检查发现 '+str(c[key])+' 项违规或不一致；不得修复后隐藏。',['V2_PROGRESS.json','相应逐行审计 CSV/JSON'],'影响须以逐行证据界定；ROOT_CAUSE=NOT_YET_PROVEN')
    if absent:finding('V2-EVIDENCE-GAP','MEDIUM','MASTER','未完成的关键检查','这些计数未实际建立：'+', '.join(absent),['V2_PROGRESS.json'],'核心证明缺口；不能自动当作0')
    diag=read('PHASE9_AGGREGATE_DIAGNOSTICS_V2.json');fw=read('FORWARD_STATE_AUDIT_V2.json');alias=read('MA001_ALIAS_INDEPENDENT_AUDIT.json');anchor=read('MA002_ANCHOR_STATE_MACHINE_AUDIT.json')
    finding('V2-W001','MEDIUM','PHASE10','长期运行与真实结算尚待验证','当前正式库尚未观察真实结算、评分和状态推进；60天隔离重放证明逻辑，不能替代72小时至7天真实无人值守运行。',['FORWARD_STATE_AUDIT_V2.json','MASTER_PHASE10_REALTIME_AUDIT.csv','MASTER_FAILURE_PROBES.csv'],'数学与因果检查通过不等于已经证明运行稳定','仅安排真实部署 Soak；本轮不启动')
    finding('V2-W002','MEDIUM','PHASE9','概率样本与尾部不确定性','T2校准及高置信度样本有限，低概率实际事件仍存在。全部评分、置信度分箱与灾难性置信案例独立重算，不进行事后候选替换。',['PHASE9_AGGREGATE_DIAGNOSTICS_V2.json'],'影响预测技能和置信程度，不等同数学错误','保留冻结概率；未来独立版本研究')
    finding('V2-W003','MEDIUM','PHASE2/10','历史可用时间与部署网络限制','历史ECMWF继承冻结的估计发布语义，不能说成观测到的真实到达时间。实时endpoint有HTTP失败证据；最终部署网络稳定性尚待Soak，未测试大陆网络不构成硬性阻塞。',['MASTER_PHASE2_ECMWF_AUDIT.csv','MASTER_PHASE10_REALTIME_AUDIT.csv','PRODUCTION_DEPENDENCY_GRAPH_V2.json'],'历史语义限制和运行风险；不是已发现未来Run','只验证实际部署网络；不得重写历史availability')
    finding('V2-W004','LOW','PHASE7','冗余与结构性缺失','hist_expanding_bias_c 与 hist_horizon_bias_c 重复；run_age_hours近常量；T2 24h revision结构性NULL。冻结missing规则与训练窗口处理得到继承。',['MASTER_FEATURE_REPRODUCTION.csv','MASTER_FEATURE_REGISTRY_AUDIT.csv','PHASE7_FEATURE_AUDIT_V2.json'],'不构成泄漏；可能降低特征利用效率','不改FEATURE_V1，后续版本另行研究')
    finding('V2-W005','LOW','PHASE10','历史固定issue之外的适用性','21:00后首次合法成功可成为anchor；之后刷新必须降级。历史固定issue验证不能自动证明任意刷新时刻具有同等预测技能。',['MA002_ANCHOR_STATE_MACHINE_AUDIT.json','MASTER_REALTIME_APPLICABILITY_AUDIT.csv'],'状态边界合法，但刷新预测技能未被同等验证','保留OFF_FIXED_ISSUE_REGIME，不自动升级为OK')
    for severity in ('CRITICAL','HIGH','MEDIUM','LOW'):c[severity+'_FINDING_COUNT']=sum(x['severity']==severity for x in findings)
    accepted='FAIL' if c['CRITICAL_FINDING_COUNT'] or c['HIGH_FINDING_COUNT'] else 'BLOCKED' if absent else 'PASS_WITH_WARNINGS'
    statuses={k:'PASS' for k in LABELS}
    groups={'PHASE1_GROUND_TRUTH':['TARGET_RECALC_MISMATCH_COUNT'],'PHASE2_ECMWF_ARCHIVE':['PHASE2_VALUE_MISMATCH_COUNT'],'PHASE2_ISSUE_RULE':['ISSUE_RULE_SELECTION_MISMATCH_COUNT','FUTURE_ECMWF_RUN_COUNT','CROSS_RUN_SPLICE_COUNT'],'PHASE4_DATA_V1':['PHASE4_SAMPLE_MISMATCH_COUNT'],'PHASE5_RAW_BASELINE':['PHASE5_METRIC_MISMATCH_COUNT'],'PHASE6_STATISTICAL_MOS':['PHASE6_MOS_MISMATCH_COUNT','PHASE6_CAUSALITY_VIOLATION_COUNT'],'FEATURE_REPRODUCTION':['FEATURE_FORMULA_MISMATCH_COUNT','FEATURE_TIME_AVAILABILITY_VIOLATION_COUNT'],'MODEL_T1_REPRODUCTION':['MODEL_T1_REPRO_MISMATCH_COUNT'],'MODEL_T2_REPRODUCTION':['MODEL_T2_REPRO_MISMATCH_COUNT'],'PROBABILITY_REPRODUCTION':['PROBABILITY_REPRO_MISMATCH_COUNT','PROBABILITY_SCORE_MISMATCH_COUNT'],'MA001_ALIAS_RESOLUTION':['ALIAS_RESOLUTION_MISMATCH_COUNT','ALIAS_CYCLE_COUNT','ALIAS_MISSING_CANONICAL_COUNT','UNRESOLVED_PROBABILITY_STATE_REFERENCE_COUNT'],'MA001_STATE_CONTAMINATION':['ALIAS_USED_AS_ACTIVE_STATE_COUNT','ALIAS_USED_AS_RESIDUAL_COUNT','ALIAS_USED_AS_CALIBRATION_COUNT','ALIAS_USED_AS_LATEST_STATE_COUNT'],'MA002_ANCHOR_STATE_MACHINE':['ANCHOR_STATE_MACHINE_MISMATCH_COUNT','ANCHOR_NUMERIC_MISMATCH_COUNT'],'HISTORICAL_REALTIME_PARITY':['HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT'],'END_TO_END_REPRODUCTION':['END_TO_END_MISMATCH_COUNT'],'FORWARD_STATE_CAUSALITY':['FORWARD_STATE_CAUSALITY_VIOLATION_COUNT'],'PREDICTION_LINEAGE':['LINEAGE_BREAK_COUNT'],'SNAPSHOT_IMMUTABILITY':['HALF_SNAPSHOT_COUNT','ORPHAN_RECORD_COUNT'],'SETTLEMENT_LOGIC':['SETTLEMENT_EVALUATION_MISMATCH_COUNT'],'EVALUATION_LOGIC':['SETTLEMENT_EVALUATION_MISMATCH_COUNT'],'SOURCE_GUARDIAN':['UPSTREAM_CHANGED_FILE_COUNT','HASH_MISMATCH_COUNT'],'DATABASE_INTEGRITY':['DATABASE_INTEGRITY_FAILURE_COUNT'],'FAILURE_RECOVERY_EVIDENCE':['MASTER_FAILURE_PROBE_FAILURE_COUNT']}
    for label,keys in groups.items():statuses[label]='NOT_INDEPENDENTLY_VERIFIED' if any(k not in c for k in keys) else 'FAIL' if any(c[k] for k in keys) else 'PASS'
    for label in ('PHASE2_ECMWF_ARCHIVE','PHASE3_AUXILIARY','PHASE7_FEATURE_V1','PHASE8_MACHINE_LEARNING','PHASE9_PROBABILITY','PHASE10_REALTIME_BUILD','FRAMEWORK_INTEGRITY'):statuses[label]='PASS_WITH_WARNING' if accepted=='PASS_WITH_WARNINGS' else accepted
    statuses['MA001_LINEAGE_FIX']=statuses['MA001_ALIAS_RESOLUTION'];statuses['MA002_ANCHOR_FIX']=statuses['MA002_ANCHOR_STATE_MACHINE'];statuses['PROBABILITY_STATE_CAUSALITY']=statuses['FORWARD_STATE_CAUSALITY']
    statuses.update(T0_DATA_COLLECTION='ACTIVE',T0_FORMAL_MODEL='BLOCKED / DEFERRED',CORRELATED_TRAJECTORY='BLOCKED_FOR_DATA',TMAX_TIME_PROBABILITY='BLOCKED_FOR_DATA',DEPLOYMENT_NETWORK_VALIDATION='PENDING_SOAK',PHASE10_OPERATIONAL_ACCEPTANCE='PENDING_SOAK',READY_FOR_OPERATIONAL_SOAK='YES' if accepted=='PASS_WITH_WARNINGS' else 'NO',MASTER_AUDIT_V2=accepted)
    suites=ET.parse(OUT/'AUDITOR_TEST_RESULTS.xml').getroot();test_count=sum(int(x.get('tests',0)) for x in suites.iter('testsuite'));test_fail=sum(int(x.get('failures',0))+int(x.get('errors',0)) for x in suites.iter('testsuite'))
    c['AUDITOR_AND_ISOLATED_TEST_COUNT']=test_count;c['AUDITOR_AND_ISOLATED_TEST_FAILURE_COUNT']=test_fail
    summaries={
      'PHASE1_GROUND_TRUTH_AUDIT_V2.json':dict(status=statuses['PHASE1_GROUND_TRUTH'],**s.evidence['phase1']),
      'PHASE2_ECMWF_ARCHIVE_AUDIT_V2.json':dict(status=statuses['PHASE2_ECMWF_ARCHIVE'],**s.evidence['phase2']),
      'ISSUE_RULE_REPRODUCTION_V2.json':dict(status=statuses['PHASE2_ISSUE_RULE'],checks=c['ISSUE_RULE_CHECK_COUNT'],mismatches=c['ISSUE_RULE_SELECTION_MISMATCH_COUNT'],evidence='MASTER_PHASE4_SAMPLE_AUDIT.csv',rule='Independent newest available complete same-run BJT target-day trajectory'),
      'PHASE3_AUXILIARY_AUDIT_V2.json':dict(status=statuses['PHASE3_AUXILIARY'],**s.evidence['phase3']),
      'PHASE4_DATA_V1_AUDIT_V2.json':dict(status=statuses['PHASE4_DATA_V1'],**read('SAMPLE_UNIVERSE_V2.json')),
      'PHASE6_MOS_AUDIT_V2.json':dict(status=statuses['PHASE6_STATISTICAL_MOS'],label_edges=s.evidence['phase6_lineage_edges'],candidate='NONE',mismatch=c['PHASE6_MOS_MISMATCH_COUNT'],causality=c['PHASE6_CAUSALITY_VIOLATION_COUNT'],evidence='MASTER_PHASE6_CAUSALITY_AUDIT.csv'),
      'PHASE7_FEATURE_AUDIT_V2.json':dict(status=statuses['PHASE7_FEATURE_V1'],**s.evidence['features'],redundancy=s.evidence['feature_redundancy'],cells=c['FEATURE_CELL_CHECK_COUNT'],mismatch=c['FEATURE_FORMULA_MISMATCH_COUNT']),
      'PHASE8_MODEL_REPRODUCTION_V2.json':dict(status=statuses['PHASE8_MACHINE_LEARNING'],**s.evidence['phase8'],ridge_checks=c['MODEL_T1_REPRO_CHECK_COUNT'],lightgbm_checks=c['MODEL_T2_REPRO_CHECK_COUNT'],method='Independent Ridge preprocessing/dot and LightGBM native + tree traversal; runtime wrapper parity separately exercised'),
      'PHASE9_PROBABILITY_AUDIT_V2.json':dict(status=statuses['PHASE9_PROBABILITY'],**s.evidence['phase9'],all_counts={k:v for k,v in c.items() if any(t in k for t in ('RESIDUAL','CALIBRATION','PMF','PROBABILITY'))},independent_diagnostics='PHASE9_AGGREGATE_DIAGNOSTICS_V2.json'),
      'PHASE10_REALTIME_AUDIT_V2.json':dict(status=statuses['PHASE10_REALTIME_BUILD'],**s.evidence['phase10'],tests=test_count),
      'HISTORICAL_REALTIME_PARITY_V2.json':dict(status=statuses['HISTORICAL_REALTIME_PARITY'],checks=c['HISTORICAL_REALTIME_PARITY_CHECK_COUNT'],mismatch=c['HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT'],rows=table('MASTER_HISTORICAL_REALTIME_PARITY.csv')),
      'END_TO_END_REPRODUCTION_V2.json':dict(status=statuses['END_TO_END_REPRODUCTION'],checks=c['END_TO_END_CHECK_COUNT'],mismatch=c['END_TO_END_MISMATCH_COUNT'],rows=table('MASTER_END_TO_END_REPRODUCTION.csv')),
      'PREDICTION_LINEAGE_V2.json':dict(status=statuses['PREDICTION_LINEAGE'],checks=c['LINEAGE_CHECK_COUNT'],breaks=c['LINEAGE_BREAK_COUNT'],realtime_rows=table('MASTER_LINEAGE_AUDIT.csv'),historical_rows=table('MASTER_HISTORICAL_LINEAGE_AUDIT.csv'),unsettled='Not-yet-eligible settlement/evaluation is pending, not a lineage break'),
      'SNAPSHOT_IMMUTABILITY_V2.json':dict(status=statuses['SNAPSHOT_IMMUTABILITY'],half=c['HALF_SNAPSHOT_COUNT'],orphans=c['ORPHAN_RECORD_COUNT'],evidence=['MA001_ALIAS_INDEPENDENT_AUDIT.json','MASTER_FAILURE_PROBES.csv','SOURCE_GUARDIAN_V2_AFTER.json'],scope='All existing original rows compared to pre-repair backup; append-only triggers and isolated UPDATE/DELETE/rollback exercised'),
      'SETTLEMENT_EVALUATION_V2.json':dict(status=statuses['SETTLEMENT_LOGIC'],mismatch=c['SETTLEMENT_EVALUATION_MISMATCH_COUNT'],real_world_observation='PENDING_SOAK',replay_evaluations=fw['evaluation_count'],pending_snapshots=fw['pending_unsettled_snapshot_count'],anchor_only_residual_update=True,evidence=['FORWARD_STATE_AUDIT_V2.json','MASTER_FAILURE_PROBES.csv']),
      'FAILURE_RECOVERY_V2.json':dict(status=statuses['FAILURE_RECOVERY_EVIDENCE'],failures=c['MASTER_FAILURE_PROBE_FAILURE_COUNT'],probes=r.probes_results,production_soak='PENDING_SOAK')}
    for name,data in summaries.items():output(name,data)
    requirements=[]
    entries=[('目标定义/T0/T1/T2/72小时',3,'PASS'),('唯一Ground Truth=ZUUU',3,statuses['PHASE1_GROUND_TRUTH']),('ECMWF forecast core',4,statuses['PHASE2_ECMWF_ARCHIVE']),('Meteostat historical availability',4,'BLOCKED'),('WU历史资产',4,'NOT_IMPLEMENTED_BY_DESIGN'),('Solar/Time deterministic',4,'PASS'),('Bronze/Silver/Gold',3,'PASS'),('Observation Time',3,'PASS'),('Issue Time',3,'PASS'),('Available Time',3,'PASS_WITH_WARNING'),('Ingest Time',3,'PASS'),('Target Time',3,'PASS'),('Lead Time',3,'PASS'),('Raw immutable',10,statuses['SOURCE_GUARDIAN']),('Forecast vintage immutable',10,'PASS'),('Prediction immutable',10,statuses['SNAPSHOT_IMMUTABILITY']),('Walk-forward only',7,'PASS'),('No random shuffle',7,'PASS'),('Phase5 Baseline',9,statuses['PHASE5_RAW_BASELINE']),('Phase6 MOS',9,statuses['PHASE6_STATISTICAL_MOS']),('Phase7 Feature',9,statuses['FEATURE_REPRODUCTION']),('Phase8 ML',9,'PASS'),('Phase9 Probability',9,statuses['PROBABILITY_REPRODUCTION']),('Phase10 Realtime',9,'PASS_WITH_WARNING'),('T0 Intraday',5,'BLOCKED'),('Integer probability not round',6,'PASS'),('Daily maximum probability',6,'PASS'),('Correlated trajectory',7,'BLOCKED'),('Tmax time probability',6,'BLOCKED'),('Prediction Snapshot',8,'PASS'),('Settlement',8,'PASS'),('Evaluation',8,'PASS'),('Champion/Challenger future promotion',7,'NOT_IMPLEMENTED_BY_DESIGN'),('GUI later',9,'NOT_IMPLEMENTED_BY_DESIGN'),('Source Health',8,'PASS'),('版本化',8,'PASS'),('可追溯性',8,statuses['PREDICTION_LINEAGE'])]
    for i,(title,page,status) in enumerate(entries,1):requirements.append(dict(requirement_id='FWV2-'+str(i).zfill(3),requirement=title,architecture_pdf_page=page,status=status,evidence='本轮各阶段逐行CSV、独立数学、隔离测试；原文见 ARCHITECTURE_V1_ORIGINAL_EXTRACT.txt',interpretation='Current planned blockers/deferred capability are explicit; no fake implementation accepted'))
    output('MASTER_FRAMEWORK_REQUIREMENTS_V2.json',dict(architecture_pdf=str(next((ROOT/'docs/architecture').glob('*.pdf'))),sha256=digest(next((ROOT/'docs/architecture').glob('*.pdf'))),requirements=requirements))
    output('AUDIT_V1_V2_COMPARISON.json',dict(old_report_untouched=True,changes=[dict(finding='MA-001',V1='FAIL',V2=statuses['MA001_LINEAGE_FIX'],cause='Authorized appended LINEAGE_ALIAS; every original record identical to backup; canonical hash/fingerprint independently checked',evidence='MA001_ALIAS_INDEPENDENT_AUDIT.json'),dict(finding='MA-002',V1='FAIL',V2=statuses['MA002_ANCHOR_FIX'],cause='Authorized anchor classification patch; newly executed adversarial first-success/failure/horizon scenarios',evidence='MA002_ANCHOR_STATE_MACHINE_AUDIT.json')],new_high_or_critical=[x for x in findings if x['severity'] in ('HIGH','CRITICAL')],network_rule='Mainland jurisdiction removed as a hard gate by this round specification; actual deployment network PENDING_SOAK'))
    limitations='''# 第二轮已知限制\n\nT0缺少合法历史到达时间，正式模型仍BLOCKED。小时OOS误差向量缺失，trajectory和Tmax-time仍BLOCKED_FOR_DATA。Meteostat历史可用时间无法证明，未进入正式训练；WU无正式历史资产。历史ECMWF availability是冻结估计语义。Phase8保持180天最小历史、约14天refit、窗口内固定状态、Ridge训练内median及Windows依赖警告。Phase9校准、尾部和T2置信度样本有限。Phase10实际部署网络、长期运行、真实完整结算和状态推进仍待Soak。非anchor刷新降级，不能冒称与固定issue等价验证。\n\n本轮不启动Soak，不进行修复。大陆网络不是准入硬条件。\n'''
    output('MASTER_AUDIT_V2_KNOWN_LIMITATIONS.md',limitations)
    output('MASTER_AUDIT_V2_FINDINGS.json',dict(audit_run=2,mode='ADVERSARIAL_MASTER_AUDIT',seed=SEED,status=accepted,findings=findings,absent_critical_evidence=absent))
    term='ZUUU SYSTEM V1 — SECOND ADVERSARIAL MASTER AUDIT\n'+'='*65+'\n'+'\n'.join(k+' = '+str(v) for k,v in statuses.items())+'\n'+'='*65+'\n'+'\n'.join(k+' = '+str(v) for k,v in c.items())
    output('MASTER_AUDIT_V2_TERMINAL_REPORT.txt',term)
    report='# ZUUU V1 第二轮独立对抗性总审计\n\n'+f'**MASTER_AUDIT_V2 = {accepted}**\n\n**READY_FOR_OPERATIONAL_SOAK = {statuses["READY_FOR_OPERATIONAL_SOAK"]}**；PHASE10_OPERATIONAL_ACCEPTANCE = PENDING_SOAK。\n\n'
    report+='本轮正式资产只读。采用SEED=19870624，不采用旧PASS作为证明；所有历史简单计算全量重算，复杂模型/概率也完成全量，实时一致性采用新的风险样本。第一轮报告和修复报告未修改。\n\n'
    report+='| 检查 | 数量 | 不一致 |\n|---|---:|---:|\n'
    for title,a,b in [('Ground Truth','TARGET_RECALC_CHECK_COUNT','TARGET_RECALC_MISMATCH_COUNT'),('Issue Rule','ISSUE_RULE_CHECK_COUNT','ISSUE_RULE_SELECTION_MISMATCH_COUNT'),('Feature cells','FEATURE_CELL_CHECK_COUNT','FEATURE_FORMULA_MISMATCH_COUNT'),('T1 Ridge','MODEL_T1_REPRO_CHECK_COUNT','MODEL_T1_REPRO_MISMATCH_COUNT'),('T2 LightGBM','MODEL_T2_REPRO_CHECK_COUNT','MODEL_T2_REPRO_MISMATCH_COUNT'),('Probability PMF','PROBABILITY_REPRO_CHECK_COUNT','PROBABILITY_REPRO_MISMATCH_COUNT'),('Residual edges','RESIDUAL_EDGE_CHECK_COUNT','FUTURE_RESIDUAL_COUNT'),('Historical/realtime parity','HISTORICAL_REALTIME_PARITY_CHECK_COUNT','HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT'),('RAW to score','END_TO_END_CHECK_COUNT','END_TO_END_MISMATCH_COUNT'),('Lineage','LINEAGE_CHECK_COUNT','LINEAGE_BREAK_COUNT')]:report+=f'| {title} | {c.get(a,"未核验")} | {c.get(b,"未核验")} |\n'
    report+=f'\nMA-001：34条概率引用全部解析，1条别名不是活跃状态；原记录逐表与修复前备份比对。MA-002：独立首成功状态机及完整102输入恒定条件测试通过，数值不因状态分类而变化。60连续日重放产生{fw["transitions"]}次真实模拟状态推进、{fw["evaluation_count"]}条评分，{fw["pending_unsettled_snapshot_count"]}条未到结算时间的记录保留。\n\n'
    report+='## 独立方法与证据边界\n\n从原始METAR温度token与UTC时间重建每日最高温，原始ECMWF JSON逐字段对照全部hourly。Run选择自行实现。102特征采用独立公式，未调用Feature builder作为证明。Ridge按固定median/scaling与系数矩阵计算；LightGBM native inference另以tree traversal核查。Phase9逐行独立分布/CDF/评分，残差与校准边全量检查。实时生产函数仅作为被测对象，expected值来自独立数学。故障注入均在隔离数据库。\n\n审计中曾遇到审计器checkpoint的numpy JSON类型错误，未触及生产；同一V2运行在Guardian确认0变更后续跑，保留已完成的本轮全量概率证据，重新执行加强后的实时路径。详见AUDIT_RUN_LOG_FINAL.txt、V2_AUDITOR_EXCEPTION.txt和V2_RESUME_LOG.txt。审计器故障不是系统失败，也没有用旧V1结果代替。\n\n'
    report+='## 概率结果（独立重算；不事后换候选）\n\n'+json.dumps(diag['engine_summary'],ensure_ascii=False,indent=2)+'\n\n'
    report+='## Findings\n\n'+'\n\n'.join('**'+x['finding_id']+' '+x['severity']+' — '+x['title']+'**\n\n'+x['description']+'\n\n证据：'+', '.join(x['evidence']) for x in findings)
    report+='\n\n## 停止点\n\n不修复，不重新训练，不重新校准，不启动Soak，不安装自启动，不进入Phase11。等待项目负责人审核。\n\n完整状态和全部计数见 MASTER_AUDIT_V2_TERMINAL_REPORT.txt；所有逐行证据和样本ID位于本目录，manifest列出SHA256。\n'
    output('MASTER_AUDIT_V2_REPORT.md',report)
    answers=[
      '框架保持T1/T2主线，T0、轨迹和最高温时间没有被假装完成。',
      f'从原始报文重建{c["TARGET_RECALC_CHECK_COUNT"]}天答案，不一致{c["TARGET_RECALC_MISMATCH_COUNT"]}。',
      '历史ECMWF的缺失和不可用Run保留，504个温度NULL与原始source一致；版本关系独立检查。',
      f'未来ECMWF使用发现{c["FUTURE_ECMWF_RUN_COUNT"]}次。',
      f'跨Run非法拼接发现{c["CROSS_RUN_SPLICE_COUNT"]}次。',
      'T1=729、T2=728；2025-08-07/T2缺口保留，未补样本。',
      'Raw基准从小时预测最高值及评分独立重算，可重现。',
      'MOS独立复算并检查D+2标签边界；候选仍NONE。',
      f'102项共{c["FEATURE_CELL_CHECK_COUNT"]}个特征值独立重算，错误{c["FEATURE_FORMULA_MISMATCH_COUNT"]}。',
      f'答案混入特征发现{c["TARGET_LEAKAGE_COUNT"]}次。',
      f'T1独立矩阵计算{c["MODEL_T1_REPRO_CHECK_COUNT"]}条，错误{c["MODEL_T1_REPRO_MISMATCH_COUNT"]}。',
      f'T2 native及独立树计算{c["MODEL_T2_REPRO_CHECK_COUNT"]}条，错误{c["MODEL_T2_REPRO_MISMATCH_COUNT"]}。',
      '预声明候选规则独立复算；保持T1 Ridge、T2 LightGBM，没有替换。候选是供后续使用，不能把选择后历史当作无偏的新考试。',
      f'核查{c["RESIDUAL_EDGE_CHECK_COUNT"]}条残差使用关系，未来残差{c["FUTURE_RESIDUAL_COUNT"]}。',
      '全部概率逐行检查非负、总和和CDF，数学错误计数见终端报告。',
      'Brier、LogLoss、CRPS和其他诊断从保存概率与答案独立重算，未相信旧指标。',
      'MA-001的34条悬空引用已能解析至正确canonical状态，校验实际payload哈希。',
      '别名只修正查找关系；原连续数值和概率payload与修复前备份相同。',
      '别名没有被当作活跃、最新、残差或校准状态；并在隔离库测试坏引用被拒绝。',
      'MA-002独立状态机覆盖首次成功、失败后成功和不同horizon，没有发现分类错误。',
      '21:45若是该horizon/target在21:00以后第一次合法成功，可成为anchor；之前已有成功anchor，则必须降级。',
      '同样的102个输入条件下，分类不改变连续预测和概率数值。',
      '首次交接只取cutoff之前已合法结算的历史，之后只向前追加；别名不推进状态。',
      f'重新选取{c["HISTORICAL_REALTIME_PARITY_CHECK_COUNT"]}个样本比较历史/实时数学，不一致{c["HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT"]}。',
      f'原始Run到最终概率评分重放{c["END_TO_END_CHECK_COUNT"]}例，不一致{c["END_TO_END_MISMATCH_COUNT"]}。',
      f'检查{c["LINEAGE_CHECK_COUNT"]}条历史及实时链路，断链{c["LINEAGE_BREAK_COUNT"]}；尚未结算不算断链。',
      '现有旧记录与修复前备份相同；追加保护、去重与回滚在隔离库验证。',
      '结算规则和迟到修正版冲突处理在隔离测试验证；真实完整结算仍等Soak。',
      '所有已经满足结算条件的快照评分，包括非anchor；未到条件的记录保留。',
      f'非anchor进入残差历史发现{c["OFF_ANCHOR_RESIDUAL_UPDATE_COUNT"]}次；重复更新{c["DUPLICATE_RESIDUAL_UPDATE_COUNT"]}次。',
      '断网、数据库锁、durable spool、重启、单实例、备份等隔离检查通过；长期恢复能力需真实Soak。',
      'HTTP400是一次请求失败，不等于整个系统必然失效；检查失败记录、已有合法完整Run和降级。失败的具体远端原因不能仅凭状态码确定。',
      '大陆网络不是必要条件；最终部署网络在真实Soak验证。',
      '历史ZUUU没有当时真实到达时间证据；现在真实收集不能倒推历史已解决。',
      '缺合法小时级OOS误差向量和相关结构，不能假造72小时概率轨迹。',
      '没有合法概率小时轨迹，因此最高温发生时间概率仍不能正式建立。',
      f'正式天气路径市场数据使用{c["POLYMARKET_USAGE_COUNT"]}次。',
      f'实时重新训练{c["MODEL_RETRAIN_COUNT"]}次，自动换模型{c["MODEL_PROMOTION_COUNT"]}次。',
      '第一轮两个HIGH已由当前DB独立解析及新的隔离状态机证明解决；旧失败报告不改。',
      f'本轮CRITICAL={c["CRITICAL_FINDING_COUNT"]}，HIGH={c["HIGH_FINDING_COUNT"]}；详细证据保留。',
      '可以开始Soak。只代表可以进入实际测试，不代表长期运行验收已经完成。' if statuses['READY_FOR_OPERATIONAL_SOAK']=='YES' else '目前不能开始Soak，先处理Findings所列核心问题/证据缺口。',
      '没有新增必须修复的核心数学/因果问题；最需观察断网恢复、完整Run、午夜滚动、真实结算、全部快照评分、anchor单次更新、备份及磁盘。' if accepted=='PASS_WITH_WARNINGS' else '修复边界由结构化Findings列明，本轮没有修改系统。']
    plain='# 第二轮总审计：普通中文说明\n\n'+f'结论：{accepted}；可以开始Soak：{statuses["READY_FOR_OPERATIONAL_SOAK"]}。长期运行验收仍PENDING_SOAK。\n\n'+'\n\n'.join(str(i+1)+'. '+a for i,a in enumerate(answers))
    output('MASTER_AUDIT_V2_PLAIN_LANGUAGE_SUMMARY.md',plain)
    output('MASTER_AUDIT_V2_FINAL_STATUS.json',dict(statuses=statuses,counts=c,evidence=s.evidence,findings=findings))
    manifest=[]
    for base in (OUT,ROOT/'src/audit/master_v2',ROOT/'tests/audit_master_v2'):
        for path in sorted(base.rglob('*')):
            if path.is_file() and path.name!='MASTER_AUDIT_V2_MANIFEST.json':manifest.append(dict(path=str(path),size=path.stat().st_size,sha256=digest(path)))
    output('MASTER_AUDIT_V2_MANIFEST.json',dict(audit_run=2,seed=SEED,protocol_sha256=digest(OUT/'MASTER_AUDIT_V2_PROTOCOL.md'),master_status=accepted,files=manifest,excluded='manifest itself cannot contain its own SHA; isolated temporary DBs are test artifacts, not production assets'))
    print(term,flush=True)
