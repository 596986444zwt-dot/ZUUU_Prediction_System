import json
from datetime import datetime, timezone, timedelta

import requests

from config.settings import (
    STATION_LATITUDE,
    STATION_LONGITUDE,
)


API_URL = (
    "https://single-runs-api.open-meteo.com/v1/forecast"
)

HEADERS = {
    "User-Agent": "ZUUU-Prediction-System/1.0"
}


# ==========================================================
# 第一次探测只使用核心变量
# ==========================================================

HOURLY_VARIABLES = [
    "temperature_2m",
    "dew_point_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "pressure_msl",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
]


def get_candidate_runs():
    """
    生成最近几个合法 ECMWF Run。

    注意：
    Run Time != Available Time

    因此这里故意往前多找几个 Run，
    逐个尝试，直到找到 API 已经能够提供的数据。
    """

    now_utc = datetime.now(
        timezone.utc
    )

    run_hours = [
        0,
        6,
        12,
        18,
    ]

    candidates = []

    # 往前检查 48 小时
    for hours_back in range(0, 49, 6):

        reference = (
            now_utc
            - timedelta(hours=hours_back)
        )

        valid_hours = [
            hour
            for hour in run_hours
            if hour <= reference.hour
        ]

        if valid_hours:

            run_hour = max(
                valid_hours
            )

            run_time = reference.replace(
                hour=run_hour,
                minute=0,
                second=0,
                microsecond=0,
            )

        else:

            previous_day = (
                reference
                - timedelta(days=1)
            )

            run_time = (
                previous_day.replace(
                    hour=18,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            )

        if run_time not in candidates:
            candidates.append(
                run_time
            )

    return sorted(
        candidates,
        reverse=True
    )


def request_run(run_time):

    run_string = run_time.strftime(
        "%Y-%m-%dT%H:%M"
    )

    params = {
        "latitude": STATION_LATITUDE,
        "longitude": STATION_LONGITUDE,

        "hourly": ",".join(
            HOURLY_VARIABLES
        ),

        # 明确指定 ECMWF IFS HRES 9 km
        "models": "ecmwf_ifs025",

        "run": run_string,

        # API时间保持UTC
        "timezone": "UTC",

        # 第一次只看72小时
        "forecast_hours": 72,
    }

    print()
    print("=" * 72)

    print(
        f"尝试 ECMWF Run："
        f"{run_string} UTC"
    )

    response = requests.get(
        API_URL,
        params=params,
        headers=HEADERS,
        timeout=60,
    )

    print(
        f"HTTP状态码："
        f"{response.status_code}"
    )

    return response, params


def inspect_response(
    run_time,
    response,
    params
):

    data = response.json()

    print()
    print("=" * 72)
    print("ECMWF Single Run 探测成功")
    print("=" * 72)

    print(
        f"请求Run："
        f"{run_time.isoformat()}"
    )

    print(
        f"请求坐标："
        f"{STATION_LATITUDE}, "
        f"{STATION_LONGITUDE}"
    )

    print()

    print(
        f"API返回纬度："
        f"{data.get('latitude')}"
    )

    print(
        f"API返回经度："
        f"{data.get('longitude')}"
    )

    print(
        f"API返回海拔："
        f"{data.get('elevation')}"
    )

    print(
        f"Timezone："
        f"{data.get('timezone')}"
    )

    print()

    hourly_units = (
        data.get(
            "hourly_units",
            {}
        )
    )

    hourly = data.get(
        "hourly",
        {}
    )

    print("Hourly Units：")

    print(
        json.dumps(
            hourly_units,
            ensure_ascii=False,
            indent=2,
        )
    )

    print()

    print(
        "Hourly字段："
    )

    for key in hourly.keys():

        values = hourly.get(
            key
        )

        if isinstance(
            values,
            list
        ):

            print(
                f"  {key}: "
                f"{len(values)} 个值"
            )

        else:

            print(
                f"  {key}: "
                f"{type(values).__name__}"
            )

    times = hourly.get(
        "time",
        []
    )

    temperatures = hourly.get(
        "temperature_2m",
        []
    )

    print()

    print(
        f"小时记录数量："
        f"{len(times)}"
    )

    if times:

        print(
            f"第一Target Time："
            f"{times[0]}"
        )

        print(
            f"最后Target Time："
            f"{times[-1]}"
        )

    print()

    print("前5小时样本：")

    for index in range(
        min(
            5,
            len(times)
        )
    ):

        temperature = (
            temperatures[index]
            if index < len(temperatures)
            else None
        )

        print(
            f"{times[index]} | "
            f"T2m={temperature}"
        )

    print()

    print("实际请求参数：")

    print(
        json.dumps(
            params,
            ensure_ascii=False,
            indent=2,
        )
    )

    print()

    print(
        "实际请求URL："
    )

    print(
        response.url
    )

    print()

    print(
        "[PASS] ECMWF Single Run API "
        "真实结构探测完成"
    )

    print("=" * 72)


def main():

    print("=" * 72)
    print("ECMWF IFS HRES Single Run Probe V1")
    print("=" * 72)

    print(
        f"ZUUU坐标："
        f"{STATION_LATITUDE}, "
        f"{STATION_LONGITUDE}"
    )

    candidates = (
        get_candidate_runs()
    )

    for run_time in candidates:

        try:

            response, params = (
                request_run(
                    run_time
                )
            )

        except requests.RequestException as error:

            print(
                f"网络请求失败：{error}"
            )

            continue

        if response.status_code == 200:

            try:

                inspect_response(
                    run_time,
                    response,
                    params,
                )

                return

            except (
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ) as error:

                print(
                    "返回数据解析失败："
                    f"{error}"
                )

                continue

        else:

            print(
                "该Run当前不可用。"
            )

            try:

                error_data = (
                    response.json()
                )

                print(
                    json.dumps(
                        error_data,
                        ensure_ascii=False,
                        indent=2,
                    )
                )

            except Exception:

                print(
                    response.text[
                        :1000
                    ]
                )

    raise RuntimeError(
        "最近候选ECMWF Run均未探测成功"
    )


if __name__ == "__main__":
    main()