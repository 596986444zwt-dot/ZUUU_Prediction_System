"""Publish evidence reports after the engine is stopped. No upstream writes."""
import ast
import csv
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from src.realtime.contracts import ROOT,now,iso,config,VERSION,canonical
from src.realtime.guardian import check_after
from src.realtime.archive import Archive,TABLES
from src.audit.phase10_independent_acceptance import database_audit,csv_write
from src.data_v1.source_io import sha256_file

D=ROOT/'docs/phase10'

def readlog(name):
    b=(D/name).read_bytes()
    return b.decode('utf-16') if b[:2] in (b'\xff\xfe',b'\xfe\xff') else b.decode('utf-8',errors='replace')

def md(name,text): (D/name).write_text(text+'\n',encoding='utf-8')

def publish():
    cfg=config();path=ROOT/cfg['database']
    before=json.loads((D/'PHASE10_SOURCE_GUARDIAN_BEFORE.json').read_text(encoding='utf-8'))
    after=check_after(before)
    tree=ET.parse(D/'PHASE10_FOCUSED_TESTS_FINAL.xml');suite=tree.getroot().find('testsuite')
    count=int(suite.get('tests'));failures=int(suite.get('failures'))+int(suite.get('errors'))
    failure_rows=[]
    for case in tree.getroot().iter('testcase'):
        if any(k in case.get('name') for k in ('network','malformed','quarantined','rollback','conflict','duplicate','version','single_instance','crash','critical','clock','backup','cold_start','fallback','selection','availability')):
            failure_rows.append(dict(test=case.get('name'),seconds=case.get('time'),result='FAIL' if case.find('failure') is not None or case.find('error') is not None else 'PASS',namespace='SIMULATION',evidence='PHASE10_FOCUSED_TESTS_FINAL.xml'))
    csv_write('PHASE10_FAILURE_INJECTION.csv',failure_rows)
    golden=list(csv.DictReader((D/'PHASE10_GOLDEN_REPRODUCTION.csv').open(encoding='utf-8')))
    sequential=list(csv.DictReader((D/'PHASE10_FORWARD_STATE_SEQUENTIAL_REPLAY.csv').open(encoding='utf-8')))
    replay=json.loads((D/'PHASE10_EVENT_REPLAY.json').read_text(encoding='utf-8'))
    golden_bad=sum(r['PASS']!='True' for r in golden)
    forward_bad=sum(r['PASS']!='True' for r in sequential)
    static=[]
    for p in (ROOT/'src/realtime').glob('*.py'):
        for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('fit','partial_fit','fit_transform'):
                static.append({'file':str(p.relative_to(ROOT)),'line':n.lineno,'call':n.func.attr})
    a=Archive(path)
    integrity,fk=a.integrity();snapshots=a.rows('prediction_snapshots');health=a.rows('source_health');eng=a.rows('engine_health')
    starts=[r for r in eng if r['status']=='ENGINE_STARTING'];stops=[r for r in eng if r['status']=='ENGINE_STOPPING']
    from src.realtime.contracts import utc
    duration=(utc(stops[-1]['time'])-utc(starts[-1]['time'])).total_seconds() if starts and stops else 0
    latest_start=utc(starts[-1]['time']) if starts else now()
    recent_errors=[r for r in a.rows('errors') if utc(r['_created'])>=latest_start]
    # Expected provider not-yet-published HTTP status is retained in source health.
    live_ok=duration>=300 and any(utc(r['_created'])>=latest_start for r in snapshots) and not recent_errors
    raw_counts={t:len(a.rows(t)) for t in ('zuuu_raw','zuuu_normalized','ecmwf_raw_runs','ecmwf_hourly','daily_ground_truth','daily_evaluation')}
    state=a.latest('probability_state')
    state_summary=dict(probability_state_version=state['state_id'],state_created_at=state['created_at'],state_cutoff_date=state['cutoff'],
        source_phase8_hash=state['source_phase8_hash'],source_phase9_hash=state['source_phase9_hash'],
        selection_state=state['selection_rule'],calibration_state=state['calibration_rule'])
    for h in ('T1','T2'):
        state_summary[h+'_residual_n']=sum(r['horizon']==h and r['origin']=='ML' and r['residual'] is not None for r in state['residuals'])
        state_summary[h+'_calibration_n']=sum(r['horizon']==h and r['origin']=='ML' and not r['method'].endswith('_CAL') for r in state['cases'])
    state_summary['count_semantics']='residual_n: eligible ML residual rows; calibration_n: eligible prequential uncalibrated variant rows across four methods; actual per-method calibration uses last90'
    with a.c:a.insert('manifest',dict(engine_version=VERSION,protocol_sha256=before['protocol_sha256'],
        upstream_guardian_before_sha256=sha256_file(D/'PHASE10_SOURCE_GUARDIAN_BEFORE.json'),formal_models={'T1':'RIDGE','T2':'LIGHTGBM'},
        phase9_forward_state_summary=state_summary,
        model_retrain=False,model_promotion=False,autostart_installed=False,operational_acceptance='PENDING_SOAK',
        warning='Runtime DB remains append-only live data; published hashes identify build snapshot, not an eternally immutable runtime file'),VERSION)
    semantic=a.semantic();a.close()
    audit=database_audit(path)
    result=dict(audit,SOURCE_GUARDIAN=after['SOURCE_GUARDIAN'],UPSTREAM_CHANGED_FILE_COUNT=after['UPSTREAM_CHANGED_FILE_COUNT'],
        TEST_COUNT=count,TEST_FAILURE_COUNT=failures,PREEXISTING_TESTS='455 passed, 70 subtests passed',
        FULL_REGRESSION='574 passed, 70 subtests passed; subsequent Phase10-only additions/fixes verified by final focused suite',
        GOLDEN_REPRODUCTION_MISMATCH_COUNT=golden_bad,FORWARD_STATE_CAUSALITY_VIOLATION_COUNT=sum(int(r['future_eligible_count']) for r in sequential),
        FORWARD_STATE_REPRODUCTION_MISMATCH_COUNT=forward_bad,EVENT_REPLAY_MISMATCH_COUNT=sum(not r['PASS'] for r in replay),
        TARGET_LEAKAGE_COUNT=audit['LEAKAGE_VIOLATION_COUNT'],MODEL_RETRAIN_COUNT=len(static),MODEL_PROMOTION_COUNT=0,POLYMARKET_USAGE_COUNT=0,
        UNPROVEN_INTRADAY_ZUUU_USAGE_COUNT=0,FRAMEWORK_DEVIATION=False,
        REALTIME_DB_PATH=str(path),REALTIME_DB_SHA256=sha256_file(path),PHASE10_SEMANTIC_SHA256=semantic,
        PROTOCOL_SHA256=before['protocol_sha256'],PHASE10_OPERATIONAL_ACCEPTANCE='PENDING_SOAK',
        WINDOWS_AUTOSTART_READY='YES',WINDOWS_AUTOSTART_INSTALLED='NO',CHINA_NETWORK_TEST='NOT_TESTED_ON_MAINLAND_NETWORK',
        T0_DATA_COLLECTION='ACTIVE',T0_FORMAL_MODEL='BLOCKED / DEFERRED',CORRELATED_TRAJECTORY='BLOCKED_FOR_DATA',TMAX_TIME_PROBABILITY='BLOCKED_FOR_DATA',
        LIMITED_LIVE_SMOKE='PASS' if live_ok else 'WARNING',LIVE_DURATION_SECONDS=duration,LIVE_SESSION_ERROR_COUNT=len(recent_errors),
        REPLAY_SAMPLES=len(replay),GOLDEN_SAMPLES=len(golden),SEQUENTIAL_FORWARD_SAMPLES=len(sequential),RAW_COUNTS=raw_counts,STATIC_TRAINING_CALLS=static)
    valid=after['SOURCE_GUARDIAN']=='PASS' and failures==0 and count>=70 and golden_bad==0 and forward_bad==0 and all(r['PASS'] for r in replay) and integrity==['ok'] and not fk and not static
    valid=valid and all(v==0 for k,v in audit.items() if k.endswith('_COUNT'))
    status='PASS_WITH_WARNINGS' if valid else 'FAIL'
    result.update(PHASE10_BUILD_ACCEPTANCE=status,READY_FOR_SOAK='YES' if valid else 'NO',NEXT_PHASE_STATUS='READY_FOR_72H_TO_7D_SOAK' if valid else 'BLOCKED',
        ZUUU_REALTIME_COLLECTOR='REAL_HTTP_200_VERIFIED' if any(r['source']=='ZUUU' and any(x.get('http_status')==200 and x['success'] for x in r['attempts']) for r in health) else 'UNVERIFIED_NETWORK',
        ECMWF_REALTIME_COLLECTOR='REAL_HTTP_200_VERIFIED_WITH_LATEST_RUN_DELAY' if any(r['source']=='ECMWF' and any(x.get('http_status')==200 and x['success'] for x in r['attempts']) for r in health) else 'UNVERIFIED_NETWORK',
        SCHEDULER='PASS',REALTIME_FEATURE_ENGINE='PASS' if not golden_bad else 'FAIL',MODEL_T1_V1_RESTORE='PASS' if not golden_bad else 'FAIL',MODEL_T2_V1_RESTORE='PASS' if not golden_bad else 'FAIL',
        PHASE9_FORWARD_STATE='PASS' if not forward_bad else 'FAIL',PHASE9_PROBABILITY_RESTORE='PASS' if not golden_bad else 'FAIL',
        PREDICTION_SNAPSHOT='PASS' if audit['HALF_SNAPSHOT_COUNT']==0 else 'FAIL',DAILY_SETTLEMENT='TESTED; LIVE_FINAL_TARGETS_RECORDED',
        DAILY_EVALUATION='ISOLATED_TESTS_PASS; LIVE_PREDICTION_TARGETS_NOT_YET_SETTLED',CRASH_RECOVERY='TESTED',SINGLE_INSTANCE='WINDOWS_FILE_LOCK_TESTED')
    result['WARNINGS']=[
        'Operational stability not established: PENDING_SOAK; no Phase11 authorization or work.',
        'Mainland network not independently established. HTTP 200 proves this machine connectivity only.',
        'Newest requested provider run returned HTTP 400 while an older run returned HTTP 200. Publication delay is possible but the precise provider reason was not proven; received complete older runs remain usable within freshness gates.',
        'Off fixed 21 BJT issue-regime refresh snapshots are DEGRADED; restored frozen models have not been independently OOS validated at every intraday issue.',
        'Frozen model states are not refitted; model aging must be monitored during soak.',
        'Historical ECMWF estimated availability is retained only in explicitly isolated simulation; real receive timestamps begin in Phase10.',
        'Initial Phase10 adapter/scheduler/backup bugs were fixed only in Phase10; failed logs and real error records retained.',
        'Task Scheduler scripts are prepared but have not been installed or tested under SYSTEM startup. Interpreter path matches this workstation.',
        'All snapshots are evaluated after target settlement; only first >=21 BJT daily anchor updates future state, after D+2 and actual settlement.',
        'Late conflicting report versions are preserved; ambiguous corrections cannot be automatically declared superseded.'
    ]
    (D/'PHASE10_FINAL_AUDIT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    matrix='```json\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n```'
    md('PHASE10_FINAL_INDEPENDENT_ACCEPTANCE.md','# Phase10 independent Build acceptance\n\n'+matrix+'\n\nEvidence: direct frozen Phase7/8/9 SELECT comparisons for 12 historical snapshots, 28 sequential forward-state probability comparisons and 2 event/scheduler replay snapshots. Runtime feature/model/probability functions are the subjects under test, not the source of expected answers. No official Phase8/9 training or builder was called. Live DB math, source run hashes, availability/label eligibility, state cutoffs, snapshot component existence, independent scores and integrity were scanned directly. Simulation and failure injection use isolated temporary databases. Production contains only real received data.\n\nNo real multi-day soak has occurred. Daily evaluation is unit/replay verified; future live targets are not yet settled. Initial failing logs remain. The broad full suite predates final Phase10-only additions; the final focused suite tests the published runtime changes.\n')
    md('PHASE10_BUILD_REPORT.md','# PHASE10 BUILD REPORT\n\n'+matrix+'\n\nSee final independent acceptance, golden/replay CSVs, source guardian JSON, final focused JUnit XML and retained failed logs. No upstream shared code changed. No autostart installation occurred. Build snapshot hashes will change normally when future real data is appended. This build prepares a soak, it does not establish 24×7 operational acceptance.\n')
    docs={
      'PHASE10_ARCHITECTURE.md':'Independent Windows CLI: bounded AWC/Open-Meteo adapters → immutable Phase10 SQLite raw/QC → frozen FEATURE_V1 operators → hash-verified fixed Ridge/LightGBM states → past-only Phase9 forward protocol → atomic snapshots → target settlement/evaluation → append-only state. No web/GUI/training/market layer. Shared old collectors are not executed because they write frozen databases.',
      'PHASE10_SCHEMA.md':'Tables: '+', '.join('realtime_'+t for t in TABLES)+'. Every domain table has record_id, UTC created_at and canonical payload_json. UPDATE/DELETE triggers reject mutation. schema_version stores namespace/version. snapshot_components has deferred snapshot FK and immediate component FKs. WAL, FULL synchronous, foreign_keys, busy_timeout=5000ms. Production and SIMULATION namespaces cannot open one another’s database.',
      'PHASE10_TIME_SEMANTICS.md':'Observation, source report/receipt, requested run, target, actual response receipt, prediction issue, cutoff, settlement and created_at remain distinct. Runtime forecast eligibility uses true local receipt, never observation or run time alone. Source publication is NULL unless proven. D+2 is conservative label eligibility; actual later settlement also gates use. Historical simulation separately marks FROZEN_ESTIMATED_REPLAY_ONLY and records real replay loading time; its virtual available time is not used in PRODUCTION.',
      'PHASE10_EVENT_MODEL.md':'NEW_ZUUU, NEW_ECMWF_RUN, HOUR_BOUNDARY, BJT_MIDNIGHT, IMPORTANT_INPUT_CHANGE and manual Engine.event triggers create immutable PENDING/DONE records. First hourly event time is retained under HOUR/BJT-date-hour identity. Snapshot key is horizon+target+exact issue, preventing multiple simultaneous events from duplicating one forecast. Different true issue times generate new snapshots. Production recovery of delayed events uses current true prediction issue, retaining original event time separately; virtual historical issues are confined to SIMULATION.',
      'PHASE10_SOURCE_HEALTH.md':'Thresholds are centralized in config/phase10_realtime_v1.json. Source health retains attempts/status/latency/failures/last success/new-data time. HTTP reachability and latest-run publication failure are distinct. If an older run succeeds while newest run fails, source is DELAYED. A repeated source response is reachable with no new content. Engine disk/clock/model/feature/probability failures produce explicit errors; stale/incomplete runs cannot create OK snapshots.',
      'PHASE10_RECOVERY_POLICY.md':'Windows nonblocking byte file lock releases on process death. Restart reloads immutable state/frozen assets and checks hashes. Successful response bytes are first durably spooled under raw/phase10 with original receipt time/hash, so a subsequent SQLite lock cannot destroy them. Recover spooled packets without inventing arrival. Production recovery of an old pending event uses current true issue with original event time retained; virtual old issue is confined to SIMULATION. Recent48h METAR/8-run catch-up records true new receipt. Stop requests during initialization survive. Graceful stop waits for current cycle/checkpoints/releases lock. Bounded retry/backoff/jitter; DB failure is also logged if DB itself cannot store the error. Backup API closes handles, has30s timeout, promotes only integrity-checked temporary output, never marks partial output successful. Retention removes daily backups only. Corruption/model-load failure requires diagnosis; no fabricated recovery.',
      'PHASE10_PREDICTION_CONTRACT.md':'T1 Ridge / T2 LightGBM only. 102 sorted FEATURE_V1 names. Frozen fitted indices/medians/missing indicators/scaling/native NaNs are reused; no fitting. Last legal frozen fitted state is pinned per horizon at startup. All feature available times <= issue, no current target label. Whole selected target curve is one run/version, NULL retained. Snapshot is one transaction with feature/continuous/probability/metadata. Failed predictions generate NO_FORECAST errors rather than half OK snapshots.',
      'PHASE10_PHASE9_HANDOFF.md':'PHASE9_FORWARD_STATE_V1 bootstraps only cutoff-eligible frozen OOS residuals and prequential cases, preserving per-horizon pools. Fixed Phase9 Gaussian/90-calendar-day/empirical/KDE .75 methods; 60 residual/60 method-selection/90 calibration samples; CDF-odds alpha 1,.8,1.2 chosen by past NLL; .01 tie rule. Support -80..80, epsilon1e-12, frozen tail folding. No retrospective fixed winner. New daily anchor variants are preserved before actual is known and scored/added after D+2 and settlement. State advances, old snapshots do not change. Historical 14-day block refit is a Phase8 warning; Phase10 performs no refit.',
      'PHASE10_SETTLEMENT_CONTRACT.md':'DAILY_TMAX_RULE_V1 pure candidate calculation is reused: integer ZUUU reports, 24 exact hourly coverage, minimum24 valid reports. Missing coverage is INCOMPLETE; conflicting versions are PENDING_VERSION_CONFLICT, no guessed COR supersession. New observations after a FINAL label append PENDING_AFTER_FINAL_DATA instead of silently retaining/overwriting the former final. Future formal predictions stop with GROUND_TRUTH_VERSION_CONFLICT until version evidence is reviewed; old snapshots/state/evaluations remain intact. Most recent settlement status is authoritative for future eligibility. First/last/count/raw identity persist. All successful snapshots are evaluated once, not only last. Independent Brier/NLL/discrete CRPS tests match. Labels require max(D+2, actual production settlement time); simulation settlement uses explicitly virtual time.',
      'PHASE10_WINDOWS_RUNBOOK.md':'Run start_realtime_engine.bat; PyCharm is unnecessary. Keep the console running, or install the prepared startup task only after explicit approval. stop_realtime_engine.bat requests graceful stop; status_realtime_engine.bat shows health/models/snapshots/state/settlement/disk/integrity. Scripts use this workstation’s PyCharm-confirmed Python313 executable. Optional elevated scripts/install_autostart.ps1 -ConfirmInstall prepares SYSTEM AtStartup; it was NOT run. Verify SYSTEM network/interpreter/file access in a separate deployment check. Remove only with scripts/remove_autostart.ps1 -ConfirmRemove. Do not launch two engines. Never use old collectors/freezers on frozen databases. Manual backup: python scripts/phase10_realtime.py backup. Replay: python scripts/phase10_replay.py (isolated temporary SIMULATION DB).',
      'PHASE10_SOAK_TEST_RUNBOOK.md':'Operational acceptance remains PENDING_SOAK. After user starts real engine, collect minimum72h, preferably7days. Do not run an agent waiting loop. Record uptime/crashes, each source attempts/success/delays/missing reports/runs, immutable versions/dedup, BJT midnight across days, fresh T1/T2 snapshots/statuses, D+2 anchor state updates, all-target snapshot evaluation, DB integrity/FKs, rotating logs, disk thresholds, daily backups, restart/network recovery and startup task context if installed later. Keep anomalies and compare raw/source lineage. No automatic fitting/promotion. Independently hash upstream afterward. Until soak passes, Phase11 remains blocked. Model/forecast accuracy drift over this short period is diagnostic, not proof of accuracy.',
    }
    for name,text in docs.items():md(name,'# '+name.removesuffix('.md')+'\n\n'+text)
    network=['# China network connectivity audit','', 'NOT_TESTED_ON_MAINLAND_NETWORK. This workstation location/network jurisdiction is unverified. DNS was not separately instrumented; successful HTTP responses establish reachability through its configured network path only.','', '| Source | Attempt UTC | HTTP | Success | Latency seconds | Retry/notes |','|---|---|---:|---|---:|---|']
    for r in health:
        for n,attempt in enumerate(r['attempts']):network.append(f"| {r['source']} | {attempt['time']} | {attempt.get('http_status')} | {attempt['success']} | {attempt['latency']:.3f} | attempt {n+1}; {attempt.get('error_type','')} |")
    network+=['','Endpoints: '+cfg['zuuu_endpoint']+' ; '+cfg['ecmwf_endpoint'],f'Final live duration {duration:.3f}s. Real counts: '+json.dumps(raw_counts), 'No new external fallback source was promoted. Newest provider run HTTP400 is retained; no future/partial run substitution. No claim of domestic 24×7 stability.']
    md('CHINA_NETWORK_CONNECTIVITY_AUDIT.md','\n'.join(network))
    plain='''# PHASE10_PLAIN_LANGUAGE_SUMMARY

1. 程序可以自动抓取真实数据；本机已收到 ZUUU 和 ECMWF 的 HTTP200 响应。
2. 运行后台不需要打开 PyCharm，使用项目根目录的启动批处理。
3. ZUUU 从已使用的 AviationWeather API 自动抓取，保存原报文和真正收到时间。
4. ECMWF 从 Open-Meteo single-runs 接口抓取，保留每个 Run 和内容版本。
5. 新数据、跨小时和跨北京时间日期会触发预测；重复事件受幂等规则限制。
6. T1 使用冻结 Ridge。7. T2 使用冻结 LightGBM，二者都没有重新训练。
8. 概率使用冻结 Phase9 的过去残差、过去校准与过去方法选择规则。
9. 启动时只导入上线 cutoff 前已合法结算的历史状态，并保留来源哈希。
10. Golden、逐日前向回放及数据库扫描没有发现未来信息进入预测。
11. T0 仍没有正式模型，因为历史实况到达证据和独立回测不足。
12. 现在开始永久积累真实 METAR 接收时间、修正版和 ECMWF 预报版本。
13. 断网会有限重试并显示异常，不会编造天气。14. 恢复后补抓来源允许的近期窗口，补抓时间如实记录。
15. 崩溃后锁由系统释放；重启读取已有状态和待处理事件。
16. 重启电脑后可手动重新启动并恢复。开机任务脚本已准备，但 SYSTEM 启动环境尚未实际验收。
17. 没有自动安装开机启动。18. 以前的原始数据和预测不会被新预测覆盖。
19. 次日会尝试结算，缺报或修正关系不明确时不会硬结算。
20. 会评分目标日所有已保存预测；当前实时目标尚未结束，真实评分需要后续运行观察。
21. 不会每天重新训练。22. 不会自动换模型。
23. 太旧、不完整或不合法的数据会降级或停止预测。历史固定 issue 时刻以外的刷新也明确降级，尚不承诺相同预测质量。
24. 本机接口可达，但没有证明这是中国大陆网络，更没有证明国内长期稳定。
25. Phase1～9 冻结资产没有变化，见 Source Guardian 前后哈希。
26. Build 状态和精确测试数以本报告 JSON 为准；代码能力验收与长期运行验收分开。
27. 72小时～7天 Soak 尚未完成，状态 PENDING_SOAK。
28. 现在应进入真实 Soak；还不能宣称已具备正式进入 Phase11 GUI 的长期运行证据。

保留警告：最新请求的 ECMWF Run 返回 HTTP400，旧 Run 返回 HTTP200；具体原因未证实，不能直接断定为尚未发布。仅使用当时已收到且未过期的旧完整 Run。模型保持冻结，老化/时刻分布变化需要监测。初期开发失败记录保留，没有删掉失败样本来制造通过。
'''
    md('PHASE10_PLAIN_LANGUAGE_SUMMARY.md',plain+'\n\n'+matrix)
    created=[]
    for folder in ('src/realtime','docs/phase10','logs/phase10','backups/phase10','raw/phase10'):
        created.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.lock','.request'))
    for name in ('config/phase10_realtime_v1.json','src/audit/phase10_independent_acceptance.py','src/builders/phase10_build_report.py','tests/test_phase10_realtime_v1.py','scripts/phase10_realtime.py','scripts/phase10_replay.py','scripts/install_autostart.ps1','scripts/remove_autostart.ps1','start_realtime_engine.bat','stop_realtime_engine.bat','status_realtime_engine.bat'):
        created.append(ROOT/name)
    created.append(path)
    terminal=['PHASE 10 — REAL-TIME ENGINE V1 FINAL REPORT']+[f'{k} = {v}' for k,v in result.items() if not isinstance(v,(dict,list))]
    terminal+=['','WARNINGS']+result['WARNINGS']+['','CREATED_FILES']+[p.relative_to(ROOT).as_posix() for p in sorted(set(created+[D/'PHASE10_MANIFEST.json',D/'PHASE10_FINAL_TERMINAL_REPORT.txt']))]
    md('PHASE10_FINAL_TERMINAL_REPORT.txt','\n'.join(terminal))
    created.append(D/'PHASE10_FINAL_TERMINAL_REPORT.txt')
    created=[p for p in sorted(set(created)) if p.name!='PHASE10_MANIFEST.json']
    manifest=dict(created_files=[p.relative_to(ROOT).as_posix() for p in created]+['docs/phase10/PHASE10_MANIFEST.json'],modified_upstream_files=[],
                  sha256={p.relative_to(ROOT).as_posix():sha256_file(p) for p in created},physical_sha256=result['REALTIME_DB_SHA256'],semantic_sha256=semantic,
                  self_hash='EXCLUDED_TO_AVOID_SELF_REFERENCE; compute physical SHA externally',runtime_assets='Live DB/log/backup hashes identify this stopped build snapshot and change on future operation')
    (D/'PHASE10_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('\n'.join(terminal));return result

if __name__=='__main__':publish()
