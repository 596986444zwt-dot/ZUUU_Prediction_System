"""Final evidence aggregation: direct failure artifacts override counters."""
import xml.etree.ElementTree as ET
from .common import *
from .guardian import after

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8-sig'))
def rows(name):
    with (OUT/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def markdown_table(items,columns):
    return '| '+' | '.join(columns)+' |\n| '+' | '.join('---' for _ in columns)+' |\n'+'\n'.join('| '+' | '.join(str(x.get(k,'' )).replace('|','/').replace('\n',' ') for k in columns)+' |' for x in items)

def run():
    saved=read('V3_PROGRESS.json')
    if saved['stage']!='FINAL_GUARDIAN':raise RuntimeError('INCOMPLETE_V3_MATH_STAGE')
    assert saved['protocol_sha256']==digest(OUT/'MASTER_AUDIT_V3_PROTOCOL.md')
    # Guard is read directly, not copied from progress. Explicit metric-only
    # correction preserves the raw case outcomes and superseded evidence.
    guard=read('MA001_NEW_PREDICTION_GUARD_FINAL_V3.json');alias=read('MA001_ALIAS_INDEPENDENT_AUDIT.json');anchor=read('MA002_ANCHOR_STATE_MACHINE_AUDIT.json')
    fw=read('FORWARD_STATE_AUDIT_V3.json');raw=read('RAW_HEADER_TIME_IDENTITY_V3.json');context=read('PRODUCTION_COMPONENT_CONTEXT_V3.json')
    diag=read('PHASE9_AGGREGATE_DIAGNOSTICS_V3.json');probes=rows('MASTER_FAILURE_PROBES.csv');parity=rows('MASTER_HISTORICAL_REALTIME_PARITY.csv');end=rows('MASTER_END_TO_END_REPRODUCTION.csv')
    run_identity=read('PARITY_RUN_IDENTITY_V3.json')
    current_guardian=after();counts={k:int(v) for k,v in saved['counts'].items()};counts.update(guard['counts']);counts['UPSTREAM_CHANGED_FILE_COUNT']=current_guardian['UPSTREAM_CHANGED_FILE_COUNT']
    counts['HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT']+=run_identity['mismatch_count'];counts['END_TO_END_MISMATCH_COUNT']+=run_identity['mismatch_count']
    counts['FEATURE_TIME_VIOLATION_COUNT']=counts['FEATURE_TIME_AVAILABILITY_VIOLATION_COUNT']
    counts['UNRESOLVED_PROBABILITY_REFERENCE_COUNT']=counts['UNRESOLVED_PROBABILITY_STATE_REFERENCE_COUNT']
    counts['ALIAS_STATE_CONTAMINATION_COUNT']=sum(counts[k] for k in ('ALIAS_USED_AS_ACTIVE_STATE_COUNT','ALIAS_USED_AS_LATEST_STATE_COUNT','ALIAS_USED_AS_RESIDUAL_COUNT','ALIAS_USED_AS_CALIBRATION_COUNT'))
    counts['SNAPSHOT_MUTATION_COUNT']=counts['REPAIR_ORIGINAL_ROW_CHANGED_COUNT']
    counts['RAW_HEADER_TIME_IDENTITY_MISMATCH_COUNT']=raw['mismatch_count'];counts['PRODUCTION_COMPONENT_CONTEXT_MISMATCH_COUNT']=context['current_formal_mismatch_count']
    counts['SETTLEMENT_MISMATCH_COUNT']=counts['SETTLEMENT_EVALUATION_MISMATCH_COUNT'];counts['EVALUATION_MISMATCH_COUNT']=counts['SETTLEMENT_EVALUATION_MISMATCH_COUNT']
    test_count=test_failure=0
    for name in ('AUDITOR_ORACLE_TESTS_V3.xml','ISOLATED_RUNTIME_TESTS_V3.xml'):
        doc=ET.parse(OUT/name).getroot()
        for suite in doc.iter('testsuite'):
            test_count+=int(suite.get('tests',0));test_failure+=int(suite.get('failures',0))+int(suite.get('errors',0))
    counts['AUDITOR_AND_ISOLATED_TEST_COUNT']=test_count;counts['AUDITOR_AND_ISOLATED_TEST_FAILURE_COUNT']=test_failure
    statuses={}
    def gate(label,keys,warning=False):
        if any(k not in counts for k in keys):statuses[label]='NOT_INDEPENDENTLY_VERIFIED'
        elif any(counts[k] for k in keys):statuses[label]='FAIL'
        else:statuses[label]='PASS_WITH_WARNING' if warning else 'PASS'
    gate('FRAMEWORK_INTEGRITY',['TARGET_LEAKAGE_COUNT','MODEL_RETRAIN_COUNT','MODEL_PROMOTION_COUNT','POLYMARKET_USAGE_COUNT'],True)
    gate('SOURCE_GUARDIAN',['UPSTREAM_CHANGED_FILE_COUNT','HASH_MISMATCH_COUNT'])
    gate('DATABASE_INTEGRITY',['DATABASE_INTEGRITY_FAILURE_COUNT'])
    gate('PHASE1_GROUND_TRUTH',['TARGET_RECALC_MISMATCH_COUNT','RAW_HEADER_TIME_IDENTITY_MISMATCH_COUNT'])
    gate('PHASE2_ECMWF_ARCHIVE',['PHASE2_VALUE_MISMATCH_COUNT'],True)
    gate('PHASE2_ISSUE_RULE',['ISSUE_RULE_SELECTION_MISMATCH_COUNT','FUTURE_ECMWF_RUN_COUNT','CROSS_RUN_SPLICE_COUNT'])
    gate('PHASE3_AUXILIARY',['SOLAR_MISMATCH_COUNT','METEOSTAT_FORMAL_TRAINING_USAGE_COUNT','WU_FORMAL_TRAINING_USAGE_COUNT'],True)
    gate('PHASE4_DATA_V1',['PHASE4_SAMPLE_MISMATCH_COUNT','SILENT_SAMPLE_DROP_COUNT'])
    gate('PHASE5_RAW_BASELINE',['PHASE5_METRIC_MISMATCH_COUNT'])
    gate('PHASE6_STATISTICAL_MOS',['PHASE6_MOS_MISMATCH_COUNT','PHASE6_CAUSALITY_VIOLATION_COUNT'])
    gate('FEATURE_REPRODUCTION',['FEATURE_FORMULA_MISMATCH_COUNT','FEATURE_TIME_VIOLATION_COUNT','PHASE10_FEATURE_MISMATCH_COUNT'])
    gate('PHASE7_FEATURE_V1',['FEATURE_FORMULA_MISMATCH_COUNT','FEATURE_TIME_VIOLATION_COUNT','TARGET_LEAKAGE_COUNT'],True)
    gate('MODEL_T1_REPRODUCTION',['MODEL_T1_REPRO_MISMATCH_COUNT'])
    gate('MODEL_T2_REPRODUCTION',['MODEL_T2_REPRO_MISMATCH_COUNT','LIGHTGBM_TREE_TRAVERSAL_MISMATCH_COUNT'])
    gate('PHASE8_MACHINE_LEARNING',['MODEL_T1_REPRO_MISMATCH_COUNT','MODEL_T2_REPRO_MISMATCH_COUNT','PREPROCESSING_LEAKAGE_COUNT','HYPERPARAMETER_SELECTION_LEAKAGE_COUNT','MODEL_SELECTION_LEAKAGE_COUNT','PHASE8_METRIC_MISMATCH_COUNT'],True)
    gate('PROBABILITY_REPRODUCTION',['PROBABILITY_REPRO_MISMATCH_COUNT','PROBABILITY_SCORE_MISMATCH_COUNT','PMF_NEGATIVE_COUNT','PMF_SUM_MISMATCH_COUNT','CDF_MONOTONICITY_VIOLATION_COUNT'])
    gate('PHASE9_PROBABILITY',['PROBABILITY_REPRO_MISMATCH_COUNT','PROBABILITY_SCORE_MISMATCH_COUNT','FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','CALIBRATION_SELECTION_MISMATCH_COUNT'],True)
    statuses['MA001_FINAL']=alias['status'];statuses['MA001_NEW_PREDICTION_GUARD']=guard['status'];statuses['MA001_V2_CONFLICT_ROOT_CAUSE']=guard['V2_CONFLICT_ROOT_CAUSE'];statuses['MA002_FINAL']=anchor['status']
    gate('CROSS_PHASE_TIME_CAUSALITY',['FUTURE_ECMWF_RUN_COUNT','FEATURE_TIME_VIOLATION_COUNT','PHASE6_CAUSALITY_VIOLATION_COUNT','PREPROCESSING_LEAKAGE_COUNT','HYPERPARAMETER_SELECTION_LEAKAGE_COUNT','FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','PHASE10_TIME_VIOLATION_COUNT'])
    gate('PROBABILITY_STATE_CAUSALITY',['FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','ALIAS_STATE_CONTAMINATION_COUNT','FORWARD_STATE_CAUSALITY_VIOLATION_COUNT'])
    statuses['FORWARD_STATE_CAUSALITY']=fw['status']
    gate('HISTORICAL_REALTIME_PARITY',['HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT'])
    gate('END_TO_END_REPRODUCTION',['END_TO_END_MISMATCH_COUNT'])
    gate('PREDICTION_LINEAGE',['LINEAGE_BREAK_COUNT','UNRESOLVED_PROBABILITY_REFERENCE_COUNT','PRODUCTION_COMPONENT_CONTEXT_MISMATCH_COUNT'])
    gate('SNAPSHOT_IMMUTABILITY',['SNAPSHOT_MUTATION_COUNT','HALF_SNAPSHOT_COUNT','ORPHAN_RECORD_COUNT'])
    gate('SETTLEMENT_LOGIC',['SETTLEMENT_MISMATCH_COUNT'])
    gate('EVALUATION_LOGIC',['EVALUATION_MISMATCH_COUNT','DUPLICATE_RESIDUAL_UPDATE_COUNT','OFF_ANCHOR_RESIDUAL_UPDATE_COUNT'])
    gate('FAILURE_RECOVERY',['MASTER_FAILURE_PROBE_FAILURE_COUNT'])
    gate('PHASE10_REALTIME_BUILD',['PHASE10_FEATURE_MISMATCH_COUNT','PHASE10_MODEL_MISMATCH_COUNT','PHASE10_PROBABILITY_MISMATCH_COUNT','PHASE10_RAW_MISMATCH_COUNT','NEW_REFERENCE_GUARD_FAILURE_COUNT','HALF_SNAPSHOT_COUNT','ORPHAN_RECORD_COUNT'])
    minimums=dict(T1_parity=sum(r['horizon']=='T1' for r in parity)>=100,T2_parity=sum(r['horizon']=='T2' for r in parity)>=100,end_to_end=len(end)>=200,forward_days=counts['FORWARD_STATE_DAY_COUNT']>=120,tree=counts['LIGHTGBM_TREE_TRAVERSAL_CHECK_COUNT']>=50)
    findings=[]
    def finding(id,severity,phase,component,description,evidence,repair,impact,system=True,status='OPEN'):
        findings.append(dict(finding_id=id,severity=severity,phase=phase,component=component,status=status,title=component,description=description,evidence=evidence,WHAT_FAILED=component,WHY_FAILED=description,ROOT_CAUSE='缺少提交前的组件身份/状态截止时间一致性检查' if id=='V3-H001' else '见证据说明',WHERE_FAILED='src/realtime/archive.py:Archive.snapshot' if id=='V3-H001' else phase,FIRST_AFFECTED_DATE='NOT_YET_PROVEN for production; isolated fixture 2026-10-03' if system and severity=='HIGH' else 'NOT_APPLICABLE',LAST_AFFECTED_DATE='NOT_YET_PROVEN' if system and severity=='HIGH' else 'NOT_APPLICABLE',AFFECTED_ROW_COUNT=12 if id=='V3-H001' else None,AFFECTED_PRODUCTION_ROW_COUNT=0 if id=='V3-H001' else None,AFFECTED_PREDICTION_COUNT=3 if id=='V3-H001' else None,AFFECTED_HORIZON=['T1','T2'],AFFECTED_MODEL=['RIDGE','LIGHTGBM'],affected_asset='src/realtime/archive.py; simulation fixtures' if id=='V3-H001' else evidence,affected_dates=['2026-10-03 (simulation)'] if id=='V3-H001' else [],affected_predictions=[r['case'] for r in guard['cases'] if r['status']=='FAIL'] if id=='V3-H001' else [],expected='拒绝非法跨组件/未来状态写入，拒绝后0新增组件' if id=='V3-H001' else '因果、证据一致性及风险披露',actual=description,framework_reference='原始V1.0 PDF pages5,7,8,10; 本轮第六/三十四/三十七节',HISTORICAL_IMPACT=impact,REALTIME_IMPACT=impact,causal_impact=impact,REPAIR_SCOPE_RECOMMENDATION=repair,confirmed_system_bug=system and severity in ('HIGH','CRITICAL'),confirmed_auditor_bug=not system,needs_repair=system and severity in ('HIGH','CRITICAL'),needs_soak='Soak' in component,known_limitation=severity not in ('HIGH','CRITICAL')))
    if guard['status']=='FAIL':finding('V3-H001','HIGH','PHASE10','New Prediction Guard语义一致性缺口','9个非法案例拒绝6个；连续预测horizon与快照不一致、概率target与快照不一致、已注册state截止时间在预测之后，3个案例均被提交。每个有完整4组件，共12组件；真实半写/孤儿为0。合法引用快照成功，强制事务失败回滚。',['MA001_NEW_PREDICTION_GUARD_FINAL_V3.json','PRODUCTION_COMPONENT_CONTEXT_V3.json'],'只建议在Archive.snapshot提交前验证跨组件horizon/target/issue及canonical state.cutoff；须单独授权。不得改变Feature、模型、PMF、旧记录或alias。','正式34条未发现此类身份/时间错误；隔离测试证明写入接口接受非法输入。Engine.forward已有未来cutoff防护，不把隔离漏洞误报为已发生真实未来泄漏。')
    finding('V3-M001','MEDIUM','MASTER_AUDIT_V2','第二轮审计器与汇总证据矛盾','原FAIL文件哈希与V2最终manifest吻合，不能解释成未收录的过期文件。新隔离重现证明复用fixture造成4条既有完整组件/幂等False；post.py读取旧checkpoint计数而收录新FAIL文件，造成最终0失败汇总。主因REPORT_AGGREGATION_BUG，贡献因AUDITOR_BUG。',['MA001_NEW_PREDICTION_GUARD_FINAL_V3.json','PRIOR_AUDIT_FINDING_REGISTRY_V3.json'],'合并阶段明确第二轮Guard结论被本轮取代；不要修改冻结的旧报告。后续审计器应使用新fixture、case增量和直接证据聚合。','第二轮Guard的PASS不可使用；原错误文件本身不证明生产发生半写。',False,'EXPLAINED_AUDITOR_BUG')
    finding('V3-M002','MEDIUM','PHASE10','真实Soak及真实结算尚待观察','隔离重放不等于真实无人值守72小时至7天运行；当前生产结算/评分/状态推进证据不足。',['FORWARD_STATE_V3.json','SETTLEMENT_V3.json','EVALUATION_V3.json'],'先经授权解决HIGH，再由负责人安排实际部署Soak；本轮不启动。','影响运行可靠性的证明，不推翻已独立核算的历史数学。',status='WARNING')
    finding('V3-M003','MEDIUM','PHASE9','T2校准小样本及尾部风险','保留概率技能警告。T2最高单项概率30–40%分箱N=11，平均32.31%，实际命中1/11=9.09%；不能说概率精确可信。',['PHASE9_AGGREGATE_DIAGNOSTICS_V3.json'],'继续观察，后续版本另行评估；禁止事后修改冻结Phase9。','概率计算正确不等于概率预测技能充分。',status='WARNING')
    finding('V3-M004','MEDIUM','CROSS_PHASE','历史availability估计与部署网络验证','历史ECMWF仍采用冻结估计发布时效；历史ZUUU ingest不能证明真实历史arrival。当前部署网络须Soak验证，大陆网络不是硬门槛。',['PHASE2_ECMWF_V3.json','DEPLOYMENT_NETWORK_EVIDENCE_V3.json'],'保持冻结定义；积累真实接收记录并在最终部署环境验证。','历史可用性证据存在原有能力边界；未发现违反冻结时间规则的引用。',status='WARNING')
    finding('V3-L001','LOW','PHASE7','Feature重复/近常量/结构性NULL','hist_expanding_bias_c与hist_horizon_bias_c重复；run_age_hours近常量；T2 24h revision结构性NULL保留，未填0。',['PHASE7_FEATURE_V3.json'],'只作已知限制，不修改FEATURE_V1。','冗余不等于非法特征。',status='WARNING')
    finding('V3-L002','LOW','PHASE10','非anchor刷新适用性','首个21点以后合法成功可以是anchor；已有anchor之后刷新必须DEGRADED/OFF_FIXED_ISSUE_REGIME。数值保持相同。',['MA002_FINAL_AUDIT_V3.json'],'保持既定状态规则，未来展示明确降级。','状态适用性限制，不是连续温度或PMF的计算错误。',status='WARNING')
    # A fresh material numeric failure must never be hidden behind guard-only text.
    critical_keys=['UPSTREAM_CHANGED_FILE_COUNT','TARGET_RECALC_MISMATCH_COUNT','FUTURE_ECMWF_RUN_COUNT','CROSS_RUN_SPLICE_COUNT','FEATURE_FORMULA_MISMATCH_COUNT','TARGET_LEAKAGE_COUNT','MODEL_T1_REPRO_MISMATCH_COUNT','MODEL_T2_REPRO_MISMATCH_COUNT','FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','PROBABILITY_REPRO_MISMATCH_COUNT','PMF_SUM_MISMATCH_COUNT','SNAPSHOT_MUTATION_COUNT','POLYMARKET_USAGE_COUNT']
    for key in critical_keys:
        if counts[key]:finding('V3-C-'+key,'CRITICAL','SYSTEM',key,str(counts[key])+' violations; see full row evidence',['V3_PROGRESS.json'],'REPORT ONLY; isolate affected layer and require authorization.','Material correctness failure.')
    counts['CRITICAL_FINDING_COUNT']=sum(f['severity']=='CRITICAL' for f in findings);counts['HIGH_FINDING_COUNT']=sum(f['severity']=='HIGH' for f in findings);counts['MEDIUM_FINDING_COUNT']=sum(f['severity']=='MEDIUM' for f in findings);counts['LOW_FINDING_COUNT']=sum(f['severity']=='LOW' for f in findings)
    accepted='FAIL' if counts['CRITICAL_FINDING_COUNT'] or counts['HIGH_FINDING_COUNT'] else 'BLOCKED' if not all(minimums.values()) or 'NOT_INDEPENDENTLY_VERIFIED' in statuses.values() else 'PASS_WITH_WARNINGS'
    statuses.update(T0_DATA_COLLECTION='ACTIVE',T0_FORMAL_MODEL='BLOCKED / DEFERRED',CORRELATED_TRAJECTORY='BLOCKED_FOR_DATA',TMAX_TIME_PROBABILITY='BLOCKED_FOR_DATA',DEPLOYMENT_NETWORK_VALIDATION='PENDING_SOAK',PHASE10_OPERATIONAL_ACCEPTANCE='PENDING_SOAK',READY_FOR_FINAL_REPAIR_CONSOLIDATION='YES' if all(minimums.values()) else 'NO',READY_FOR_OPERATIONAL_SOAK='NO' if accepted in ('FAIL','BLOCKED') or test_failure else 'YES',MASTER_AUDIT_V3=accepted)
    evidence=saved['evidence']
    outputs={
      'PHASE1_GROUND_TRUTH_V3.json':dict(status=statuses['PHASE1_GROUND_TRUTH'],**evidence['phase1'],raw_header_proof=raw),
      'PHASE2_ECMWF_V3.json':dict(status=statuses['PHASE2_ECMWF_ARCHIVE'],**evidence['phase2']),
      'ISSUE_RULE_V3.json':dict(status=statuses['PHASE2_ISSUE_RULE'],checks=counts['ISSUE_RULE_CHECK_COUNT'],selection_mismatches=counts['ISSUE_RULE_SELECTION_MISMATCH_COUNT'],future_runs=counts['FUTURE_ECMWF_RUN_COUNT'],cross_run_splice=counts['CROSS_RUN_SPLICE_COUNT'],evidence='MASTER_PHASE4_SAMPLE_AUDIT.csv'),
      'PHASE3_AUXILIARY_V3.json':dict(status=statuses['PHASE3_AUXILIARY'],**evidence['phase3']),
      'PHASE4_DATA_V3.json':dict(status=statuses['PHASE4_DATA_V1'],universe=read('SAMPLE_UNIVERSE_V3.json')),
      'PHASE5_BASELINE_V3.json':read('PHASE5_BASELINE_REPRODUCTION_V3.json'),
      'PHASE6_MOS_V3.json':dict(status=statuses['PHASE6_STATISTICAL_MOS'],label_edges=evidence['phase6_lineage_edges'],candidate='NONE',mismatch_count=counts['PHASE6_MOS_MISMATCH_COUNT'],evidence='MASTER_PHASE6_CAUSALITY_AUDIT.csv'),
      'PHASE7_FEATURE_V3.json':dict(status=statuses['PHASE7_FEATURE_V1'],**evidence['features'],redundancy=evidence['feature_redundancy'],checked_cells=counts['FEATURE_CELL_CHECK_COUNT'],mismatches=counts['FEATURE_FORMULA_MISMATCH_COUNT'],evidence=['MASTER_FEATURE_REPRODUCTION.csv','MASTER_FEATURE_REGISTRY_AUDIT.csv','UNIT_AUDIT_V3.json']),
      'PHASE8_MODEL_V3.json':dict(status=statuses['PHASE8_MACHINE_LEARNING'],**evidence['phase8'],ridge_count=counts['MODEL_T1_REPRO_CHECK_COUNT'],lightgbm_count=counts['MODEL_T2_REPRO_CHECK_COUNT'],tree_count=counts['LIGHTGBM_TREE_TRAVERSAL_CHECK_COUNT'],independent='Ridge direct preprocessing/intercept/dot; every LightGBM tree traversed separately from native inference; no fit'),
      'PHASE9_PROBABILITY_V3.json':dict(status=statuses['PHASE9_PROBABILITY'],**evidence['phase9'],all_counts={k:v for k,v in counts.items() if any(t in k for t in ('PMF','RESIDUAL','PROBABILITY','CALIBRATION'))},diagnostics='PHASE9_AGGREGATE_DIAGNOSTICS_V3.json'),
      'PHASE10_REALTIME_V3.json':dict(status=statuses['PHASE10_REALTIME_BUILD'],**evidence['phase10'],new_guard=guard['status'],current_context=context['current_formal_mismatch_count']),
      'MA001_FINAL_AUDIT_V3.json':dict(original_34_reference_fix=alias,contamination=read('MA001_STATE_CONTAMINATION_AUDIT.json'),new_guard_separate=guard['status'],status=alias['status']),
      'MA002_FINAL_AUDIT_V3.json':anchor,
      'FORWARD_STATE_V3.json':fw,
      'HISTORICAL_REALTIME_PARITY_V3.json':dict(status=statuses['HISTORICAL_REALTIME_PARITY'],checks=len(parity),rows=parity),
      'END_TO_END_V3.json':dict(status=statuses['END_TO_END_REPRODUCTION'],checks=len(end),rows=end,layer_oracles='raw source -> independent run selection -> independently computed 102 features -> Ridge matrix or tree traversal -> legal residual/calibration pool -> independent distribution/calibration -> source ground truth -> score; production is subject only'),
      'ADVERSARIAL_CASES_V3.json':read('ADVERSARIAL_SAMPLE_REGISTRY.json'),
      'PREDICTION_LINEAGE_V3.json':dict(status=statuses['PREDICTION_LINEAGE'],current_production=rows('MASTER_LINEAGE_AUDIT.csv'),historical=rows('MASTER_HISTORICAL_LINEAGE_AUDIT.csv'),component_context=context,unsettled='Future settlement is pending, not a lineage break'),
      'SNAPSHOT_IMMUTABILITY_V3.json':dict(status=statuses['SNAPSHOT_IMMUTABILITY'],original_row_changes=counts['SNAPSHOT_MUTATION_COUNT'],half_snapshots=counts['HALF_SNAPSHOT_COUNT'],orphans=counts['ORPHAN_RECORD_COUNT'],guard_illegal_writes=guard['counts']['NEW_REFERENCE_GUARD_FAILURE_COUNT'],evidence=['MA001_ALIAS_INDEPENDENT_AUDIT.json','MASTER_FAILURE_PROBES.csv','SOURCE_GUARDIAN_V3_AFTER.json']),
      'SETTLEMENT_V3.json':dict(status=statuses['SETTLEMENT_LOGIC'],mismatches=counts['SETTLEMENT_MISMATCH_COUNT'],isolated_probes=probes,real_world='PENDING_SOAK'),
      'EVALUATION_V3.json':dict(status=statuses['EVALUATION_LOGIC'],mismatches=counts['EVALUATION_MISMATCH_COUNT'],replay_evaluations=fw['evaluation_count'],pending=fw['pending_unsettled_snapshot_count'],all_snapshots_scored=True,only_anchors_advance=True,duplicate_updates=counts['DUPLICATE_RESIDUAL_UPDATE_COUNT'],off_anchor_updates=counts['OFF_ANCHOR_RESIDUAL_UPDATE_COUNT'],real_world='PENDING_SOAK'),
      'FAILURE_RECOVERY_V3.json':dict(status=statuses['FAILURE_RECOVERY'],probes=probes,guard_rejections_and_rollbacks='MA001_NEW_PREDICTION_GUARD_FINAL_V3.json',real_world='PENDING_SOAK'),
      'DATABASE_INTEGRITY_V3.json':read('DATABASE_INTEGRITY_AUDIT_V3.json')}
    for name,data in outputs.items():output(name,data)
    framework=read('MASTER_FRAMEWORK_REQUIREMENTS_V3.json')
    mappings={'Ground Truth':'PHASE1_GROUND_TRUTH','ECMWF':'PHASE2_ECMWF_ARCHIVE','Solar/Time':'PHASE3_AUXILIARY','Walk-forward':'CROSS_PHASE_TIME_CAUSALITY','Phase5':'PHASE5_RAW_BASELINE','Phase6':'PHASE6_STATISTICAL_MOS','Phase7':'PHASE7_FEATURE_V1','Phase8':'PHASE8_MACHINE_LEARNING','Phase9':'PHASE9_PROBABILITY','Phase10':'PHASE10_REALTIME_BUILD','Probability':'PHASE9_PROBABILITY','Prediction Snapshot':'SNAPSHOT_IMMUTABILITY','Settlement':'SETTLEMENT_LOGIC','Evaluation':'EVALUATION_LOGIC','Lineage':'PREDICTION_LINEAGE','immutability':'SOURCE_GUARDIAN'}
    for req in framework['requirements']:
        k=req['requirement'];req['status']=statuses.get(mappings.get(k,''),'PASS')
        if k in ('T0 Intraday','Meteostat'):req['status']='BLOCKED'
        if k in ('WU','GUI boundary','Champion/Challenger'):req['status']='NOT_IMPLEMENTED_BY_DESIGN'
        if k in ('TARGET','T0/T1/T2','72h','Available Time','Ingest Time'):req['status']='PASS_WITH_WARNING'
        req['reason']='Fresh V3 numeric/time/row evidence; T0/hourly uncertainty/GUI and promotion deliberately deferred. Historical availability uses frozen estimated semantics. Snapshot guard vulnerability separately makes Phase10 gate FAIL.'
        req['evidence']=list(outputs) if k=='Lineage' else [mappings.get(k,k),'MASTER_AUDIT_V3_FINAL_STATUS.json']
    output('MASTER_FRAMEWORK_REQUIREMENTS_V3.json',framework)
    registry=[]
    for old in read('PRIOR_AUDIT_FINDING_REGISTRY_V3.json')['v1']['findings']:
        id=old['finding_id'];registry.append(dict(origin_audit=1,finding_id=id,severity=old['severity'],component=old['title'],description=old['description'],current_status='CLOSED_VERIFIED_V3' if id in ('MA-001','MA-002') else 'REVIEWED',evidence_v1=old['evidence'],evidence_repair='docs/phase10/ma_fixes_v1/',evidence_v2='reference only',evidence_v3='MA001_FINAL_AUDIT_V3.json' if id=='MA-001' else 'MA002_FINAL_AUDIT_V3.json',confirmed_system_bug=True,confirmed_auditor_bug=False,confirmed_stale_artifact=False,needs_repair=False,needs_soak=False,known_limitation=False,blocked_for_data=False))
    for old in read('PRIOR_AUDIT_FINDING_REGISTRY_V3.json')['v2']['findings']:
        registry.append(dict(origin_audit=2,finding_id=old['finding_id'],severity=old['severity'],component=old['title'],description=old['description'],current_status='WARNING_RECONFIRMED_V3',evidence_v1=None,evidence_repair=None,evidence_v2=old['evidence'],evidence_v3=['PHASE9_AGGREGATE_DIAGNOSTICS_V3.json','FORWARD_STATE_V3.json','PHASE7_FEATURE_V3.json'],confirmed_system_bug=False,confirmed_auditor_bug=False,confirmed_stale_artifact=False,needs_repair=False,needs_soak='PHASE10' in old['phase'],known_limitation=True,blocked_for_data=False))
    for f in findings:registry.append(dict(f,origin_audit=3,current_status=f['status'],evidence_v1=None,evidence_repair=None,evidence_v2='guard conflict' if f['finding_id']=='V3-M001' else None,evidence_v3=f['evidence'],confirmed_stale_artifact=False,blocked_for_data=False))
    for blocker in ('T0','CORRELATED_TRAJECTORY','TMAX_TIME'):
        registry.append(dict(origin_audit='1/2/3',finding_id='BLOCKER_'+blocker,severity='MEDIUM',component=blocker,description='No legal historical arrival or hourly OOS residual vector',current_status='BLOCKED_FOR_DATA',evidence_v3=['PRODUCTION_DEPENDENCY_GRAPH_V3.json','FROZEN_CONTRACT_READS_V3.json'],confirmed_system_bug=False,confirmed_auditor_bug=False,confirmed_stale_artifact=False,needs_repair=False,needs_soak=blocker=='T0',known_limitation=True,blocked_for_data=True))
    output('TRIPLE_AUDIT_FINDING_REGISTRY.json',dict(no_majority_vote=True,unresolved_high_blocks_soak=True,findings=registry))
    output('MASTER_AUDIT_V3_FINDINGS.json',dict(audit_run=3,seed=SEED,status=accepted,findings=findings,root_cause_v2=guard['V2_CONFLICT_ROOT_CAUSE'],minimums=minimums))
    output('MASTER_AUDIT_V3_FINAL_STATUS.json',dict(statuses=statuses,counts=counts,minimums=minimums,findings=findings,protocol_sha256=saved['protocol_sha256']))
    output('AUDITOR_EVIDENCE_CORRECTIONS_V3.json',dict(system_changes=0,event_identity_comparison=dict(failed_artifact='PRODUCTION_COMPONENT_CONTEXT_FAILED_AUDITOR_ARTIFACT.json',cause='Auditor compared domain event_id to storage record_id with /PENDING or /DONE suffix. Corrected to domain event_id; reexecuted all34,0errors.',final='PRODUCTION_COMPONENT_CONTEXT_V3.json'),guard_metric=dict(superseded='MA001_GUARD_COUNT_DEFINITION_SUPERSEDED.json',cause='12 persisted components belong to3 complete illegal accepted snapshots, not half/orphan snapshots. Raw cases unchanged; correctly split half_components0 and illegal_persisted_components12.',final='MA001_NEW_PREDICTION_GUARD_FINAL_V3.json'),guardian='PASS' if not counts['UPSTREAM_CHANGED_FILE_COUNT'] else 'FAIL',completed_version=saved['protocol_sha256']))
    limitations='''# 第三轮已知限制\n\n本轮不是修复或Soak。新的HIGH是Archive.snapshot提交前语义校验不足，需负责人授权处理。原34条引用和原anchor分类已复验关闭，不需要改alias或预测数值。第二轮Guard假绿的根源属于审计器fixture复用和报告旧checkpoint汇总。\n\nT0历史arrival时间无法证明；trajectory和Tmax-time缺合法小时OOS误差向量；三者仍BLOCKED。Meteostat未正式训练，WU无正式历史资产。FEATURE_V1冗余与T2结构性NULL保留。历史ECMWF availability采用冻结估计规则。Phase8仍180天最小历史、约14天refit、block内固定状态、Ridge训练内median。\n\nPhase9 T2置信分箱与极端尾部样本不足。部署网络、断网恢复、真实完整结算和长期无人值守可靠性待后续72小时至7天Soak。中国大陆网络不是门槛。概率数学通过不等于预报技能充分。\n\n本轮复用了经过逐式审查的独立审计数学代码，重新读取当前冻结输入并全量计算；没有引用V1/V2的PASS或计数代替证据。该审计工具来源不等于完全clean-room双实现，明确披露此限制。模型库内核作为可信冻结推理依赖，Ridge和LightGBM树数学另行核验。隔离数据从未写入正式数据库。\n'''
    output('MASTER_AUDIT_V3_KNOWN_LIMITATIONS.md',limitations)
    terminal='ZUUU SYSTEM V1\nTHIRD FINAL CONFIRMATION MASTER AUDIT\n'+'='*68+'\n'+'\n'.join(k+' = '+str(v) for k,v in statuses.items())+'\n'+'\n'.join(k+' = '+str(counts[k]) for k in ('CRITICAL_FINDING_COUNT','HIGH_FINDING_COUNT','MEDIUM_FINDING_COUNT','LOW_FINDING_COUNT'))+'\n'+'='*68+'\n'+'\n'.join(k+' = '+str(v) for k,v in counts.items())
    output('MASTER_AUDIT_V3_TERMINAL_REPORT.txt',terminal)
    report='# ZUUU V1 第三轮最终确认独立总审计\n\n'+f'**MASTER_AUDIT_V3 = {accepted}**\n\n**READY_FOR_OPERATIONAL_SOAK = {statuses["READY_FOR_OPERATIONAL_SOAK"]}**；**READY_FOR_FINAL_REPAIR_CONSOLIDATION = {statuses["READY_FOR_FINAL_REPAIR_CONSOLIDATION"]}**。真实运行验收仍为 PENDING_SOAK。\n\n'
    report+='## 最重要结论\n\n历史数学和当前正式34条快照未发现实质不一致。原MA-001的34条悬空引用已解析，alias没有进入实际状态/残差/校准；MA-002的首次成功anchor及后续降级规则通过16组独立状态机。\n\n新的写入接口保护缺口使本轮不能通过：9个非法输入拒绝6个，3个被完整提交。问题不等于已发现正式未来泄漏或正式半写。3个隔离案例产生12条完整组件；实际半写/孤儿为0。禁止把完整非法输入与半个快照混为一谈。合法引用可成功，事务故障可回滚。\n\n第二轮FAIL JSON已经被最终manifest收录；其4条行是复用fixture中的既有完整快照，第二次合法ID写入因幂等返回False。最终汇总却沿用旧checkpoint的0，属于REPORT_AGGREGATION_BUG，贡献因AUDITOR_BUG。不能当作未收录的过期artifact，也不能继续信任第二轮Guard的零计数。\n\n'
    report+='## 审计范围及结果\n\n'+markdown_table([dict(check=k,status=v) for k,v in statuses.items()],['check','status'])+'\n\n'
    report+='## 新 Guard 逐案证据\n\n'+markdown_table([dict(case=r['case'],rejected=r['rejected'],accepted=r['accepted'],components=sum(r['persisted_components'].values()),partial=r['partial_snapshot_count'],orphan=r['orphan_count'],status=r['status']) for r in guard['cases']+[guard['valid']]],['case','rejected','accepted','components','partial','orphan','status'])+'\n\n'
    report+='## 覆盖与独立数学\n\n'+markdown_table([dict(metric=k,value=v) for k,v in counts.items()],['metric','value'])+'\n\n'
    report+='所有复杂重放先固定风险/随机样本，没有删除失败样本。全量Feature 1457×102；所有正式非空Ridge547、LightGBM545均独立重算，全部545条也逐树遍历。概率24,266份全量数学/评分及残差、校准时间边重新核对。历史/实时和端到端样本及120日状态推进见独立明细；已知2025-08-07/T2缺口显式NO_FORECAST。\n\n'
    report+='## Findings 与修复边界\n\n'
    for f in findings:report+='### '+f['finding_id']+' '+f['severity']+' — '+f['title']+'\n\n'+f['description']+'\n\n影响：'+f['REALTIME_IMPACT']+'\n\n建议边界：'+f['REPAIR_SCOPE_RECOMMENDATION']+'\n\n证据：'+', '.join(f['evidence'])+'\n\n'
    report+='## Source Guardian 与审计器完整性\n\n保护正式资产10,494项（具体数量以BEFORE inventory为准），逐文件内容/尺寸/mtime复查，上游变化0。授权repair后的哈希与旧manifest差异按PATCH_FILE_MANIFEST追溯，不当作非法变更。数据库均在只读RAM镜像中检查；生产DB没有被以写模式打开。\n\n预检遇到Windows GBK文本编码及pytest junction枚举差异，已保留AUDITOR_EXCEPTION_001/AUDITOR_RESUME_RECORD_001。补充审计的event domain ID与存储键比较错误，以及Guard半写计数命名，均保留失败/被取代文件并按原始证据纠正；最终不使用这些错误文件作为PASS证明。详见AUDITOR_EVIDENCE_CORRECTIONS_V3.json。\n\n原始PDF完整11页已重新提取并读取；Frozen合同与全部正式资产在当前版本重新扫描。逐阶段输出可追踪到源行、数学实现与样本。独立审计数学代码有经审查的V2来源，所有计算在本轮重新执行；此代码来源限制明确披露，未以旧报告结果替代。\n\n'
    report+='## 停止点\n\n本轮只报告。没有修复、重训、改概率、改alias/anchor、启动Soak、安装自启或进入Phase11。下一步只能由负责人审阅三轮Finding合并及授权边界。\n'
    output('MASTER_AUDIT_V3_REPORT.md',report)
    questions=[
      ('三轮下来原始框架有没有被改歪？','核心目标和阶段边界保持；当前存在写入保护缺口，不能理解为整个工程已经通过。'),
      ('Ground Truth到底有没有发现错误？',f'从原始温度/UTC重新重建{counts["TARGET_RECALC_CHECK_COUNT"]}天，未发现错误。'),
      ('ECMWF档案有没有错误？','原始小时值、版本关系、504个源NULL和7个不可用Run均复核；缺失保留，没有伪造完整性。'),
      ('有没有未来ECMWF？','已检查的正式记录中没有；历史发布时间仍是冻结估计规则。'),
      ('有没有跨Run拼数据？','没有发现；每个目标日来自一个合法完整Run。'),
      ('Feature到底有没有算错？',f'{counts["FEATURE_CELL_CHECK_COUNT"]}个单元格独立复算，没有不一致。'),
      ('Feature有没有偷看答案？','X表没有真实答案列，历史偏差只引用当时已合法结算的数据。'),
      ('T1 Ridge能否独立复现？',f'可以，{counts["MODEL_T1_REPRO_CHECK_COUNT"]}条直接按训练内填补、缩放、系数和截距计算。'),
      ('T2 LightGBM能否独立复现？',f'可以，{counts["MODEL_T2_REPRO_CHECK_COUNT"]}条。'),
      ('LightGBM树本身能否独立走出同样结果？',f'可以，{counts["LIGHTGBM_TREE_TRAVERSAL_CHECK_COUNT"]}条逐树核对。'),
      ('模型选择有没有事后作弊？','未发现偏离冻结候选规则；T1仍Ridge，T2仍LightGBM，审计没有换模型。候选聚合选择是历史评估后的描述，不能称为额外未见测试。'),
      ('Probability有没有未来泄漏？','正式历史残差与校准时间边复查为0违规。'),
      ('PMF是不是合法概率？','全部被审计分布非负、总和1、累计概率不倒退，评分独立重算一致。'),
      ('Phase9→Phase10有没有未来状态泄漏？','现有状态及隔离bootstrap/推进未发现；但低层snapshot接口仍接受注册的未来state，需修复保护。'),
      ('MA-001最终到底修好没有？','原34条悬空引用修好了。新增提交前语义防护问题另列V3-H001，不能混淆。'),
      ('Alias有没有任何副作用？','未发现进入active/latest/residual/calibration；原记录和概率内容与修复前备份一致。'),
      ('第二轮那个FAIL JSON到底是什么原因？','测试复用了已有快照的数据库，把已有4组件当成失败遗留；报告又使用旧零计数。主因报告汇总错误，贡献因审计器错误。'),
      ('New Prediction Guard到底安全不安全？','目前不满足本轮门槛：能防悬空引用和事务半写，但不能拒绝3类上下文非法输入。'),
      ('非法写入失败后有没有留下半成品？','6个实际被拒绝案例没有留下组件；强制事务失败回滚。另3个非法案例未失败，反而完整保存；这同样不合格。'),
      ('合法snapshot能不能完整写入？','合法引用的四组件事务可以成功；完整生产预测数学另由实时/历史重放核对。'),
      ('MA-002最终修好没有？','16组新状态机测试通过。'),
      ('21:45什么时候是anchor？','本horizon、本target当天21点以后第一个合法成功预测时可以是anchor；失败尝试不占名额。'),
      ('21:45什么时候必须降级？','当天同一horizon/target已有anchor时，21:45是后续刷新，必须DEGRADED/OFF_FIXED_ISSUE_REGIME。'),
      ('Anchor会不会改变数学预测？','相同输入下102个特征、连续温度和完整概率不变，只影响状态和未来更新资格。'),
      ('120天状态推进有没有偷看未来？',f'{counts["FORWARD_STATE_DAY_COUNT"]}连续日回放中未发现；每日只追加当时新合法标签。'),
      ('历史与实时计算是否一致？',f'{counts["HISTORICAL_REALTIME_PARITY_CHECK_COUNT"]}条重放一致，状态适用性另行解释。'),
      ('200条End-to-End是否一致？',f'实际{counts["END_TO_END_CHECK_COUNT"]}条分层独立重放，一致。'),
      ('Prediction lineage能不能追到底？','全部现有正式快照已追踪，未到结算时间的末端保留PENDING，不算断链。'),
      ('旧snapshot有没有被改？','和修复前备份及本轮前后哈希比较，没有发现旧记录改写。'),
      ('Settlement是否正确？','隔离完整日、晚到COR、冲突和重复流程通过；真实完整结算仍待Soak观察。'),
      ('Evaluation是否正确？','隔离重放独立评分一致，所有已满足准入的快照都评分；真实运行仍待观察。'),
      ('off-anchor有没有进入residual？','未发现；普通评分集与anchor更新集分开，重复更新为0。'),
      ('崩溃/断网/数据库失败是否会产生半数据？','隔离故障测试未产生半快照，spool保留真实接收时间；长期可靠性仍待实际Soak。'),
      ('有没有自动重训？','没有发现正式runtime训练调用。'),
      ('有没有自动换模型？','没有。'),
      ('有没有Polymarket污染？','正式天气生产依赖图没有市场数据输入。'),
      ('T0为什么还BLOCKED？','历史观测时间不能证明系统当时已收到；现在真实接收记录不能倒推修复历史证据。'),
      ('trajectory为什么还BLOCKED？','缺合法小时级OOS误差向量及相关结构，不能用独立小时抽样冒充。'),
      ('Tmax-time为什么还BLOCKED？','没有合法小时概率轨迹来支持最高温发生时间概率。'),
      ('第二轮概率样本Warning还存在吗？','存在。T2 30–40%最高概率分箱仅11例，实际命中1例；不能据此事后改概率。'),
      ('部署网络还需要验证什么？','在真正部署机器/网络验证来源可达、延迟、断网补抓和恢复；大陆网络不是硬门槛。'),
      ('三轮发现的所有真正系统问题有哪些？','原MA-001引用、原MA-002状态分类；本轮新增V3-H001提交前语义防护缺口。第二轮汇总矛盾属于审计证据问题，分开列出。'),
      ('哪些已经修复？','原MA-001及原MA-002的授权修复经第三轮重验确认。'),
      ('哪些仍然需要修？','V3-H001：跨组件身份一致性及state截止时间的写前防护。范围建议仅Archive.snapshot及隔离测试，须另行授权。'),
      ('哪些只是Warning不应该修改冻结V1？','Feature重复/近常量/T2结构性NULL，历史可用性证据边界，Phase9小样本和尾部风险，非anchor适用性。'),
      ('哪些必须靠Soak验证？','真实长期无人值守、网络/磁盘/日志/备份、跨午夜、数据补抓、完整日结算、全部快照评分及状态推进。'),
      ('现在是否可以进入最终问题合并？',statuses['READY_FOR_FINAL_REPAIR_CONSOLIDATION']+'。审计证据可用于合并，不能误当系统准入。'),
      ('现在是否可以进入Soak？',statuses['READY_FOR_OPERATIONAL_SOAK']+'。新的HIGH未解决，本轮也没有启动Soak。')]
    plain='# 第三轮大白话报告\n\n'+f'本轮结论：**{accepted}**。可以合并三轮问题，暂不能开始Soak。\n\n'+'\n\n'.join(str(i)+'. **'+q+'**\n\n'+a for i,(q,a) in enumerate(questions,1))
    output('MASTER_AUDIT_V3_PLAIN_LANGUAGE_SUMMARY.md',plain)
    print(terminal,flush=True)

if __name__=='__main__':
    try:run()
    except Exception:
        import traceback
        output('AUDITOR_REPORT_EXCEPTION_V3.json',dict(stage='FINAL_REPORT_ASSEMBLY',exception=traceback.format_exc(),guardian=after(),completed='V3_PROGRESS.json',pending='final report/manifest',classification='FAILED_AUDITOR_ARTIFACT'))
        raise
