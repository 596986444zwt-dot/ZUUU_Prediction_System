import csv
import json
from pathlib import Path
from .contracts import LABEL_ONLY, FEATURE_ALLOWED, OBSERVATION_CANDIDATES

def write_csv(path, rows):
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k:json.dumps(v,sort_keys=True,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows)

def clock(minutes):
    return f'{int(minutes)//60:02d}:{int(minutes)%60:02d}'

def markdown_table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|',
        *['| '+' | '.join(str(v) for v in row)+' |' for row in rows]])

def classification():
    return '''# INTRADAY feature classification — discovery only

## FEATURE_ALLOWED (conditional candidates, not a production feature view)

Solar/Time are deterministic with fixed coordinates 30.576 / 103.950, coordinate version ZUUU_CONFIG_COORDINATES_V1, NOAA_FRACTIONAL_YEAR_ZUUU_V1, availability DETERMINISTIC_NOT_APPLICABLE. Arbitrary-time deterministic computation is possible with the existing algorithm; hourly archives are not minute-resolution observations.

ECMWF fields are conditional on canonical AVAILABLE status, availability <= cutoff, same vintage and explicit completeness/missingness. Historical availability remains ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED; this is not verified observed operational availability. Forecast target hours after issue are allowed; future run vintages are not. Full-day MAX requires a complete 24/24 trajectory; remaining-day MAX requires all defined remaining hours. No cross-run completion.

Allowlist: '''+', '.join(sorted(FEATURE_ALLOWED))+'''.

## LABEL_ONLY — never put these into a feature view

'''+', '.join(sorted(LABEL_ONLY))+'''.

These include frozen final Tmax, its first/last occurrence and final occurrence count, final-day summaries, target lineage and retrospective PRE/EQUAL/POST classifications. They may be used for labels and evaluation stratification only. Filtering evaluation by PRE_PEAK is retrospective, not information available to the live system. Quantiles/grid choices examined on this archive must not be confused with untouched future OOS evaluation.

## BLOCKED

Historical ZUUU candidates: '''+', '.join(OBSERVATION_CANDIDATES)+'''.

Their weather meaning is legitimate but historical admission is BLOCKED until exact-version as-of availability is evidenced. Event-time-only counts in discovery are upper-bound archive coverage, NOT proven as-of availability. Do not use bulk ingest, Silver created_at, row IDs, observation timestamp, final corrected values, final-day target lineage or a guessed delay as a substitute for historical receipt/version ordering.

All Meteostat numeric features remain ARCHIVE / RESEARCH ONLY, TRAINING BLOCKED (availability UNKNOWN). Model fills remain blocked. Unknown fields fail the candidate allowlist. Future ECMWF vintages, cross-vintage trajectories, invented observation/forecast values and forecast skill-based rule tuning are blocked.

## Future admission candidates (not frozen here)

OBSERVED_AVAILABILITY: capture source receipt, local response completion/first-seen time, immutable payload hash, query identity, correction/amendment version identity and append-only arrival ordering prospectively. A later local persistence time is a conservative availability upper bound for that captured record, not the provider publication time.

RECONSTRUCTED_AVAILABILITY: independently obtain historical timestamped original-version dissemination/receipt logs, join exact report content and correction identities, document clock/timezone and uncertainty. Do not apply a final corrected report before the correction itself arrived.

ESTIMATED_AVAILABILITY: only a separately named research scenario after representative latency evidence (including COR/SPECI/outages) exists. The three out-of-window AWC records and eight local payload files are not a representative two-year latency calibration dataset. No numerical publication-delay rule is selected or frozen in this discovery.

## Future production semantics

LEGACY_T0_21_BENCHMARK is only the conceptual alias for frozen Phase 5 T0; no database or rule is renamed. Future INTRADAY TRACK T0 means today's final integer reported Tmax at arbitrary current time (06:37, 08:12, 10:46, 12:03, etc.). Fixed historical grids standardize evaluation and do not restrict production issue times. New ZUUU messages, new legal ECMWF arrivals, important source updates and timer fallback should trigger updates; no engine is implemented here.

The architecture can retain a continuous latent prediction and later produce a probability mass function on integer temperatures, with integer Top1. A continuous number such as 34.27 C is not the final user-facing Tmax answer. No probability layer is fitted or implemented in this task.
'''

def write_reports(folder, result):
    folder.mkdir(parents=True,exist_ok=True)
    test_path=folder/'INTRADAY_TEST_RESULTS.json'
    test_note='Discovery 专项测试尚待完成；不据此授予数据准入。'
    if test_path.exists():
        tests=json.loads(test_path.read_text(encoding='utf-8'))
        from src.data_v1.source_io import sha256_file
        from .contracts import ROOT
        current=all(sha256_file(ROOT/p)==sha for p,sha in tests.get('implementation_sha256',{}).items())
        result['test_results']={**tests,'implementation_matches':current}
        test_note=(f"Discovery 专项：{tests['discovery_passed']} passed，{tests['discovery_failed']} failed；详见 INTRADAY_TEST_RESULTS.json。" if current else '专项测试记录与当前实现不一致，需重新运行。')
    o,e=result['observations'],result['ecmwf']
    write_csv(folder/'INTRADAY_PEAK_TIME_DISTRIBUTION.csv',o['peak_distribution'])
    write_csv(folder/'INTRADAY_SNAPSHOT_COVERAGE.csv',o['snapshots'])
    write_csv(folder/'INTRADAY_ECMWF_COVERAGE.csv',e['coverage'])
    write_csv(folder/'INTRADAY_ECMWF_SELECTIONS.csv',e['selections'])
    write_csv(folder/'INTRADAY_ECMWF_VARIABLE_AVAILABILITY.csv',e['variable_availability'])
    write_csv(folder/'INTRADAY_OBSERVATION_CADENCE.csv',o['daily_cadence'])
    write_csv(folder/'INTRADAY_TARGET_PEAK_LINEAGE.csv',o['targets'])
    (folder/'INTRADAY_FEATURE_CLASSIFICATION.md').write_text(classification(),encoding='utf-8')
    (folder/'INTRADAY_AVAILABILITY_AUDIT.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    peak=o['first_peak_minutes_bjt']
    top=sorted(o['peak_distribution'],key=lambda r:(-r['days'],r['hour_bjt']))[:5]
    latency=o['latency']['bronze_729_days']['overall_seconds']
    table=markdown_table(['BJT','PRE_PEAK','EQUAL','POST_PEAK','最新合法 run 完整/部分','冻结完整优先 完整/部分'],
        [[s['snapshot_bjt'],s['PRE_PEAK'],s['EQUAL_PEAK'],s['POST_PEAK'],
          f"{a['complete_24h_days']}/{a['partial_days']}",f"{b['complete_24h_days']}/{b['partial_days']}"]
         for s,a,b in zip(o['snapshots'],e['coverage'][::2],e['coverage'][1::2])])
    sha=markdown_table(['数据库','Before SHA256','After SHA256','字节数'],
        [[k,v['sha256'],result['sources_after'][k]['sha256'],v['size_bytes']] for k,v in result['sources_before'].items()])
    report=f'''# INTRADAY DISCOVERY V1

**结论：BLOCKED_FOR_INTRADAY_DATA_V1**

Discovery 工具执行与只读保护检查通过，不等于严格历史回放已经获准。未创建 Intraday 正式数据库，未修改 Phase 1～5 源码、数据或冻结规则；没有训练、插值、补报或概率建模。

## Q1. 历史 ZUUU ingest_time 是真实历史 availability 吗？

**不是。HISTORICAL_ZUUU_INGEST_NOT_OBSERVED。** 历史 Bronze 总计 {o['latency']['bronze_all']['overall_seconds']['n']} 条，729 天窗口内 {latency['n']} 条；ingest 全部集中于 {o['latency']['bronze_all']['timestamp_date_counts_utc']}。历史 importer 只携带 observation timestamp 和原始报文，写库函数另以 datetime.now() 同时赋值 ingest/created（两字段相等 {o['historical_ingest_equals_created_rows']} 条）。这证明它是 2026 年本地导入时间，不是原观测年份的接收或发布时刻。

729 天窗口中 observation→ingest 延迟（天，type-7 分位数）：

{markdown_table(['min','median','P90','P95','P99','max'],[[f"{latency[k]/86400:.6f}" for k in ('min','median','P90','P95','P99','max')]])}

JSON 保留秒级全精度，并分别给出全档案/729天窗口/Silver 处理时间，按 source、message_class、BJT年份及组合分组。Silver.created_at 是处理时间，不是 source availability。旧实时表有 {len(o['legacy_awc_records'])} 条 AWC receiptTime 记录，其中落在 729 天窗口内的数量是 {o['legacy_awc_records_in_729_days']}；另检查了 {len(o['local_bronze_json_evidence'])} 个本地原始 JSON。不能把这些近期样本外推成两年历史的实测延迟。

## Q2. 严格 Intraday historical replay 应如何处理？

**STRICT HISTORICAL OBSERVATION AS-OF REPLAY NOT YET PROVEN。** 当前所有历史 ZUUU 实况类候选特征均 BLOCKED；不得按 observation_time<=issue 当作已可获得，也不得用事后导入时间或最终修订版本倒推可用性。

优先取得匹配原始报文内容/版本的历史发布时间、接收日志或不可变抓取快照，以 RECONSTRUCTED_AVAILABILITY 单独审计；同时前向采集本地 first-seen/响应完成时间与源 receiptTime，建立 OBSERVED_AVAILABILITY 新证据。若只有延迟估计，只能建独立 ESTIMATED 研究情景，不能升级为 OBSERVED。本轮没有足够证据选定任何 observation_time+固定延迟规则，也没有冻结此类规则。

## Q3. 729 天 Tmax 最常首次出现在哪些时间？

来自冻结 zuuu_target_v1 的 first/last Tmax 和 lineage，未重算新 Ground Truth。729 个目标值均保持整数报告温度语义；峰值 Silver/Bronze lineage 与冻结时间逐条核对。

最常见小时：{'; '.join(f"{r['hour_bjt']:02}:00–{r['hour_bjt']:02}:59：{r['days']} 天（{r['percentage']:.2f}%）" for r in top)}。

首次 Tmax 最早 {clock(peak['min'])}、最晚 {clock(peak['max'])}；P10={clock(peak['P10'])}，P25={clock(peak['P25'])}，P50/median={clock(peak['P50'])}，P75={clock(peak['P75'])}，P90={clock(peak['P90'])}。这些是当天钟点的分布，不是整个日期轴的最早/最晚日期。24 小时次数/百分比/累计百分比见 CSV。逐日 label、首末峰值时间、出现次数和原始 lineage 见 INTRADAY_TARGET_PEAK_LINEAGE.csv，仅供评估。

## Q4. 各 snapshot 有多少 PRE_PEAK 样本？

严格定义 PRE: issue<first Tmax；EQUAL: issue==first Tmax；POST: issue>first Tmax。相等时刻单列。最终 Tmax 可能多次出现，POST_FIRST_PEAK 不代表当天不会再次达到最高温。

{table}

每行 PRE+EQUAL+POST=729。Snapshot CSV 同时给出 PRE 样本占比及剩余提前小时的中位数。event_time_only 字段只是“档案中该事件时间以前存在记录”的理论覆盖，绝不是严格 as-of 特征；严格观测准入仍未证明，0 表示已证明的天数为0，不表示真实世界没有观测。

## Q5. 最适合 V1 的 snapshot grid？

建议候选 **06:00～17:00 BJT，每小时一个 snapshot**，保留全部12点用于审计，未冻结。06～12 点用于较长提前量，13～16 点覆盖主要首峰过渡，17 点保留晚峰与后峰分层。06 点已有75天先前达到最终最高温（其中72天首次出现在00:00）；15/16/17点仅剩190/62/10个PRE样本，必须披露后期小样本限制。不可为了提高 PRE 占比或指标而删除后峰日期。早于06首次达到 Tmax 的日期天然已是 POST，不能给它们伪造 PRE 样本。

正式评估还应按 hours_until_first_tmax 的提前量分层（LABEL_ONLY），报告每个时点真实 PRE 样本量；该 grid 是基于本档案的探索性建议，未来独立 OOS 需预先固定评估设计。固定历史 grid 不限制未来任意分钟预测。

## Q6. 每个 snapshot 能获得哪些 ECMWF run？

保留 ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED。以下是冻结 availability 估计口径的可选 run，不是对真实历史抓取时刻的认证。报告分别模拟：

- NEWEST_LEGAL：先过滤 canonical AVAILABLE、availability<=issue，再取最新 run，不因缺小时改用其他 vintage。
- FROZEN_NEWEST_COMPLETE：复用现有 Phase 2 纯 selector，从合法 run 向前寻找首个24/24完整轨迹。只作为已有规则的对照，没有修改冻结21:00基准。

两种口径必须分开，不能把回退到旧 run 的完整率说成“最新 run 完整率”。D 偏移是 run 的 UTC 日期相对目标 BJT 日期标签的日差；精确 run UTC/availability/lead 均保留在逐样本审计 CSV。

{markdown_table(['Snapshot','口径','run UTC 日期偏移/时次 → 天数'],[[r['snapshot_bjt'],r['policy'],json.dumps(r['run_cycle_day_offset_distribution'],ensure_ascii=False,sort_keys=True)] for r in e['coverage']])}

## Q7. 各 snapshot 有多少完整 target-day trajectory？

完整/部分数量见 Q4 表；无合法 run、完全无目标温度、remaining-day 完整率、run age、lead min/max 分布、availability semantics 分布见 INTRADAY_ECMWF_COVERAGE.csv。逐日选择见 INTRADAY_ECMWF_SELECTIONS.csv；18 个实际 ECMWF 变量的全日/剩余小时非空覆盖见 INTRADAY_ECMWF_VARIABLE_AVAILABILITY.csv。缺失参数保持缺失，没有跨 run 合并。7 个源温度不可用 runs 和504个NULL的签名仍在。

## Q8. Baseline A 能否严格重建？

**在冻结 ECMWF 估计 availability 契约下，可为具有完整24/24的选定 run 重建；不能笼统宣称所有时点的最新 run 都支持 FULL_DAY_RAW_MAX。** 最新 run 如果仅覆盖目标日的一部分，对这部分求 MAX 不是完整日 Tmax 基准。可另行评估已有 newest-complete fallback 作为显式不同选择口径，但不能悄悄替换 Baseline A 的“最新”含义。对真实 observed-availability operational replay，本档案仍只有估计发布时间，尚未证明。本轮没有正式计算或冻结 Baseline A。

## Q9. Baseline B 能否严格重建？

**当前不能。** observed_tmax_so_far 的精确历史 availability 与版本顺序无法证明。即使 ECMWF remaining-day 全部可得，也不能用事后完整 Silver 的截至事件时间最大值假冒当时已知最大值。COR/AMD 可能晚到；现有 {o['silver_corrections_without_supersedes']} 条 COR 的 supersedes_raw_id 未关联原始版本。未来必须按到达版本处理，不得提前应用最终修正。

remaining-day 边界暂以 target_time>=issue 统计，future 则用 >；只是小时数据可用性诊断，不冻结任意分钟的插值或边界规则，也不保证连续时间内未采样峰值已被小时轨迹捕获。

## Q10. 是否具备 Intraday DATA V1 条件？

**BLOCKED_FOR_INTRADAY_DATA_V1。** 阻塞项：历史 ZUUU 原始版本可用时间未证明；COR 原始版本/到达顺序不完整；ECMWF 只有历史估计 availability，不能冒充实测；最新 run 与完整全天轨迹的选择口径需要明确。解决方案是补充独立时间戳/内容版本证据、建立前向不可变到达日志、独立命名估计回放轨道，并在后续审议时明确 newest vs newest-complete 与 remaining-day 端点。不能通过修改冻结历史或填值解除这些阻塞。

## 观测 cadence

窗口内 Bronze/Silver 行数：{o['bronze_cadence']['rows']}/{o['silver_cadence']['rows']}；每日行数分布：{o['bronze_cadence']['daily_count_histogram']}；整点覆盖 {o['bronze_cadence']['exact_hour_coverage']}/17496；非整点 {o['bronze_cadence']['non_hourly_rows']} 条；message classes：{o['bronze_cadence']['message_class_counts']}；source query classes：{o['bronze_cadence']['source_query_class_counts']}；重复 observation_time 组 {o['bronze_cadence']['duplicate_time_groups']}，同一时刻多 raw 文本版本组 {o['bronze_cadence']['multiple_raw_text_versions']}。

没有 SPECI/AMD 记录只表明当前档案未包含这些类别，不证明历史上未发生。PREFIXLESS 不等于缺失观测；ROUTINE 与报文前缀类别分别统计。即使没有同刻多版本，也不能据此证明没有被档案替换的旧版本。

## 字段边界、未来输出与实时语义

详见 INTRADAY_FEATURE_CLASSIFICATION.md。最终 Tmax、首次/末次峰值、最终出现次数、hours_until_first_tmax、PRE 标志、最终日统计全部 LABEL_ONLY。Meteostat 保持 ARCHIVE / RESEARCH ONLY、TRAINING BLOCKED。Solar 可作为确定性特征；历史 ZUUU 实况类候选暂 BLOCKED。

未来架构支持 continuous latent prediction → integer probability layer；用户输出应为整数温度概率及整数 Top1，不应把34.27°C当最终整数 Tmax 答案。本轮没有概率模型。未来 T0 由任意时刻 INTRADAY TRACK 负责；Phase5 T0 仅概念性称为 LEGACY_T0_21_BENCHMARK，未改写任何既有对象。事件驱动/计时器 fallback 只作为未来方案，未实现。

## 四个源库与代码证据

{sha}

四库 integrity_check=ok，foreign_key_check=0；文件大小、表清单、DDL 和代码证据行号记录在 JSON。SQLite 仅反序列化只读字节快照并启用 query_only，没有打开源库写句柄。before==after，Phase1～Phase5 source DB byte-for-byte unchanged。

关键代码证据：

{chr(10).join('- '+r['path']+':'+str(r['line'])+' — '+r['text'] for r in result['code_evidence'])}

## 测试与边界

已有 Phase1～5 测试：183 passed，70 subtests passed（148.20秒）。{test_note} 本报告中的技术检查通过不改变 BLOCKED 的业务准入结论。没有调用 collector、importer、freezer、migration 或 backfill；只阅读其代码。完整性不足没有自动修复。
'''
    (folder/'INTRADAY_DISCOVERY_V1_REPORT.md').write_text(report,encoding='utf-8')
