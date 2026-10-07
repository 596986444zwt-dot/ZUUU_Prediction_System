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
# ECMWF Historical Backfill V2
# ==========================================================

BACKFILL_VERSION = "ECMWF_HISTORICAL_BACKFILL_V2"

DATA_SPEC_VERSION = "ECMWF_18_VARIABLE_V2"

MODEL = "IFS_HRES"

# ----------------------------------------------------------
# IMPORTANT
#
# Historical HRES Single Runs:
# 使用 ecmwf_ifs
#
# 不再使用：
# ecmwf_ifs025
# ----------------------------------------------------------

API_MODEL = "ecmwf_ifs"

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

MAX_RETRIES = 4

RETRY_DELAYS = [
    5,
    15,
    30,
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
# Custom Download Error
# ==========================================================

class DownloadError(Exception):

    def __init__(
        self,
        message,
        http_status=None,
        retryable=False,
    ):

        super().__init__(message)

        self.http_status = http_status
        self.retryable = retryable


# ==========================================================
# Time Utilities
# ==========================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def iso_utc(dt):

    return (
        dt.astimezone(
            timezone.utc
        )
        .isoformat()
    )


def parse_date(
    date_text,
):

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


def sha256_text(
    text,
):

    return hashlib.sha256(
        text.encode(
            "utf-8"
        )
    ).hexdigest()


# ==========================================================
# Availability
# ==========================================================

def source_available_time(
    run_time,
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
    run_time,
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
            "Run早于Historical "
            "Archive起始日期"
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
            "Run必须为"
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

    end = parse_date(
        end_date
    )

    if (
        start
        < MIN_HISTORICAL_RUN
    ):

        raise ValueError(
            "开始日期不能早于"
            "2024-03-14"
        )

    if end < start:

        raise ValueError(
            "结束日期早于开始日期"
        )

    now = utc_now()

    runs = []

    day = start

    while day <= end:

        for hour in RUN_HOURS:

            run_time = (
                day
                + timedelta(
                    hours=hour
                )
            )

            validate_run_time(
                run_time
            )

            if run_is_available(
                run_time,
                now,
            ):

                runs.append(
                    run_time
                )

        day += timedelta(
            days=1
        )

    return runs


# ==========================================================
# API Parameters
# ==========================================================

def build_parameters(
    run_time,
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
# Database
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

    cursor = (
        connection.cursor()
    )

    cursor.execute(
        """
        SELECT
            id,
            run_time_utc,
            model_cycle,
            api_model,
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
            iso_utc(
                run_time
            ),
        )
    )

    rows = cursor.fetchall()

    if len(rows) == 0:

        return None

    if len(rows) > 1:

        raise RuntimeError(
            "发现重复Historical Raw："
            f"{iso_utc(run_time)}"
        )

    return rows[0]


# ==========================================================
# Status
# ==========================================================

def ensure_status_task(
    connection,
    run_time,
):

    now = iso_utc(
        utc_now()
    )

    cursor = (
        connection.cursor()
    )

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
            iso_utc(
                run_time
            ),
            now,
            now,
        )
    )

    connection.commit()


def get_status(
    connection,
    run_time,
):

    cursor = (
        connection.cursor()
    )

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
            iso_utc(
                run_time
            ),
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

    cursor = (
        connection.cursor()
    )

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
            iso_utc(
                run_time
            ),
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

    cursor = (
        connection.cursor()
    )

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
            iso_utc(
                run_time
            ),
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

    cursor = (
        connection.cursor()
    )

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
            iso_utc(
                run_time
            ),
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

    cursor = (
        connection.cursor()
    )

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
            iso_utc(
                run_time
            ),
        )
    )

    connection.commit()


# ==========================================================
# Response Validation
# ==========================================================

def validate_response(
    data,
    run_time,
):

    if not isinstance(
        data,
        dict,
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
        dict,
    ):

        raise RuntimeError(
            "API缺少hourly"
        )

    if not isinstance(
        units,
        dict,
    ):

        raise RuntimeError(
            "API缺少hourly_units"
        )

    fields = [
        "time",
        *HOURLY_VARIABLES,
    ]

    for field in fields:

        if field not in hourly:

            raise RuntimeError(
                f"缺少字段：{field}"
            )

        values = hourly[
            field
        ]

        if not isinstance(
            values,
            list,
        ):

            raise RuntimeError(
                f"{field}不是list"
            )

        if (
            len(values)
            != FORECAST_HOURS
        ):

            raise RuntimeError(
                f"{field}长度错误："
                f"{len(values)}"
            )

    # ======================================================
    # Timeline
    # ======================================================

    for lead in range(
        FORECAST_HOURS
    ):

        raw_time = (
            hourly[
                "time"
            ][lead]
        )

        actual = (
            datetime.fromisoformat(
                raw_time
            )
        )

        if actual.tzinfo is None:

            actual = (
                actual.replace(
                    tzinfo=timezone.utc
                )
            )

        actual = (
            actual.astimezone(
                timezone.utc
            )
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
                "时间轴错误："
                f"{actual} != "
                f"{expected}"
            )

    # ======================================================
    # Units
    # ======================================================

    for (
        field,
        expected_unit
    ) in EXPECTED_UNITS.items():

        actual_unit = (
            units.get(
                field
            )
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
# HTTP Error Classification
# ==========================================================

def classify_http_error(
    response,
):

    status = (
        response.status_code
    )

    text = (
        response.text[:1000]
    )

    # ------------------------------------------------------
    # 400
    #
    # 请求确定性失败。
    # 同一个Run立即重试没有意义。
    # ------------------------------------------------------

    if status == 400:

        return DownloadError(
            message=(
                f"HTTP 400: {text}"
            ),
            http_status=400,
            retryable=False,
        )

    # ------------------------------------------------------
    # 401 / 403 / 404
    # ------------------------------------------------------

    if status in (
        401,
        403,
        404,
    ):

        return DownloadError(
            message=(
                f"HTTP {status}: "
                f"{text}"
            ),
            http_status=status,
            retryable=False,
        )

    # ------------------------------------------------------
    # Rate Limit
    # ------------------------------------------------------

    if status == 429:

        return DownloadError(
            message=(
                f"HTTP 429: {text}"
            ),
            http_status=429,
            retryable=True,
        )

    # ------------------------------------------------------
    # Server Error
    # ------------------------------------------------------

    if (
        500
        <= status
        <= 599
    ):

        return DownloadError(
            message=(
                f"HTTP {status}: "
                f"{text}"
            ),
            http_status=status,
            retryable=True,
        )

    # ------------------------------------------------------
    # Other HTTP
    # ------------------------------------------------------

    return DownloadError(
        message=(
            f"HTTP {status}: "
            f"{text}"
        ),
        http_status=status,
        retryable=False,
    )


# ==========================================================
# Download
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

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = requests.get(
                API_URL,
                params=parameters,
                timeout=REQUEST_TIMEOUT,
            )

            if (
                response.status_code
                == 200
            ):

                try:

                    data = (
                        response.json()
                    )

                except Exception as exc:

                    raise DownloadError(
                        message=(
                            "HTTP 200但JSON解析失败："
                            f"{exc}"
                        ),
                        http_status=200,
                        retryable=True,
                    )

                validate_response(
                    data,
                    run_time,
                )

                return (
                    response,
                    parameters,
                )

            error = (
                classify_http_error(
                    response
                )
            )

            if not error.retryable:

                raise error

            last_error = error

        except DownloadError as exc:

            last_error = exc

            if not exc.retryable:

                raise

        except (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:

            last_error = (
                DownloadError(
                    message=(
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                    http_status=None,
                    retryable=True,
                )
            )

        except requests.exceptions.RequestException as exc:

            last_error = (
                DownloadError(
                    message=(
                        f"RequestException: "
                        f"{exc}"
                    ),
                    http_status=None,
                    retryable=True,
                )
            )

        # --------------------------------------------------
        # Retry
        # --------------------------------------------------

        if attempt < MAX_RETRIES:

            delay = (
                RETRY_DELAYS[
                    attempt - 1
                ]
            )

            print(
                f"      [RETRY] "
                f"{attempt}/"
                f"{MAX_RETRIES} "
                f"→ {delay}s"
            )

            print(
                f"      Reason："
                f"{last_error}"
            )

            time.sleep(
                delay
            )

    raise last_error


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

    temp_path.write_text(
        raw_text,
        encoding="utf-8",
    )

    verify = (
        temp_path.read_text(
            encoding="utf-8"
        )
    )

    if verify != raw_text:

        try:

            temp_path.unlink()

        except Exception:

            pass

        raise RuntimeError(
            "Bronze临时文件验证失败"
        )

    temp_path.replace(
        final_path
    )

    return final_path


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
        source_available_time(
            run_time
        )
    )

    content_sha256 = (
        sha256_text(
            raw_text
        )
    )

    cursor = (
        connection.cursor()
    )

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

    # ======================================================
    # Existing Raw
    # ======================================================

    existing = (
        find_existing_raw(
            connection,
            run_time,
        )
    )

    if existing:

        status = (
            get_status(
                connection,
                run_time,
            )
        )

        # 已经成功的，不改成SKIPPED

        if (
            status
            and
            status["status"]
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

    # ======================================================
    # Status integrity
    # ======================================================

    status = (
        get_status(
            connection,
            run_time,
        )
    )

    if (
        status
        and
        status["status"]
        in (
            "SUCCESS",
            "SKIPPED_EXISTING",
        )
    ):

        raise RuntimeError(
            "Status显示完成，"
            "但Raw不存在："
            f"{iso_utc(run_time)}"
        )

    # ======================================================
    # Download
    # ======================================================

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

        raw_text = (
            response.text
        )

        # 再验证一次即将落盘的内容

        data = json.loads(
            raw_text
        )

        validate_response(
            data,
            run_time,
        )

        ingest_time = (
            utc_now()
        )

        # ==================================================
        # Bronze
        # ==================================================

        bronze_path = (
            save_bronze(
                raw_text,
                run_time,
                ingest_time,
            )
        )

        # ==================================================
        # SHA Verify
        # ==================================================

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

        # ==================================================
        # Raw
        # ==================================================

        raw_id = (
            insert_raw(
                connection=connection,
                run_time=run_time,
                raw_text=raw_text,
                bronze_path=bronze_path,
                ingest_time=ingest_time,
                parameters=parameters,
            )
        )

        # ==================================================
        # Status Success
        # ==================================================

        mark_success(
            connection,
            run_time,
            raw_id,
        )

        return (
            "SUCCESS",
            raw_id,
        )

    except DownloadError as exc:

        connection.rollback()

        mark_failed(
            connection,
            run_time,
            exc,
            exc.http_status,
        )

        return (
            "FAILED",
            None,
            exc,
        )

    except Exception as exc:

        connection.rollback()

        mark_failed(
            connection,
            run_time,
            exc,
            None,
        )

        return (
            "FAILED",
            None,
            exc,
        )


# ==========================================================
# Build Plan
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

        result[
            cycle
        ] += 1

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

    print(
        "=" * 78
    )

    print(
        "Historical Backfill "
        "V2 Plan"
    )

    print(
        "=" * 78
    )

    print(
        f"First Run："
        f"{iso_utc(runs[0])}"
    )

    print(
        f"Last Run ："
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
        "Forecast rows："
        f"{plan['total'] * 72}"
    )

    print(
        "Variable values："
        f"{plan['total'] * 72 * 18}"
    )


# ==========================================================
# Run Backfill
# ==========================================================

def run_backfill(
    connection,
    runs,
):

    total = len(
        runs
    )

    success = 0
    existing_success = 0
    skipped = 0
    failed = 0

    start_time = (
        time.monotonic()
    )

    print()

    print(
        "=" * 78
    )

    print(
        "Historical Backfill "
        "V2 Started"
    )

    print(
        "=" * 78
    )

    for (
        index,
        run_time
    ) in enumerate(
        runs,
        start=1,
    ):

        cycle = (
            get_ecmwf_model_cycle(
                run_time
            )
        )

        progress = (
            index
            / total
            * 100
        )

        print()

        print(
            f"[{index}/{total}] "
            f"{progress:6.2f}% | "
            f"{run_time.strftime('%Y-%m-%d %HZ')} | "
            f"{cycle}"
        )

        try:

            result = (
                process_run(
                    connection,
                    run_time,
                )
            )

            status = (
                result[0]
            )

            if status == "SUCCESS":

                success += 1

                print(
                    "  [SUCCESS] "
                    f"Raw ID "
                    f"{result[1]}"
                )

            elif (
                status
                == "EXISTING_SUCCESS"
            ):

                existing_success += 1

                print(
                    "  [EXISTING SUCCESS] "
                    f"Raw ID "
                    f"{result[1]}"
                )

            elif (
                status
                == "SKIPPED_EXISTING"
            ):

                skipped += 1

                print(
                    "  [SKIP EXISTING] "
                    f"Raw ID "
                    f"{result[1]}"
                )

            elif (
                status
                == "FAILED"
            ):

                failed += 1

                print(
                    "  [FAILED] "
                    f"{result[2]}"
                )

        except KeyboardInterrupt:

            print()

            print(
                "[STOP] 用户中断"
            )

            print(
                "已经完成的数据不会丢失。"
            )

            print(
                "重新执行同一命令即可续跑。"
            )

            break

        except Exception as exc:

            failed += 1

            print(
                "  [FAILED] "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        elapsed = (
            time.monotonic()
            - start_time
        )

        average = (
            elapsed
            / index
        )

        remaining = (
            total
            - index
        )

        eta = (
            average
            * remaining
        )

        print(
            f"  Remaining："
            f"{remaining}"
        )

        print(
            "  Session："
            f"SUCCESS={success} | "
            f"EXISTING="
            f"{existing_success} | "
            f"SKIP={skipped} | "
            f"FAILED={failed}"
        )

        print(
            f"  ETA："
            f"{eta / 60:.1f} min"
        )

        if index < total:

            time.sleep(
                BETWEEN_RUN_SECONDS
            )

    print()

    print(
        "=" * 78
    )

    print(
        "Historical Backfill "
        "V2 Session Summary"
    )

    print(
        "=" * 78
    )

    print(
        f"SUCCESS："
        f"{success}"
    )

    print(
        f"EXISTING SUCCESS："
        f"{existing_success}"
    )

    print(
        f"SKIPPED EXISTING："
        f"{skipped}"
    )

    print(
        f"FAILED："
        f"{failed}"
    )


# ==========================================================
# CLI
# ==========================================================

def parse_arguments():

    parser = argparse.ArgumentParser(
        description=(
            "ECMWF Historical "
            "Backfill V2"
        )
    )

    parser.add_argument(
        "--start",
        default="2024-03-14",
    )

    parser.add_argument(
        "--end",
        default=None,
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

    return parser.parse_args()


# ==========================================================
# Main
# ==========================================================

def main():

    args = (
        parse_arguments()
    )

    if args.end is None:

        end_date = (
            utc_now()
            .strftime(
                "%Y-%m-%d"
            )
        )

    else:

        end_date = (
            args.end
        )

    print(
        "=" * 78
    )

    print(
        "ECMWF Historical "
        "Backfill V2"
    )

    print(
        "=" * 78
    )

    print(
        f"Version："
        f"{BACKFILL_VERSION}"
    )

    print(
        f"API Model："
        f"{API_MODEL}"
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
        f"Variables："
        f"{len(HOURLY_VARIABLES)}"
    )

    print(
        f"Forecast："
        f"{FORECAST_HOURS}h"
    )

    print(
        "400 Retry：NO"
    )

    print(
        "SSL/Connection/Timeout/"
        "429/5xx Retry：YES"
    )

    runs = (
        generate_runs(
            args.start,
            end_date,
        )
    )

    if not runs:

        raise RuntimeError(
            "没有符合条件的Run"
        )

    connection = (
        connect_database()
    )

    try:

        plan = (
            build_plan(
                connection,
                runs,
            )
        )

        print_plan(
            runs,
            plan,
        )

        if args.plan:

            print()

            print(
                "[PLAN ONLY] "
                "没有下载数据。"
            )

            return

        print()

        print(
            "开始Historical "
            "Backfill V2。"
        )

        print(
            "Ctrl+C可以安全停止。"
        )

        run_backfill(
            connection,
            runs,
        )

        print()

        print(
            "注意："
            "当前程序只写"
            "Bronze + Raw。"
        )

        print(
            "暂时不要运行"
            "Silver Processor。"
        )

    finally:

        connection.close()


if __name__ == "__main__":

    main()