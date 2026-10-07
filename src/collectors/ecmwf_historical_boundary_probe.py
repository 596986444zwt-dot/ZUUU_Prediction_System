"""
ECMWF Historical Boundary Probe V1

目标：
A. 精确定位 ecmwf_ifs 的 06Z / 18Z 从何时开始可用
B. 精确定位 ecmwf_ifs025 从何时开始可用

只请求：
    temperature_2m
    forecast_hours=1

不会写入：
    Bronze
    Raw
    Silver
    Status
    Database
"""

import time
from datetime import datetime, timedelta, timezone

import requests


VERSION = "ECMWF_HISTORICAL_BOUNDARY_PROBE_V1"

API_URL = "https://single-runs-api.open-meteo.com/v1/forecast"

LATITUDE = 30.576
LONGITUDE = 103.950

TIMEOUT = 30
MAX_NETWORK_RETRIES = 3
RETRY_DELAYS = [2, 5]

AVAILABLE = "AVAILABLE"
NOT_AVAILABLE = "NOT_AVAILABLE"
UNCERTAIN = "UNCERTAIN"


# ============================================================
# HTTP Probe
# ============================================================

def probe_run(session, model, run_time):

    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": "temperature_2m",
        "forecast_hours": 1,
        "timezone": "UTC",
        "models": model,
        "run": run_time.strftime("%Y-%m-%dT%H:%M"),
    }

    last_error = None

    for attempt in range(1, MAX_NETWORK_RETRIES + 1):

        try:

            response = session.get(
                API_URL,
                params=params,
                timeout=TIMEOUT,
            )

            if response.status_code == 200:

                try:
                    data = response.json()

                    hourly = data.get("hourly", {})

                    times = hourly.get("time", [])
                    temps = hourly.get("temperature_2m", [])

                    if not times or not temps:
                        return UNCERTAIN, "HTTP 200 but hourly data empty"

                    return (
                        AVAILABLE,
                        f"{times[0]} T={temps[0]}"
                    )

                except Exception as exc:

                    return (
                        UNCERTAIN,
                        f"JSON error: {exc}"
                    )

            if response.status_code == 400:

                text = response.text

                if (
                    "requested model run is not available"
                    in text.lower()
                ):
                    return (
                        NOT_AVAILABLE,
                        text[:300]
                    )

                return (
                    UNCERTAIN,
                    f"HTTP 400: {text[:300]}"
                )

            if response.status_code == 429:

                last_error = (
                    f"HTTP 429: {response.text[:200]}"
                )

            elif 500 <= response.status_code <= 599:

                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )

            else:

                return (
                    UNCERTAIN,
                    f"HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )

        except (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:

            last_error = (
                f"{type(exc).__name__}: {exc}"
            )

        except requests.exceptions.RequestException as exc:

            return (
                UNCERTAIN,
                f"{type(exc).__name__}: {exc}"
            )

        if attempt < MAX_NETWORK_RETRIES:

            delay = RETRY_DELAYS[
                min(
                    attempt - 1,
                    len(RETRY_DELAYS) - 1
                )
            ]

            time.sleep(delay)

    return (
        UNCERTAIN,
        last_error or "Unknown network error"
    )


# ============================================================
# Helper
# ============================================================

def make_run(date_value, hour):

    return datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        hour,
        0,
        tzinfo=timezone.utc,
    )


def probe_date(session, model, date_value, hour):

    run_time = make_run(
        date_value,
        hour,
    )

    state, detail = probe_run(
        session,
        model,
        run_time,
    )

    print(
        f"    {run_time.strftime('%Y-%m-%d %HZ')} "
        f"→ {state}"
    )

    return state


# ============================================================
# Binary Search Date Boundary
# ============================================================

def find_first_available_date(
    session,
    model,
    hour,
    known_unavailable_date,
    known_available_date,
):

    print()
    print("=" * 78)

    print(
        f"SEARCH：{model} {hour:02d}Z"
    )

    print(
        f"Known unavailable："
        f"{known_unavailable_date}"
    )

    print(
        f"Known available  ："
        f"{known_available_date}"
    )

    print("=" * 78)

    low = datetime.strptime(
        known_unavailable_date,
        "%Y-%m-%d",
    ).date()

    high = datetime.strptime(
        known_available_date,
        "%Y-%m-%d",
    ).date()

    # --------------------------------------------------------
    # Verify endpoints
    # --------------------------------------------------------

    low_state = probe_date(
        session,
        model,
        low,
        hour,
    )

    high_state = probe_date(
        session,
        model,
        high,
        hour,
    )

    if low_state != NOT_AVAILABLE:

        raise RuntimeError(
            f"Lower boundary不是NOT_AVAILABLE："
            f"{model} {low} {hour:02d}Z "
            f"= {low_state}"
        )

    if high_state != AVAILABLE:

        raise RuntimeError(
            f"Upper boundary不是AVAILABLE："
            f"{model} {high} {hour:02d}Z "
            f"= {high_state}"
        )

    # --------------------------------------------------------
    # Binary search
    # --------------------------------------------------------

    while (high - low).days > 1:

        distance = (
            high - low
        ).days

        middle = (
            low
            + timedelta(
                days=distance // 2
            )
        )

        state = probe_date(
            session,
            model,
            middle,
            hour,
        )

        if state == AVAILABLE:

            high = middle

        elif state == NOT_AVAILABLE:

            low = middle

        else:

            raise RuntimeError(
                "出现UNCERTAIN结果，"
                "为避免错误确定边界，程序停止。\n"
                f"Model={model}\n"
                f"Date={middle}\n"
                f"Hour={hour:02d}Z"
            )

        time.sleep(0.5)

    # --------------------------------------------------------
    # Final verification
    # --------------------------------------------------------

    print()
    print("  Final verification:")

    previous_state = probe_date(
        session,
        model,
        low,
        hour,
    )

    first_state = probe_date(
        session,
        model,
        high,
        hour,
    )

    if (
        previous_state != NOT_AVAILABLE
        or
        first_state != AVAILABLE
    ):

        raise RuntimeError(
            "最终边界验证失败"
        )

    print()

    print(
        f"  >>> FIRST AVAILABLE："
        f"{high} {hour:02d}Z"
    )

    return high


# ============================================================
# ecmwf_ifs025:
# Find first available RUN, not merely first available date
# ============================================================

def find_first_ifs025_run(
    session,
    first_available_dates,
):

    print()
    print("=" * 78)
    print("ecmwf_ifs025 FIRST AVAILABLE RUN")
    print("=" * 78)

    candidate_dates = set()

    for value in first_available_dates.values():

        candidate_dates.add(
            value - timedelta(days=1)
        )

        candidate_dates.add(
            value
        )

        candidate_dates.add(
            value + timedelta(days=1)
        )

    candidate_dates = sorted(
        candidate_dates
    )

    available_runs = []

    for date_value in candidate_dates:

        print()
        print(
            f"[DATE] {date_value}"
        )

        for hour in (
            0,
            6,
            12,
            18,
        ):

            run_time = make_run(
                date_value,
                hour,
            )

            state, detail = probe_run(
                session,
                "ecmwf_ifs025",
                run_time,
            )

            print(
                f"    "
                f"{run_time.strftime('%Y-%m-%d %HZ')} "
                f"→ {state}"
            )

            if state == UNCERTAIN:

                raise RuntimeError(
                    "ecmwf_ifs025最终边界检查"
                    "出现UNCERTAIN：\n"
                    f"{run_time}\n"
                    f"{detail}"
                )

            if state == AVAILABLE:

                available_runs.append(
                    run_time
                )

            time.sleep(0.4)

    if not available_runs:

        raise RuntimeError(
            "没有找到ecmwf_ifs025可用Run"
        )

    first_run = min(
        available_runs
    )

    print()
    print(
        f">>> ecmwf_ifs025 "
        f"FIRST AVAILABLE RUN："
        f"{first_run.strftime('%Y-%m-%d %HZ')}"
    )

    return first_run


# ============================================================
# Main
# ============================================================

def main():

    print("=" * 78)
    print("ECMWF Historical Boundary Probe V1")
    print("=" * 78)

    print(
        f"Version：{VERSION}"
    )

    print(
        "READ ONLY："
        "不会写入数据库/Bronze/Raw/Silver/Status"
    )

    session = requests.Session()

    results = {}

    try:

        # ====================================================
        # Boundary A
        #
        # Previous probe:
        #
        # 2024-06-01:
        # 06Z unavailable
        # 18Z unavailable
        #
        # 2024-11-11:
        # 06Z available
        # 18Z available
        # ====================================================

        results[
            "ifs_06z"
        ] = find_first_available_date(
            session=session,
            model="ecmwf_ifs",
            hour=6,
            known_unavailable_date="2024-06-01",
            known_available_date="2024-11-11",
        )

        results[
            "ifs_18z"
        ] = find_first_available_date(
            session=session,
            model="ecmwf_ifs",
            hour=18,
            known_unavailable_date="2024-06-01",
            known_available_date="2024-11-11",
        )

        # ====================================================
        # Boundary B
        #
        # Previous probe:
        #
        # 2026-01-15:
        # ecmwf_ifs025 unavailable
        #
        # 2026-05-11:
        # all 4 available
        #
        # Search every run hour independently.
        # ====================================================

        ifs025_dates = {}

        for hour in (
            0,
            6,
            12,
            18,
        ):

            first_date = find_first_available_date(
                session=session,
                model="ecmwf_ifs025",
                hour=hour,
                known_unavailable_date="2026-01-15",
                known_available_date="2026-05-11",
            )

            ifs025_dates[
                hour
            ] = first_date

        first_ifs025_run = (
            find_first_ifs025_run(
                session,
                ifs025_dates,
            )
        )

        # ====================================================
        # Result
        # ====================================================

        print()
        print()
        print("=" * 78)
        print("FINAL BOUNDARY RESULT")
        print("=" * 78)

        print()

        print("ecmwf_ifs:")

        print(
            "  First available 06Z date："
            f"{results['ifs_06z']}"
        )

        print(
            "  First available 18Z date："
            f"{results['ifs_18z']}"
        )

        print()

        print("ecmwf_ifs025:")

        for hour in (
            0,
            6,
            12,
            18,
        ):

            print(
                f"  First available "
                f"{hour:02d}Z date："
                f"{ifs025_dates[hour]}"
            )

        print()

        print(
            "  FIRST AVAILABLE RUN："
            f"{first_ifs025_run.strftime('%Y-%m-%d %HZ')}"
        )

        print()
        print("=" * 78)

        print(
            "Probe完成。"
        )

        print(
            "没有修改任何项目数据。"
        )

    except KeyboardInterrupt:

        print()
        print(
            "[STOP] 用户中断。"
        )

        print(
            "本程序没有写入任何数据。"
        )

    finally:

        session.close()


if __name__ == "__main__":

    main()