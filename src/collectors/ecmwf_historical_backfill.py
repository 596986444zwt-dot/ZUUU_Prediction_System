import argparse
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
# ECMWF Historical Backfill V1
# ==========================================================

BACKFILL_VERSION = "ECMWF_HISTORICAL_BACKFILL_V1"

DATA_SPEC_VERSION = "ECMWF_18_VARIABLE_V2"

MODEL = "IFS_HRES"
API_MODEL = "ecmwf_ifs025"

SOURCE = "Open-Meteo Single Runs API"

API_URL = (
    "https://single-runs-api.open-meteo.com/v1/forecast"
)

ARCHIVE_TYPE = "historical"

AVAILABILITY_TYPE = "official_schedule_estimate"

AVAILABILITY_RULE_VERSION = (
    "HISTORICAL_ECMWF_DISSEMINATION_END_V1"
)

STATUS_TABLE = "ecmwf_historical_run_status"

FORECAST_HOURS = 72

MIN_HISTORICAL_RUN = datetime(
    2024,
    3,
    14,
    0,
    0,
    tzinfo=timezone.utc,
)

RUN_HOURS = (
    0,
    6,
    12,
    18,
)

REQUEST_TIMEOUT = 90

MAX_RETRIES_PER_SESSION = 4

RETRY_DELAYS = [
    5,
    15,
    30,
    60,
]

BETWEEN_RUN_SECONDS = 2


# ==========================================================
# 18 Variables
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
# Time Utilities
# ==========================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def iso_utc(dt):

    return dt.astimezone(
        timezone.utc
    ).isoformat()


def parse_date(date_text):

    try:

        dt = datetime.strptime(
            date_text,
            "%Y-%m-%d",
        )

    except ValueError:

        raise ValueError(
            "日期格式必须是 YYYY-MM-DD"
        )

    return dt.replace(
        tzinfo=timezone.utc
    )


def sha256_text(text):

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ==========================================================
# Availability Rule
# ==========================================================

def source_available_time(
    run_time
):

    return (
        run_time
        + timedelta(
            hours=6,
            minutes=12,
        )
    )


def run_is_available(
    run_time,
    now=None,
):

    if now is None:
        now = utc_now()

    return (
        source_available_time(
            run_time
        )
        <= now
    )


# ==========================================================
# Run Validation
# ==========================================================

def validate_run_time(
    run_time
):

    if run_time.tzinfo is None:

        raise ValueError(
            "Run Time必须带Timezone"
        )

    run_time = (
        run_time.astimezone(
            timezone.utc
        )
    )

    if (
        run_time
        < MIN_HISTORICAL_RUN
    ):

        raise ValueError(
            "禁止访问2024-03-14 "
            "00Z之前的Historical Run"
        )

    if (
        run_time.minute != 0
        or
        run_time.second != 0
        or
        run_time.microsecond != 0
    ):

        raise ValueError(
            "Run必须是整点"
        )

    if (
        run_time.hour
        not in RUN_HOURS
    ):

        raise ValueError(
            "Run必须为 "
            "00/06/12/18Z"
        )

    return run_time


# ==========================================================
# Generate Runs
# ==========================================================

def generate_runs(
    start_date,
    end_date,
):

    start = parse_date(
        start_date
    )

    end_day = parse_date(
        end_date
    )

    if start < MIN_HISTORICAL_RUN:

        raise ValueError(
            "开始日期不能早于"
            "2024-03-14"
        )

    if end_day < start:

        raise ValueError(
            "结束日期早于开始日期"
        )

    now = utc_now()

    runs = []

    current_day = start

    while current_day <= end_day:

        for hour in RUN_HOURS:

            run_time = (
                current_day
                + timedelta(
                    hours=hour
                )
            )

            validate_run_time(
                run_time
            )

            # 尚未达到官方可用时间
            # 不进入任务列表

            if run_is_available(
                run_time,
                now,
            ):

                runs.append(
                    run_time
                )

        current_day += timedelta(
            days=1
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
            run_time.strftime(
                "%Y-%m-%dT%H:%M"
            ),
    }


# ==========================================================
# DB Helpers
# ==========================================================

def connect_database():

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    connection.execute(
        "PRAGMA foreign_keys=ON;"
    )

    connection.execute(
        "PRAGMA journal_mode=WAL;"
    )

    return connection


# ==========================================================
# Existing Raw
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
            run_time_utc,
            model_cycle,
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
            AND api_model = ?

        ORDER BY id ASC;
        """,
        (
            MODEL,
            iso_utc(run_time),
            API_MODEL,
        )
    )

    rows = cursor.fetchall()

    if len(rows) == 0:

        return None

    if len(rows) > 1:

        raise RuntimeError(
            "发现重复Historical V2 Raw："
            f"{iso_utc(run_time)}"
        )

    return rows[0]


# ==========================================================
# Status Task
# ==========================================================

def ensure_status_task(
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
            ?,
            ?,
            'PENDING',
            0,
            ?,
            ?
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


def get_status(
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
            status =
                'DOWNLOADING',

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

            collector_version = ?,

            updated_at_utc = ?

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            now,
            now,
            BACKFILL_VERSION,
            now,
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

            collector_version = ?,

            updated_at_utc = ?

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            http_status,
            str(error)[:4000],
            BACKFILL_VERSION,
            now,
            MODEL,
            iso_utc(run_time),
        )
    )

    connection.commit()


def mark_success(
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
            status = 'SUCCESS',

            last_http_status = 200,

            last_error = NULL,

            raw_run_id = ?,

            completed_time_utc = ?,

            collector_version = ?,

            updated_at_utc = ?

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            raw_id,
            now,
            BACKFILL_VERSION,
            now,
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

            raw_run_id = ?,

            completed_time_utc =
                COALESCE(
                    completed_time_utc,
                    ?
                ),

            collector_version = ?,

            updated_at_utc = ?

        WHERE
            model = ?
            AND run_time_utc = ?;
        """,
        (
            raw_id,
            now,
            BACKFILL_VERSION,
            now,
            MODEL,
            iso_utc(run_time),
        )
    )

    connection.commit()


# ==========================================================
# Validate API Response
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

    required_fields = [
        "time",
        *HOURLY_VARIABLES,
    ]

    for field in required_fields:

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

        if (
            len(hourly[field])
            != FORECAST_HOURS
        ):

            raise RuntimeError(
                f"{field}长度不是"
                f"{FORECAST_HOURS}"
            )

    # ======================================================
    # Timeline
    # ======================================================

    for lead in range(
        FORECAST_HOURS
    ):

        actual = datetime.fromisoformat(
            hourly["time"][lead]
        )

        if actual.tzinfo is None:

            actual = actual.replace(
                tzinfo=timezone.utc
            )

        actual = actual.astimezone(
            timezone.utc
        )

        expected = (
            run_time
            + timedelta(
                hours=lead
            )
        )

        if actual != expected:

            raise RuntimeError(
                f"Lead {lead} "
                "时间轴错误"
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
                f"{field}单位错误："
                f"{actual_unit} != "
                f"{expected_unit}"
            )

    return hourly


# ==========================================================
# HTTP Download
# ==========================================================

def download_run(
    run_time,
):

    parameters = (
        build_parameters(
            run_time
        )
    )

    last_error = None

    last_status = None

    for attempt in range(
        1,
        MAX_RETRIES_PER_SESSION + 1,
    ):

        try:

            response = requests.get(
                API_URL,
                params=parameters,
                timeout=REQUEST_TIMEOUT,
            )

            last_status = (
                response.status_code
            )

            if (
                response.status_code
                == 200
            ):

                data = response.json()

                validate_response(
                    data,
                    run_time,
                )

                return (
                    response,
                    parameters,
                )

            last_error = RuntimeError(
                f"HTTP "
                f"{response.status_code}: "
                f"{response.text[:500]}"
            )

        except Exception as exc:

            last_error = exc

        if (
            attempt
            < MAX_RETRIES_PER_SESSION
        ):

            delay = (
                RETRY_DELAYS[
                    attempt - 1
                ]
            )

            print(
                f"      Retry "
                f"{attempt}/"
                f"{MAX_RETRIES_PER_SESSION}"
                f" → {delay}s"
            )

            time.sleep(
                delay
            )

    raise RuntimeError(
        "下载失败。"
        f"HTTP={last_status}, "
        f"Error={last_error}"
    )


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

    final_path = (
        bronze_dir
        / filename
    )

    temp_path = (
        bronze_dir
        / (
            filename
            + ".tmp"
        )
    )

    if final_path.exists():

        raise RuntimeError(
            "Bronze文件意外重名"
        )

    # 先写临时文件

    temp_path.write_text(
        raw_text,
        encoding="utf-8",
    )

    verify_text = (
        temp_path.read_text(
            encoding="utf-8"
        )
    )

    if verify_text != raw_text:

        try:
            temp_path.unlink()
        except Exception:
            pass

        raise RuntimeError(
            "Bronze临时文件验证失败"
        )

    # 原子改名

    temp_path.replace(
        final_path
    )

    return final_path


# ==========================================================
# Raw Insert
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
        source_available_time(
            run_time
        )
    )

    content_sha256 = (
        sha256_text(
            raw_text
        )
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

            content_sha256,

            iso_utc(
                ingest_time
            ),

            ARCHIVE_TYPE,

            DATA_SPEC_VERSION,

            BACKFILL_VERSION,

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
# Plan
# ==========================================================

def build_plan(
    connection,
    runs,
):

    result = {
        "total": len(runs),
        "existing": 0,
        "missing": 0,
        "48r1": 0,
        "49r1": 0,
        "50r1": 0,
    }

    for run_time in runs:

        cycle = (
            get_ecmwf_model_cycle(
                run_time
            )
        )

        result[cycle] += 1

        existing = (
            find_existing_raw(
                connection,
                run_time,
            )
        )

        if existing:

            result[
                "existing"
            ] += 1

        else:

            result[
                "missing"
            ] += 1

    return result


def print_plan(
    runs,
    plan,
):

    print()
    print("=" * 78)
    print("Historical Backfill Plan")
    print("=" * 78)

    if runs:

        print(
            "First Run："
            f"{iso_utc(runs[0])}"
        )

        print(
            "Last Run ："
            f"{iso_utc(runs[-1])}"
        )

    print(
        f"Total Runs："
        f"{plan['total']}"
    )

    print(
        f"Existing Raw："
        f"{plan['existing']}"
    )

    print(
        f"Need Download："
        f"{plan['missing']}"
    )

    print()

    print(
        f"48r1："
        f"{plan['48r1']}"
    )

    print(
        f"49r1："
        f"{plan['49r1']}"
    )

    print(
        f"50r1："
        f"{plan['50r1']}"
    )

    print()

    print(
        "Forecast rows after "
        "complete："
        f"{plan['total'] * 72}"
    )

    print(
        "Variable values："
        f"{plan['total'] * 72 * 18}"
    )


# ==========================================================
# Process One Run
# ==========================================================

def process_run(
    connection,
    run_time,
):

    ensure_status_task(
        connection,
        run_time,
    )

    existing = (
        find_existing_raw(
            connection,
            run_time,
        )
    )

    if existing:

        status_row = get_status(
            connection,
            run_time,
        )

        # 已经是SUCCESS的任务保持SUCCESS，
        # 不把正式下载成功任务改成SKIPPED。

        if (
            status_row
            and
            status_row["status"]
            == "SUCCESS"
        ):

            return (
                "EXISTING_SUCCESS",
                existing["id"],
            )

        mark_skipped_existing(
            connection,
            run_time,
            existing["id"],
        )

        return (
            "SKIPPED_EXISTING",
            existing["id"],
        )

    status_row = get_status(
        connection,
        run_time,
    )

    if (
        status_row
        and
        status_row["status"]
        in (
            "SUCCESS",
            "SKIPPED_EXISTING",
        )
    ):

        # Status说完成但Raw不存在，
        # 不能静默继续。

        raise RuntimeError(
            "Status显示已完成，"
            "但Historical Raw不存在："
            f"{iso_utc(run_time)}"
        )

    mark_downloading(
        connection,
        run_time,
    )

    try:

        response, parameters = (
            download_run(
                run_time
            )
        )

        raw_text = response.text

        # 再解析一次，确保准备落盘的
        # exact raw_text可正常读取

        data = json.loads(
            raw_text
        )

        validate_response(
            data,
            run_time,
        )

        ingest_time = utc_now()

        bronze_path = save_bronze(
            raw_text,
            run_time,
            ingest_time,
        )

        # Bronze SHA二次验证

        bronze_text = (
            bronze_path.read_text(
                encoding="utf-8"
            )
        )

        if (
            sha256_text(
                bronze_text
            )
            !=
            sha256_text(
                raw_text
            )
        ):

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

        return (
            "FAILED",
            None,
            exc,
        )


# ==========================================================
# Backfill
# ==========================================================

def run_backfill(
    connection,
    runs,
):

    total = len(runs)

    counters = {
        "success": 0,
        "existing_success": 0,
        "skipped": 0,
        "failed": 0,
    }

    started = time.monotonic()

    print()
    print("=" * 78)
    print("Historical Backfill Started")
    print("=" * 78)

    for index, run_time in enumerate(
        runs,
        start=1,
    ):

        progress = (
            index
            / total
            * 100
        )

        cycle = (
            get_ecmwf_model_cycle(
                run_time
            )
        )

        print()
        print(
            f"[{index}/{total}] "
            f"{progress:6.2f}% | "
            f"{run_time.strftime('%Y-%m-%d %HZ')} | "
            f"{cycle}"
        )

        try:

            result = process_run(
                connection,
                run_time,
            )

            status = result[0]

            if status == "SUCCESS":

                counters[
                    "success"
                ] += 1

                print(
                    f"  [SUCCESS] "
                    f"Raw ID {result[1]}"
                )

            elif (
                status
                == "EXISTING_SUCCESS"
            ):

                counters[
                    "existing_success"
                ] += 1

                print(
                    f"  [EXISTING SUCCESS] "
                    f"Raw ID {result[1]}"
                )

            elif (
                status
                == "SKIPPED_EXISTING"
            ):

                counters[
                    "skipped"
                ] += 1

                print(
                    f"  [SKIP EXISTING] "
                    f"Raw ID {result[1]}"
                )

            elif status == "FAILED":

                counters[
                    "failed"
                ] += 1

                print(
                    "  [FAILED] "
                    f"{result[2]}"
                )

        except KeyboardInterrupt:

            print()
            print()
            print(
                "[STOP] 用户中断。"
            )

            print(
                "已完成数据不会丢失，"
                "下次执行同一命令即可续跑。"
            )

            break

        except Exception as exc:

            counters[
                "failed"
            ] += 1

            print(
                f"  [FAILED] "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        elapsed = (
            time.monotonic()
            - started
        )

        average = (
            elapsed
            / index
        )

        remaining = (
            total
            - index
        )

        eta_seconds = (
            average
            * remaining
        )

        print(
            f"  Remaining："
            f"{remaining}"
        )

        print(
            f"  Session："
            f"SUCCESS="
            f"{counters['success']} | "
            f"EXISTING="
            f"{counters['existing_success']} | "
            f"SKIP="
            f"{counters['skipped']} | "
            f"FAILED="
            f"{counters['failed']}"
        )

        print(
            f"  ETA："
            f"{eta_seconds / 60:.1f} min"
        )

        if index < total:

            time.sleep(
                BETWEEN_RUN_SECONDS
            )

    return counters


# ==========================================================
# Database Summary
# ==========================================================

def database_summary(
    connection,
    runs,
):

    if not runs:

        return

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

        GROUP BY status;
        """,
        (
            MODEL,
            *run_strings,
        )
    )

    status_counts = {
        row["status"]:
            row["c"]

        for row in cursor.fetchall()
    }

    cursor.execute(
        """
        SELECT COUNT(*)

        FROM ecmwf_raw_runs

        WHERE
            model = ?
            AND archive_type =
                'historical'
            AND data_spec_version =
                'ECMWF_18_VARIABLE_V2';
        """,
        (
            MODEL,
        )
    )

    historical_raw = (
        cursor.fetchone()[0]
    )

    print()
    print("=" * 78)
    print("Database Summary")
    print("=" * 78)

    for status in (
        "PENDING",
        "DOWNLOADING",
        "SUCCESS",
        "FAILED",
        "SKIPPED_EXISTING",
    ):

        print(
            f"{status:<20}"
            f"{status_counts.get(status, 0)}"
        )

    print()

    print(
        "Historical V2 Raw Total："
        f"{historical_raw}"
    )


# ==========================================================
# CLI
# ==========================================================

def parse_arguments():

    parser = argparse.ArgumentParser(
        description=(
            "ECMWF Historical "
            "Backfill V1"
        )
    )

    parser.add_argument(
        "--start",
        default="2024-03-14",
        help=(
            "开始日期 YYYY-MM-DD，"
            "默认2024-03-14"
        ),
    )

    parser.add_argument(
        "--end",
        default=None,
        help=(
            "结束日期 YYYY-MM-DD，"
            "默认今天UTC日期"
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
        help="只生成计划，不下载",
    )

    mode.add_argument(
        "--run",
        action="store_true",
        help="正式执行历史回填",
    )

    return parser.parse_args()


# ==========================================================
# Main
# ==========================================================

def main():

    args = parse_arguments()

    if args.end is None:

        end_date = (
            utc_now()
            .strftime(
                "%Y-%m-%d"
            )
        )

    else:

        end_date = args.end

    print("=" * 78)
    print("ECMWF Historical Backfill V1")
    print("=" * 78)

    print(
        f"Version："
        f"{BACKFILL_VERSION}"
    )

    print(
        f"Cycle Mapping："
        f"{get_mapping_version()}"
    )

    print(
        f"Start Date："
        f"{args.start}"
    )

    print(
        f"End Date："
        f"{end_date}"
    )

    print(
        "Availability Rule："
        f"{AVAILABILITY_RULE_VERSION}"
    )

    print(
        "Variables：18"
    )

    print(
        "Forecast：72h"
    )

    runs = generate_runs(
        args.start,
        end_date,
    )

    if not runs:

        raise RuntimeError(
            "没有符合条件的Historical Run"
        )

    connection = (
        connect_database()
    )

    try:

        plan = build_plan(
            connection,
            runs,
        )

        print_plan(
            runs,
            plan,
        )

        if args.plan:

            print()
            print(
                "[PLAN ONLY] "
                "没有下载任何数据。"
            )

            return

        print()
        print(
            "正式历史回填开始。"
        )

        print(
            "可随时 Ctrl+C 停止；"
            "下次运行同一命令自动续跑。"
        )

        run_backfill(
            connection,
            runs,
        )

        database_summary(
            connection,
            runs,
        )

        print()
        print("=" * 78)

        print(
            "Historical Backfill "
            "Session Finished"
        )

        print("=" * 78)

        print(
            "注意："
            "本程序只负责 Bronze + Raw。"
        )

        print(
            "不要在这里自动生成Silver。"
        )

        print(
            "Raw完整审计通过后，"
            "再运行Silver Processor。"
        )

    finally:

        connection.close()


if __name__ == "__main__":
    main()