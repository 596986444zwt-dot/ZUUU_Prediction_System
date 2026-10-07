"""
ZUUU Bronze Full Archive Audit V1.0

目标：
    对正式 Bronze Raw Archive 做全库只读审计。

当前预期：
    IEM historical range:
        2024-09-02 UTC
        ~
        2026-09-01 UTC

    IEM records:
        Routine = 17518
        Special = 1
        Total   = 17519

    Recovery:
        OGIMET = 2

    Database expected total:
        17521

重要原则：
    1. 只读
    2. 不修改数据库
    3. 不访问网络
    4. 不删除重复
    5. 不合并 COR
    6. 不生成 Ground Truth
    7. Bronze 中发现异常只报告，留给 QC/Silver 处理
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH


UTC = timezone.utc

START_DATE = datetime(2024, 9, 2, tzinfo=UTC)
END_DATE_EXCLUSIVE = datetime(2026, 9, 2, tzinfo=UTC)

EXPECTED_DAYS = 730
EXPECTED_IEM = 17519
EXPECTED_OGIMET = 2
EXPECTED_TOTAL = 17521


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        raise ValueError(
            f"Naive datetime found: {value}"
        )

    return dt.astimezone(UTC)


def build_expected_raw_identity(
    source: str,
    observation_time_utc: str,
    raw_metar: str,
) -> str:
    """
    必须与 zuuu_raw_archive.py 当前 identity 规则一致：

        source
        +
        observation UTC ISO
        +
        exact raw_metar

    如果你之前的 zuuu_raw_archive.py 使用的分隔符不同，
    这里会报告 identity mismatch。

    为避免误判，我们优先尝试导入正式函数。
    """

    payload = (
        f"{source}|"
        f"{observation_time_utc}|"
        f"{raw_metar}"
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def classify_raw(raw: str) -> str:

    if raw.startswith("METAR "):
        return "METAR"

    if raw.startswith("SPECI "):
        return "SPECI"

    if raw.startswith("COR "):
        return "COR"

    if raw.startswith("AMD "):
        return "AMD"

    if raw.startswith("ZUUU "):
        return "PREFIXLESS"

    return "OTHER"


def main():

    print("=" * 78)
    print("ZUUU Bronze Full Archive Audit V1.0")
    print("=" * 78)

    print(f"Database : {DEFAULT_DB_PATH}")
    print(
        "Range    : "
        f"{START_DATE.date()} "
        "to "
        f"{(END_DATE_EXCLUSIVE - timedelta(days=1)).date()} UTC"
    )

    print("=" * 78)

    # ---------------------------------------------------------
    # 尝试使用正式 raw_identity 函数
    # ---------------------------------------------------------

    official_identity_builder = None

    try:
        from src.database.zuuu_raw_archive import build_raw_identity

        official_identity_builder = build_raw_identity

        print(
            "Raw Identity Builder : OFFICIAL"
        )

    except ImportError:

        print(
            "Raw Identity Builder : LOCAL FALLBACK"
        )

    # ---------------------------------------------------------
    # DB
    # ---------------------------------------------------------

    conn = sqlite3.connect(
        DEFAULT_DB_PATH
    )

    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            id,
            raw_identity,
            station,
            source,
            source_query_class,
            message_class,
            observation_time_utc,
            ingest_time_utc,
            raw_metar,
            recovery_reason,
            created_at_utc
        FROM zuuu_raw_metar
        ORDER BY observation_time_utc, id
        """
    ).fetchall()

    conn.close()

    print()
    print(
        f"Database Raw Rows : {len(rows)}"
    )

    # ---------------------------------------------------------
    # Counters
    # ---------------------------------------------------------

    source_counter = Counter()
    query_counter = Counter()
    message_counter = Counter()
    recovery_counter = Counter()

    utc_day_rows = defaultdict(list)
    timestamp_rows = defaultdict(list)

    hard_errors = []
    review_items = []

    identity_mismatches = []

    outside_range = []

    # ---------------------------------------------------------
    # Row-level audit
    # ---------------------------------------------------------

    for row in rows:

        row_id = row["id"]

        station = row["station"]
        source = row["source"]
        query_class = row["source_query_class"]
        message_class = row["message_class"]
        observation_text = row["observation_time_utc"]
        raw = row["raw_metar"]
        recovery_reason = row["recovery_reason"]

        source_counter[source] += 1
        query_counter[
            query_class if query_class is not None else "NULL"
        ] += 1
        message_counter[message_class] += 1

        if recovery_reason:
            recovery_counter[recovery_reason] += 1

        # station
        if station != "ZUUU":
            hard_errors.append(
                f"ID={row_id} BAD_STATION={station}"
            )

        # raw
        if raw is None or raw == "":
            hard_errors.append(
                f"ID={row_id} EMPTY_RAW"
            )
            continue

        # time
        try:
            dt = parse_utc(
                observation_text
            )

        except Exception as exc:

            hard_errors.append(
                f"ID={row_id} BAD_TIME "
                f"{observation_text!r} "
                f"{exc}"
            )

            continue

        if not (
            START_DATE
            <= dt
            < END_DATE_EXCLUSIVE
        ):
            outside_range.append(
                (
                    row_id,
                    dt.isoformat(),
                    source,
                )
            )

        utc_day_rows[
            dt.date().isoformat()
        ].append(row)

        timestamp_rows[
            dt.isoformat()
        ].append(row)

        # message class against exact raw
        detected_class = classify_raw(
            raw
        )

        if detected_class != message_class:

            hard_errors.append(
                f"ID={row_id} "
                f"MESSAGE_CLASS_MISMATCH "
                f"stored={message_class} "
                f"detected={detected_class}"
            )

        # raw identity
        try:

            if official_identity_builder:

                expected_identity = (
                    official_identity_builder(
                        source=source,
                        observation_time_utc=dt,
                        raw_metar=raw,
                    )
                )

            else:

                expected_identity = (
                    build_expected_raw_identity(
                        source,
                        observation_text,
                        raw,
                    )
                )

            if (
                expected_identity
                != row["raw_identity"]
            ):

                identity_mismatches.append(
                    row_id
                )

        except TypeError:

            # 如果正式函数参数签名不同，
            # 不让整个审计崩溃。
            review_items.append(
                "RAW_IDENTITY_SIGNATURE_REVIEW_REQUIRED"
            )

            official_identity_builder = None

    # ---------------------------------------------------------
    # Date coverage
    # ---------------------------------------------------------

    expected_dates = []

    cursor = START_DATE

    while cursor < END_DATE_EXCLUSIVE:

        expected_dates.append(
            cursor.date().isoformat()
        )

        cursor += timedelta(days=1)

    missing_dates = [
        day
        for day in expected_dates
        if day not in utc_day_rows
    ]

    # ---------------------------------------------------------
    # Logical hourly coverage
    #
    # Bronze允许：
    # 同一时间多个Raw
    # COR
    # 多来源
    #
    # 所以这里不把 timestamp > 1 直接当硬错误。
    # ---------------------------------------------------------

    missing_hour_slots = []

    multi_raw_timestamps = []

    for day_text in expected_dates:

        day_start = datetime.fromisoformat(
            day_text
        ).replace(
            tzinfo=UTC
        )

        for hour in range(24):

            target = (
                day_start
                + timedelta(hours=hour)
            )

            key = target.isoformat()

            count = len(
                timestamp_rows.get(
                    key,
                    []
                )
            )

            if count == 0:

                missing_hour_slots.append(
                    key
                )

            elif count > 1:

                multi_raw_timestamps.append(
                    (
                        key,
                        timestamp_rows[key],
                    )
                )

    # ---------------------------------------------------------
    # COR records
    # ---------------------------------------------------------

    cor_rows = [
        row
        for row in rows
        if row["message_class"] == "COR"
    ]

    # ---------------------------------------------------------
    # Recovery rows
    # ---------------------------------------------------------

    recovery_rows = [
        row
        for row in rows
        if row["recovery_reason"]
    ]

    # ---------------------------------------------------------
    # Per-day raw counts
    # ---------------------------------------------------------

    day_count_counter = Counter(
        {
            day: len(day_rows)
            for day, day_rows
            in utc_day_rows.items()
        }
    )

    abnormal_day_counts = []

    for day in expected_dates:

        count = day_count_counter.get(
            day,
            0
        )

        if count != 24:

            abnormal_day_counts.append(
                (
                    day,
                    count,
                )
            )

    # ---------------------------------------------------------
    # Summary
    # ---------------------------------------------------------

    print()
    print("=" * 78)
    print("SOURCE DISTRIBUTION")
    print("=" * 78)

    for key, value in sorted(
        source_counter.items()
    ):
        print(
            f"{key:<20}: {value}"
        )

    print()
    print("=" * 78)
    print("SOURCE QUERY CLASS")
    print("=" * 78)

    for key, value in sorted(
        query_counter.items()
    ):
        print(
            f"{str(key):<20}: {value}"
        )

    print()
    print("=" * 78)
    print("MESSAGE CLASS")
    print("=" * 78)

    for key, value in sorted(
        message_counter.items()
    ):
        print(
            f"{key:<20}: {value}"
        )

    print()
    print("=" * 78)
    print("RECOVERY")
    print("=" * 78)

    if recovery_counter:

        for key, value in sorted(
            recovery_counter.items()
        ):
            print(
                f"{key:<20}: {value}"
            )

    else:

        print("NONE")

    # ---------------------------------------------------------
    # COR details
    # ---------------------------------------------------------

    print()
    print("=" * 78)
    print("COR RECORDS")
    print("=" * 78)

    print(
        f"COR Count : {len(cor_rows)}"
    )

    for row in cor_rows:

        print()
        print(
            f"ID      : {row['id']}"
        )
        print(
            f"UTC     : {row['observation_time_utc']}"
        )
        print(
            f"Source  : {row['source']}"
        )
        print(
            f"Query   : {row['source_query_class']}"
        )
        print(
            f"Raw     : {row['raw_metar']}"
        )

    # ---------------------------------------------------------
    # Recovery details
    # ---------------------------------------------------------

    print()
    print("=" * 78)
    print("RECOVERY RECORDS")
    print("=" * 78)

    print(
        f"Recovery Count : {len(recovery_rows)}"
    )

    for row in recovery_rows:

        print()
        print(
            f"UTC     : {row['observation_time_utc']}"
        )
        print(
            f"Source  : {row['source']}"
        )
        print(
            f"Reason  : {row['recovery_reason']}"
        )
        print(
            f"Raw     : {row['raw_metar']}"
        )

    # ---------------------------------------------------------
    # Multi raw details
    # ---------------------------------------------------------

    print()
    print("=" * 78)
    print("MULTI-RAW TIMESTAMPS")
    print("=" * 78)

    print(
        f"Count : {len(multi_raw_timestamps)}"
    )

    for timestamp, same_time_rows in (
        multi_raw_timestamps
    ):

        print()
        print(
            f"UTC : {timestamp}"
        )

        for row in same_time_rows:

            print(
                f"    ID={row['id']} "
                f"Source={row['source']} "
                f"Class={row['message_class']} "
                f"Raw={row['raw_metar']}"
            )

    # ---------------------------------------------------------
    # Abnormal day counts
    # ---------------------------------------------------------

    print()
    print("=" * 78)
    print("UTC DAY RAW COUNTS != 24")
    print("=" * 78)

    print(
        f"Count : {len(abnormal_day_counts)}"
    )

    for day, count in abnormal_day_counts:

        print(
            f"{day} : {count}"
        )

    # ---------------------------------------------------------
    # Final expectations
    # ---------------------------------------------------------

    if len(rows) != EXPECTED_TOTAL:

        hard_errors.append(
            "DATABASE_TOTAL "
            f"expected={EXPECTED_TOTAL} "
            f"actual={len(rows)}"
        )

    if source_counter["IEM"] != EXPECTED_IEM:

        hard_errors.append(
            "IEM_TOTAL "
            f"expected={EXPECTED_IEM} "
            f"actual={source_counter['IEM']}"
        )

    if source_counter["OGIMET"] != EXPECTED_OGIMET:

        hard_errors.append(
            "OGIMET_TOTAL "
            f"expected={EXPECTED_OGIMET} "
            f"actual={source_counter['OGIMET']}"
        )

    if len(expected_dates) != EXPECTED_DAYS:

        hard_errors.append(
            "EXPECTED_DATE_RANGE_ERROR "
            f"expected={EXPECTED_DAYS} "
            f"actual={len(expected_dates)}"
        )

    if missing_dates:

        hard_errors.append(
            f"MISSING_DATES={len(missing_dates)}"
        )

    if missing_hour_slots:

        hard_errors.append(
            "MISSING_HOURLY_SLOTS="
            f"{len(missing_hour_slots)}"
        )

    if identity_mismatches:

        hard_errors.append(
            "RAW_IDENTITY_MISMATCH="
            f"{len(identity_mismatches)}"
        )

    # Known recovery verification
    expected_recovery = {
        "2025-09-24T03:00:00+00:00",
        "2025-09-24T04:00:00+00:00",
    }

    actual_recovery = {
        row["observation_time_utc"]
        for row in recovery_rows
        if (
            row["source"] == "OGIMET"
            and row["recovery_reason"]
            == "IEM_MISSING"
        )
    }

    if actual_recovery != expected_recovery:

        hard_errors.append(
            "RECOVERY_SET_MISMATCH "
            f"expected={sorted(expected_recovery)} "
            f"actual={sorted(actual_recovery)}"
        )

    # ---------------------------------------------------------
    # FINAL REPORT
    # ---------------------------------------------------------

    print()
    print("=" * 78)
    print("FINAL REPORT")
    print("=" * 78)

    print(
        f"Expected UTC Days      : {len(expected_dates)}"
    )

    print(
        f"Days Present           : "
        f"{len(set(expected_dates) - set(missing_dates))}"
    )

    print(
        f"Missing UTC Dates      : {len(missing_dates)}"
    )

    print(
        f"Database Raw Rows      : {len(rows)}"
    )

    print(
        f"IEM Rows               : {source_counter['IEM']}"
    )

    print(
        f"OGIMET Rows            : {source_counter['OGIMET']}"
    )

    print(
        f"COR Rows               : {len(cor_rows)}"
    )

    print(
        f"Recovery Rows          : {len(recovery_rows)}"
    )

    print(
        f"Missing Hour Slots     : {len(missing_hour_slots)}"
    )

    print(
        f"Multi-Raw Timestamps   : {len(multi_raw_timestamps)}"
    )

    print(
        f"UTC Days Raw Count!=24 : {len(abnormal_day_counts)}"
    )

    print(
        f"Outside Audit Range    : {len(outside_range)}"
    )

    print(
        f"Identity Mismatches    : {len(identity_mismatches)}"
    )

    print(
        f"Hard Errors            : {len(hard_errors)}"
    )

    print(
        f"Review Items           : {len(set(review_items))}"
    )

    # Missing details only if any
    if missing_dates:

        print()
        print("Missing Dates:")

        for item in missing_dates:
            print(
                f"    {item}"
            )

    if missing_hour_slots:

        print()
        print("Missing Hour Slots:")

        for item in missing_hour_slots:
            print(
                f"    {item}"
            )

    if identity_mismatches:

        print()
        print("Identity Mismatch IDs:")

        for item in identity_mismatches[:50]:
            print(
                f"    {item}"
            )

    if hard_errors:

        print()
        print("=" * 78)
        print("HARD ERRORS")
        print("=" * 78)

        for error in hard_errors:
            print(
                f"ERROR: {error}"
            )

    if review_items:

        print()
        print("=" * 78)
        print("REVIEW ITEMS")
        print("=" * 78)

        for item in sorted(
            set(review_items)
        ):
            print(
                f"REVIEW: {item}"
            )

    print()
    print("=" * 78)

    if hard_errors:

        print(
            "RESULT: BRONZE FULL ARCHIVE AUDIT FAILED"
        )

        raise SystemExit(2)

    print(
        "RESULT: BRONZE FULL ARCHIVE AUDIT PASS"
    )


if __name__ == "__main__":
    main()