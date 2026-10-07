"""
ZUUU COR Semantics Audit V1.0

目的：
    对 Bronze Archive 中所有 COR 报文进行语义审计。

重点：
    1. 找出全部 COR
    2. 查看 COR 前后观测
    3. 查看同一小时/同一 valid time 是否存在其他 Raw
    4. 解析温度
    5. 查看 COR 是否可能影响 Daily Tmax
    6. 为后续 Silver/QC 规则提供证据

原则：
    - READ ONLY
    - 不修改 Bronze
    - 不删除 COR
    - 不自动判断 supersession
    - 不访问网络
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH


UTC = timezone.utc

TEMP_RE = re.compile(
    r"\s(M?\d{2})/(M?\d{2}|//)\s"
)


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        raise ValueError(
            f"Naive datetime: {value}"
        )

    return dt.astimezone(UTC)


def parse_temperature(raw: str):

    match = TEMP_RE.search(
        raw
    )

    if not match:
        return None

    token = match.group(1)

    if token.startswith("M"):
        return -int(
            token[1:]
        )

    return int(token)


def print_record(
    row,
    prefix="",
):

    temp = parse_temperature(
        row["raw_metar"]
    )

    print(
        f"{prefix}"
        f"{row['observation_time_utc']} | "
        f"{row['source']:<7} | "
        f"{row['source_query_class'] or '-':<8} | "
        f"{row['message_class']:<10} | "
        f"T={str(temp):>4} | "
        f"{row['raw_metar']}"
    )


def main():

    print("=" * 90)
    print("ZUUU COR Semantics Audit V1.0")
    print("=" * 90)

    print(
        f"Database : {DEFAULT_DB_PATH}"
    )

    conn = sqlite3.connect(
        DEFAULT_DB_PATH
    )

    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            id,
            station,
            source,
            source_query_class,
            message_class,
            observation_time_utc,
            raw_metar,
            recovery_reason
        FROM zuuu_raw_metar
        ORDER BY observation_time_utc, id
        """
    ).fetchall()

    conn.close()

    # --------------------------------------------------------
    # Parse all rows
    # --------------------------------------------------------

    parsed_rows = []

    for row in rows:

        dt = parse_utc(
            row["observation_time_utc"]
        )

        temp = parse_temperature(
            row["raw_metar"]
        )

        parsed_rows.append(
            {
                "row": row,
                "dt": dt,
                "temp": temp,
            }
        )

    cor_items = [
        item
        for item in parsed_rows
        if item["row"]["message_class"]
        == "COR"
    ]

    print()
    print(
        f"Total Bronze Rows : {len(rows)}"
    )

    print(
        f"COR Records       : {len(cor_items)}"
    )

    print()

    # --------------------------------------------------------
    # Audit every COR
    # --------------------------------------------------------

    review_count = 0

    for number, cor_item in enumerate(
        cor_items,
        start=1,
    ):

        cor_row = cor_item["row"]
        cor_dt = cor_item["dt"]
        cor_temp = cor_item["temp"]

        print("=" * 90)
        print(
            f"COR #{number}"
        )
        print("=" * 90)

        print(
            f"ID          : {cor_row['id']}"
        )

        print(
            f"UTC         : {cor_dt.isoformat()}"
        )

        print(
            f"Source      : {cor_row['source']}"
        )

        print(
            "Query Class : "
            f"{cor_row['source_query_class']}"
        )

        print(
            f"Temperature : {cor_temp}"
        )

        print(
            f"Raw         : {cor_row['raw_metar']}"
        )

        # ----------------------------------------------------
        # Exact same timestamp
        # ----------------------------------------------------

        same_time = [
            item
            for item in parsed_rows
            if item["dt"] == cor_dt
        ]

        print()
        print(
            f"[A] SAME VALID TIME ({len(same_time)} Raw)"
        )
        print("-" * 90)

        for item in same_time:
            print_record(
                item["row"],
                prefix="    ",
            )

        # ----------------------------------------------------
        # ±3 hours context
        # ----------------------------------------------------

        window_start = (
            cor_dt
            - timedelta(hours=3)
        )

        window_end = (
            cor_dt
            + timedelta(hours=3)
        )

        context = [
            item
            for item in parsed_rows
            if (
                window_start
                <= item["dt"]
                <= window_end
            )
        ]

        print()
        print("[B] ±3 HOUR CONTEXT")
        print("-" * 90)

        for item in context:

            marker = (
                ">>> "
                if item["row"]["id"]
                == cor_row["id"]
                else "    "
            )

            print_record(
                item["row"],
                prefix=marker,
            )

        # ----------------------------------------------------
        # UTC calendar day
        # ----------------------------------------------------

        day_start = cor_dt.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        day_end = (
            day_start
            + timedelta(days=1)
        )

        day_items = [
            item
            for item in parsed_rows
            if (
                day_start
                <= item["dt"]
                < day_end
            )
        ]

        valid_day_temps = [
            item["temp"]
            for item in day_items
            if item["temp"] is not None
        ]

        day_max = (
            max(valid_day_temps)
            if valid_day_temps
            else None
        )

        cor_is_utc_day_max = (
            cor_temp is not None
            and day_max is not None
            and cor_temp == day_max
        )

        print()
        print("[C] UTC DAY TEMPERATURE CONTEXT")
        print("-" * 90)

        print(
            f"UTC Date       : {day_start.date()}"
        )

        print(
            f"Raw Rows       : {len(day_items)}"
        )

        print(
            f"Parsed Temps   : {len(valid_day_temps)}"
        )

        print(
            f"UTC Day Max    : {day_max}"
        )

        print(
            "COR Equals Max : "
            f"{cor_is_utc_day_max}"
        )

        max_rows = [
            item
            for item in day_items
            if (
                item["temp"] is not None
                and item["temp"] == day_max
            )
        ]

        if max_rows:

            print()
            print("UTC Day Max Record(s):")

            for item in max_rows:

                print_record(
                    item["row"],
                    prefix="    ",
                )

        # ----------------------------------------------------
        # BJT business day
        # ----------------------------------------------------

        bjt = timezone(
            timedelta(hours=8)
        )

        cor_bjt = cor_dt.astimezone(
            bjt
        )

        bjt_day_start = datetime(
            cor_bjt.year,
            cor_bjt.month,
            cor_bjt.day,
            0,
            0,
            0,
            tzinfo=bjt,
        )

        bjt_day_end = (
            bjt_day_start
            + timedelta(days=1)
        )

        bjt_start_utc = (
            bjt_day_start.astimezone(
                UTC
            )
        )

        bjt_end_utc = (
            bjt_day_end.astimezone(
                UTC
            )
        )

        business_day_items = [
            item
            for item in parsed_rows
            if (
                bjt_start_utc
                <= item["dt"]
                < bjt_end_utc
            )
        ]

        business_temps = [
            item["temp"]
            for item in business_day_items
            if item["temp"] is not None
        ]

        business_max = (
            max(business_temps)
            if business_temps
            else None
        )

        cor_is_business_max = (
            cor_temp is not None
            and business_max is not None
            and cor_temp == business_max
        )

        print()
        print("[D] BJT BUSINESS DAY CONTEXT")
        print("-" * 90)

        print(
            f"BJT Time       : {cor_bjt.isoformat()}"
        )

        print(
            f"Business Date  : {cor_bjt.date()}"
        )

        print(
            f"Business Max   : {business_max}"
        )

        print(
            "COR Equals Max : "
            f"{cor_is_business_max}"
        )

        business_max_rows = [
            item
            for item in business_day_items
            if (
                item["temp"] is not None
                and item["temp"]
                == business_max
            )
        ]

        if business_max_rows:

            print()
            print(
                "Business Day Max Record(s):"
            )

            for item in business_max_rows:

                print_record(
                    item["row"],
                    prefix="    ",
                )

        # ----------------------------------------------------
        # Audit interpretation
        # ----------------------------------------------------

        print()
        print("[E] AUDIT FLAGS")
        print("-" * 90)

        flags = []

        if len(same_time) == 1:

            flags.append(
                "NO_ORIGINAL_RAW_AT_SAME_TIMESTAMP"
            )

        else:

            flags.append(
                "MULTIPLE_RAW_AT_SAME_TIMESTAMP"
            )

        if cor_is_business_max:

            flags.append(
                "COR_AFFECTS_OR_EQUALS_BUSINESS_DAY_MAX"
            )

        if cor_row["source_query_class"] == "SPECIAL":

            flags.append(
                "SOURCE_QUERY_SPECIAL"
            )

        if cor_row["source_query_class"] == "ROUTINE":

            flags.append(
                "SOURCE_QUERY_ROUTINE"
            )

        if not flags:

            flags.append(
                "NO_SPECIAL_FLAG"
            )

        for flag in flags:

            print(
                f"    {flag}"
            )

        if (
            len(same_time) == 1
            or cor_is_business_max
        ):

            review_count += 1

        print()

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    print("=" * 90)
    print("FINAL REPORT")
    print("=" * 90)

    print(
        f"Bronze Rows             : {len(rows)}"
    )

    print(
        f"COR Records             : {len(cor_items)}"
    )

    print(
        "COR Requiring Review    : "
        f"{review_count}"
    )

    print()
    print(
        "RESULT: COR SEMANTICS AUDIT COMPLETE"
    )

    print(
        "NOTE: No Bronze records were modified."
    )


if __name__ == "__main__":
    main()