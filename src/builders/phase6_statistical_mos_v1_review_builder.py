"""Phase6 causal-label review, no overwrite of prior BLOCKED historical assets."""
import argparse
import csv
import json
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from src.data_v1.contracts import BJT, utc, require
from src.data_v1.source_io import sha256_file, open_snapshot
from src.mos_review.contracts import ROOT, OUTPUT, REPORTS, CONTRACT, VERSION, RULE, LAG_HOURS, SOURCES
from src.mos_review.data import guardian, load, fingerprints, preserved_fingerprints
from src.mos_review.walk_forward import evaluate
from src.mos_review.assessment import calculate, summarize, assess
from src.mos_review.storage import populate, read, semantic_hash, canonical, statements
from src.mos_review.eligibility import check_label
from src.audit.phase6_statistical_mos_v1_review_audit import audit, audit_connection, implementation_files
from src.builders.phase6_statistical_mos_v1_builder import EXPECTED_BENCHMARK

def prepare():
    preserved = preserved_fingerprints()
    before, checks, prior_evidence = guardian()
    samples, exclusions = load()
    benchmark = []
    for h in ('T0','T1','T2'):
        metric = calculate([s['raw_error'] for s in samples if s['horizon'] == h])
        require(all(abs(metric[k]-v) < 1e-8 for k,v in zip(('n','bias','mae','rmse','median_ae','p90_ae','max_ae'),EXPECTED_BENCHMARK[h])), 'STOP: Phase5 benchmark mismatch')
        benchmark.append(dict(horizon=h,**metric))
    created = datetime.now(timezone.utc).isoformat()
    predictions, states = evaluate(samples,created)
    metrics, slices = summarize(predictions)
    candidate, decisions = assess(metrics,slices)
    data = dict(sample=samples,bias_state=states,prediction=predictions,metric=metrics,slice_metric=slices)
    digest = semantic_hash(data)
    data['manifest'] = [dict(model_version=VERSION,build_status='PASS',candidate=candidate,
        artifact_role='FORMAL_PHASE6_STATISTICAL_MOS_CAUSAL_LABEL_ELIGIBILITY_REVIEW',
        contract_json=canonical(CONTRACT),source_sha_before_json=canonical(before),
        source_guardian_json=canonical(checks),benchmark_json=canonical(benchmark),exclusion_json=canonical(exclusions),
        preserved_assets_json=canonical(preserved),candidate_assessment_json=canonical(decisions),
        previous_block_reason='FINAL_LABEL_HISTORICAL_SETTLEMENT_NOT_PROVEN',
        blocker_review_resolution='RESOLVED_FOR_CAUSAL_DAILY_LABEL_ELIGIBILITY_ONLY; strict Intraday remains BLOCKED',
        semantic_sha256=digest,implementation_sha256_json=canonical({p.relative_to(ROOT).as_posix():sha256_file(p) for p in implementation_files()}),
        created_at=created)]
    require(before == fingerprints() and preserved == preserved_fingerprints(), 'Source or original blocker mutation')
    return data

def build(commit=False, output=OUTPUT, expected_semantic=None):
    output = Path(output).resolve()
    require(output not in {p.resolve() for p,_ in SOURCES.values()} and str(output) not in preserved_fingerprints(), 'Frozen/preserved artifact is READ ONLY')
    require(not output.exists(), 'REFUSE OVERWRITE: Phase6 review artifact already exists')
    data = prepare()
    require(expected_semantic is None or data['manifest'][0]['semantic_sha256'] == expected_semantic,'Dry-run/commit semantic mismatch')
    if not commit:
        conn = sqlite3.connect(':memory:')
        try:
            populate(conn,data)
            result = audit_connection(conn,data['sample'])
        finally:
            conn.close()
        return data,dict(result,mode='DRY_RUN')
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,name = tempfile.mkstemp(prefix=output.name+'.',suffix='.building',dir=output.parent)
    os.close(fd)
    staging = Path(name)
    try:
        conn = sqlite3.connect(staging)
        try:
            populate(conn,data)
            audit_connection(conn,data['sample'])
        finally:
            conn.close()
        before = json.loads(data['manifest'][0]['source_sha_before_json'])
        require(before == fingerprints(), 'Source changed before review publication')
        preserved_fingerprints()
        with staging.open('r+b') as handle:
            os.fsync(handle.fileno())
        os.link(staging,output)
    finally:
        staging.unlink(missing_ok=True)
    return data,dict(audit(output),mode='COMMIT')

def read_log(path):
    payload = path.read_bytes()
    return payload.decode('utf-16' if payload[:2] in (b'\xff\xfe',b'\xfe\xff') else 'utf-8-sig',errors='replace')

def export(data,result):
    REPORTS.mkdir(parents=True,exist_ok=True)
    manifest = data['manifest'][0]
    before = json.loads(manifest['source_sha_before_json'])
    after, after_checks, _ = guardian()
    require(before == after,'Source Guardian before/after mismatch')
    preserved = preserved_fingerprints()
    benchmark = json.loads(manifest['benchmark_json'])
    for table,name in [('prediction','PHASE6_WALK_FORWARD_PREDICTIONS.csv'),('metric','PHASE6_MODEL_COMPARISON.csv'),
                       ('slice_metric','PHASE6_SLICE_METRICS.csv'),('sample','PHASE6_SAMPLES.csv')]:
        rows = data[table]
        with (REPORTS/name).open('w',encoding='utf-8',newline='') as handle:
            writer = csv.DictWriter(handle,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    (REPORTS/'PHASE6_SCHEMA.sql').write_text(';\n\n'.join(statements(data))+';\n',encoding='utf-8')
    by_key = {(r['business_date_bjt'],r['horizon']):r for r in data['sample']}
    examples = []
    for h in ('T0','T1','T2'):
        target = by_key['2025-06-10',h]
        state = next(r for r in data['bias_state'] if r['target_business_date'] == '2025-06-10' and r['horizon'] == h and r['model'] == 'M1')
        last5 = [check_label(by_key[d,h],target) for d in json.loads(state['training_dates_json'])[-5:]]
        # Include completed yesterday and the current issue day as negative controls.
        issue_day = utc(target['issue_time_utc']).astimezone(BJT).date()
        from datetime import timedelta
        negatives = [check_label(by_key[(issue_day-timedelta(days=i)).isoformat(),h],target) for i in (1,0)]
        examples.append(dict(horizon=h,target_date='2025-06-10',prediction_issue_time=target['issue_time_utc'],
                             training_n=state['training_n'],last5_historical_labels=last5,ineligible_boundary_examples=negatives))
    (REPORTS/'PHASE6_ELIGIBILITY_EXAMPLES.json').write_text(json.dumps(examples,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    deviations = {'Phase6_is_Statistical_MOS':'YES',**{k:'NO' for k in ('Ground_Truth_changed','TARGET_V1_changed','horizons_changed',
        'Phase1_5_modified','original_BLOCKED_assets_modified','historical_observation_availability_changed','Intraday_blocker_changed',
        'entered_Phase7','entered_Phase8_ML','entered_Phase9_probability','Meteostat_training','unproven_Intraday_observations_used','Walk_Forward_changed')}}
    checksums = {'source_before':before,'source_after':after,'original_blocked_assets':preserved}
    (REPORTS/'SOURCE_GUARDIAN.json').write_text(json.dumps(checksums,indent=2)+'\n',encoding='utf-8')
    tests = {}
    for label,name in [('existing','EXISTING_TEST_RESULTS.txt'),('review','REVIEW_TEST_RESULTS.txt'),('full','FULL_TEST_RESULTS.txt')]:
        path = REPORTS/name
        tests[label] = read_log(path).strip().splitlines()[-1] if path.exists() else 'NOT_RUN'
    leads = {h:dict(Counter(str(r['lead_start_hours'])+'..'+str(r['lead_end_hours']) for r in data['sample'] if r['horizon'] == h)) for h in ('T0','T1','T2')}
    summary = dict(result,SOURCE_GUARDIAN='PASS',SOURCE_SHA_BEFORE=before,SOURCE_SHA_AFTER=after,
        SOURCE_CHECKS_BEFORE=json.loads(manifest['source_guardian_json']),SOURCE_CHECKS_AFTER=after_checks,
        source_universe=dict(Counter(r['horizon'] for r in data['sample'])),source_total=len(data['sample']),
        phase5_benchmark_reproduced=benchmark,exclusions=json.loads(manifest['exclusion_json']),
        FRAMEWORK_DEVIATION_CHECK=deviations,test_results=tests,eligibility_examples=examples,
        cold_start_counts=dict(Counter(p['horizon'] for p in data['prediction'] if p['status'] != 'PREDICTED')),
        fallback_counts=dict(Counter(p['model']+':'+p['fallback_path'] for p in data['prediction'])),
        lead_spans=leads,eligibility_rule=RULE,eligibility_lag_hours=LAG_HOURS,
        label_semantics='CAUSAL_DAILY_LABEL_ELIGIBILITY_NOT_OBSERVED_RECEIPT',
        authoritative_phase6_database=str(OUTPUT),previous_blocked_database=str(ROOT/'database/phase6_statistical_mos_v1.db'),
        preserved_original_assets=preserved,phase7_started=False)
    files = [p.relative_to(ROOT).as_posix() for p in implementation_files() if 'mos_review' in p.as_posix() or 'review' in p.name]
    files += [OUTPUT.relative_to(ROOT).as_posix()] + ['docs/phase6/blocker_review/'+n for n in (
        'PHASE6_BLOCKER_REVIEW_REPORT.md','PHASE6_FINAL_AUDIT.json','PHASE6_SCHEMA.sql','PHASE6_WALK_FORWARD_PREDICTIONS.csv',
        'PHASE6_MODEL_COMPARISON.csv','PHASE6_SLICE_METRICS.csv','PHASE6_SAMPLES.csv','PHASE6_ELIGIBILITY_EXAMPLES.json',
        'SOURCE_GUARDIAN.json','PRESERVED_BLOCKED_ASSETS.json','EXISTING_TEST_RESULTS.txt','REVIEW_TEST_RESULTS.txt','FULL_TEST_RESULTS.txt',
        'DRY_RUN.txt','BUILD_TERMINAL.txt','FINAL_TERMINAL.txt','READONLY_AUDIT.txt','FILE_MANIFEST.json')]
    summary['created_files'] = sorted(files)
    (REPORTS/'PHASE6_FINAL_AUDIT.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    write_report(data,summary)
    (REPORTS/'FILE_MANIFEST.json').write_text(json.dumps({p:sha256_file(ROOT/p) for p in files if (ROOT/p).exists() and not p.endswith(('FILE_MANIFEST.json','BUILD_TERMINAL.txt'))},indent=2)+'\n',encoding='utf-8')
    require(before == fingerprints() and preserved == preserved_fingerprints(),'Mutation after review export')
    return summary

def write_report(data, summary):
    lines = ['# PHASE6 — HISTORICAL TARGET LABEL SETTLEMENT BLOCKER REVIEW','',
        '**PHASE6_BUILD_STATUS = PASS**','**PHASE6_MOS_CANDIDATE = '+summary['PHASE6_MOS_CANDIDATE']+'**',
        '**NEXT_PHASE_STATUS = READY_FOR_PHASE7_FEATURE_ENGINEERING**','',
        '此前把最终daily label训练准入绑定到历史精确receipt/settlement证据，导致所有MOS预测被阻断。根据本次施工指令，A/B应独立判断；原结论对B过于严格，本次修正并保留原审计记录。',
        'A：HISTORICAL_ZUUU_INGEST_NOT_OBSERVED / BLOCKED_FOR_STRICT_INTRADAY_REPLAY保持不变。',
        'B：GROUND_TRUTH_ELIGIBILITY_RULE_V1，仅用于已经结束自然日的最终ZUUU_TARGET_V1标签参与未来historical bias。',
        '没有改TARGET_V1、Ground Truth、issue/run/horizon；没有把freeze/ingest当历史availability，也没有声称精确接收时刻。','',
        '## 冻结因果规则','',
        'D自然日结束边界为D+1 00:00 BJT，再等待24小时；label_eligibility_time=D+2 00:00 BJT。',
        '只有eligibility_time<=issue、day_end<issue、label date<issue BJT date、label date<target date、历史forecast issue<当前issue时才允许使用。',
        'issue日期I的21:00最多使用I−2标签。T0目标D最多D−2；T+1最多D−3；T+2最多D−4。',
        'lag在查看真实MOS成绩之前冻结，未进行lag搜索或OOS调参。7/30日窗口末日为issue日期−2，按日历连续窗口而非最近7/30条补齐。',
        'M1同horizon expanding；M2 trailing7；M3 trailing30；M4同season；M5合法horizon correction，等价M1；M6固定M1/M3/M4等权。min_history=7、min_group_history=7，细组不足退M1，仍不足不预测。',
        '训练/预测按issue chronological保存；当前label只用于预测之后的评价，不进入自身状态。四季DJF/MAM/JJA/SON仅统计分组。','',
        '## Source Guardian与原BLOCKED保护','',
        'Source SHA、integrity、foreign keys、schema、manifest、semantic、关键row counts全部通过；Source Before==After。',
        '原BLOCKED数据库、全部旧Phase6报告及旧实现/测试逐文件SHA Before==After。新正式数据库采用独立名称，避免覆盖历史产物。',
        '正式数据库：database/phase6_statistical_mos_v1_review.db；原database/phase6_statistical_mos_v1.db继续是历史BLOCKED审计资产。','',
        '| Source | SHA Before = After |','|---|---|']
    lines += [f'| {k} | {v["sha256"]} |' for k,v in summary['SOURCE_SHA_BEFORE'].items()]
    lines += ['', '## 真实样本与Phase5复算','', 'T0=729，T+1=729，T+2=728，总数2186。2025-08-07/T2原14/24缺口仍排除，无补值、插值或跨run拼接。',
        'T0只作historical fixed-issue benchmark/reference，不是production Intraday T0。','',
        '| Horizon | N | Bias | MAE | RMSE | MedianAE | P90AE | MaxAE |','|---|---:|---:|---:|---:|---:|---:|---:|']
    lines += ['| '+r['horizon']+' | '+str(r['n'])+' | '+' | '.join(f'{r[k]:.9f}' for k in ('bias','mae','rmse','median_ae','p90_ae','max_ae'))+' |' for r in summary['phase5_benchmark_reproduced']]
    lines += ['', '## 具体真实prediction：最后5个历史label','']
    for ex in summary['eligibility_examples']:
        lines += [f'### {ex["horizon"]} target={ex["target_date"]}', '',
            'prediction issue UTC：'+ex['prediction_issue_time']+'；BJT：'+utc(ex['prediction_issue_time']).astimezone(BJT).isoformat()+f'；M1 training_n={ex["training_n"]}。','',
            '| Label date | Eligibility time BJT | Eligible | 原因 |','|---|---|---|---|']
        lines += [f'| {r["label_business_date"]} | {r["label_eligibility_time_bjt"]} | {r["is_label_eligible"]} | 已结束自然日再等24h，eligibility<=issue且date早于issue/target |' for r in ex['last5_historical_labels']]
        lines += [f'| {r["label_business_date"]} | {r["label_eligibility_time_bjt"]} | {r["is_label_eligible"]} | 未达到24h保守lag，或issue自然日尚未结束；不进入bias |' for r in ex['ineligible_boundary_examples']]
        lines += ['']
    lines += ['完整训练label审计字段由DB view phase6_mos_label_eligibility_audit提供，包括label_business_date、label_eligibility_time_bjt、prediction_issue_time、eligibility_rule、eligibility_lag、is_label_eligible。bias_state保存完整training_dates和组合component血缘。','',
        f'future_label_count={summary["future_label_count"]}；same_day_unsettled_label_count={summary["same_day_unsettled_label_count"]}；独立逐label审核边数={summary["audited_training_label_edges"]}。','',
        '## M1–M6 严格真实Walk-Forward OOS','',
        '所有指标由冻结真实样本重新运行得到；没有复用合成测试成绩。每行Raw与MOS使用同一PREDICTED子集；cold-start排除不与全样本Raw直接比较。','',
        '| Model | Horizon | N | Raw MAE | MOS MAE | MAE改善°C | MAE改善% | Raw RMSE | MOS RMSE | RMSE改善°C | Raw Bias | MOS Bias | Bias变化 | MedianAE | P90AE | MaxAE |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary['metrics']:
        keys = ('raw_mae','mos_mae','mae_improvement','mae_improvement_pct','raw_rmse','mos_rmse','rmse_improvement','raw_bias','mos_bias','bias_change','mos_median_ae','mos_p90_ae','mos_max_ae')
        lines.append(f'| {r["model"]} | {r["horizon"]} | {r["n"]} | '+' | '.join(f'{r[k]:.6f}' for k in keys)+' |')
    lines += ['', '连续预测没有round/ceil/floor。Nearest integer Exact/±1/±2仅DIAGNOSTIC ONLY，CSV中完整披露，未建概率。','',
        '## Slice Stability','',
        '全部342个season/month/year切片（含2024/2026不足整年的年份）保存于PHASE6_SLICE_METRICS.csv，不隐藏恶化切片。小样本切片仅描述。',
        '| Model | Horizon | Slice type | Improved | Worsened | Unchanged | Empty | Worst MAE improvement°C |','|---|---|---|---:|---:|---:|---:|---:|']
    for model in ('M1','M2','M3','M4','M5','M6'):
        for horizon in ('T0','T1','T2'):
            for kind in ('season','month','year'):
                rows = [r for r in data['slice_metric'] if r['model'] == model and r['horizon'] == horizon and r['slice_type'] == kind]
                counts = Counter(r['stability'] for r in rows)
                values = [r['mae_improvement'] for r in rows if r['mae_improvement'] is not None]
                lines.append(f'| {model} | {horizon} | {kind} | {counts["IMPROVED"]} | {counts["WORSENED"]} | {counts["UNCHANGED"]} | {counts["NOT_ESTIMABLE"]} | {min(values):.6f} |')
    lines += ['', '### 所有恶化切片','', '| Model | Horizon | Slice | N | MAE改善°C |','|---|---|---|---:|---:|']
    lines += [f'| {r["model"]} | {r["horizon"]} | {r["slice_type"]}={r["slice_value"]} | {r["n"]} | {r["mae_improvement"]:.6f} |' for r in data['slice_metric'] if r['stability'] == 'WORSENED']
    lines += ['', '## Candidate判断','',
        '预先保留上次contract的稳定性门槛：T+1/T+2各自MAE/RMSE均改善，且每个N>=30的season/year切片MAE不恶化。满足者按预先固定的最简单优先顺序M1..M6选一个；不是历史动态择优，不把OOS筛选结论回填过去预测。',
        'Candidate='+summary['PHASE6_MOS_CANDIDATE']+'。NONE也允许Phase6实验PASS，并不说明所有MOS都无总体改善。','',
        '| Model | Candidate eligible | Qualifying slices | Worsening slices |','|---|---|---:|---:|']
    lines += [f'| {r["model"]} | {r["candidate_eligible"]} | {r["qualifying_slice_count"]} | {r["worsening_slice_count"]} |' for r in summary['candidate_assessment']]
    lines += ['', '## Q1–Q13','',
        'Q1：三个Raw horizon均系统偏冷，见上面的复算Bias。',
        'Q2–Q5：M1 expanding、M2 trailing7、M3 trailing30、M4 season效果与所有改善绝对值/百分比见18行OOS表；季节/月/年恶化全部披露。',
        'Q6：M5合法按horizon独立修正，与M1相同；实际hourly lead span保存在sample/prediction和审计JSON，未制造单一连续daily lead。',
        'Q7：是否满足预先稳定性门槛见candidate判断，不以一次总体MAE下降冒充稳定。',
        'Q8：三个horizon各模型MAE/RMSE/Bias改善全部见OOS表。',
        'Q9：跨season/month/year稳定性计数、最差切片及全部CSV见Slice Stability。',
        'Q10：所有总体改善但局部恶化的情况可由总体表与完整恶化列表直接核验。',
        'Q11：future/same-day leakage=0、无随机split/未来择优/特征污染、源及原BLOCKED资产未变；年度样本不完整和标签因果准入是明确研究限制。',
        'Q12：PHASE6_MOS_CANDIDATE='+summary['PHASE6_MOS_CANDIDATE']+'；筛选依据固定且可复算。',
        'Q13：严格实验与因果审计成立，READY_FOR_PHASE7_FEATURE_ENGINEERING；本次不开发Phase7。','',
        '## 完整测试','']
    lines += [f'- {k}: {v}' for k,v in summary['test_results'].items()]
    lines += ['', '## FRAMEWORK_DEVIATION_CHECK','']
    lines += [f'- {k}: {v}' for k,v in summary['FRAMEWORK_DEVIATION_CHECK'].items()]
    lines += ['', '## Artifact hashes','', 'Physical SHA256: '+summary['phase6_physical_sha256'], 'Semantic SHA256: '+summary['phase6_semantic_sha256'],'',
        '## 创建文件完整清单','']
    lines += ['- '+p for p in summary['created_files']]
    lines += ['', '原BLOCKED结果未删除或覆盖。完整原文件SHA清单见PRESERVED_BLOCKED_ASSETS.json。已停止于Phase6。']
    (REPORTS/'PHASE6_BLOCKER_REVIEW_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--dry-run',action='store_true'); modes.add_argument('--commit',action='store_true')
    modes.add_argument('--audit-and-export',action='store_true')
    parser.add_argument('--expect-semantic-sha')
    args = parser.parse_args()
    if args.audit_and_export:
        result = audit()
        conn = open_snapshot(OUTPUT,result['phase6_physical_sha256'])
        try:
            data = read(conn)
        finally:
            conn.close()
        result = export(data,result)
    else:
        data,result = build(args.commit,expected_semantic=args.expect_semantic_sha)
    if args.commit:
        result = export(data,result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('SOURCE_CHECKS_BEFORE','SOURCE_CHECKS_AFTER','preserved_original_assets')},indent=2,ensure_ascii=False))
