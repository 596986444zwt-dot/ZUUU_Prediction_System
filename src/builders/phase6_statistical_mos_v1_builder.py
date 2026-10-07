"""Build an isolated, honestly labelled Phase6 artifact; never overwrite."""
import argparse
import csv
import json
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from src.mos.contracts import ROOT, OUTPUT, REPORTS, VERSION, CONTRACT, SOURCES
from src.mos.data import guardian, fingerprints, load_samples
from src.mos.walk_forward import evaluate
from src.mos.metrics import calculate, summarize
from src.mos.schema import populate, statements
from src.mos.semantic_hash import semantic_hash, canonical
from src.data_v1.contracts import require
from src.data_v1.source_io import sha256_file
from src.audit.phase6_statistical_mos_v1_audit import audit_connection, audit

EXPECTED_BENCHMARK = {
    'T0': [729, -.532921811, 1.498079561, 1.865832412, 1.3, 3.1, 5.9],
    'T1': [729, -.545816187, 1.551028807, 1.935244550, 1.4, 3.1, 7.1],
    'T2': [728, -.408241758, 1.741758242, 2.173820256, 1.4, 3.5, 8.1],
}

def prepare():
    before, checks, availability = guardian()
    samples, exclusions = load_samples()
    benchmark = []
    for h in ('T0','T1','T2'):
        actual = calculate([s['raw_error'] for s in samples if s['horizon'] == h])
        require(all(abs(actual[k]-v) < 1e-8 for k,v in zip(('n','bias','mae','rmse','median_ae','p90_ae','max_ae'), EXPECTED_BENCHMARK[h])), 'STOP: Phase5 benchmark not reproduced')
        benchmark.append(dict(horizon=h, **actual))
    created = datetime.now(timezone.utc).isoformat()
    predictions, states = evaluate(samples, created)
    metrics, slices = summarize(predictions)
    data = {'sample': samples, 'bias_state': states, 'prediction': predictions, 'metric': metrics, 'slice_metric': slices}
    # No supported label settlement evidence => no candidate can be assessed.
    require(not any(s['settlement_verified'] for s in samples), 'New evidence requires an explicit reviewed source adapter, not silent admission')
    files = implementation_files()
    data['manifest'] = [dict(model_version=VERSION, build_status='BLOCKED', candidate='NONE',
        artifact_role='BLOCKED_AUDIT_AND_ABSTENTION_ARTIFACT_NOT_ACCEPTED_MOS_MODEL',
        block_reason='FINAL_LABEL_HISTORICAL_SETTLEMENT_NOT_PROVEN', contract_json=canonical(CONTRACT),
        source_sha_before_json=canonical(before), source_guardian_json=canonical(checks),
        benchmark_json=canonical(benchmark), exclusion_json=canonical(exclusions),
        label_availability_evidence_json=canonical(availability), semantic_sha256=semantic_hash(data),
        implementation_sha256_json=canonical({str(p.relative_to(ROOT)).replace('\\','/'):sha256_file(p) for p in files}), created_at=created)]
    require(before == fingerprints(), 'SOURCE_MUTATION during preparation')
    return data

def implementation_files():
    return sorted((ROOT/'src/mos').glob('*.py')) + [ROOT/'src/builders/phase6_statistical_mos_v1_builder.py',
        ROOT/'src/audit/phase6_statistical_mos_v1_audit.py', ROOT/'tests/test_phase6_statistical_mos_v1.py']

def build(commit=False, output=OUTPUT):
    output = Path(output).resolve()
    require(output not in {p.resolve() for p,_ in SOURCES.values()}, 'Frozen source is READ ONLY')
    require(not output.exists(), 'REFUSE OVERWRITE: existing Phase6 artifact; inspect with audit')
    data = prepare()
    if not commit:
        conn = sqlite3.connect(':memory:')
        try:
            populate(conn, data)
            result = audit_connection(conn, data['sample'])
        finally:
            conn.close()
        return data, dict(result, mode='DRY_RUN')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=output.name+'.', suffix='.building', dir=output.parent)
    os.close(fd)
    staging = Path(name)
    try:
        conn = sqlite3.connect(staging)
        try:
            populate(conn, data)
            audit_connection(conn, data['sample'])
        finally:
            conn.close()
        result = audit(staging)
        require(json.loads(data['manifest'][0]['source_sha_before_json']) == fingerprints(), 'SOURCE_MUTATION before publication')
        with staging.open('r+b') as handle:
            os.fsync(handle.fileno())
        os.link(staging, output)  # Atomic no-overwrite publication, including races.
    finally:
        staging.unlink(missing_ok=True)
    result = audit(output)
    return data, dict(result, mode='COMMIT')

def export(data, result):
    REPORTS.mkdir(parents=True, exist_ok=True)
    manifest = data['manifest'][0]
    availability = json.loads(manifest['label_availability_evidence_json'])
    benchmark = json.loads(manifest['benchmark_json'])
    before = json.loads(manifest['source_sha_before_json'])
    after, after_checks, after_evidence = guardian()
    require(before == after and availability == after_evidence, 'SOURCE_MUTATION or changed availability evidence')
    (REPORTS/'PHASE6_SOURCE_PREFLIGHT.txt').write_text(json.dumps({
        'SOURCE_GUARDIAN':'PASS', 'SOURCE_SHA_BEFORE':before, 'SOURCE_SHA_AFTER':after,
        'verified_historical_final_label_settlement_days':availability['verified_historical_settlement_days'],
        'phase5_benchmark_reproduced':benchmark}, indent=2)+'\n', encoding='utf-8')
    for rows, name in [(data['metric'], 'PHASE6_MODEL_COMPARISON.csv'), (data['prediction'], 'PHASE6_WALK_FORWARD_PREDICTIONS.csv'),
                       (data['slice_metric'], 'PHASE6_SLICE_METRICS.csv'), (availability['daily_lineage'], 'PHASE6_LABEL_AVAILABILITY.csv'),
                       (benchmark, 'PHASE6_RAW_BENCHMARK.csv')]:
        with (REPORTS/name).open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (REPORTS/'PHASE6_SCHEMA.sql').write_text(';\n\n'.join(statements(data))+';\n', encoding='utf-8')
    deviations = {'Phase6_is_Statistical_MOS': 'YES', **{k:'NO' for k in (
        'Ground_Truth_changed','horizons_changed','Phase1_5_modified','entered_Phase7','entered_Phase8_ML',
        'entered_Phase9_probability','Meteostat_training','unproven_Intraday_observations_used','Walk_Forward_changed')}}
    def read_log(path):
        payload = path.read_bytes()
        return payload.decode('utf-16' if payload[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig', errors='replace')
    old = read_log(REPORTS/'PHASE6_OLD_TEST_RESULTS.txt')
    new_path = REPORTS/'PHASE6_TEST_RESULTS.txt'
    tests = read_log(new_path) if new_path.exists() else 'NOT_RUN'
    summary = dict(result, SOURCE_GUARDIAN='PASS', SOURCE_SHA_BEFORE=before, SOURCE_SHA_AFTER=after,
        SOURCE_CHECKS_BEFORE=json.loads(manifest['source_guardian_json']), SOURCE_CHECKS_AFTER=after_checks,
        phase5_benchmark_reproduced=benchmark, source_universe=dict(Counter(s['horizon'] for s in data['sample'])),
        excluded=json.loads(manifest['exclusion_json']), final_label_availability=availability,
        old_test_summary=old.strip().splitlines()[-1], phase6_test_summary=tests.strip().splitlines()[-1],
        FRAMEWORK_DEVIATION_CHECK=deviations, blocking_reason=manifest['block_reason'],
        candidate_selection='NONE: no legal MOS comparable OOS subset; no assessment of model quality is possible',
        stability='NOT_ESTIMABLE: zero legal MOS predictions, all season/month/year slices disclosed',
        model_M5='Horizon-specific correction is identifiable and identical to M1; hourly lead spans are preserved; no continuous daily lead model identified',
        model_M6='Predeclared equal weights M1/M3/M4; same availability gate, no retrospective selection',
        source_selection_bias='No OOS-driven selection or tuning performed',
        database_role=manifest['artifact_role'], phase7_development='NOT_STARTED')
    created_files = [str(p.relative_to(ROOT)).replace('\\','/') for p in implementation_files()]
    created_files += ['database/phase6_statistical_mos_v1.db']
    created_files += ['docs/phase6/'+n for n in ('PHASE6_DESIGN.md','PHASE6_BUILD_REPORT.md','PHASE6_FINAL_AUDIT.json',
        'PHASE6_SCHEMA.sql','PHASE6_MODEL_COMPARISON.csv','PHASE6_WALK_FORWARD_PREDICTIONS.csv','PHASE6_SLICE_METRICS.csv',
        'PHASE6_LABEL_AVAILABILITY.csv','PHASE6_RAW_BENCHMARK.csv','PHASE6_OLD_TEST_RESULTS.txt','PHASE6_TEST_RESULTS.txt',
        'PHASE6_SOURCE_PREFLIGHT.txt','PHASE6_BUILD_TERMINAL.txt','PHASE6_FILE_MANIFEST.json',
        'PHASE6_TEST_INITIAL_FAILURES.txt','PHASE6_TEST_SECOND_FAILURES.txt','PHASE6_SOURCE_PREFLIGHT_INITIAL_FAILURE.txt',
        'PHASE6_DRY_RUN.txt','PHASE6_READONLY_AUDIT.txt')]
    summary['created_or_modified_files'] = sorted(created_files)
    (REPORTS/'PHASE6_FINAL_AUDIT.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    lines = ['# PHASE 6 — STATISTICAL MOS V1', '', '**PHASE6_BUILD_STATUS = BLOCKED**',
        '**PHASE6_MOS_CANDIDATE = NONE**', '**NEXT_PHASE_STATUS = BLOCKED_FOR_PHASE7_FEATURE_ENGINEERING**', '',
        '施工得到可复现的 MOS 实现与独立阻塞审计资产，未完成可验收的严格历史 OOS 实验。',
        '数据库是 BLOCKED_AUDIT_AND_ABSTENTION_ARTIFACT_NOT_ACCEPTED_MOS_MODEL；artifact_audit=PASS 仅代表阻塞记录正确。', '',
        '## Source Guardian', '', '四个输入 SHA、integrity、foreign keys、schema、manifest、semantic 和关键行数核验通过。Before == After。',
        'TARGET_V1、ECMWF Archive、Issue Rule、DATA_V1、Phase5 semantic 均重新计算匹配。完整 schema、row counts、manifests 见 FINAL_AUDIT JSON。', '',
        'Phase5建库manifest的sidecars为空，当前production存在空WAL/SHM；物理主文件仍精确匹配冻结SHA。原manifest保留不改，当前sidecars在本阶段Before/After精确匹配。', '',
        '| Source | SHA Before = After |', '|---|---|']
    lines += [f'| {k} | {v["sha256"]} |' for k,v in before.items()]
    lines += ['', '## 测试', '', '旧测试：'+summary['old_test_summary'], 'Phase6 测试：'+summary['phase6_test_summary'],
        '阻塞情况下不能把合成数据上的算法测试通过描述成真实 MOS OOS 验收通过。',
        'Phase6初次测试暴露了新适配器把Phase5建库时sidecar清单与当前清单混淆、JSON tuple/list归一化问题；均修复于Phase6代码，旧业务逻辑未改。初次失败日志保留。', '',
        '## Universe / Phase5 独立复算', '', 'T0=729，T+1=729，T+2=728，总计2186。2025-08-07/T2 保持14/24排除。',
        'T0 是历史固定21:00 issue benchmark，不是生产 Intraday T0。', '',
        '| Horizon | N | Bias | MAE | RMSE | Median AE | P90 AE | Max AE |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    lines += ['| '+r['horizon']+' | '+str(r['n'])+' | '+' | '.join(f'{r[k]:.9f}' for k in ('bias','mae','rmse','median_ae','p90_ae','max_ae'))+' |' for r in benchmark]
    lines += ['', '## 历史标签结算准入', '', 'FINAL_LABEL_HISTORICAL_SETTLEMENT_NOT_PROVEN。', availability['reason'],
        f'历史 Bronze {availability["historical_bronze_rows"]} 行，导入日分布 {availability["ingest_day_counts"]}。',
        f'Candidate creation range: {availability["target_candidate_created_range"]}。', f'Target freeze range: {availability["target_frozen_range"]}。',
        '这不否认历史上天气已发生，而是冻结资料无法证明最终版本整数标签在每个训练截止时刻已合法可得。',
        '次日零点、最后观测时间、峰值时间和事后导入/冻结时间均不能冒充历史结算时间。既有 Intraday Discovery 结论未被扩大成对天气事实的否定。',
        '未修改 Ground Truth；没有把历史最终标签伪造为当时已可用。', '',
        '## 所有 Statistical MOS 候选', '', '| Model | T0 OOS N | T+1 OOS N | T+2 OOS N | MAE/RMSE/Bias改善 |', '|---|---:|---:|---:|---|']
    lines += [f'| {m} | 0 | 0 | 0 | NOT_ESTIMABLE |' for m in ('M1','M2','M3','M4','M5','M6')]
    lines += ['', 'M0 Raw 的全样本指标仅为复现 benchmark。各 MOS comparable subset 均为0，MOS及对应 Raw 指标均为NULL；没有将0预测视作0误差或与729样本Raw比较。',
        '13116 次 MOS 尝试均保存 BLOCKED_LABEL_AVAILABILITY、training_n=0、bias=NULL、forecast=NULL 和血缘；正式 MOS 预测0条。',
        '各模型定义、7/30日按issue之前最后结束的自然日锚定、最小历史7、回退和M6固定组合见 DESIGN/contract。M5合法使用horizon分组，与M1等价。', '',
        '## Slice Stability', '', '342个 season/month/year 切片全部披露，均 NOT_ESTIMABLE。不能声称改善跨季节稳定，也不能判断任何季节是否恶化。',
        'Integer diagnostic ONLY：在可比子集上定义，当前全部NULL；未生成概率、Brier、Log Loss、Calibration。', '',
        '## Q1–Q13', '',
        'Q1：三个horizon均偏冷，Bias分别为 '+', '.join(f'{r["horizon"]} {r["bias"]:.9f}°C' for r in benchmark)+'。',
        'Q2：Expanding MOS 未能合法评价，结算可用性未证明。', 'Q3：7-day MOS 未能合法评价。',
        'Q4：30-day MOS 未能合法评价。', 'Q5：Season-aware MOS 未能合法评价。',
        'Q6：Horizon correction 可按冻结语义定义，M5=M1；hourly lead span保留，未制造连续daily lead精度；效果未知。',
        'Q7：尚无证据证明简单MOS稳定优于Raw；这不等于证明MOS无效。',
        'Q8：T+1、T+2、T0 MAE absolute/%改善、RMSE改善、Bias变化均NOT_ESTIMABLE。',
        'Q9：跨season/month/year稳定性未知。', 'Q10：不能判断总体改善而局部恶化的情况。',
        'Q11：检测到潜在look-ahead路径：用最终历史标签并假设次日可用；已在准入门槛阻断。无实际未来标签训练、择优、特征污染或source mutation。',
        'Q12：PHASE6_MOS_CANDIDATE=NONE，原因是未形成合法可评估OOS实验，不能强行选模。',
        'Q13：当前不具备依据本施工指令验收Phase6并进入Phase7的条件。', '',
        '## FRAMEWORK_DEVIATION_CHECK', '']
    lines += [f'- {k}: {v}' for k,v in deviations.items()]
    lines += ['', '没有执行框架偏离。缺少历史标签结算证据是数据准入阻塞，不是自行改变框架。', '',
        '## 解除阻塞所需证据', '', '需要匹配冻结最终标签及修订血缘的历史发布/接收/结算证据，或已冻结且有证据支持的标签settlement规则。',
        '任何新证据必须走显式只读sidecar准入审计；不能改写冻结库或自动把估计延迟升级为真实availability。当前代码不接纳无证据手工时间。',
        '现有BLOCKED数据库永不覆盖；有新证据后需要显式新版本/新产物，保留本次历史。', '',
        '## Artifact hashes', '', 'Physical SHA256: '+result['phase6_physical_sha256'], 'Semantic SHA256: '+result['phase6_semantic_sha256'], '',
        '## 创建/修改文件清单', '']
    lines += ['- '+p for p in sorted(created_files)]
    lines += ['', '未修改Phase1–5源码/数据库。运行测试产生的缓存不属于正式交付资产。', '', '已停止；未进入Phase7。']
    (REPORTS/'PHASE6_BUILD_REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    file_manifest = {p:sha256_file(ROOT/p) for p in created_files if (ROOT/p).exists() and p not in ('docs/phase6/PHASE6_FILE_MANIFEST.json','docs/phase6/PHASE6_BUILD_TERMINAL.txt')}
    (REPORTS/'PHASE6_FILE_MANIFEST.json').write_text(json.dumps(file_manifest, indent=2)+'\n', encoding='utf-8')
    require(before == fingerprints(), 'SOURCE_MUTATION after export')
    return summary

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--commit', action='store_true')
    args = parser.parse_args()
    data, result = build(args.commit)
    if args.commit:
        result = export(data, result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('SOURCE_CHECKS_BEFORE','SOURCE_CHECKS_AFTER','final_label_availability')}, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result['PHASE6_BUILD_STATUS'] == 'PASS' else 2)
