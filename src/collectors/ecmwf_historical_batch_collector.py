import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from config.settings import (
    DATABASE_PATH,
    ECMWF_BRONZE_DIR,
    STATION_LATITUDE,
    STATION_LONGITUDE,
)

from src.ecmwf_model_cycle import (
    get_ecmwf_model_cycle,
    get_mapping_version,
)


# ==========================================================
# ECMWF Historical Batch Collector V2
# ==========================================================
#
# 当前安全测试范围：
#
# 2026-09-14 00Z
#       ↓
# 2026-09-20 18Z
#
# 7天 × 4 Runs = 28 Runs
#
# 功能：
#
# 1. 自动生成Historical任务
# 2. Status数据库状态机
# 3. 已有Raw自动跳过
# 4. FAILED自动重试
# 5. SSL/网络错误指数退避
# 6. 单Run失败不终止整个批次
# 7. Bronze永久保存
# 8. Raw永久保存
# 9. 18变量V2
# 10. 72小时
# 11. Model Cycle自动映射
# 12. 支持程序中断后续跑
#
# ==========================================================


COLLECTOR_VERSION = (
    "ECMWF_HISTORICAL_BATCH_COLLECTOR_V2"
)

DATA_SPEC_VERSION = (
    "ECMWF_18_VARIABLE_V2"
)

MODEL = "IFS_HRES"

API_MODEL = "ecmwf_ifs025"

SOURCE = "Open-Meteo Single Runs API"

API_URL = (
    "https://single-runs-api.open-meteo.com/v1/forecast"
)

ARCHIVE_TYPE = "historical"

AVAILABILITY_TYPE = (
    "official_schedule_estimate"
)

AVAILABILITY_RULE_VERSION = (
    "HISTORICAL_ECMWF_DISSEMINATION_END_V1"
)

STATUS_TABLE = (
    "ecmwf_historical_run_status"
)

FORECAST_HOURS = 72

REQUEST_TIMEOUT = 90


# ==========================================================
# Retry
# ==========================================================

MAX_RETRIES = 4

RETRY_DELAYS = [
    5,
    15,
    30,
    60,
]

BETWEEN_RUN_SECONDS = 2


# ==========================================================
# 当前安全测试范围
# ==========================================================

TEST_START = datetime(
    2026,
    9,
    14,
    0,
    0,
    tzinfo=timezone.utc,
)

TEST_END = datetime(
    2026,
    9,
    20,
    18,
    0,
    tzinfo=timezone.utc,
)


# ==========================================================
# Historical最早允许日期
# ==========================================================

MIN_HISTORICAL_RUN = datetime(
    2024,
    3,
    14,
    0,
    0,
    tzinfo=timezone.utc,
)


# ==========================================================
# V2 18 Variables
# ==========================================================

HOURLY_VARIABLES = [
    "temperature_2m",
    "dew_point_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "pressure_msl",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "precipitation",
    "rain",
    "cape",
]


EXPECTED_UNITS = {
    "temperature_2m": "°C",
    "dew_point_2m": "°C",
    "relative_humidity_2m": "%",
    "surface_pressure": "hPa",
    "pressure_msl": "hPa",

    "cloud_cover": "%",
    "cloud_cover_low": "%",
    "cloud_cover_mid": "%",
    "cloud_cover_high": "%",

    "wind_speed_10m": "km/h",
    "wind_direction_10m": "°",
    "wind_gusts_10m": "km/h",

    "shortwave_radiation": "W/m²",
    "direct_radiation": "W/m²",
    "diffuse_radiation": "W/m²",

    "precipitation": "mm",
    "rain": "mm",

    "cape": "J/kg",
}


# ==========================================================
# Basic Utilities
# ==========================================================

def utc_now():
    return datetime.now(
        timezone.utc
    )


def iso_utc(dt):
    return dt.astimezone(
        timezone.utc
    ).isoformat()


def run_parameter(dt):
    return dt.astimezone(
        timezone.utc
    ).strftime(
        "%Y-%m-%dT%H:%M"
    )


def sha256_text(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ==========================================================
# Availability
# ==========================================================

def calculate_available_time(
    run_time
):
    return (
        run_time
        + timedelta(
            hours=6,
            minutes=12,
        )
    )


# ==========================================================
# Run Safety
# ==========================================================

def validate_run_time(
    run_time
):

    if run_time.tzinfo is None:
        raise ValueError(
            "Run Time必须带Timezone"
        )

    run_time = run_time.astimezone(
        timezone.utc
    )

    if (
        run_time
        < MIN_HISTORICAL_RUN
    ):
        raise ValueError(
            "禁止下载2024-03-14"
            "之前的Run"
        )

    if (
        run_time.minute != 0
        or run_time.second != 0
        or run_time.microsecond != 0
    ):
        raise ValueError(
            "Run时间必须整点"
        )

    if run_time.hour not in (
        0,
        6,
        12,
        18,
    ):
        raise ValueError(
            "只允许00/06/12/18Z"
        )

    return run_time


# ==========================================================
# Generate Runs
# ==========================================================

def generate_runs(
    start,
    end,
):

    start = validate_run_time(
        start
    )

    end = validate_run_time(
        end
    )

    if start > end:
        raise ValueError(
            "Start晚于End"
        )

    runs = []

    current = start

    while current <= end:

        validate_run_time(
            current
        )

        runs.append(
            current
        )

        current += timedelta(
            hours=6
        )

    return runs


# ==========================================================
# API Parameters
# ==========================================================

def build_parameters(
    run_time
):

    return {
        "latitude":
            STATION_LATITUDE,

        "longitude":
            STATION_LONGITUDE,

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
            run_parameter(
                run_time
            ),
    }


# ==========================================================
# Status Helpers
# ==========================================================

def get_status_row(
    connection,
    run_time,
):

    cursor = connection.cursor()

    cursor.execute(
        f"""
        SELECT *
        FROM {STATUS_TABLE}

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            MODEL,
            iso_utc(run_time),
        )
    )

    return cursor.fetchone()


def create_status_task(
    connection,
    run_time,
):

    now = iso_utc(
        utc_now()
    )

    cursor = connection.cursor()

    cursor.execute(
        f"""
        INSERT OR IGNORE INTO
        {STATUS_TABLE} (

            model,
            run_time_utc,
            status,
            attempt_count,
            created_at_utc,
            updated_at_utc

        )
        VALUES (
            ?, ?, 'PENDING', 0, ?, ?
        );
        """,
        (
            MODEL,
            iso_utc(run_time),
            now,
            now,
        )
    )

    connection.commit()


def mark_downloading(
    connection,
    run_time,
):

    now = iso_utc(
        utc_now()
    )

    cursor = connection.cursor()

    cursor.execute(
        f"""
        UPDATE {STATUS_TABLE}

        SET
            status = 'DOWNLOADING',

            attempt_count =
                attempt_count + 1,

            first_attempt_time_utc =
                COALESCE(
                    first_attempt_time_utc,
                    ?
                ),

            last_attempt_time_utc = ?,

            last_http_status = NULL,

            last_error = NULL,

            updated_at_utc = ?,

            collector_version = ?

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            now,
            now,
            now,
            COLLECTOR_VERSION,
            MODEL,
            iso_utc(run_time),
        )
    )

    connection.commit()


def mark_failed(
    connection,
    run_time,
    error,
    http_status=None,
):

    now = iso_utc(
        utc_now()
    )

    cursor = connection.cursor()

    cursor.execute(
        f"""
        UPDATE {STATUS_TABLE}

        SET
            status = 'FAILED',
            last_http_status = ?,
            last_error = ?,
            updated_at_utc = ?,
            collector_version = ?

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            http_status,
            str(error)[:4000],
            now,
            COLLECTOR_VERSION,
            MODEL,
            iso_utc(run_time),
        )
    )

    connection.commit()


def mark_success(
    connection,
    run_time,
    raw_id,
    http_status=200,
):

    now = iso_utc(
        utc_now()
    )

    cursor = connection.cursor()

    cursor.execute(
        f"""
        UPDATE {STATUS_TABLE}

        SET
            status = 'SUCCESS',
            last_http_status = ?,
            last_error = NULL,
            completed_time_utc = ?,
            raw_run_id = ?,
            updated_at_utc = ?,
            collector_version = ?

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            http_status,
            now,
            raw_id,
            now,
            COLLECTOR_VERSION,
            MODEL,
            iso_utc(run_time),
        )
    )

    connection.commit()


def mark_skipped_existing(
    connection,
    run_time,
    raw_id,
):

    now = iso_utc(
        utc_now()
    )

    cursor = connection.cursor()

    cursor.execute(
        f"""
        UPDATE {STATUS_TABLE}

        SET
            status =
                'SKIPPED_EXISTING',

            last_error = NULL,

            completed_time_utc = ?,

            raw_run_id = ?,

            updated_at_utc = ?,

            collector_version = ?

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            now,
            raw_id,
            now,
            COLLECTOR_VERSION,
            MODEL,
            iso_utc(run_time),
        )
    )

    connection.commit()


# ==========================================================
# Existing Historical V2 Raw
# ==========================================================

def find_existing_raw(
    connection,
    run_time,
):

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            bronze_file_path,
            content_sha256

        FROM ecmwf_raw_runs

        WHERE
            model = ?
            AND run_time_utc = ?
            AND archive_type =
                'historical'
            AND data_spec_version =
                'ECMWF_18_VARIABLE_V2'

        ORDER BY id ASC;
        """,
        (
            MODEL,
            iso_utc(run_time),
        )
    )

    rows = cursor.fetchall()

    if not rows:
        return None

    if len(rows) > 1:
        raise RuntimeError(
            "同一个Historical V2 "
            f"Run存在多条Raw："
            f"{iso_utc(run_time)}"
        )

    return rows[0]


# ==========================================================
# HTTP
# ==========================================================

def download_run(
    parameters
):

    last_error = None
    last_http_status = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        print(
            f"    HTTP "
            f"{attempt}/"
            f"{MAX_RETRIES}"
        )

        try:

            response = requests.get(
                API_URL,
                params=parameters,
                timeout=REQUEST_TIMEOUT,
            )

            last_http_status = (
                response.status_code
            )

            print(
                f"    Status："
                f"{response.status_code}"
            )

            if (
                response.status_code
                == 200
            ):
                return (
                    response,
                    200,
                )

            error_text = (
                response.text[:1000]
            )

            last_error = (
                RuntimeError(
                    f"HTTP "
                    f"{response.status_code}: "
                    f"{error_text}"
                )
            )

            print(
                "    API Error："
                f"{error_text[:300]}"
            )

        except Exception as exc:

            last_error = exc

            print(
                "    Network："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        if attempt < MAX_RETRIES:

            delay = (
                RETRY_DELAYS[
                    attempt - 1
                ]
            )

            print(
                f"    Retry after "
                f"{delay}s"
            )

            time.sleep(
                delay
            )

    raise RuntimeError(
        "Historical Run下载失败："
        f"{last_error}"
    )


# ==========================================================
# Response Validation
# ==========================================================

def validate_response(
    data,
    run_time,
):

    if not isinstance(
        data,
        dict
    ):
        raise RuntimeError(
            "API返回不是JSON对象"
        )

    hourly = data.get(
        "hourly"
    )

    units = data.get(
        "hourly_units"
    )

    if not isinstance(
        hourly,
        dict
    ):
        raise RuntimeError(
            "缺少hourly"
        )

    if not isinstance(
        units,
        dict
    ):
        raise RuntimeError(
            "缺少hourly_units"
        )

    required = [
        "time",
        *HOURLY_VARIABLES,
    ]

    for field in required:

        if field not in hourly:
            raise RuntimeError(
                f"缺少字段：{field}"
            )

        if not isinstance(
            hourly[field],
            list
        ):
            raise RuntimeError(
                f"{field}不是list"
            )

        if len(
            hourly[field]
        ) != 72:
            raise RuntimeError(
                f"{field}长度"
                f"不是72"
            )

    # ======================================================
    # Time
    # ======================================================

    for lead in range(72):

        target = datetime.fromisoformat(
            hourly["time"][lead]
        )

        if target.tzinfo is None:
            target = target.replace(
                tzinfo=timezone.utc
            )

        target = target.astimezone(
            timezone.utc
        )

        expected = (
            run_time
            + timedelta(
                hours=lead
            )
        )

        if target != expected:
            raise RuntimeError(
                f"Lead {lead} "
                "Target Time异常"
            )

    # ======================================================
    # Units
    # ======================================================

    for (
        field,
        expected_unit
    ) in EXPECTED_UNITS.items():

        actual_unit = (
            units.get(field)
        )

        if (
            actual_unit
            != expected_unit
        ):
            raise RuntimeError(
                f"{field}单位异常："
                f"{actual_unit} != "
                f"{expected_unit}"
            )

    return hourly


# ==========================================================
# Bronze
# ==========================================================

def save_bronze(
    raw_text,
    run_time,
    ingest_time,
):

    bronze_dir = Path(
        ECMWF_BRONZE_DIR
    )

    bronze_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    run_text = (
        run_time.strftime(
            "%Y%m%dT%H%MZ"
        )
    )

    ingest_text = (
        ingest_time.strftime(
            "%Y%m%dT%H%M%S_%fZ"
        )
    )

    filename = (
        "ecmwf_ifs_hres_"
        "historical_"
        f"run_{run_text}_"
        f"ingest_{ingest_text}.json"
    )

    path = (
        bronze_dir
        / filename
    )

    if path.exists():
        raise RuntimeError(
            "Bronze意外重名"
        )

    path.write_text(
        raw_text,
        encoding="utf-8",
    )

    verify = path.read_text(
        encoding="utf-8"
    )

    if verify != raw_text:
        raise RuntimeError(
            "Bronze写入验证失败"
        )

    return path


# ==========================================================
# Insert Raw
# ==========================================================

def insert_raw(
    connection,
    run_time,
    raw_text,
    bronze_path,
    ingest_time,
    parameters,
):

    model_cycle = (
        get_ecmwf_model_cycle(
            run_time
        )
    )

    available_time = (
        calculate_available_time(
            run_time
        )
    )

    sha256 = sha256_text(
        raw_text
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO ecmwf_raw_runs (

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
            api_model

        )

        VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        );
        """,
        (
            MODEL,
            model_cycle,

            iso_utc(
                run_time
            ),

            iso_utc(
                available_time
            ),

            AVAILABILITY_TYPE,

            iso_utc(
                ingest_time
            ),

            SOURCE,

            API_URL,

            json.dumps(
                parameters,
                ensure_ascii=False,
                sort_keys=True,
            ),

            raw_text,

            str(
                bronze_path
            ),

            sha256,

            iso_utc(
                ingest_time
            ),

            ARCHIVE_TYPE,

            DATA_SPEC_VERSION,

            COLLECTOR_VERSION,

            AVAILABILITY_RULE_VERSION,

            API_MODEL,
        )
    )

    raw_id = (
        cursor.lastrowid
    )

    connection.commit()

    return raw_id


# ==========================================================
# One Run
# ==========================================================

def process_run(
    connection,
    run_time,
):

    run_time = validate_run_time(
        run_time
    )

    print()
    print("-" * 78)

    print(
        f"Run："
        f"{iso_utc(run_time)}"
    )

    # ======================================================
    # Existing Raw
    # ======================================================

    existing = find_existing_raw(
        connection,
        run_time,
    )

    if existing is not None:

        raw_id = existing["id"]

        mark_skipped_existing(
            connection,
            run_time,
            raw_id,
        )

        print(
            f"  [SKIP] "
            f"Raw ID {raw_id}"
        )

        return (
            "SKIPPED_EXISTING",
            raw_id,
        )

    # ======================================================
    # Status
    # ======================================================

    status_row = get_status_row(
        connection,
        run_time,
    )

    if status_row is None:
        raise RuntimeError(
            "Status任务不存在"
        )

    if status_row[
        "status"
    ] in (
        "SUCCESS",
        "SKIPPED_EXISTING",
    ):

        # Status说完成，
        # 但Raw不存在。
        # 这是数据库一致性问题，
        # 不能偷偷重新下载。

        raise RuntimeError(
            "Status显示完成，"
            "但找不到Historical V2 Raw"
        )

    # ======================================================
    # Start Attempt
    # ======================================================

    mark_downloading(
        connection,
        run_time,
    )

    parameters = build_parameters(
        run_time
    )

    try:

        response, http_status = (
            download_run(
                parameters
            )
        )

        raw_text = (
            response.text
        )

        data = (
            response.json()
        )

        hourly = (
            validate_response(
                data,
                run_time,
            )
        )

        null_total = sum(
            value is None
            for field in HOURLY_VARIABLES
            for value in hourly[field]
        )

        print(
            "    Validation：PASS"
        )

        print(
            f"    NULL Total："
            f"{null_total}"
        )

        print(
            f"    Grid："
            f"{data.get('latitude')}, "
            f"{data.get('longitude')}, "
            f"{data.get('elevation')}m"
        )

        ingest_time = utc_now()

        bronze_path = save_bronze(
            raw_text,
            run_time,
            ingest_time,
        )

        bronze_sha = sha256_text(
            bronze_path.read_text(
                encoding="utf-8"
            )
        )

        raw_sha = sha256_text(
            raw_text
        )

        if bronze_sha != raw_sha:
            raise RuntimeError(
                "Bronze SHA验证失败"
            )

        raw_id = insert_raw(
            connection=connection,
            run_time=run_time,
            raw_text=raw_text,
            bronze_path=bronze_path,
            ingest_time=ingest_time,
            parameters=parameters,
        )

        mark_success(
            connection,
            run_time,
            raw_id,
            http_status,
        )

        print(
            f"  [SUCCESS] "
            f"Raw ID {raw_id}"
        )

        print(
            f"    Cycle："
            f"{get_ecmwf_model_cycle(run_time)}"
        )

        return (
            "SUCCESS",
            raw_id,
        )

    except Exception as exc:

        connection.rollback()

        mark_failed(
            connection,
            run_time,
            exc,
        )

        print(
            f"  [FAILED] "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return (
            "FAILED",
            None,
        )


# ==========================================================
# Create 28 Tasks
# ==========================================================

def prepare_tasks(
    connection,
    runs,
):

    for run_time in runs:

        create_status_task(
            connection,
            run_time,
        )


# ==========================================================
# Summary
# ==========================================================

def print_status_summary(
    connection,
    runs,
):

    run_strings = [
        iso_utc(run)
        for run in runs
    ]

    placeholders = ",".join(
        "?"
        for _ in run_strings
    )

    cursor = connection.cursor()

    cursor.execute(
        f"""
        SELECT
            status,
            COUNT(*) AS c

        FROM {STATUS_TABLE}

        WHERE
            model = ?
            AND run_time_utc IN (
                {placeholders}
            )

        GROUP BY status

        ORDER BY status;
        """,
        (
            MODEL,
            *run_strings,
        )
    )

    rows = cursor.fetchall()

    counts = {
        row["status"]:
            row["c"]
        for row in rows
    }

    print()
    print("=" * 78)

    print(
        "Historical Batch "
        "Status Summary"
    )

    print("=" * 78)

    for status in (
        "PENDING",
        "DOWNLOADING",
        "SUCCESS",
        "FAILED",
        "SKIPPED_EXISTING",
    ):

        print(
            f"{status:<18}"
            f"{counts.get(status, 0)}"
        )

    total = sum(
        counts.values()
    )

    print(
        f"{'TOTAL':<18}"
        f"{total}"
    )

    return counts


# ==========================================================
# Main
# ==========================================================

def main():

    print("=" * 78)

    print(
        "ECMWF Historical "
        "Batch Collector V2"
    )

    print("=" * 78)

    print(
        "安全测试范围："
        "2026-09-14 → "
        "2026-09-20"
    )

    print(
        "Expected Runs：28"
    )

    print(
        "Runs/day：4"
    )

    print(
        "Variables：18"
    )

    print(
        "Forecast：72h"
    )

    print(
        f"Cycle Mapping："
        f"{get_mapping_version()}"
    )

    runs = generate_runs(
        TEST_START,
        TEST_END,
    )

    if len(runs) != 28:
        raise RuntimeError(
            "测试任务应该是28个Run，"
            f"实际{len(runs)}"
        )

    print(
        f"Generated Runs："
        f"{len(runs)}"
    )

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    connection.execute(
        "PRAGMA foreign_keys=ON;"
    )

    try:

        # ==================================================
        # 1. Prepare
        # ==================================================

        prepare_tasks(
            connection,
            runs,
        )

        print(
            "[PASS] 28个Status任务"
            "准备完成"
        )

        # ==================================================
        # 2. Process
        # ==================================================

        success = 0
        skipped = 0
        failed = 0

        for index, run_time in enumerate(
            runs,
            start=1,
        ):

            print()
            print(
                f"[{index:02d}/28]"
            )

            status, raw_id = process_run(
                connection,
                run_time,
            )

            if status == "SUCCESS":
                success += 1

            elif (
                status
                == "SKIPPED_EXISTING"
            ):
                skipped += 1

            elif status == "FAILED":
                failed += 1

            # 每个Run之间稍微停顿
            if index < len(runs):
                time.sleep(
                    BETWEEN_RUN_SECONDS
                )

        # ==================================================
        # 3. Summary
        # ==================================================

        counts = print_status_summary(
            connection,
            runs,
        )

        print()
        print(
            "本次运行："
        )

        print(
            f"Success：{success}"
        )

        print(
            f"Skipped：{skipped}"
        )

        print(
            f"Failed：{failed}"
        )

        # ==================================================
        # Raw Count
        # ==================================================

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM ecmwf_raw_runs;
            """
        )

        raw_total = (
            cursor.fetchone()[0]
        )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM ecmwf_raw_runs

            WHERE
                archive_type =
                    'historical'

                AND data_spec_version =
                    'ECMWF_18_VARIABLE_V2';
            """
        )

        historical_v2_total = (
            cursor.fetchone()[0]
        )

        print()

        print(
            f"Raw Total："
            f"{raw_total}"
        )

        print(
            "Historical V2 Total："
            f"{historical_v2_total}"
        )

        # ==================================================
        # Final
        # ==================================================

        print()
        print("=" * 78)

        if (
            counts.get(
                "FAILED",
                0
            ) == 0

            and counts.get(
                "PENDING",
                0
            ) == 0

            and counts.get(
                "DOWNLOADING",
                0
            ) == 0

            and (
                counts.get(
                    "SUCCESS",
                    0
                )
                +
                counts.get(
                    "SKIPPED_EXISTING",
                    0
                )
                == 28
            )
        ):

            print(
                "[PASS] Historical "
                "Batch 28-Run Test"
            )

            print()

            print(
                "下一步："
                "28-Run Raw Audit"
            )

        else:

            print(
                "[WARNING] "
                "28-Run Batch尚未完整"
            )

            print()

            print(
                "重新运行本程序即可续跑。"
            )

        print("=" * 78)

    finally:

        connection.close()


if __name__ == "__main__":
    main()