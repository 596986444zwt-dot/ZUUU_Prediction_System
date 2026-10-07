"""
ECMWF Historical Run Schedule Probe V1

目的：
1. 探测 Open-Meteo Single Runs API 中 ECMWF 历史 Run 的实际可用性
2. 比较：
       ecmwf_ifs
       ecmwf_ifs025
3. 检查：
       00Z / 06Z / 12Z / 18Z
4. 覆盖 2024 -> 2026 关键时间节点

重要：
- 不写 Bronze
- 不写 Raw
- 不写 Silver
- 不修改数据库
- HTTP 400 = NOT_AVAILABLE
- SSL / Connection / Timeout = NETWORK_ERROR
"""

import time
from datetime import datetime

import requests


# ============================================================
# Version
# ============================================================

PROBE_VERSION = "ECMWF_HISTORICAL_RUN_SCHEDULE_PROBE_V1"


# ============================================================
# API
# ============================================================

API_URL = "https://single-runs-api.open-meteo.com/v1/forecast"


# ============================================================
# ZUUU
# ============================================================

LATITUDE = 30.576
LONGITUDE = 103.950


# ============================================================
# Models
# ============================================================

MODELS = [
    "ecmwf_ifs",
    "ecmwf_ifs025",
]


# ============================================================
# Representative dates
# ============================================================

TEST_DATES = [

    # Early archive
    "2024-03-15",

    # Middle of 2024
    "2024-06-01",

    # Around operational cycle transition period
    "2024-11-11",
    "2024-11-13",

    # 2025
    "2025-01-01",
    "2025-06-01",
    "2025-12-01",

    # 2026 before 50r1
    "2026-01-15",

    # Around 50r1 transition
    "2026-05-11",
    "2026-05-13",

    # Known recent period
    "2026-09-20",
]


RUN_HOURS = [
    0,
    6,
    12,
    18,
]


# ============================================================
# Request configuration
# ============================================================

TIMEOUT = 30

MAX_NETWORK_RETRIES = 2

RETRY_DELAY = 3

REQUEST_INTERVAL = 0.8


# ============================================================
# Result states
# ============================================================

AVAILABLE = "AVAILABLE"

NOT_AVAILABLE = "NOT_AVAILABLE"

INVALID_MODEL = "INVALID_MODEL"

NETWORK_ERROR = "NETWORK_ERROR"

HTTP_ERROR = "HTTP_ERROR"

INVALID_RESPONSE = "INVALID_RESPONSE"


# ============================================================
# Build Run
# ============================================================

def build_run(
    date_text,
    hour,
):

    dt = datetime.strptime(
        date_text,
        "%Y-%m-%d",
    )

    return (
        dt.replace(
            hour=hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        .strftime(
            "%Y-%m-%dT%H:%M"
        )
    )


# ============================================================
# Parameters
# ============================================================

def build_params(
    model,
    run,
):

    # 这里只取 temperature_2m
    # 并且只取1小时
    #
    # 目的：
    # 最大程度减少网络流量，
    # 因为这里仅测试Run是否存在。

    return {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,

        "hourly":
            "temperature_2m",

        "forecast_hours":
            1,

        "timezone":
            "UTC",

        "models":
            model,

        "run":
            run,
    }


# ============================================================
# Parse 400
# ============================================================

def classify_400(
    text,
):

    lower = text.lower()

    if (
        "requested model run is not available"
        in lower
    ):

        return NOT_AVAILABLE

    if (
        "invalid value"
        in lower
        or
        "cannot initialize"
        in lower
    ):

        return INVALID_MODEL

    return HTTP_ERROR


# ============================================================
# Probe One Run
# ============================================================

def probe_one(
    session,
    model,
    run,
):

    params = build_params(
        model,
        run,
    )

    last_network_error = None

    for attempt in range(
        1,
        MAX_NETWORK_RETRIES + 1,
    ):

        try:

            response = session.get(
                API_URL,
                params=params,
                timeout=TIMEOUT,
            )

        except (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:

            last_network_error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            if (
                attempt
                < MAX_NETWORK_RETRIES
            ):

                time.sleep(
                    RETRY_DELAY
                )

                continue

            return {
                "state":
                    NETWORK_ERROR,

                "http":
                    None,

                "detail":
                    last_network_error,
            }

        except requests.exceptions.RequestException as exc:

            return {
                "state":
                    NETWORK_ERROR,

                "http":
                    None,

                "detail":
                    (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
            }

        # ====================================================
        # HTTP 200
        # ====================================================

        if response.status_code == 200:

            try:

                data = response.json()

            except Exception as exc:

                return {
                    "state":
                        INVALID_RESPONSE,

                    "http":
                        200,

                    "detail":
                        f"JSON Error: {exc}",
                }

            hourly = data.get(
                "hourly",
                {}
            )

            times = hourly.get(
                "time",
                []
            )

            temps = hourly.get(
                "temperature_2m",
                []
            )

            if (
                len(times) < 1
                or
                len(temps) < 1
            ):

                return {
                    "state":
                        INVALID_RESPONSE,

                    "http":
                        200,

                    "detail":
                        "hourly数据为空",
                }

            return {
                "state":
                    AVAILABLE,

                "http":
                    200,

                "detail":
                    (
                        f"{times[0]} "
                        f"T={temps[0]}"
                    ),
            }

        # ====================================================
        # HTTP 400
        # ====================================================

        if response.status_code == 400:

            state = classify_400(
                response.text
            )

            return {
                "state":
                    state,

                "http":
                    400,

                "detail":
                    response.text[:300],
            }

        # ====================================================
        # 429
        # ====================================================

        if response.status_code == 429:

            if (
                attempt
                < MAX_NETWORK_RETRIES
            ):

                time.sleep(
                    RETRY_DELAY
                )

                continue

            return {
                "state":
                    HTTP_ERROR,

                "http":
                    429,

                "detail":
                    response.text[:300],
            }

        # ====================================================
        # 5xx
        # ====================================================

        if (
            500
            <= response.status_code
            <= 599
        ):

            if (
                attempt
                < MAX_NETWORK_RETRIES
            ):

                time.sleep(
                    RETRY_DELAY
                )

                continue

            return {
                "state":
                    HTTP_ERROR,

                "http":
                    response.status_code,

                "detail":
                    response.text[:300],
            }

        # ====================================================
        # Other HTTP
        # ====================================================

        return {
            "state":
                HTTP_ERROR,

            "http":
                response.status_code,

            "detail":
                response.text[:300],
        }

    return {
        "state":
            NETWORK_ERROR,

        "http":
            None,

        "detail":
            last_network_error,
    }


# ============================================================
# Symbol
# ============================================================

def state_symbol(
    state,
):

    mapping = {

        AVAILABLE:
            "OK",

        NOT_AVAILABLE:
            "--",

        INVALID_MODEL:
            "XX",

        NETWORK_ERROR:
            "NET",

        HTTP_ERROR:
            "ERR",

        INVALID_RESPONSE:
            "BAD",
    }

    return mapping.get(
        state,
        "???",
    )


# ============================================================
# Print Matrix
# ============================================================

def print_matrix(
    results,
):

    print()
    print("=" * 86)
    print("AVAILABILITY MATRIX")
    print("=" * 86)

    print()

    print(
        "说明："
    )

    print(
        "OK  = Run可获取"
    )

    print(
        "--  = API明确返回Run不存在"
    )

    print(
        "NET = 网络错误，不能判断Run是否存在"
    )

    print(
        "ERR = 其他HTTP错误"
    )

    print(
        "BAD = HTTP 200但响应结构异常"
    )

    print()

    for model in MODELS:

        print()
        print(
            "-" * 86
        )

        print(
            f"MODEL：{model}"
        )

        print(
            "-" * 86
        )

        print(
            f"{'DATE':<14}"
            f"{'00Z':<10}"
            f"{'06Z':<10}"
            f"{'12Z':<10}"
            f"{'18Z':<10}"
        )

        for date_text in TEST_DATES:

            cells = []

            for hour in RUN_HOURS:

                key = (
                    model,
                    date_text,
                    hour,
                )

                result = results[
                    key
                ]

                cells.append(
                    state_symbol(
                        result[
                            "state"
                        ]
                    )
                )

            print(
                f"{date_text:<14}"
                f"{cells[0]:<10}"
                f"{cells[1]:<10}"
                f"{cells[2]:<10}"
                f"{cells[3]:<10}"
            )


# ============================================================
# Summary
# ============================================================

def print_summary(
    results,
):

    print()
    print("=" * 86)
    print("SUMMARY")
    print("=" * 86)

    for model in MODELS:

        counts = {
            AVAILABLE: 0,
            NOT_AVAILABLE: 0,
            INVALID_MODEL: 0,
            NETWORK_ERROR: 0,
            HTTP_ERROR: 0,
            INVALID_RESPONSE: 0,
        }

        for (
            key,
            result
        ) in results.items():

            if key[0] != model:

                continue

            state = result[
                "state"
            ]

            counts[
                state
            ] = (
                counts.get(
                    state,
                    0,
                )
                + 1
            )

        print()

        print(
            f"MODEL：{model}"
        )

        print(
            f"AVAILABLE      ："
            f"{counts[AVAILABLE]}"
        )

        print(
            f"NOT_AVAILABLE  ："
            f"{counts[NOT_AVAILABLE]}"
        )

        print(
            f"NETWORK_ERROR  ："
            f"{counts[NETWORK_ERROR]}"
        )

        print(
            f"HTTP_ERROR     ："
            f"{counts[HTTP_ERROR]}"
        )

        print(
            f"INVALID_RESPONSE："
            f"{counts[INVALID_RESPONSE]}"
        )


# ============================================================
# Detailed Uncertain Results
# ============================================================

def print_uncertain(
    results,
):

    uncertain = []

    for (
        key,
        result
    ) in results.items():

        if result[
            "state"
        ] in (
            NETWORK_ERROR,
            HTTP_ERROR,
            INVALID_RESPONSE,
        ):

            uncertain.append(
                (
                    key,
                    result,
                )
            )

    print()
    print("=" * 86)
    print("UNCERTAIN RESULTS")
    print("=" * 86)

    if not uncertain:

        print(
            "无。所有测试Run均得到确定结果。"
        )

        return

    for (
        key,
        result
    ) in uncertain:

        model, date_text, hour = key

        print()

        print(
            f"{model} | "
            f"{date_text} "
            f"{hour:02d}Z"
        )

        print(
            f"State："
            f"{result['state']}"
        )

        print(
            f"HTTP："
            f"{result['http']}"
        )

        print(
            f"Detail："
            f"{result['detail']}"
        )


# ============================================================
# Main
# ============================================================

def main():

    print(
        "=" * 86
    )

    print(
        "ECMWF Historical "
        "Run Schedule Probe V1"
    )

    print(
        "=" * 86
    )

    print(
        f"Version："
        f"{PROBE_VERSION}"
    )

    print(
        f"Station：ZUUU"
    )

    print(
        f"Location："
        f"{LATITUDE}, "
        f"{LONGITUDE}"
    )

    print(
        f"Models："
        f"{', '.join(MODELS)}"
    )

    print(
        f"Dates："
        f"{len(TEST_DATES)}"
    )

    print(
        f"Runs per date："
        f"{len(RUN_HOURS)}"
    )

    total_requests = (
        len(MODELS)
        * len(TEST_DATES)
        * len(RUN_HOURS)
    )

    print(
        f"Maximum Requests："
        f"{total_requests}"
    )

    print()

    print(
        "READ ONLY："
        "不会修改数据库。"
    )

    results = {}

    session = requests.Session()

    request_number = 0

    try:

        for model in MODELS:

            print()
            print(
                "=" * 86
            )

            print(
                f"MODEL：{model}"
            )

            print(
                "=" * 86
            )

            for date_text in TEST_DATES:

                print()

                print(
                    f"[DATE] "
                    f"{date_text}"
                )

                for hour in RUN_HOURS:

                    request_number += 1

                    run = build_run(
                        date_text,
                        hour,
                    )

                    print(
                        f"  "
                        f"[{request_number}/"
                        f"{total_requests}] "
                        f"{hour:02d}Z "
                        f"... ",
                        end="",
                        flush=True,
                    )

                    result = probe_one(
                        session,
                        model,
                        run,
                    )

                    results[
                        (
                            model,
                            date_text,
                            hour,
                        )
                    ] = result

                    symbol = (
                        state_symbol(
                            result[
                                "state"
                            ]
                        )
                    )

                    print(
                        f"{symbol}"
                    )

                    time.sleep(
                        REQUEST_INTERVAL
                    )

    except KeyboardInterrupt:

        print()
        print()
        print(
            "[STOP] 用户中断。"
        )

        print(
            "本Probe没有写数据库，"
            "无需回滚。"
        )

        return

    finally:

        session.close()

    print_matrix(
        results
    )

    print_summary(
        results
    )

    print_uncertain(
        results
    )

    print()
    print("=" * 86)
    print(
        "Probe Finished"
    )
    print("=" * 86)

    print()

    print(
        "本程序没有写入 "
        "Bronze / Raw / Silver / Status。"
    )


if __name__ == "__main__":

    main()