# T0 Experimental V1 实施报告

身份：T0_EXPERIMENTAL / FORWARD_VALIDATION。生成时间：2026-10-05T05:36:36.940035+00:00。

独立 SQLite：database/t0_forward_v1/t0_forward_v1.db；WAL、synchronous FULL、foreign keys ON。命名空间 FORWARD_VALIDATION；测试库 TEST_FIXTURE，测试无法写入正式数据库。首次 schema 创建及本实验自身添加 publication 表的迁移均有身份/既有 trigger 校验，不触碰任何正式 schema。

表职责：metadata 版本；transport_receipt 原始请求；receipt_publication durable 可查询时刻；zuuu_receipt_ledger 与 sighting 原报文/再次看到；ecmwf_receipt_ledger 与 sighting run/version；prediction_snapshot 与 snapshot_observation 不可变上下文/输入链接；prediction_method_output 两方法；ground_truth_evidence 独立标签证据；settlement 追加评分；evaluation_state 追加汇总；worker_event 运维事件。

完整 DDL 如下。Archive 在建表后为所有表安装 update/delete/duplicate-primary-key 拒绝触发器，迁移和每次启动验证守卫；不能靠 OR REPLACE 绕过。运行日志、锁和 CSV 是独立可变操作文件，不属于预测历史。

```sql
CREATE TABLE t0_metadata (
    version TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('FORWARD_VALIDATION','TEST_FIXTURE')),
    created_at TEXT NOT NULL
);
CREATE TABLE t0_transport_receipt (
    receipt_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    download_start_time TEXT NOT NULL,
    download_complete_time TEXT NOT NULL,
    body_complete INTEGER NOT NULL CHECK(body_complete IN (0,1)),
    payload_sha256 TEXT NOT NULL,
    spool_identity TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE t0_zuuu_receipt_ledger (
    observation_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES t0_transport_receipt(receipt_id),
    observation_time TEXT NOT NULL,
    business_date_bjt TEXT NOT NULL,
    first_seen_time TEXT NOT NULL,
    ingest_time TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_message TEXT NOT NULL,
    raw_payload_hash TEXT NOT NULL,
    temperature_c INTEGER,
    message_class TEXT NOT NULL,
    is_cor INTEGER NOT NULL CHECK(is_cor IN (0,1)),
    is_speci INTEGER NOT NULL CHECK(is_speci IN (0,1)),
    qc_status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE t0_receipt_publication (
    receipt_id TEXT PRIMARY KEY REFERENCES t0_transport_receipt(receipt_id),
    published_at TEXT NOT NULL,
    publication_basis TEXT NOT NULL
);
CREATE INDEX t0_zuuu_asof ON t0_zuuu_receipt_ledger(business_date_bjt, first_seen_time, ingest_time);
CREATE TABLE t0_zuuu_sighting (
    sighting_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES t0_zuuu_receipt_ledger(observation_id),
    receipt_id TEXT NOT NULL REFERENCES t0_transport_receipt(receipt_id),
    seen_time TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE t0_ecmwf_receipt_ledger (
    run_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES t0_transport_receipt(receipt_id),
    run_time TEXT NOT NULL,
    first_seen_time TEXT NOT NULL,
    download_start_time TEXT NOT NULL,
    download_complete_time TEXT NOT NULL,
    ready_at TEXT NOT NULL,
    source TEXT NOT NULL,
    product_identity TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    availability_status TEXT NOT NULL,
    valid_hour_count INTEGER NOT NULL,
    trajectory_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX t0_ecmwf_asof ON t0_ecmwf_receipt_ledger(first_seen_time,ready_at,run_time);
CREATE TABLE t0_ecmwf_sighting (
    sighting_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES t0_ecmwf_receipt_ledger(run_id),
    receipt_id TEXT NOT NULL REFERENCES t0_transport_receipt(receipt_id),
    seen_time TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE t0_prediction_snapshot (
    prediction_id TEXT PRIMARY KEY,
    prediction_time TEXT NOT NULL,
    target_date TEXT NOT NULL,
    cutoff_bjt TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('SCHEDULED','EVENT','STARTUP','RECOVERY')),
    latest_observation_id TEXT REFERENCES t0_zuuu_receipt_ledger(observation_id),
    ecmwf_run_id TEXT REFERENCES t0_ecmwf_receipt_ledger(run_id),
    tmax_so_far REAL,
    remaining_trajectory_hash TEXT,
    status TEXT NOT NULL,
    code_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    method_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE t0_snapshot_observation (
    prediction_id TEXT NOT NULL REFERENCES t0_prediction_snapshot(prediction_id),
    observation_id TEXT NOT NULL REFERENCES t0_zuuu_receipt_ledger(observation_id),
    PRIMARY KEY(prediction_id,observation_id)
);
CREATE TABLE t0_prediction_method_output (
    prediction_id TEXT NOT NULL REFERENCES t0_prediction_snapshot(prediction_id),
    method TEXT NOT NULL CHECK(method IN ('LEVEL0','L1_A')),
    prediction REAL,
    integer_prediction INTEGER,
    status TEXT NOT NULL,
    candidate_status TEXT NOT NULL CHECK(candidate_status IN ('EXPERIMENTAL_SHADOW','EXPERIMENTAL_ACTIVE_CANDIDATE')),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(prediction_id,method)
);
CREATE TABLE t0_ground_truth_evidence (
    truth_id TEXT PRIMARY KEY,
    target_date TEXT NOT NULL,
    actual_integer_tmax INTEGER,
    confirmation_time TEXT NOT NULL,
    status TEXT NOT NULL,
    supersedes_truth_id TEXT REFERENCES t0_ground_truth_evidence(truth_id),
    payload_json TEXT NOT NULL
);
CREATE TABLE t0_settlement (
    settlement_id TEXT PRIMARY KEY,
    prediction_id TEXT NOT NULL,
    method TEXT NOT NULL,
    truth_id TEXT NOT NULL REFERENCES t0_ground_truth_evidence(truth_id),
    actual_integer_tmax INTEGER NOT NULL,
    continuous_error REAL NOT NULL,
    absolute_error REAL NOT NULL,
    integer_prediction INTEGER NOT NULL,
    integer_exact_hit INTEGER NOT NULL CHECK(integer_exact_hit IN (0,1)),
    within_1c INTEGER NOT NULL CHECK(within_1c IN (0,1)),
    created_at TEXT NOT NULL,
    FOREIGN KEY(prediction_id,method) REFERENCES t0_prediction_method_output(prediction_id,method),
    UNIQUE(prediction_id,method,truth_id)
);
CREATE TABLE t0_evaluation_state (
    evaluation_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE t0_worker_event (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

```
