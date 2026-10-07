import json
from datetime import datetime, timezone, timedelta

import requests

from config.settings import (
    STATION_LATITUDE,
    STATION_LONGITUDE,
    ECMWF_RUN_HOURS_UTC,
)


API_URL = (
    "https://single-runs-api.open-meteo.com/v1/forecast"
)

API_MODEL = "ecmwf_ifs025"

HEADERS = {
    "User-Agent": "ZUUU-Prediction-System/1.0"
}

FORECAST_HOURS = 72
TIMEOUT_SECONDS = 60


# ==========================================================
# V1 已验证核心变量
# ==========================================================

CORE_VARIABLES = [
    "temperature_2m",
    "dew_point_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "pressure_msl",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
]


# ==========================================================
# 本轮需要验证的扩展变量
# ==========================================================

CANDIDATE_VARIABLES = [
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "wind_gusts_10m",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "precipitation",
    "rain",
    "cape",
]


def get_candidate_runs():

    now = datetime.now(
        timezone.utc
    )

    latest_hour = max(
        hour
        for hour in ECMWF_RUN_HOURS_UTC
        if hour <= now.hour
    )

    latest_run = now.replace(
        hour=latest_hour,
        minute=0,
        second=0,
        microsecond=0,
    )

    return [
        latest_run
        - timedelta(hours=6 * index)
        for index in range(8)
    ]


def build_params(
    run_time,
    variables
):

    return {
        "latitude": STATION_LATITUDE,
        "longitude": STATION_LONGITUDE,
        "hourly": ",".join(variables),
        "models": API_MODEL,
        "run": run_time.strftime(
            "%Y-%m-%dT%H:%M"
        ),
        "timezone": "UTC",
        "forecast_hours": FORECAST_HOURS,
    }


def find_available_run():
    """
    先用已经验证过的 temperature_2m
    寻找一个真实可用 Run。
    """

    print()
    print("=" * 78)
    print("寻找可用 ECMWF Run")
    print("=" * 78)

    for run_time in get_candidate_runs():

        params = build_params(
            run_time,
            ["temperature_2m"],
        )

        print(
            f"尝试："
            f"{run_time.isoformat()}"
        )

        try:

            response = requests.get(
                API_URL,
                params=params,
                headers=HEADERS,
                timeout=TIMEOUT_SECONDS,
            )

        except requests.RequestException as error:

            print(
                f"网络失败：{error}"
            )

            continue

        print(
            f"HTTP：{response.status_code}"
        )

        if response.status_code != 200:
            continue

        try:

            data = response.json()

            times = (
                data.get(
                    "hourly",
                    {}
                ).get(
                    "time",
                    []
                )
            )

            if len(times) == FORECAST_HOURS:

                print()
                print(
                    "[PASS] 找到可用Run："
                    f"{run_time.isoformat()}"
                )

                return run_time

        except Exception as error:

            print(
                f"结构异常：{error}"
            )

    raise RuntimeError(
        "没有找到可用ECMWF Run"
    )


def test_variable(
    run_time,
    variable
):
    """
    单独测试一个变量。

    单独请求的好处：
    某个变量失败不会影响其他变量的判断。
    """

    params = build_params(
        run_time,
        [variable],
    )

    try:

        response = requests.get(
            API_URL,
            params=params,
            headers=HEADERS,
            timeout=TIMEOUT_SECONDS,
        )

    except requests.RequestException as error:

        return {
            "variable": variable,
            "status": "NETWORK_ERROR",
            "detail": str(error),
        }

    if response.status_code != 200:

        detail = response.text[:500]

        try:
            detail = json.dumps(
                response.json(),
                ensure_ascii=False,
            )
        except Exception:
            pass

        return {
            "variable": variable,
            "status": "UNSUPPORTED_OR_ERROR",
            "http": response.status_code,
            "detail": detail,
        }

    try:

        data = response.json()

        hourly = data.get(
            "hourly",
            {}
        )

        units = data.get(
            "hourly_units",
            {}
        )

        times = hourly.get(
            "time",
            []
        )

        values = hourly.get(
            variable
        )

        if not isinstance(values, list):

            return {
                "variable": variable,
                "status": "MISSING",
                "detail": (
                    "HTTP 200但返回中没有该变量"
                ),
            }

        null_count = sum(
            value is None
            for value in values
        )

        non_null_count = (
            len(values)
            - null_count
        )

        sample_values = [
            value
            for value in values
            if value is not None
        ][:5]

        status = "PASS"

        if len(times) != FORECAST_HOURS:

            status = "LENGTH_WARNING"

        if len(values) != len(times):

            status = "LENGTH_ERROR"

        if non_null_count == 0:

            status = "ALL_NULL"

        return {
            "variable": variable,
            "status": status,
            "unit": units.get(variable),
            "time_count": len(times),
            "value_count": len(values),
            "non_null": non_null_count,
            "null": null_count,
            "samples": sample_values,
        }

    except Exception as error:

        return {
            "variable": variable,
            "status": "PARSE_ERROR",
            "detail": str(error),
        }


def combined_test(
    run_time,
    supported_variables
):
    """
    最后把核心变量 + 所有通过的扩展变量
    一次性请求。

    防止变量单独可用，
    组合请求却出现问题。
    """

    variables = (
        CORE_VARIABLES
        + supported_variables
    )

    params = build_params(
        run_time,
        variables,
    )

    print()
    print("=" * 78)
    print("最终组合请求测试")
    print("=" * 78)

    print(
        f"变量数量：{len(variables)}"
    )

    response = requests.get(
        API_URL,
        params=params,
        headers=HEADERS,
        timeout=TIMEOUT_SECONDS,
    )

    print(
        f"HTTP：{response.status_code}"
    )

    if response.status_code != 200:

        print(
            response.text[:1000]
        )

        raise RuntimeError(
            "最终组合请求失败"
        )

    data = response.json()

    hourly = data.get(
        "hourly",
        {}
    )

    times = hourly.get(
        "time",
        []
    )

    if len(times) != FORECAST_HOURS:

        raise RuntimeError(
            "组合请求时间轴不是72小时"
        )

    for variable in variables:

        values = hourly.get(
            variable
        )

        if not isinstance(values, list):

            raise RuntimeError(
                f"组合请求缺少：{variable}"
            )

        if len(values) != len(times):

            raise RuntimeError(
                f"组合请求长度异常：{variable}"
            )

    print(
        "[PASS] 所有变量组合请求成功"
    )

    print(
        f"小时数量：{len(times)}"
    )

    print(
        f"第一Target：{times[0]}"
    )

    print(
        f"最后Target：{times[-1]}"
    )

    print()
    print("最终Hourly Units：")

    print(
        json.dumps(
            data.get(
                "hourly_units",
                {}
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


def main():

    print("=" * 78)
    print(
        "ECMWF Variable Expansion Probe V1"
    )
    print("=" * 78)

    print(
        f"ZUUU："
        f"{STATION_LATITUDE}, "
        f"{STATION_LONGITUDE}"
    )

    print(
        f"Model：{API_MODEL}"
    )

    # ======================================================
    # 找Run
    # ======================================================

    run_time = find_available_run()

    # ======================================================
    # 单变量测试
    # ======================================================

    print()
    print("=" * 78)
    print("扩展变量逐项测试")
    print("=" * 78)

    results = []

    for variable in CANDIDATE_VARIABLES:

        print()
        print(
            f"测试：{variable}"
        )

        result = test_variable(
            run_time,
            variable,
        )

        results.append(
            result
        )

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )

    # ======================================================
    # 汇总
    # ======================================================

    supported = [
        result["variable"]
        for result in results
        if result["status"] == "PASS"
    ]

    failed = [
        result["variable"]
        for result in results
        if result["status"] != "PASS"
    ]

    print()
    print("=" * 78)
    print("扩展变量测试汇总")
    print("=" * 78)

    print(
        f"PASS：{len(supported)}"
    )

    for variable in supported:
        print(
            f"  [PASS] {variable}"
        )

    print()

    print(
        f"未通过：{len(failed)}"
    )

    for variable in failed:
        print(
            f"  [CHECK] {variable}"
        )

    # ======================================================
    # 最终组合测试
    # ======================================================

    if supported:

        combined_test(
            run_time,
            supported,
        )

    print()
    print("=" * 78)
    print(
        "ECMWF Variable Expansion "
        "Probe V1 完成"
    )
    print("=" * 78)


if __name__ == "__main__":
    main()