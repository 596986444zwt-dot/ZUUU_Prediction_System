"""
ECMWF Historical Backfill V3.2

目标：
1. 保留 V3.1 的历史 ECMWF 下载、Bronze、Raw 入库逻辑。
2. 已经存在的 canonical Raw 自动跳过。
3. Open-Meteo 明确返回：
   "The requested model run is not available"
   时，将其识别为永久档案缺失。
4. 永久缺失 Run 后续自动跳过，不再重复请求。
5. --plan 严格只读。
6. 429 / 5xx / 网络超时仍按真正失败处理。
7. 不删除任何 Raw / Bronze。
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from config.settings import (
    DATABASE_PATH,
    ECMWF_BRONZE_DIR,
    STATION_LATITUDE,
    STATION_LONGITUDE,
)

from database.schema import (
    backup_database,
    connect,
    ensure_schema,
)

from src.ecmwf_contract import (
    DATA_SPEC_VERSION,
    FORECAST_HOURS,
    HOURLY_VARIABLES,
    MODEL,
    SOURCE,
    archive_cycle,
    iso_utc,
    parse_utc,
    validate_payload,
)


# ============================================================
# 基础配置
# ============================================================

BACKFILL_VERSION = "ECMWF_HISTORICAL_BACKFILL_V3_2"

AVAILABILITY_RULE_VERSION = (
    "HISTORICAL_ECMWF_DISSEMINATION_END_V1"
)

ARCHIVE_SCHEDULE_VERSION = (
    "ECMWF_HISTORICAL_ARCHIVE_SCHEDULE_V1"
)

API_URL = (
    "https://single-runs-api.open-meteo.com/v1/forecast"
)

API_MODEL = "ecmwf_ifs"

ARCHIVE_TYPE = "historical"

BRONZE_DIR = ECMWF_BRONZE_DIR

LATITUDE = STATION_LATITUDE
LONGITUDE = STATION_LONGITUDE


# ============================================================
# 历史档案时间范围
# ============================================================

MINIMUM_DATE = date(2024, 3, 14)

EARLY_END_DATE = date(2024, 8, 5)

TRANSITION_DATE = date(2024, 8, 6)

FULL_FOUR_RUN_START_DATE = date(2024, 8, 7)


# ============================================================
# 网络设置
# ============================================================

TIMEOUT = 90

MAX_ATTEMPTS = 4

RETRY_DELAYS = (
    5,
    15,
    30,
)

REQUEST_INTERVAL_SECONDS = 0.8


# ============================================================
# 时间工具
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def parse_date(value):
    return datetime.strptime(
        value,
        "%Y-%m-%d",
    ).date()


# ============================================================
# ECMWF 历史 Run Schedule
# ============================================================

def get_run_hours(day):

    if MINIMUM_DATE <= day <= EARLY_END_DATE:
        return (
            0,
            12,
        )

    if day == TRANSITION_DATE:
        return (
            0,
            12,
            18,
        )

    if day >= FULL_FOUR_RUN_START_DATE:
        return (
            0,
            6,
            12,
            18,
        )

    return ()


def build_run_time(day, hour):

    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        tzinfo=timezone.utc,
    )


def estimate_source_available_time(run_time):

    # 注意：
    # 这是历史档案可用时间估算，
    # 并不是历史真实发布时间。

    return run_time + timedelta(
        hours=6,
        minutes=12,
    )


def build_run_plan(
    start_date,
    end_date,
    now=None,
):

    if start_date < MINIMUM_DATE:
        raise ValueError(
            "start_date earlier than archive minimum"
        )

    if end_date < start_date:
        raise ValueError(
            "end_date earlier than start_date"
        )

    now = (
        utc_now()
        if now is None
        else parse_utc(now)
    )

    runs = []

    day = start_date

    while day <= min(
        end_date,
        now.date(),
    ):

        for hour in get_run_hours(day):

            run_time = build_run_time(
                day,
                hour,
            )

            if (
                estimate_source_available_time(
                    run_time
                )
                <= now
            ):
                runs.append(run_time)

        day += timedelta(days=1)

    return runs


# ============================================================
# ECMWF Cycle
# ============================================================

def get_archive_model_cycle(run_time):

    return archive_cycle(
        run_time,
        API_MODEL,
    )


# ============================================================
# 数据库
# ============================================================

def connect_db(
    *,
    readonly=False,
    path=None,
):

    return connect(
        DATABASE_PATH
        if path is None
        else path,
        readonly=readonly,
    )


# ============================================================
# API 请求参数
# ============================================================

def build_request_params(run_time):

    return {

        "latitude":
            LATITUDE,

        "longitude":
            LONGITUDE,

        "hourly":
            ",".join(
                HOURLY_VARIABLES
            ),

        "forecast_hours":
            FORECAST_HOURS,

        "timezone":
            "UTC",

        "models":
            API_MODEL,

        "run":
            run_time.strftime(
                "%Y-%m-%dT%H:%M"
            ),

        "wind_speed_unit":
            "kmh",

        "temperature_unit":
            "celsius",

        "precipitation_unit":
            "mm",
    }


# ============================================================
# Task Identity
# ============================================================

def task_key(run_time):

    identity = [

        MODEL,

        API_MODEL,

        iso_utc(run_time),

        LATITUDE,

        LONGITUDE,

        DATA_SPEC_VERSION,

        ARCHIVE_TYPE,
    ]

    encoded = json.dumps(
        identity,
        separators=(",", ":"),
    ).encode()

    return hashlib.sha256(
        encoded
    ).hexdigest()


# ============================================================
# 查找已有 canonical Raw
# ============================================================

def find_existing_raw(
    connection,
    run_time,
):

    if connection is None:
        return None

    table = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table'
          AND name='ecmwf_raw_runs'
        """
    ).fetchone()

    if not table:
        return None

    available = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(ecmwf_raw_runs)"
        )
    }

    required = {
        "api_model",
        "data_spec_version",
        "archive_type",
    }

    if not required <= available:
        return None

    rows = connection.execute(
        """
        SELECT *
        FROM ecmwf_raw_runs
        WHERE model=?
          AND run_time_utc=?
          AND api_model=?
          AND data_spec_version=?
          AND archive_type=?
        ORDER BY id DESC
        """,
        (
            MODEL,
            iso_utc(run_time),
            API_MODEL,
            DATA_SPEC_VERSION,
            ARCHIVE_TYPE,
        ),
    )

    for row in rows:

        try:

            params = json.loads(
                row["request_parameters_json"]
            )

            if (
                float(params["latitude"])
                != LATITUDE
            ):
                continue

            if (
                float(params["longitude"])
                != LONGITUDE
            ):
                continue

            if (
                params.get("models")
                != API_MODEL
            ):
                continue

            if (
                row["model_cycle"]
                != get_archive_model_cycle(
                    run_time
                )
            ):
                raise ValueError(
                    "Archive cycle metadata "
                    "requires migration"
                )

            digest = hashlib.sha256(
                row["raw_json"].encode(
                    "utf-8"
                )
            ).hexdigest()

            if (
                digest
                != row["content_sha256"]
            ):
                raise ValueError(
                    "Raw checksum mismatch"
                )

            validate_payload(
                json.loads(
                    row["raw_json"]
                ),
                run_time,
            )

            return row

        except (
            ValueError,
            TypeError,
            KeyError,
        ) as exc:

            print(
                f"Raw {row['id']} "
                f"requires review: {exc}"
            )

    return None


# ============================================================
# 创建任务
# ============================================================

def ensure_status_task(
    connection,
    run_time,
):

    now = iso_utc(
        utc_now()
    )

    connection.execute(
        """
        INSERT OR IGNORE
        INTO ecmwf_backfill_tasks
        (
            task_key,
            model,
            api_model,
            run_time_utc,
            latitude,
            longitude,
            data_spec_version,
            archive_type,
            status,
            collector_version,
            created_at_utc,
            updated_at_utc
        )
        VALUES
        (
            ?,?,?,?,?,?,?,?,
            'PENDING',
            ?,?,?
        )
        """,
        (
            task_key(run_time),

            MODEL,

            API_MODEL,

            iso_utc(run_time),

            LATITUDE,

            LONGITUDE,

            DATA_SPEC_VERSION,

            ARCHIVE_TYPE,

            BACKFILL_VERSION,

            now,

            now,
        ),
    )

    connection.commit()


# ============================================================
# 更新任务状态
# ============================================================

def mark_status(
    connection,
    run_time,
    status,
    *,
    http_status=None,
    error=None,
    raw_run_id=None,
    increment_attempt=False,
    commit=True,
):

    now = iso_utc(
        utc_now()
    )

    connection.execute(
        """
        UPDATE ecmwf_backfill_tasks
        SET
            status=?,
            collector_version=?,
            updated_at_utc=?,
            last_http_status=?,
            last_error=?,
            raw_run_id=?,
            completed_time_utc=?,

            attempt_count=
                attempt_count + ?,

            first_attempt_time_utc=
                CASE
                    WHEN ?
                    THEN COALESCE(
                        first_attempt_time_utc,
                        ?
                    )
                    ELSE first_attempt_time_utc
                END,

            last_attempt_time_utc=
                CASE
                    WHEN ?
                    THEN ?
                    ELSE last_attempt_time_utc
                END

        WHERE task_key=?
        """,
        (
            status,

            BACKFILL_VERSION,

            now,

            http_status,

            (
                str(error)[:2000]
                if error
                else None
            ),

            raw_run_id,

            (
                now
                if status
                in (
                    "SUCCESS",
                    "SKIPPED_EXISTING",
                )
                else None
            ),

            int(
                increment_attempt
            ),

            int(
                increment_attempt
            ),

            now,

            int(
                increment_attempt
            ),

            now,

            task_key(
                run_time
            ),
        ),
    )

    if commit:
        connection.commit()


# ============================================================
# 判断是否属于“永久档案不存在”
# ============================================================

def is_archive_not_available_error(
    error,
):

    if not error:
        return False

    text = str(
        error
    ).lower()

    return (
        "requested model run "
        "is not available"
        in text
    )


# ============================================================
# 查询已经确认永久缺档的 Run
# ============================================================

def get_known_archive_unavailable_runs(
    connection,
    runs,
):

    if connection is None:
        return set()

    table = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table'
          AND name='ecmwf_backfill_tasks'
        """
    ).fetchone()

    if not table:
        return set()

    columns = {
        row[1]
        for row in connection.execute(
            """
            PRAGMA table_info(
                ecmwf_backfill_tasks
            )
            """
        )
    }

    required = {
        "model",
        "api_model",
        "run_time_utc",
        "data_spec_version",
        "archive_type",
        "last_http_status",
        "last_error",
    }

    if not required <= columns:
        return set()

    wanted = {
        iso_utc(run_time)
        for run_time in runs
    }

    unavailable = set()

    rows = connection.execute(
        """
        SELECT
            run_time_utc,
            last_http_status,
            last_error
        FROM ecmwf_backfill_tasks
        WHERE model=?
          AND api_model=?
          AND data_spec_version=?
          AND archive_type=?
        """,
        (
            MODEL,
            API_MODEL,
            DATA_SPEC_VERSION,
            ARCHIVE_TYPE,
        ),
    )

    for row in rows:

        run_text = row[
            "run_time_utc"
        ]

        if run_text not in wanted:
            continue

        if (
            row["last_http_status"]
            == 400
            and
            is_archive_not_available_error(
                row["last_error"]
            )
        ):
            unavailable.add(
                run_text
            )

    return unavailable


# ============================================================
# 下载单个 Run
# ============================================================

def download_run(
    session,
    run_time,
    on_attempt=None,
):

    last_error = None
    last_http = None

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):

        if on_attempt:
            on_attempt()

        last_http = None

        try:

            response = session.get(
                API_URL,
                params=build_request_params(
                    run_time
                ),
                timeout=TIMEOUT,
            )

            last_http = (
                response.status_code
            )

            # --------------------------------
            # 成功
            # --------------------------------

            if last_http == 200:

                try:

                    data = response.json()

                    validate_payload(
                        data,
                        run_time,
                    )

                except (
                    ValueError,
                    TypeError,
                    KeyError,
                ) as exc:

                    return {
                        "success": False,
                        "archive_unavailable": False,
                        "retryable": False,
                        "attempts": attempt,
                        "http_status": 200,
                        "error":
                            f"Invalid response: {exc}",
                        "data": None,
                        "request_url":
                            response.url,
                    }

                return {
                    "success": True,
                    "archive_unavailable": False,
                    "retryable": False,
                    "attempts": attempt,
                    "http_status": 200,
                    "error": None,
                    "data": data,
                    "raw_text":
                        response.text,
                    "request_url":
                        response.url,
                }

            # --------------------------------
            # HTTP 非200
            # --------------------------------

            last_error = (
                f"HTTP {last_http}: "
                f"{response.text[:1000]}"
            )

            # --------------------------------
            # 永久档案不存在
            # --------------------------------

            if (
                last_http == 400
                and
                is_archive_not_available_error(
                    last_error
                )
            ):

                return {
                    "success": False,
                    "archive_unavailable": True,
                    "retryable": False,
                    "attempts": attempt,
                    "http_status": 400,
                    "error":
                        last_error,
                    "data": None,
                    "request_url":
                        response.url,
                }

            # --------------------------------
            # 其他 4xx 不重试
            # --------------------------------

            if (
                last_http != 429
                and
                not (
                    500
                    <= last_http
                    <= 599
                )
            ):

                return {
                    "success": False,
                    "archive_unavailable": False,
                    "retryable": False,
                    "attempts": attempt,
                    "http_status":
                        last_http,
                    "error":
                        last_error,
                    "data":
                        None,
                    "request_url":
                        response.url,
                }

        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:

            last_error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        except requests.exceptions.RequestException as exc:

            return {
                "success": False,
                "archive_unavailable": False,
                "retryable": False,
                "attempts": attempt,
                "http_status": None,
                "error": str(exc),
                "data": None,
                "request_url": None,
            }

        # --------------------------------
        # 重试
        # --------------------------------

        if attempt < MAX_ATTEMPTS:

            delay = (
                RETRY_DELAYS[
                    attempt - 1
                ]
            )

            if last_http == 429:

                retry_after = (
                    response.headers.get(
                        "Retry-After",
                        "",
                    )
                )

                if retry_after.isdigit():

                    delay = min(
                        300,
                        max(
                            delay,
                            int(
                                retry_after
                            ),
                        ),
                    )

            print(
                f"Retry "
                f"{run_time.isoformat()} "
                f"in {delay}s: "
                f"{last_error}",
                flush=True,
            )

            time.sleep(
                delay
            )

    return {
        "success": False,
        "archive_unavailable": False,
        "retryable": True,
        "attempts":
            MAX_ATTEMPTS,
        "http_status":
            last_http,
        "error":
            last_error,
        "data":
            None,
        "request_url":
            None,
    }


# ============================================================
# Bronze
# ============================================================

def save_bronze(
    run_time,
    ingest_time,
    raw_text,
):

    BRONZE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    name = (
        "ecmwf_ifs_hres_run_"
        f"{run_time:%Y%m%dT%H%MZ}"
        "_ingest_"
        f"{ingest_time:%Y%m%dT%H%M%S_%fZ}"
        ".json"
    )

    path = (
        BRONZE_DIR
        / name
    )

    raw_bytes = raw_text.encode(
        "utf-8"
    )

    digest = hashlib.sha256(
        raw_bytes
    ).hexdigest()

    metadata = {

        "run_time_utc":
            iso_utc(
                run_time
            ),

        "ingest_time_utc":
            iso_utc(
                ingest_time
            ),

        "request_parameters":
            build_request_params(
                run_time
            ),

        "content_sha256":
            digest,

        "collector_version":
            BACKFILL_VERSION,

        "archive_type":
            ARCHIVE_TYPE,

        "data_spec_version":
            DATA_SPEC_VERSION,
    }

    outputs = (

        (
            path,
            raw_bytes,
        ),

        (
            path.with_suffix(
                ".meta.json"
            ),
            json.dumps(
                metadata,
                ensure_ascii=False,
                indent=2,
            ).encode(
                "utf-8"
            ),
        ),
    )

    for destination, content in outputs:

        if destination.exists():
            raise FileExistsError(
                destination
            )

        temporary = (
            destination.with_suffix(
                destination.suffix
                + ".tmp"
            )
        )

        with temporary.open(
            "xb"
        ) as output:

            output.write(
                content
            )

        temporary.replace(
            destination
        )

    return (
        path,
        raw_text,
        digest,
    )


# ============================================================
# Raw 入库
# ============================================================

def insert_raw(
    connection,
    run_time,
    ingest_time,
    request_url,
    request_params,
    raw_text,
    bronze_path,
    sha256,
    *,
    commit=True,
    evidence=None,
):

    validate_payload(
        json.loads(
            raw_text
        ),
        run_time,
    )

    if (
        request_params.get(
            "models"
        )
        != API_MODEL
    ):
        raise ValueError(
            "Request product does not "
            "match backfill product"
        )

    if (
        float(
            request_params.get(
                "latitude",
                "nan",
            )
        )
        != LATITUDE
    ):
        raise ValueError(
            "Request latitude mismatch"
        )

    if (
        float(
            request_params.get(
                "longitude",
                "nan",
            )
        )
        != LONGITUDE
    ):
        raise ValueError(
            "Request longitude mismatch"
        )

    calculated = hashlib.sha256(
        raw_text.encode(
            "utf-8"
        )
    ).hexdigest()

    if calculated != sha256:
        raise ValueError(
            "Raw checksum mismatch"
        )

    cursor = connection.execute(
        """
        INSERT INTO ecmwf_raw_runs
        (
            model,
            model_cycle,
            run_time_utc,
            source_available_time_utc,
            availability_type,
            ingest_time_utc,
            source,
            request_url,
            request_parameters_json,
            raw_json,
            bronze_file_path,
            content_sha256,
            created_at_utc,
            archive_type,
            data_spec_version,
            collector_version,
            availability_rule_version,
            api_model,
            requested_latitude,
            requested_longitude,
            recovery_evidence_json
        )
        VALUES
        (
            ?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            MODEL,

            get_archive_model_cycle(
                run_time
            ),

            iso_utc(
                run_time
            ),

            iso_utc(
                estimate_source_available_time(
                    run_time
                )
            ),

            "official_schedule_estimate",

            iso_utc(
                ingest_time
            ),

            SOURCE,

            request_url,

            json.dumps(
                request_params,
                ensure_ascii=False,
                sort_keys=True,
            ),

            raw_text,

            str(
                bronze_path
            ),

            sha256,

            iso_utc(
                utc_now()
            ),

            ARCHIVE_TYPE,

            DATA_SPEC_VERSION,

            (
                BACKFILL_VERSION
                if evidence is None
                else
                "ECMWF_BRONZE_RECOVERY_V2"
            ),

            AVAILABILITY_RULE_VERSION,

            API_MODEL,

            LATITUDE,

            LONGITUDE,

            (
                json.dumps(
                    evidence,
                    ensure_ascii=False,
                )
                if evidence
                else None
            ),
        ),
    )

    if commit:
        connection.commit()

    return cursor.lastrowid


# ============================================================
# 处理单个 Run
# ============================================================

def process_run(
    connection,
    session,
    run_time,
    index,
    total,
):

    print(
        f"[{index}/{total}] "
        f"{run_time.isoformat()}",
        flush=True,
    )

    ensure_status_task(
        connection,
        run_time,
    )

    existing = find_existing_raw(
        connection,
        run_time,
    )

    if existing:

        mark_status(
            connection,
            run_time,
            "SKIPPED_EXISTING",
            raw_run_id=
                existing["id"],
        )

        return "SKIPPED_EXISTING"

    result = download_run(

        session,

        run_time,

        on_attempt=lambda:
            mark_status(
                connection,
                run_time,
                "DOWNLOADING",
                increment_attempt=True,
            ),
    )

    # ========================================================
    # 永久档案缺失
    # ========================================================

    if result.get(
        "archive_unavailable"
    ):

        semantic_error = (
            "ARCHIVE_NOT_AVAILABLE: "
            + str(
                result["error"]
            )
        )

        # 注意：
        # 数据库现有 CHECK 可能只允许 FAILED 等状态。
        # 所以数据库 status 仍然写 FAILED，
        # 但 last_error 使用明确语义标记。
        #
        # 下一次 PLAN 会识别这个标记，
        # 自动从 actionable_missing 排除。

        mark_status(
            connection,
            run_time,
            "FAILED",
            http_status=400,
            error=semantic_error,
        )

        print(
            semantic_error,
            flush=True,
        )

        return (
            "ARCHIVE_NOT_AVAILABLE"
        )

    # ========================================================
    # 真正失败
    # ========================================================

    if not result["success"]:

        mark_status(
            connection,
            run_time,
            "FAILED",
            http_status=
                result["http_status"],
            error=
                result["error"],
        )

        print(
            result["error"],
            flush=True,
        )

        return "FAILED"

    # ========================================================
    # 下载成功
    # ========================================================

    ingest_time = utc_now()

    try:

        (
            bronze_path,
            raw_text,
            digest,
        ) = save_bronze(
            run_time,
            ingest_time,
            result["raw_text"],
        )

        with connection:

            raw_id = insert_raw(

                connection,

                run_time,

                ingest_time,

                result[
                    "request_url"
                ],

                build_request_params(
                    run_time
                ),

                raw_text,

                bronze_path,

                digest,

                commit=False,
            )

            mark_status(

                connection,

                run_time,

                "SUCCESS",

                http_status=200,

                raw_run_id=raw_id,

                commit=False,
            )

    except Exception as exc:

        connection.rollback()

        mark_status(
            connection,
            run_time,
            "FAILED",
            http_status=200,
            error=(
                "Persistence error: "
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )

        print(
            "Persistence failed; "
            f"Bronze retained: {exc}",
            flush=True,
        )

        return "FAILED"

    return "SUCCESS"


# ============================================================
# PLAN
# ============================================================

def build_plan_stats(
    connection,
    runs,
):

    existing = []

    unavailable = []

    missing = []

    known_unavailable = (
        get_known_archive_unavailable_runs(
            connection,
            runs,
        )
    )

    for run_time in runs:

        row = find_existing_raw(
            connection,
            run_time,
        )

        if row:

            existing.append(
                run_time
            )

        elif (
            iso_utc(run_time)
            in known_unavailable
        ):

            unavailable.append(
                run_time
            )

        else:

            missing.append(
                run_time
            )

    return {

        "existing":
            existing,

        "unavailable":
            unavailable,

        "missing":
            missing,
    }


# ============================================================
# 参数
# ============================================================

def parse_args(
    argv=None,
):

    parser = argparse.ArgumentParser(
        description=__doc__,
    )

    parser.add_argument(
        "--start",
        required=True,
        type=parse_date,
    )

    parser.add_argument(
        "--end",
        required=True,
        type=parse_date,
    )

    parser.add_argument(
        "--database",
        type=Path,
        default=DATABASE_PATH,
    )

    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Maximum actionable runs "
            "to request in this execution"
        ),
    )

    mode = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    mode.add_argument(
        "--plan",
        action="store_true",
    )

    mode.add_argument(
        "--run",
        action="store_true",
    )

    args = parser.parse_args(
        argv
    )

    if (
        args.limit is not None
        and args.limit < 1
    ):

        parser.error(
            "--limit must be positive"
        )

    return args


# ============================================================
# MAIN
# ============================================================

def main(
    argv=None,
):

    args = parse_args(
        argv
    )

    runs = build_run_plan(
        args.start,
        args.end,
    )

    connection = None

    try:

        # ====================================================
        # PLAN：严格只读
        # ====================================================

        if args.plan:

            if args.database.exists():

                connection = connect_db(
                    readonly=True,
                    path=args.database,
                )

        # ====================================================
        # RUN
        # ====================================================

        else:

            print(
                "Database backup: "
                f"{backup_database(args.database)}"
            )

            connection = connect_db(
                path=args.database
            )

            ensure_schema(
                connection
            )

        # ====================================================
        # 统计
        # ====================================================

        stats = build_plan_stats(
            connection,
            runs,
        )

        print(
            f"Product={API_MODEL}; "
            f"runs={len(runs)}; "
            f"existing="
            f"{len(stats['existing'])}; "
            f"archive_unavailable="
            f"{len(stats['unavailable'])}; "
            f"actionable_missing="
            f"{len(stats['missing'])}"
        )

        print(
            "Archive hindcasts are not "
            "historical operational forecasts. "
            "Availability is an estimate."
        )

        # ====================================================
        # PLAN 到此结束
        # ====================================================

        if args.plan:

            return 0

        # ====================================================
        # RUN 只处理真正需要请求的 Run
        # ====================================================

        selected = (
            stats["missing"][
                :args.limit
            ]
            if args.limit
            else
            stats["missing"]
        )

        success = 0

        unavailable = 0

        failed = 0

        skipped = 0

        with requests.Session() as session:

            for index, run_time in enumerate(
                selected,
                1,
            ):

                outcome = process_run(
                    connection,
                    session,
                    run_time,
                    index,
                    len(selected),
                )

                if outcome == "SUCCESS":

                    success += 1

                elif (
                    outcome
                    == "ARCHIVE_NOT_AVAILABLE"
                ):

                    unavailable += 1

                elif (
                    outcome
                    == "SKIPPED_EXISTING"
                ):

                    skipped += 1

                else:

                    failed += 1

                if index < len(
                    selected
                ):

                    time.sleep(
                        REQUEST_INTERVAL_SECONDS
                    )

        print()
        print(
            "=" * 80
        )

        print(
            "BACKFILL RESULT"
        )

        print(
            "=" * 80
        )

        print(
            f"Success             : "
            f"{success}"
        )

        print(
            f"Archive unavailable : "
            f"{unavailable}"
        )

        print(
            f"Skipped existing    : "
            f"{skipped}"
        )

        print(
            f"Failed              : "
            f"{failed}"
        )

        print(
            f"Attempted           : "
            f"{len(selected)}"
        )

        # 永久 archive unavailable
        # 不属于程序失败。
        #
        # 只有真正 failed 才返回非0。

        return (
            1
            if failed
            else 0
        )

    except KeyboardInterrupt:

        print(
            "Interrupted; committed Raw "
            "and Bronze retained."
        )

        return 130

    finally:

        if connection is not None:

            connection.close()


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except Exception as exc:

        print(
            f"[FATAL] "
            f"{type(exc).__name__}: "
            f"{exc}",
            file=sys.stderr,
        )

        sys.exit(1)