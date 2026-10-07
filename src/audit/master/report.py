"""Evidence-bound final publication; report defects without repairing production."""
import csv
import json
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from .common import *

def finding(fid,severity,phase,component,title,description,evidence,expected,actual,status='FAIL',recommendation='由项目负责人决定是否另开修复任务；本次审计不修复。'):
    if severity not in ('CRITICAL','HIGH','MEDIUM','LOW','INFO'):raise ValueError('INVALID_FINDING_SEVERITY')
    return dict(finding_id=fid,severity=severity,phase=phase,component=component,status=status,title=title,description=description,evidence=evidence,affected_asset=evidence.get('asset'),affected_dates_samples=evidence.get('samples'),expected=expected,actual=actual,framework_reference='V1.0: Data Lineage / then-available-only / immutable Prediction; Master Audit acceptance sections72–73',recommendation=recommendation)

def table(rows,columns=None):
    if not rows:return '无记录。'
    keys=columns or list(rows[0]);lines=['| '+' | '.join(keys)+' |','| '+' | '.join('---' for _ in keys)+' |']
    for row in rows:lines.append('| '+' | '.join(str(row.get(k,'')).replace('|','/').replace('\n',' ') for k in keys)+' |')
    return '\n'.join(lines)

def finish(s,f,m,p,rt):
    from .asset_audit import after
    from .hash_audit import audit_hashes
    from .static_audit import audit_static,framework
    import numpy as np
    audit_hashes(s);audit_static(s);requirements=framework()
    c=s.counts;e=s.evidence;findings=[]
    alias=rt.notes[0];refs=alias['unresolved_probability_reference']
    if refs:
        findings.append(finding('MA-001','HIGH',10,'probability_state_lineage','正式概率的状态引用没有对应状态记录',
            '概率 payload 的 probability_state_version 是另一种内容指纹；快照 metadata 保存了真实 state_id。两者不同，概率字段无法直接关联 realtime_probability_state。通过父快照仍能定位状态，34条概率数学复算正确；不能因此把冲突字段称作完整一致的血缘。',
            dict(asset='database/phase10_realtime_v1.db',samples=alias['examples'],source='src/realtime/handoff.py forward; src/realtime/engine.py predict_event',report='MASTER_PROBABILITY_STATE_REFERENCE_AUDIT.csv'),
            'Probability state version resolves to same immutable state as snapshot',dict(unresolved_probability_reference=refs,different=alias['different'])))
    probe=next((r for r in rt.probes_results if r['probe']=='off_exact21_issue_status'),None)
    if probe and probe['status']=='FAIL':
        findings.append(finding('MA-002','HIGH',10,'refresh_applicability_status','21点小时内非锚点刷新仍保存为OK',
            '冻结 Phase10 Protocol 要求 off-anchor intraday refresh 为 DEGRADED。正式代码只判断 hour!=21。隔离模拟先产生21:30锚点，再产生21:45非锚点刷新；后者T1/T2都为OK。该测试用受控推理桩只检查状态编排，不用于证明模型准确率。现有34条真实快照均已降级，尚未观察到这个生产触发场景。',
            dict(asset='src/realtime/engine.py',samples=probe['actual'],report='MASTER_FAILURE_PROBES.csv',protocol='docs/phase10/PHASE10_PROTOCOL_V1.md'),
            '21:45 non-anchor refresh DEGRADED / OFF_FIXED_ISSUE_REGIME',probe['actual']))
    for row in e['hashes']['mismatches']:
        cache=row['asset_role']=='EPHEMERAL_TEST_CACHE_NOT_FROZEN_PREDICTION_ASSET'
        findings.append(finding('MA-HASH-'+str(len(findings)+1),'LOW' if cache else 'CRITICAL',6 if cache else 0,'manifest_hash','旧清单包含变化的测试缓存' if cache else '正式资产哈希不匹配',
            '旧Phase6清单误将pytest运行缓存列入文件列表。缓存的变化不是TARGET、Feature、模型或数据库变更；本次未写该缓存，Before/After仍检查受保护文件。' if cache else '独立按冻结投影或文件清单重算不符，不自动归因SQLite metadata。',
            dict(asset=row['asset'],manifest=row.get('manifest'),report='MASTER_HASH_COMPARISON.csv'),row['expected'],row['actual'],status='PASS_WITH_WARNING' if cache else 'FAIL'))
    known=[('MA-003','MEDIUM',10,'operational_evidence','长期运行尚未验收','正式DB仅34条快照，真实evaluation为0，实际probability state只有bootstrap1条。隔离模拟证明结算、评分、锚点更新、锁/事务/恢复机制，不等于72小时–7天真实Soak。','PENDING_SOAK'),
           ('MA-004','MEDIUM',9,'probability_sample','T2校准和尾部样本有限','保留Phase9警告：T2 Top1 30–40%档11次，平均预报32.31%、命中9.09%；高置信60%/80%档无样本。不能承诺校准精度或极端尾部概率已充分验证。','PASS_WITH_WARNING'),
           ('MA-005','MEDIUM',10,'network','实时ECMWF入口和大陆网络未充分验证','正式资料保留最新Run请求HTTP400、较旧RunHTTP200的实例；生产快照使用已收到完整Run并降级。请求根因与大陆持续可达性尚不能从有限smoke证明。','PASS_WITH_WARNING'),
           ('MA-006','LOW',7,'feature_redundancy','Feature V1存在安全冗余和结构性缺失','hist_expanding_bias_c与hist_horizon_bias_c完全重复，run_age_hours近常量，T2 24h revision结构性NULL。没有修改FEATURE_V1；各训练窗口的排除/填补已独立检查。','PASS_WITH_WARNING'),
           ('MA-007','MEDIUM',2,'historical_availability','历史ECMWF availability仍为估计发布语义','全部available<=issue和Run选择在冻结estimated dissemination合同内成立；不据此声称观测到了历史真实接收时刻。','PASS_WITH_WARNING')]
    for fid,sev,phase,comp,title,desc,status in known:findings.append(finding(fid,sev,phase,comp,title,desc,dict(asset=f'docs/phase{phase}',samples='See phase evidence and master numerical CSVs'),status,status,status='PASS_WITH_WARNING'))
    formaldb=sorted((ROOT/'database').glob('*.db'));dbrows=[]
    for path in formaldb:
        db=s.get(path.name);integrity=[r[0] for r in db.execute('PRAGMA integrity_check')];fk=list(db.execute('PRAGMA foreign_key_check'))
        dbrows.append(dict(database=str(path),integrity=integrity,foreign_key_count=len(fk),status='PASS' if integrity==['ok'] and not fk else 'FAIL',scope='FORMAL_ROOT_DATABASE'))
    output('MASTER_DATABASE_INTEGRITY.csv',dbrows);c['DATABASE_COUNT']=len(dbrows);c['DATABASE_INTEGRITY_FAILURE_COUNT']=sum(r['status']=='FAIL' for r in dbrows)
    # Sources and units checked against actual raw JSON, independently declared expected API units.
    expected_units=dict(zip(('temperature_2m','dew_point_2m','relative_humidity_2m','surface_pressure','pressure_msl','cloud_cover','cloud_cover_low','cloud_cover_mid','cloud_cover_high','wind_speed_10m','wind_direction_10m','wind_gusts_10m','shortwave_radiation','direct_radiation','diffuse_radiation','precipitation','rain','cape'),('°C','°C','%','hPa','hPa','%','%','%','%','km/h','°','km/h','W/m²','W/m²','W/m²','mm','mm','J/kg')))
    unitrows=[];formal_raw_ids={r['canonical_raw_run_id'] for r in s.runs}
    for rid,units in s.rawunits.items():
        if not units:continue
        version=s.rawruns[rid]['data_spec_version'];required=list(expected_units) if version=='ECMWF_18_VARIABLE_V2' else [k for k in units if k!='time']
        mismatches=[k for k in required if k in expected_units and units.get(k)!=expected_units[k]]
        unitrows.append(dict(raw_id=rid,data_spec_version=version,canonical_formal=rid in formal_raw_ids,declared_variable_count=len(required),mismatches=mismatches,status='FAIL' if mismatches else 'PASS',reason='preserved legacy8-variable noncanonical V1, not an18-variable unit error' if version=='ECMWF_8_VARIABLE_V1' else 'actual units agree with frozen18-variable contract'))
    unitbad=sum(bool(r['mismatches']) for r in unitrows);output('MASTER_UNIT_AUDIT.csv',unitrows)
    c['LEGACY_NONCANONICAL_8_VARIABLE_RAW_COUNT']=sum(r['data_spec_version']=='ECMWF_8_VARIABLE_V1' and not r['canonical_formal'] for r in unitrows)
    c['UNIT_MISMATCH_COUNT']=unitbad
    c['METEOSTAT_TRAINING_USAGE_COUNT']=sum(r['source'].upper().startswith(('METEOSTAT','WU')) for r in f.registry)
    c['UNPROVEN_INTRADAY_ZUUU_USAGE_COUNT']=sum(r['source'].upper() in ('ZUUU_INTRADAY','ZUUU_OBSERVATION') for r in f.registry)
    c['SILENT_SAMPLE_DROP_COUNT']=int(len(f.samples)!=1457 or sum(r['horizon']=='T1' for r in f.samples.values())!=729 or sum(r['horizon']=='T2' for r in f.samples.values())!=728 or len(p.samples)!=1457 or len(m.samples)!=1457)
    # Independent model skill is descriptive only; no candidates or estimators changed.
    model_skill=[]
    for h in ('T1','T2'):
        valid=[r for r in m.formal.values() if r['horizon']==h and r['ml_prediction'] is not None]
        for name,field in [('FROZEN_ML','ml_prediction'),('RAW_SAME_SUBSET','raw_ecmwf_prediction'),('MOS_SAME_SUBSET','mos_prediction')]:
            model_skill.append(dict(horizon=h,origin=name,**metrics([r[field]-r['actual_target'] for r in valid])))
    output('MASTER_MODEL_SKILL_DIAGNOSTIC.csv',model_skill)
    probskill=[]
    for h in ('T1','T2'):
        rows=[r for r in p.engine.values() if r['horizon']==h and r['status'] in ('CALIBRATED','UNCALIBRATED')]
        vals=[scores(p.mass[r['record_id']]['pmf'],r['actual']) for r in rows]
        probskill.append(dict(horizon=h,N=len(rows),**{k:float(np.mean([v[k] for v in vals])) for k in vals[0]}))
    output('MASTER_PROBABILITY_SKILL_DIAGNOSTIC.csv',probskill)
    output('MASTER_BLOCKER_AUDIT.md','# MASTER BLOCKER AUDIT\n\nT0_DATA_COLLECTION = ACTIVE (real UTC ingest preserved)\n\nT0_FORMAL_MODEL = BLOCKED / DEFERRED\n\nCORRELATED_TRAJECTORY = BLOCKED_FOR_DATA\n\nTMAX_TIME_PROBABILITY = BLOCKED_FOR_DATA\n\nMETEOSTAT_HISTORICAL_TRAINING = BLOCKED\n\nWU = NOT_FORMAL_ASSET\n\nNo formal MODEL_T0 asset, fake covariance or hourly Monte Carlo was used. Deterministic ECMWF curves remain legal forecast inputs. D+2 training eligibility does not establish historical METAR receipt. Phase10 state advancement is probability handoff, not fitting or promoting Phase8 estimators. Autostart scripts ready, installation not performed. Source scan evidence is MASTER_STATIC_SCAN.csv. Real-world settlement observation = PENDING_SOAK; evaluated production rows = 0.\n')
    output('MASTER_KNOWN_LIMITATIONS.md','# MASTER KNOWN LIMITATIONS\n\n'+ '\n\n'.join(x[5] for x in known)+'\n\nT0 historical ingest remains unobserved; trajectory/time probabilities require legal hourly OOS errors. Phase8 preserves 180-day minimum, approximately14-day refit blocks, frozen within-block state, Ridge training-only median, native tree missing handling and native Windows dependency warning. FEATURE_V1 preserves previous-hour radiation/precipitation intervals, forecast missingness, structural revisions and redundancy. Solar coordinates are30.576/103.950. Operational soak pending; no GUI authorization from this audit.\n')
    tree=ET.parse(OUT/'MASTER_AUDIT_TESTS.xml');suites=list(tree.getroot().iter('testsuite'));test_count=sum(int(x.attrib.get('tests',0)) for x in suites);test_fail=sum(int(x.attrib.get('failures',0))+int(x.attrib.get('errors',0)) for x in suites)
    c['AUDITOR_TEST_COUNT']=test_count;c['AUDITOR_TEST_FAILURE_COUNT']=test_fail
    before=json.loads((OUT/'MASTER_SOURCE_GUARDIAN_BEFORE.json').read_text(encoding='utf-8'))['assets'];changes,additions=after(before);c['UPSTREAM_CHANGED_FILE_COUNT']=len(changes)+len(additions)
    if changes or additions:findings.append(finding('MA-GUARDIAN','CRITICAL',0,'source_guardian','审计前后受保护资产变化','正式资产变动，不进行自动修复。',dict(asset=[r.get('path',r.get('relative_path')) for r in changes+additions]),'0 changes',changes+additions))
    statuses={}
    def status(label,*keys):statuses[label]='FAIL' if any(c[k] for k in keys) else 'PASS'
    status('FRAMEWORK_INTEGRITY','MODEL_RETRAIN_COUNT','MODEL_PROMOTION_COUNT','POLYMARKET_USAGE_COUNT','TARGET_LEAKAGE_COUNT');status('SOURCE_GUARDIAN','UPSTREAM_CHANGED_FILE_COUNT');status('DATABASE_INTEGRITY','DATABASE_INTEGRITY_FAILURE_COUNT');status('PHASE1_GROUND_TRUTH','TARGET_RECALC_MISMATCH_COUNT');status('PHASE2_ECMWF_ARCHIVE','PHASE2_VALUE_MISMATCH_COUNT','FUTURE_ECMWF_RUN_COUNT','ISSUE_RULE_SELECTION_MISMATCH_COUNT');status('PHASE3_AUXILIARY','SOLAR_MISMATCH_COUNT');status('PHASE4_DATA_V1','PHASE4_SAMPLE_MISMATCH_COUNT');status('PHASE5_RAW_BASELINE','PHASE5_METRIC_MISMATCH_COUNT');status('PHASE6_STATISTICAL_MOS','PHASE6_MOS_MISMATCH_COUNT','PHASE6_CAUSALITY_VIOLATION_COUNT');status('PHASE7_FEATURE_V1','FEATURE_FORMULA_MISMATCH_COUNT','FEATURE_TIME_AVAILABILITY_VIOLATION_COUNT');status('PHASE8_MACHINE_LEARNING','MODEL_T1_REPRO_MISMATCH_COUNT','MODEL_T2_REPRO_MISMATCH_COUNT','PREPROCESSING_LEAKAGE_COUNT','HYPERPARAMETER_SELECTION_LEAKAGE_COUNT');status('PHASE9_PROBABILITY','PROBABILITY_REPRO_MISMATCH_COUNT','PROBABILITY_SCORE_MISMATCH_COUNT','CALIBRATION_SELECTION_MISMATCH_COUNT');status('PHASE10_REALTIME_BUILD','PHASE10_FEATURE_MISMATCH_COUNT','PHASE10_MODEL_MISMATCH_COUNT','PHASE10_PROBABILITY_MISMATCH_COUNT','LINEAGE_BREAK_COUNT','MASTER_FAILURE_PROBE_FAILURE_COUNT');status('CROSS_PHASE_TIME_CAUSALITY','FUTURE_ECMWF_RUN_COUNT','FEATURE_TIME_AVAILABILITY_VIOLATION_COUNT','FUTURE_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','FORWARD_STATE_CAUSALITY_VIOLATION_COUNT','PHASE10_TIME_VIOLATION_COUNT');status('FEATURE_REPRODUCTION','FEATURE_FORMULA_MISMATCH_COUNT');status('MODEL_T1_REPRODUCTION','MODEL_T1_REPRO_MISMATCH_COUNT');status('MODEL_T2_REPRODUCTION','MODEL_T2_REPRO_MISMATCH_COUNT');status('PROBABILITY_REPRODUCTION','PROBABILITY_REPRO_MISMATCH_COUNT');status('HISTORICAL_REALTIME_PARITY','HISTORICAL_REALTIME_PARITY_MISMATCH_COUNT');status('END_TO_END_REPRODUCTION','END_TO_END_MISMATCH_COUNT');status('PREDICTION_LINEAGE','LINEAGE_BREAK_COUNT');status('SETTLEMENT_LOGIC','SETTLEMENT_EVALUATION_MISMATCH_COUNT');status('EVALUATION_LOGIC','SETTLEMENT_EVALUATION_MISMATCH_COUNT')
    statuses['FAILURE_RECOVERY_EVIDENCE']='PASS_WITH_WARNING' # real operational recovery still pending; isolated probes retained
    if c['PARITY_PROBABILITY_STATUS_MISMATCH_COUNT']:statuses['HISTORICAL_REALTIME_PARITY']='FAIL'
    if c['LINEAGE_BREAK_COUNT'] or (probe and probe['status']=='FAIL'):statuses['FRAMEWORK_INTEGRITY']='FAIL'
    for name in ('PHASE2_ECMWF_ARCHIVE','PHASE3_AUXILIARY','PHASE7_FEATURE_V1','PHASE8_MACHINE_LEARNING','PHASE9_PROBABILITY'):
        if statuses[name]=='PASS':statuses[name]='PASS_WITH_WARNING'
    statuses.update(T0_DATA_COLLECTION='ACTIVE',T0_FORMAL_MODEL='BLOCKED / DEFERRED',CORRELATED_TRAJECTORY='BLOCKED_FOR_DATA',TMAX_TIME_PROBABILITY='BLOCKED_FOR_DATA',PHASE10_OPERATIONAL_ACCEPTANCE='PENDING_SOAK')
    counts=Counter(r['severity'] for r in findings)
    for sev in ('CRITICAL','HIGH','MEDIUM','LOW'):c[sev+'_FINDING_COUNT']=counts[sev]
    critical_math=['TARGET_RECALC_MISMATCH_COUNT','ISSUE_RULE_SELECTION_MISMATCH_COUNT','FUTURE_ECMWF_RUN_COUNT','CROSS_RUN_SPLICE_COUNT','FEATURE_FORMULA_MISMATCH_COUNT','TARGET_LEAKAGE_COUNT','MODEL_T1_REPRO_MISMATCH_COUNT','MODEL_T2_REPRO_MISMATCH_COUNT','PROBABILITY_REPRO_MISMATCH_COUNT','PROBABILITY_SCORE_MISMATCH_COUNT','FUTURE_RESIDUAL_COUNT','INVALID_RESIDUAL_COUNT','FUTURE_CALIBRATION_LABEL_COUNT','PMF_SUM_MISMATCH_COUNT','HALF_SNAPSHOT_COUNT','FORWARD_STATE_CAUSALITY_VIOLATION_COUNT','UPSTREAM_CHANGED_FILE_COUNT','HASH_MISMATCH_COUNT','UNIT_MISMATCH_COUNT','CALIBRATION_SELECTION_MISMATCH_COUNT']
    acceptance='FAIL' if any(c[k] for k in critical_math) or c['PARITY_PROBABILITY_STATUS_MISMATCH_COUNT'] or any(r['status']=='FAIL' and r['severity'] in ('HIGH','CRITICAL') for r in findings) else 'PASS_WITH_WARNINGS'
    statuses['READY_FOR_OPERATIONAL_SOAK']='NO' if acceptance=='FAIL' else 'YES';statuses['MASTER_ACCEPTANCE']=acceptance
    output('MASTER_AUDIT_FINDINGS.json',dict(MASTER_ACCEPTANCE=acceptance,findings=findings,counts=dict(c),statuses=statuses,evidence=e,read_only_scope='all upstream DBs opened as protected RAM snapshots; isolated tests only in audit output; no training or engine startup'))
    issue_findings=[dict(id=r['finding_id'],severity=r['severity'],phase=r['phase'],status=r['status'],title=r['title']) for r in findings]
    detailed='\n\n'.join('## '+r['finding_id']+' — '+r['title']+'\n\n'+r['description']+'\n\nEvidence: `'+str(r['evidence'])+'`\n\nExpected: '+str(r['expected'])+'\n\nActual: '+str(r['actual'])+'\n\nRecommendation: '+r['recommendation'] for r in findings)
    scope='全部729日Target、247680条小时源值、2187个issue样本（2186 eligible）、13116个MOS状态、148614个Feature单元、1185个preprocessing状态、547条T1/545条T2正式ML OOS输出、24266份PMF以及2974073条残差关系均独立检查。历史/实时数学重放60个样本（每horizon30），全部正式实时快照34条。Lineage检查94条：34条实时概率直接引用不一致，60条历史血缘正常。完整状态可经父快照恢复，数学一致不消除字段一致性缺陷。'
    independence='Source selection/target/feature arithmetic/Ridge dot product/LightGBM tree traversal/residual eligibility/PMF积分/CDF校准/score采用审计独立实现。Phase10 construct/forward/settlement/Archive作为被测对象，仅用于与独立期望比较或隔离故障注入。未调用一键历史builder，没有fit任何模型。所有模型为本地冻结hash验证资产。模拟fixture与真实数据明确分开；故障探针通过不代表真实长期运行已经验证。'
    report='# ZUUU SYSTEM V1 — MASTER AUDIT REPORT\n\nMASTER_ACCEPTANCE = '+acceptance+'\n\nREADY_FOR_OPERATIONAL_SOAK = '+statuses['READY_FOR_OPERATIONAL_SOAK']+'\n\n这是独立审计的新结论，不覆盖原Phase10报告。Phase1–9数学复算未发现不一致；Phase10两处接口/状态缺陷导致本次不能通过。未修复、未重建、未启动生产Engine、未进入Phase11。\n\n## 验收矩阵\n\n'+table([{'check':k,'result':v} for k,v in statuses.items()])+'\n\n## 核验覆盖和方法\n\n'+scope+'\n\n'+independence+'\n\nProtocol SHA256: `'+digest(OUT/'MASTER_AUDIT_PROTOCOL.md')+'`\n\nSampling seed20261002; manifest `MASTER_SAMPLE_MANIFEST.json`，全量复算部分没有人为筛选。数据库正式root共'+str(len(dbrows))+'份；旧BLOCKED Phase6数据库作为保留证据，不误认成正式Review状态。\n\n## Findings\n\n'+table(issue_findings)+'\n\n'+detailed+'\n\n## 数值与时间审计计数\n\n'+table([{'metric':k,'value':v} for k,v in sorted(c.items())])+'\n\n## 连续预测诊断（完全相同日期集合）\n\n'+table(model_skill)+'\n\n## 概率诊断（ENGINE正式ML输出）\n\n'+table(probskill)+'\n\n## 上游与正式哈希\n\n受保护文件'+str(len(before))+'个，Before/After变化'+str(c['UPSTREAM_CHANGED_FILE_COUNT'])+'个。独立semantic投影和正式manifest文件哈希见MASTER_HASH_COMPARISON.csv。旧测试缓存变化单列warning；它没有混入预测资产。\n\n'+table([{'asset':k,'semantic_sha256':v} for k,v in e['hashes']['semantic'].items()])+'\n\n## 证据边界\n\n实际state transition='+str(c['FORWARD_STATE_TRANSITION_COUNT'])+'；actual evaluated rows=0，因此真实world settlement/evaluation仍PENDING_SOAK。锚点规则先到先固定，不以最终成绩选择；一次目标日只进入一次未来误差池，所有snapshots均独立评分，隔离模拟有证据。锚点可晚于21:00，属于已冻结Phase10工程规则，存在时刻分布偏移警告；不得声称等同于全部固定时刻历史OOS。非锚点刷新OK错误另列MA-002。\n\n中国大陆网络NOT_TESTED_ON_MAINLAND_NETWORK；Task Scheduler未安装；T0/trajectory/Tmax-time保持BLOCKED。没有使用市场数据或重新训练。\n\n## 审计自身测试\n\n'+str(test_count)+' passed, failures='+str(test_fail)+'。测试证明审计工具防污染和数学基本式，不代替系统结果。终端完整摘要见MASTER_AUDIT_TERMINAL_REPORT.txt。所有新增/修改文件在本次审计目录及允许的audit/test范围，详见MASTER_AUDIT_MANIFEST.json。\n\nSTOP. 不进行自动修复。'
    output('MASTER_AUDIT_REPORT.md',report)
    answers=[
      '总体路线仍符合最初架构；但实时概率的状态引用和刷新状态有两处落实问题，本次总验收不通过。',
      '答案可靠性已核验：729天最高温、发生时间、次数和原始报文关系全部复算一致。',
      '已检查旧Run版本、原始内容和冻结哈希，没有发现后来Run替换历史输入；历史可用时间仍是估计合同。',
      '本次全量可核查的时间关系未发现偷看未来；这不表示历史真实收件时刻已有证据。',
      '102个特征共148614个值全部复算一致，包括原本为空的值。',
      '60个历史重放和34个真实快照的特征数学一致。',
      'T1 Ridge547条历史OOS输出按系数公式独立恢复一致。',
      'T2 LightGBM545条输出按树结构逐节点计算一致。',
      '24266份概率分布和评分逐条复算一致。',
      '近297万条误差使用关系及校准历史未发现未来结果。',
      '数值重放一致；但概率记录的状态编号不一致，且21点小时内非锚点刷新可能错误显示正常。',
      '没有发现偷偷填0或插值。Ridge的填补来自当时训练窗口，是已冻结模型处理规则。',
      '未发现跨Run拼接，目标日完整性和最新合法完整Run选择均重查。',
      '基础T1为729条、T2为728条，冷启动和缺口保留；未发现因预测不好删除样本。',
      '34个实时快照能通过父记录追到原始数据，但概率自身的状态引用全部悬空，所以不能称每个字段的血缘完全一致。',
      '已有日结果及隔离完整日、冲突、晚到COR测试符合最高温规则；真实长期结算仍待观察。',
      '隔离测试证明一天多个快照逐个评分；当前真实评分记录为0，不能宣称生产长期验证。',
      '普通评分与更新概率历史已区分：全部评分，一天一个锚点加入历史；现有锚点更新模拟正常。',
      '历史当天观测实际何时收到缺乏证据，所以不能训练正式T0模型。',
      '可以。Phase10保留真实收到报文的时间和版本，为将来建立T0证据。',
      '缺少合法历史逐小时预测误差及其相关关系，不能凭空模拟72小时概率轨迹。',
      '同样缺少合法小时概率轨迹，不能给最高温发生小时编概率。',
      '正式特征和生产预测没有使用Meteostat历史天气训练。',
      '没有市场价格进入正式天气预测路径。',
      '实时路径没有重新训练或换模型，仍使用冻结Ridge和LightGBM。',
      '本次Before/After检查保护文件，变化数='+str(c['UPSTREAM_CHANGED_FILE_COUNT'])+'；正式数据库未修改。',
      '最优先的三项风险：概率状态编号不一致；非锚点刷新可能冒称正常；真实长时间运行与结算尚未证明。另有T2概率小样本和网络警告。',
      '现在不能批准正式72小时–7天验收Soak，READY_FOR_OPERATIONAL_SOAK=NO。',
      '卡在MA-001和MA-002：需要负责人另行决定如何修复并复验，本次审计不会改代码或数据。',
      '待复验通过后，重点观察网络中断恢复、午夜日期滚动、所有预测评分、每天一次误差更新、数据库和日志完整性。']
    output('MASTER_AUDIT_PLAIN_LANGUAGE_SUMMARY.md','# MASTER AUDIT — 大白话报告\n\nMASTER_ACCEPTANCE = '+acceptance+'\n\n'+'\n\n'.join(str(i)+'. '+a for i,a in enumerate(answers,1))+'\n\n完整证据：MASTER_AUDIT_REPORT.md、MASTER_AUDIT_FINDINGS.json，以及各专项CSV。STOP。')
    terminal='============================================================\nZUUU PREDICTION SYSTEM V1 — MASTER AUDIT\n============================================================\n'+ '\n'.join(k+' = '+str(v) for k,v in statuses.items())+'\n============================================================\n'+'\n'.join(k+' = '+str(v) for k,v in sorted(c.items()))+'\n============================================================\nAUDIT_ONLY; NO_PRODUCTION_FIX; STOP\n'
    output('MASTER_AUDIT_TERMINAL_REPORT.txt',terminal)
    paths=sorted((ROOT/'src/audit/master').glob('*.py'))+sorted((ROOT/'tests/master_audit').glob('*.py'))+[x for x in sorted(OUT.iterdir()) if x.is_file() and x.name!='MASTER_AUDIT_MANIFEST.json']
    output('MASTER_AUDIT_MANIFEST.json',dict(created_or_modified_files=[dict(path=str(x),sha256=digest(x),size=x.stat().st_size) for x in paths],protocol_sha256=digest(OUT/'MASTER_AUDIT_PROTOCOL.md'),self_hash='excluded to avoid self-reference',MASTER_ACCEPTANCE=acceptance))
    print(terminal,flush=True)
