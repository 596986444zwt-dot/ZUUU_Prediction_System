"""
ZUUU Historical Gap Investigation V1.0
======================================

目标异常：
    2025-09-24 UTC

已知：
    Routine = 22
    Missing Hours = [03, 04]
    Special = 0
    Max Gap = 180 min

目的：
    对唯一历史缺口进行专项调查。

安全：
    不写数据库
    不写Bronze
    不写Raw
    不写Silver
"""

from __future__ import annotations

import csv
import io
import time

from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


STATION = "ZUUU"

IEM_URL = (
    "https://mesonet.agron.iastate.edu/"
    "cgi-bin/request/asos.py"
)

TARGET_DATE = date(2025, 9, 24)

HTTP_TIMEOUT = 45
MAX_RETRIES = 6
RETRY_BASE_SECONDS = 2

USER_AGENT = (
    "ZUUU-Prediction-System/"
    "Gap-Investigation-V1.0"
)


def banner(title: str):

    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def build_url(
    start_date: date,
    end_date: date,
    report_type: str,
):

    params = [
        ("station", STATION),
        ("data", "metar"),

        ("year1", start_date.year),
        ("month1", start_date.month),
        ("day1", start_date.day),

        ("year2", end_date.year),
        ("month2", end_date.month),
        ("day2", end_date.day),

        ("tz", "UTC"),
        ("format", "onlycomma"),

        ("latlon", "no"),
        ("elev", "no"),

        ("missing", "null"),
        ("trace", "null"),

        ("direct", "no"),
    ]

    for item in report_type.split(","):

        params.append(
            ("report_type", item)
        )

    return (
        IEM_URL
        + "?"
        + urlencode(params)
    )


def download(
    start_date: date,
    end_date: date,
    report_type: str,
):

    url = build_url(
        start_date,
        end_date,
        report_type,
    )

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Connection": "close",
                },
            )

            with urlopen(
                request,
                timeout=HTTP_TIMEOUT,
            ) as response:

                data = response.read()

            return data.decode(
                "utf-8",
                errors="replace",
            )

        except (
            HTTPError,
            URLError,
            TimeoutError,
            ConnectionError,
        ) as exc:

            last_error = exc

            print(
                f"[RETRY] "
                f"type={report_type} "
                f"{attempt}/{MAX_RETRIES} "
                f"{exc}"
            )

            if attempt >= MAX_RETRIES:
                break

            wait = min(
                RETRY_BASE_SECONDS
                * (2 ** (attempt - 1)),
                30,
            )

            time.sleep(wait)

    raise RuntimeError(
        f"Download failed: {last_error}"
    )


def parse_time(text: str):

    text = text.strip()

    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ):

        try:

            dt = datetime.strptime(
                text,
                fmt,
            )

            return dt.replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            pass

    raise ValueError(
        f"Unknown time: {text}"
    )


def parse_csv(text: str):

    result = []

    reader = csv.DictReader(
        io.StringIO(text)
    )

    if not reader.fieldnames:
        return result

    field_map = {
        name.lower().strip(): name
        for name in reader.fieldnames
        if name
    }

    valid_field = field_map.get(
        "valid"
    )

    metar_field = field_map.get(
        "metar"
    )

    if not valid_field or not metar_field:

        raise RuntimeError(
            "IEM CSV missing valid/metar"
        )

    for row in reader:

        valid = (
            row.get(valid_field)
            or ""
        ).strip()

        raw = (
            row.get(metar_field)
            or ""
        ).strip()

        if not valid or not raw:
            continue

        if raw.lower() in {
            "null",
            "none",
            "nan",
        }:
            continue

        result.append(
            (
                parse_time(valid),
                " ".join(raw.split()),
            )
        )

    return result


def print_records(
    title,
    records,
):

    banner(title)

    if not records:

        print("0 records")
        return

    for valid, raw in records:

        print(
            f"{valid.isoformat()} | "
            f"{raw}"
        )


def investigate():

    banner(
        "ZUUU Historical Gap Investigation V1.0"
    )

    print(
        f"Target Date：{TARGET_DATE}"
    )

    print(
        "Known Missing UTC Hours：03:00, 04:00"
    )

    print()

    print(
        "正式数据库写入：NO"
    )

    print(
        "Bronze写入：NO"
    )

    print(
        "Raw写入：NO"
    )

    print(
        "Silver写入：NO"
    )


    # ========================================================
    # 查询范围
    #
    # 2025-09-23 00:00 UTC
    # 至
    # 2025-09-26 00:00 UTC
    #
    # 这样可以检查前一天、异常日、后一天。
    # ========================================================

    start_date = (
        TARGET_DATE
        - timedelta(days=1)
    )

    end_date = (
        TARGET_DATE
        + timedelta(days=2)
    )


    # ========================================================
    # Routine
    # ========================================================

    banner(
        "Downloading Routine"
    )

    routine_text = download(
        start_date,
        end_date,
        "3",
    )

    routine = parse_csv(
        routine_text
    )

    print(
        f"Routine records："
        f"{len(routine)}"
    )

    time.sleep(1)


    # ========================================================
    # Special
    # ========================================================

    banner(
        "Downloading Special"
    )

    special_text = download(
        start_date,
        end_date,
        "4",
    )

    special = parse_csv(
        special_text
    )

    print(
        f"Special records："
        f"{len(special)}"
    )

    time.sleep(1)


    # ========================================================
    # Combined
    # ========================================================

    banner(
        "Downloading Combined"
    )

    combined_text = download(
        start_date,
        end_date,
        "3,4",
    )

    combined = parse_csv(
        combined_text
    )

    print(
        f"Combined records："
        f"{len(combined)}"
    )


    # ========================================================
    # 只显示关键窗口
    #
    # 2025-09-23 22:00
    # →
    # 2025-09-24 07:00
    # ========================================================

    window_start = datetime(
        2025,
        9,
        23,
        22,
        0,
        tzinfo=timezone.utc,
    )

    window_end = datetime(
        2025,
        9,
        24,
        7,
        0,
        tzinfo=timezone.utc,
    )


    routine_window = [
        row
        for row in routine
        if (
            window_start
            <= row[0]
            <= window_end
        )
    ]

    special_window = [
        row
        for row in special
        if (
            window_start
            <= row[0]
            <= window_end
        )
    ]

    combined_window = [
        row
        for row in combined
        if (
            window_start
            <= row[0]
            <= window_end
        )
    ]


    print_records(
        "Routine Critical Window",
        routine_window,
    )

    print_records(
        "Special Critical Window",
        special_window,
    )

    print_records(
        "Combined Critical Window",
        combined_window,
    )


    # ========================================================
    # 精确检查03:00 / 04:00
    # ========================================================

    banner(
        "Exact Missing Timestamp Check"
    )

    missing_times = [

        datetime(
            2025,
            9,
            24,
            3,
            0,
            tzinfo=timezone.utc,
        ),

        datetime(
            2025,
            9,
            24,
            4,
            0,
            tzinfo=timezone.utc,
        ),
    ]


    for target in missing_times:

        print()

        print(
            f"TARGET："
            f"{target.isoformat()}"
        )

        routine_match = [
            raw
            for valid, raw in routine
            if valid == target
        ]

        special_match = [
            raw
            for valid, raw in special
            if valid == target
        ]

        combined_match = [
            raw
            for valid, raw in combined
            if valid == target
        ]

        print(
            f"  Routine："
            f"{len(routine_match)}"
        )

        print(
            f"  Special："
            f"{len(special_match)}"
        )

        print(
            f"  Combined："
            f"{len(combined_match)}"
        )

        for raw in combined_match:

            print(
                f"    {raw}"
            )


    # ========================================================
    # 自动计算Gap
    # ========================================================

    banner(
        "Gap Analysis"
    )

    unique_times = sorted({
        valid
        for valid, raw in combined
    })

    gaps = []

    for previous, current in zip(
        unique_times,
        unique_times[1:],
    ):

        minutes = (
            current
            - previous
        ).total_seconds() / 60

        if minutes > 60:

            gaps.append(
                (
                    previous,
                    current,
                    minutes,
                )
            )


    relevant_gaps = [
        gap
        for gap in gaps
        if (
            gap[0].date()
            in {
                date(2025, 9, 23),
                date(2025, 9, 24),
                date(2025, 9, 25),
            }
            or
            gap[1].date()
            in {
                date(2025, 9, 23),
                date(2025, 9, 24),
                date(2025, 9, 25),
            }
        )
    ]


    if not relevant_gaps:

        print(
            "No >60 minute gap found."
        )

    else:

        for (
            previous,
            current,
            minutes,
        ) in relevant_gaps:

            print(
                f"{previous.isoformat()} "
                f"→ "
                f"{current.isoformat()} "
                f"| "
                f"{minutes:.1f} min"
            )


    # ========================================================
    # 三个集合比较
    # ========================================================

    banner(
        "Set Relationship"
    )

    routine_set = set(
        routine
    )

    special_set = set(
        special
    )

    combined_set = set(
        combined
    )

    union_set = (
        routine_set
        | special_set
    )

    print(
        f"Routine："
        f"{len(routine_set)}"
    )

    print(
        f"Special："
        f"{len(special_set)}"
    )

    print(
        f"Union："
        f"{len(union_set)}"
    )

    print(
        f"Combined："
        f"{len(combined_set)}"
    )

    print(
        f"Union missing from Combined："
        f"{len(union_set - combined_set)}"
    )

    print(
        f"Combined extra："
        f"{len(combined_set - union_set)}"
    )


    # ========================================================
    # 最终结论
    # ========================================================

    banner(
        "FINAL INVESTIGATION REPORT"
    )

    target_results = []

    for target in missing_times:

        routine_exists = any(
            valid == target
            for valid, raw
            in routine
        )

        special_exists = any(
            valid == target
            for valid, raw
            in special
        )

        combined_exists = any(
            valid == target
            for valid, raw
            in combined
        )

        target_results.append(
            (
                target,
                routine_exists,
                special_exists,
                combined_exists,
            )
        )


    for (
        target,
        routine_exists,
        special_exists,
        combined_exists,
    ) in target_results:

        print()

        print(
            target.isoformat()
        )

        print(
            f"  Routine："
            f"{routine_exists}"
        )

        print(
            f"  Special："
            f"{special_exists}"
        )

        print(
            f"  Combined："
            f"{combined_exists}"
        )


    print()

    all_missing = all(
        not routine_exists
        and not special_exists
        and not combined_exists

        for (
            target,
            routine_exists,
            special_exists,
            combined_exists,
        )
        in target_results
    )


    if all_missing:

        print(
            "RESULT：CONFIRMED IEM ARCHIVE GAP"
        )

        print()

        print(
            "03:00和04:00 UTC在"
            "Routine、Special、Combined"
            "三个查询中均未出现。"
        )

        print()

        print(
            "注意："
        )

        print(
            "这只能证明IEM当前历史响应"
            "不存在这两个时间点。"
        )

        print(
            "不能据此证明ZUUU当时"
            "没有发布原始报文。"
        )

    else:

        print(
            "RESULT：ALTERNATIVE RECORD FOUND"
        )

        print()

        print(
            "至少一个目标时间在"
            "Routine/Special/Combined"
            "中存在记录，需要继续调查。"
        )


    banner(
        "Safety Confirmation"
    )

    print(
        "正式数据库：未修改"
    )

    print(
        "Bronze：未修改"
    )

    print(
        "Raw：未修改"
    )

    print(
        "Silver：未修改"
    )


if __name__ == "__main__":
    investigate()