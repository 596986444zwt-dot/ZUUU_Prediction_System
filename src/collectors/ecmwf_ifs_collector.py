import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta

import requests
from database.schema import ensure_schema
from src.ecmwf_contract import DATA_SPEC_VERSION, validate_payload, archive_cycle

from config.settings import (
    DATABASE_PATH,
    ECMWF_BRONZE_DIR,
    ECMWF_MODEL,
    ECMWF_SOURCE,
    ECMWF_RUN_HOURS_UTC,
    STATION_LATITUDE,
    STATION_LONGITUDE,
)


# ==========================================================
# ECMWF IFS HRES Collector V2
# ==========================================================
#
# V2相对于V1：
#
# 1. 保留V1全部数据采集、Bronze、SHA256、Raw DB逻辑
# 2. Hourly变量由8个扩展至18个
# 3. 增加分层云量
# 4. 增加阵风
# 5. 增加太阳辐射
# 6. 增加降水/降雨
# 7. 增加CAPE
#
# 注意：
#
# - Bronze保存服务器原始响应
# - 不覆盖历史Raw
# - 同Run不同内容允许作为不同Raw Snapshot保存
# - 完全相同的 model + run_time + sha256 不重复入库
# ==========================================================


API_URL = (
    "https://single-runs-api.open-meteo.com/v1/forecast"
)

API_MODEL = "ecmwf_ifs025"

HEADERS = {
    "User-Agent": "ZUUU-Prediction-System/2.0"
}


# ==========================================================
# ECMWF V2 正式 Hourly 变量
# 共18个
# ==========================================================

HOURLY_VARIABLES = [

    # ------------------------------------------------------
    # Temperature / Moisture
    # ------------------------------------------------------

    "temperature_2m",
    "dew_point_2m",
    "relative_humidity_2m",

    # ------------------------------------------------------
    # Pressure
    # ------------------------------------------------------

    "surface_pressure",
    "pressure_msl",

    # ------------------------------------------------------
    # Cloud
    # ------------------------------------------------------

    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",

    # ------------------------------------------------------
    # Wind
    # ------------------------------------------------------

    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",

    # ------------------------------------------------------
    # Solar Radiation
    # ------------------------------------------------------

    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",

    # ------------------------------------------------------
    # Precipitation
    # ------------------------------------------------------

    "precipitation",
    "rain",

    # ------------------------------------------------------
    # Convection
    # ------------------------------------------------------

    "cape",
]


FORECAST_HOURS = 72

REQUEST_TIMEOUT_SECONDS = 60

MAX_RETRIES_PER_RUN = 2


# ==========================================================
# 时间工具
# ==========================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def iso_utc(dt):

    return dt.astimezone(
        timezone.utc
    ).isoformat()


# ==========================================================
# Run候选
# ==========================================================

def build_candidate_runs():
    """
    生成最近几个合法ECMWF Run。

    不假设最新初始化Run已经发布完成。

    从最近Run开始向前尝试：

        00 UTC
        06 UTC
        12 UTC
        18 UTC

    最多向前检查8个Run。
    """

    now = utc_now()

    latest_run_hour = max(
        hour
        for hour in ECMWF_RUN_HOURS_UTC
        if hour <= now.hour
    )

    latest_run = now.replace(
        hour=latest_run_hour,
        minute=0,
        second=0,
        microsecond=0,
    )

    candidates = []

    current = latest_run

    for _ in range(8):

        candidates.append(
            current
        )

        current -= timedelta(
            hours=6
        )

    return candidates


# ==========================================================
# 请求参数
# ==========================================================

def build_params(
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

        "models":
            API_MODEL,

        "run":
            run_time.strftime(
                "%Y-%m-%dT%H:%M"
            ),

        "timezone":
            "UTC",

        "forecast_hours":
            FORECAST_HOURS,
    }


# ==========================================================
# HTTP请求
# ==========================================================

def request_ecmwf_run(
    run_time
):
    """
    请求指定ECMWF Run。

    网络错误允许有限次数重试。
    """

    params = build_params(
        run_time
    )

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES_PER_RUN + 1
    ):

        try:

            print(
                f"请求 Run："
                f"{run_time.isoformat()} "
                f"| 第 {attempt} 次"
            )

            response = requests.get(
                API_URL,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            print(
                f"HTTP状态码："
                f"{response.status_code}"
            )

            return (
                response,
                params,
            )

        except requests.RequestException as error:

            last_error = error

            print(
                f"网络异常：{error}"
            )

            if (
                attempt
                < MAX_RETRIES_PER_RUN
            ):

                print(
                    "3秒后重试..."
                )

                time.sleep(3)

    raise requests.RequestException(
        f"Run请求连续失败："
        f"{last_error}"
    )


# ==========================================================
# Response结构检查
# ==========================================================

def validate_response(
    response,
    requested_run
):
    """
    ECMWF V2响应结构审计。

    要求：

    - JSON对象
    - hourly存在
    - hourly_units存在
    - 72小时
    - 18个正式变量全部存在
    - 每个变量长度必须与时间轴一致

    注意：

    某些变量允许内部存在NULL。

    NULL != 字段缺失
    NULL != 采集失败

    原始NULL必须保留。
    """

    data = response.json()
    validate_payload(data, requested_run)

    if not isinstance(
        data,
        dict
    ):

        raise ValueError(
            "ECMWF返回顶层不是JSON对象"
        )

    hourly = data.get(
        "hourly"
    )

    hourly_units = data.get(
        "hourly_units"
    )

    if not isinstance(
        hourly,
        dict
    ):

        raise ValueError(
            "缺少hourly数据"
        )

    if not isinstance(
        hourly_units,
        dict
    ):

        raise ValueError(
            "缺少hourly_units"
        )

    times = hourly.get(
        "time"
    )

    if not isinstance(
        times,
        list
    ):

        raise ValueError(
            "hourly.time不存在"
        )

    if (
        len(times)
        != FORECAST_HOURS
    ):

        raise ValueError(
            "ECMWF小时数量异常："
            f"{len(times)}，"
            f"预期{FORECAST_HOURS}"
        )

    # ======================================================
    # 检查18个变量
    # ======================================================

    for variable in (
        HOURLY_VARIABLES
    ):

        values = hourly.get(
            variable
        )

        if not isinstance(
            values,
            list
        ):

            raise ValueError(
                f"缺少变量："
                f"{variable}"
            )

        if (
            len(values)
            != len(times)
        ):

            raise ValueError(
                f"{variable}"
                f"长度异常："
                f"{len(values)} != "
                f"{len(times)}"
            )

    # ======================================================
    # 输出NULL统计
    # ======================================================

    print()
    print(
        "[PASS] ECMWF V2返回结构检查"
    )

    print(
        f"小时数量："
        f"{len(times)}"
    )

    print(
        f"正式变量："
        f"{len(HOURLY_VARIABLES)}"
    )

    print(
        f"第一Target："
        f"{times[0]}"
    )

    print(
        f"最后Target："
        f"{times[-1]}"
    )

    print(
        f"请求Run："
        f"{requested_run.isoformat()}"
    )

    print()
    print(
        "变量完整性："
    )

    for variable in (
        HOURLY_VARIABLES
    ):

        values = hourly[
            variable
        ]

        null_count = sum(
            value is None
            for value in values
        )

        non_null_count = (
            len(values)
            - null_count
        )

        unit = hourly_units.get(
            variable
        )

        print(
            f"  {variable:<26} "
            f"{non_null_count:>2}/"
            f"{len(values)}有效 "
            f"| NULL={null_count:<2} "
            f"| {unit}"
        )

    return data


# ==========================================================
# Bronze保存
# ==========================================================

def save_bronze(
    raw_text,
    run_time
):
    """
    原始服务器响应原样写入Bronze。

    不重新json.dumps。

    这样保证：

        HTTP Response Text
                =
        Bronze Raw Text
                =
        Raw Database raw_json

    最大程度保留原始数据。
    """

    ECMWF_BRONZE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    ingest_time = utc_now()

    ingest_timestamp = (
        ingest_time.strftime(
            "%Y%m%dT%H%M%S_%fZ"
        )
    )

    run_timestamp = (
        run_time.strftime(
            "%Y%m%dT%H00Z"
        )
    )

    filename = (
        f"ecmwf_ifs_hres_"
        f"run_{run_timestamp}_"
        f"ingest_{ingest_timestamp}.json"
    )

    file_path = (
        ECMWF_BRONZE_DIR
        / filename
    )

    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            raw_text
        )

    return (
        file_path,
        ingest_time,
    )


# ==========================================================
# SHA256
# ==========================================================

def calculate_sha256(
    raw_text
):
    """
    对服务器原始响应文本计算SHA256。
    """

    return hashlib.sha256(
        raw_text.encode(
            "utf-8"
        )
    ).hexdigest()


# ==========================================================
# ECMWF Model Cycle
# ==========================================================

def determine_model_cycle(
    run_time
):
    """
    当前项目使用的模型周期标记。

    2026-05-12 06UTC起：

        50r1

    更早历史Run暂时：

        pre_50r1

    后续进行历史数据批量采集前，
    再建立完整历史Cycle映射。
    """

    return archive_cycle(run_time, API_MODEL)

    cycle_50r1_start = datetime(
        2026,
        5,
        12,
        6,
        0,
        tzinfo=timezone.utc,
    )

    if (
        run_time
        >= cycle_50r1_start
    ):

        return "50r1"

    return "pre_50r1"


# ==========================================================
# Raw Database
# ==========================================================

def insert_raw_run(
    run_time,
    ingest_time,
    request_url,
    params,
    raw_text,
    bronze_file,
    content_sha256,
):
    """
    ECMWF Raw Snapshot -> SQLite

    唯一性：

        model
        +
        run_time
        +
        content_sha256

    完全相同Raw Snapshot不重复入库。

    但是：

        同一个Run
        +
        不同Raw内容

    允许作为新的Raw Snapshot保存。

    因此V1的8变量Raw与V2的18变量Raw
    可以同时永久存在。
    """

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    try:

        ensure_schema(connection)

        cursor = (
            connection.cursor()
        )

        model_cycle = (
            determine_model_cycle(
                run_time
            )
        )

        availability_type = (
            "observed_ingest_time"
        )

        # ==================================================
        # 实时采集阶段
        #
        # 本系统真正确认数据已经可获取的时间，
        # 就是成功取得HTTP响应的时间。
        # ==================================================

        source_available_time = (
            iso_utc(
                ingest_time
            )
        )

        params_json = json.dumps(
            params,
            ensure_ascii=False,
            sort_keys=True,
        )

        try:

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
                    archive_type, data_spec_version, collector_version,
                    availability_rule_version, api_model,
                    requested_latitude, requested_longitude

                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                );
                """,
                (
                    ECMWF_MODEL,
                    model_cycle,

                    iso_utc(
                        run_time
                    ),

                    source_available_time,
                    availability_type,

                    iso_utc(
                        ingest_time
                    ),

                    ECMWF_SOURCE,

                    request_url,
                    params_json,

                    raw_text,

                    str(
                        bronze_file
                    ),

                    content_sha256,

                    iso_utc(
                        ingest_time
                    ),
                    "realtime", DATA_SPEC_VERSION, "ECMWF_IFS_COLLECTOR_V2_1",
                    "REALTIME_OBSERVED_INGEST_V1", API_MODEL,
                    params["latitude"], params["longitude"],
                ),
            )

            connection.commit()

            return {
                "status":
                    "inserted",

                "row_id":
                    cursor.lastrowid,
            }

        except sqlite3.IntegrityError as error:

            if (
                "UNIQUE constraint failed"
                in str(error)
            ):

                connection.rollback()

                cursor.execute(
                    """
                    SELECT
                        id,
                        bronze_file_path
                    FROM ecmwf_raw_runs
                    WHERE model = ?
                      AND run_time_utc = ?
                      AND content_sha256 = ?
                    LIMIT 1;
                    """,
                    (
                        ECMWF_MODEL,

                        iso_utc(
                            run_time
                        ),

                        content_sha256,
                    ),
                )

                existing = (
                    cursor.fetchone()
                )

                return {
                    "status":
                        "duplicate",

                    "row_id":
                        (
                            existing[0]
                            if existing
                            else None
                        ),

                    "existing_bronze_file":
                        (
                            existing[1]
                            if existing
                            else None
                        ),
                }

            raise

    finally:

        connection.close()


# ==========================================================
# 找到真正可用的Run
# ==========================================================

def find_available_run():
    """
    从最新候选Run向前寻找真正可用的数据。

    最新Run尚未发布完成时，
    自动尝试上一Run。
    """

    candidates = (
        build_candidate_runs()
    )

    for run_time in candidates:

        print()
        print(
            "=" * 72
        )

        print(
            "尝试 ECMWF IFS HRES Run："
            f"{run_time.isoformat()}"
        )

        try:

            (
                response,
                params,
            ) = request_ecmwf_run(
                run_time
            )

        except requests.RequestException as error:

            print(
                f"该Run网络请求失败："
                f"{error}"
            )

            continue

        if (
            response.status_code
            != 200
        ):

            print(
                "该Run当前不可用，"
                "继续尝试上一Run。"
            )

            continue

        try:

            validate_response(
                response,
                run_time
            )

        except (
            ValueError,
            json.JSONDecodeError,
        ) as error:

            print(
                f"该Run结构检查失败："
                f"{error}"
            )

            continue

        return (
            run_time,
            response,
            params,
        )

    raise RuntimeError(
        "没有找到可用的ECMWF Run"
    )


# ==========================================================
# Main
# ==========================================================

def main():

    print(
        "=" * 72
    )

    print(
        "ECMWF IFS HRES Collector V2"
    )

    print(
        "=" * 72
    )

    print(
        f"ZUUU："
        f"{STATION_LATITUDE}, "
        f"{STATION_LONGITUDE}"
    )

    print(
        f"模型："
        f"{API_MODEL}"
    )

    print(
        f"预测长度："
        f"{FORECAST_HOURS}小时"
    )

    print(
        f"Hourly变量："
        f"{len(HOURLY_VARIABLES)}个"
    )

    # ======================================================
    # 寻找真正可用Run
    # ======================================================

    (
        run_time,
        response,
        params,
    ) = find_available_run()

    raw_text = (
        response.text
    )

    # ======================================================
    # Bronze
    # ======================================================

    (
        bronze_file,
        ingest_time,
    ) = save_bronze(
        raw_text,
        run_time,
    )

    print()
    print(
        "=" * 72
    )

    print(
        "ECMWF Bronze V2保存成功"
    )

    print(
        "=" * 72
    )

    print(
        f"Run Time UTC："
        f"{run_time.isoformat()}"
    )

    print(
        f"Ingest Time UTC："
        f"{iso_utc(ingest_time)}"
    )

    print(
        "Availability Type："
        "observed_ingest_time"
    )

    print(
        f"Bronze："
        f"{bronze_file}"
    )

    # ======================================================
    # SHA256
    # ======================================================

    content_sha256 = (
        calculate_sha256(
            raw_text
        )
    )

    print(
        f"SHA256："
        f"{content_sha256}"
    )

    # ======================================================
    # Raw DB
    # ======================================================

    result = insert_raw_run(
        run_time=run_time,
        ingest_time=ingest_time,
        request_url=response.url,
        params=params,
        raw_text=raw_text,
        bronze_file=bronze_file,
        content_sha256=content_sha256,
    )

    print()
    print(
        "=" * 72
    )

    if (
        result["status"]
        == "inserted"
    ):

        print(
            "ECMWF Raw V2数据库写入成功"
        )

        print(
            f"Raw Run ID："
            f"{result['row_id']}"
        )

    else:

        print(
            "检测到完全相同的"
            "ECMWF Raw快照"
        )

        print(
            "处理结果："
            "数据库不重复写入"
        )

        print(
            f"已有Raw Run ID："
            f"{result['row_id']}"
        )

        print(
            "原数据库记录Bronze："
        )

        print(
            result.get(
                "existing_bronze_file"
            )
        )

    print(
        "=" * 72
    )

    print()

    print(
        "[PASS] ECMWF IFS HRES "
        "Collector V2"
    )


if __name__ == "__main__":

    main()
