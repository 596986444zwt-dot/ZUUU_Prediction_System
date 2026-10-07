"""
ZUUU Bronze Archive Audit V1.0

审计目标：
    2025-09-24 UTC

验证：
1. 数据库总记录
2. 当天是否24个UTC小时齐全
3. 是否存在重复小时
4. 来源分布是否 IEM=22 / OGIMET=2
5. 03:00Z / 04:00Z 是否由OGIMET恢复
6. recovery_reason是否正确
7. Raw是否存在
8. 时间是否连续
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone

from src.database.zuuu_raw_archive import (
    DEFAULT_DB_PATH,
)


UTC = timezone.utc

TARGET_DATE = "2025-09-24"


def main():

    print("=" * 70)
    print("ZUUU Bronze Archive Audit V1.0")
    print("=" * 70)

    print(f"数据库：{DEFAULT_DB_PATH}")
    print(f"审计UTC日期：{TARGET_DATE}")
    print("=" * 70)

    conn = sqlite3.connect(
        DEFAULT_DB_PATH
    )

    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT *
        FROM zuuu_raw_metar
        WHERE observation_time_utc >= ?
          AND observation_time_utc < ?
        ORDER BY observation_time_utc, id
        """,
        (
            "2025-09-24T00:00:00+00:00",
            "2025-09-25T00:00:00+00:00",
        ),
    ).fetchall()

    conn.close()

    print()
    print(f"当天Raw记录数：{len(rows)}")

    # ========================================================
    # 基础统计
    # ========================================================

    source_counter = Counter()
    class_counter = Counter()
    hour_counter = Counter()

    problems = []

    for row in rows:

        source_counter[
            row["source"]
        ] += 1

        class_counter[
            row["message_class"]
        ] += 1

        dt = datetime.fromisoformat(
            row["observation_time_utc"]
        )

        dt = dt.astimezone(UTC)

        hour_counter[
            dt.hour
        ] += 1

        if not row["raw_metar"]:
            problems.append(
                f"EMPTY RAW: id={row['id']}"
            )

    # ========================================================
    # 来源
    # ========================================================

    print()
    print("Source Distribution:")

    for source, count in sorted(
        source_counter.items()
    ):
        print(
            f"    {source:<10}: {count}"
        )

    # ========================================================
    # Message Class
    # ========================================================

    print()
    print("Message Class Distribution:")

    for message_class, count in sorted(
        class_counter.items()
    ):
        print(
            f"    {message_class:<12}: {count}"
        )

    # ========================================================
    # 小时完整性
    # ========================================================

    missing_hours = [
        hour
        for hour in range(24)
        if hour_counter[hour] == 0
    ]

    duplicate_hours = [
        hour
        for hour in range(24)
        if hour_counter[hour] > 1
    ]

    print()
    print(
        f"Missing UTC Hours   : {missing_hours}"
    )

    print(
        f"Duplicate UTC Hours : {duplicate_hours}"
    )

    # ========================================================
    # 打印24小时
    # ========================================================

    print()
    print("UTC HOURLY ARCHIVE")
    print("-" * 70)

    for row in rows:

        print(
            row["observation_time_utc"],
            "|",
            f"{row['source']:<7}",
            "|",
            f"{row['message_class']:<10}",
            "|",
            row["recovery_reason"] or "-",
        )

    # ========================================================
    # 03 / 04 恢复检查
    # ========================================================

    print()
    print("RECOVERY CHECK")
    print("-" * 70)

    recovery_expectations = {
        3: 25,
        4: 26,
    }

    for target_hour in recovery_expectations:

        matching = []

        for row in rows:

            dt = datetime.fromisoformat(
                row["observation_time_utc"]
            ).astimezone(UTC)

            if dt.hour == target_hour:
                matching.append(row)

        if len(matching) != 1:

            problems.append(
                f"HOUR_{target_hour:02d}_COUNT="
                f"{len(matching)}"
            )

            continue

        row = matching[0]

        print()
        print(
            f"{target_hour:02d}:00Z"
        )

        print(
            f"    Source   : {row['source']}"
        )

        print(
            f"    Reason   : "
            f"{row['recovery_reason']}"
        )

        print(
            f"    Raw      : {row['raw_metar']}"
        )

        if row["source"] != "OGIMET":

            problems.append(
                f"HOUR_{target_hour:02d}_"
                f"NOT_OGIMET"
            )

        if (
            row["recovery_reason"]
            != "IEM_MISSING"
        ):

            problems.append(
                f"HOUR_{target_hour:02d}_"
                f"BAD_RECOVERY_REASON"
            )

    # ========================================================
    # 预期检查
    # ========================================================

    if len(rows) != 24:
        problems.append(
            f"EXPECTED_24_RECORDS_GOT_{len(rows)}"
        )

    if source_counter["IEM"] != 22:
        problems.append(
            "EXPECTED_IEM_22_GOT_"
            f"{source_counter['IEM']}"
        )

    if source_counter["OGIMET"] != 2:
        problems.append(
            "EXPECTED_OGIMET_2_GOT_"
            f"{source_counter['OGIMET']}"
        )

    if missing_hours:
        problems.append(
            f"MISSING_HOURS:{missing_hours}"
        )

    if duplicate_hours:
        problems.append(
            f"DUPLICATE_HOURS:{duplicate_hours}"
        )

    # ========================================================
    # Final
    # ========================================================

    print()
    print("=" * 70)
    print("FINAL AUDIT")
    print("=" * 70)

    print(
        f"Records       : {len(rows)}"
    )

    print(
        f"IEM           : {source_counter['IEM']}"
    )

    print(
        f"OGIMET        : {source_counter['OGIMET']}"
    )

    print(
        f"Missing Hours : {len(missing_hours)}"
    )

    print(
        f"Duplicate Hrs : {len(duplicate_hours)}"
    )

    print(
        f"Problems      : {len(problems)}"
    )

    if problems:

        print()

        for problem in problems:
            print(
                f"ERROR: {problem}"
            )

        print("=" * 70)

        raise SystemExit(
            "RESULT: BRONZE AUDIT FAILED"
        )

    print("=" * 70)

    print(
        "RESULT: BRONZE ARCHIVE AUDIT PASS"
    )


if __name__ == "__main__":
    main()