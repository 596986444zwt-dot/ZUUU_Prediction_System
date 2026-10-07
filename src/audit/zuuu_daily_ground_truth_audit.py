"""
ZUUU Daily Ground Truth Audit V1.0
==================================

Purpose:
    Audit Silver observations before freezing Daily Tmax Ground Truth rules.

Important:
    - READ ONLY
    - No database writes
    - No Ground Truth table creation
    - Business day = Asia/Shanghai natural day
    - Tmax uses ALL valid observations in the business day,
      not only hourly observations

Checks:
    1. Determine complete BJT business-day range
    2. Count observations per BJT day
    3. Calculate observed Tmax
    4. Identify first/last Tmax occurrence
    5. Identify Tmax supported by COR
    6. Identify Tmax supported by recovery source
    7. Identify Tmax supported by non-hour observation
    8. Detect missing hourly slots inside complete BJT days
    9. Inspect dataset boundary days
    10. Produce evidence for TARGET_V1 rule freeze
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH
from src.parsers.zuuu_time_normalizer import BJT


UTC = timezone.utc


def parse_dt(value: str) -> datetime:

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        raise ValueError(
            f"NAIVE_DATETIME:{value}"
        )

    return dt


def daterange(
    start_date: date,
    end_date: date,
):

    current = start_date

    while current <= end_date:

        yield current

        current += timedelta(days=1)


def main() -> int:

    print("=" * 94)
    print(
        "ZUUU Daily Ground Truth Audit V1.0"
    )
    print("=" * 94)

    print(
        f"Database : {DEFAULT_DB_PATH}"
    )

    print("=" * 94)

    conn = sqlite3.connect(
        DEFAULT_DB_PATH
    )

    conn.row_factory = sqlite3.Row

    try:

        rows = conn.execute(
            """
            SELECT
                id,
                bronze_raw_id,
                observation_time_utc,
                observation_time_bjt,
                business_date_bjt,
                temperature_c,
                dewpoint_c,
                source,
                source_query_class,
                message_class,
                is_correction,
                recovery_reason,
                qc_status,
                qc_flags
            FROM zuuu_silver_observation
            ORDER BY
                observation_time_utc,
                id
            """
        ).fetchall()

    finally:

        conn.close()

    print(
        f"Silver Rows : {len(rows)}"
    )

    if not rows:

        print(
            "RESULT: NO SILVER DATA"
        )

        return 2

    # ========================================================
    # Parse / group
    # ========================================================

    parsed = []

    by_business_date = defaultdict(
        list
    )

    for row in rows:

        utc_dt = parse_dt(
            row["observation_time_utc"]
        ).astimezone(UTC)

        bjt_dt = parse_dt(
            row["observation_time_bjt"]
        ).astimezone(BJT)

        business_date = date.fromisoformat(
            row["business_date_bjt"]
        )

        item = {
            "row": row,
            "utc": utc_dt,
            "bjt": bjt_dt,
            "date": business_date,
            "temp": float(
                row["temperature_c"]
            ),
        }

        parsed.append(
            item
        )

        by_business_date[
            business_date
        ].append(
            item
        )

    first_utc = parsed[0]["utc"]
    last_utc = parsed[-1]["utc"]

    first_bjt = parsed[0]["bjt"]
    last_bjt = parsed[-1]["bjt"]

    first_business_date = (
        first_bjt.date()
    )

    last_business_date = (
        last_bjt.date()
    )

    # ========================================================
    # Determine COMPLETE BJT days
    #
    # A complete BJT day requires:
    #
    #   BJT 00:00 -> next BJT 00:00
    #
    # to be fully inside the Silver archive time coverage.
    #
    # We do NOT assume the first/last business date is complete.
    # ========================================================

    complete_dates = []

    partial_dates = []

    for business_date in daterange(
        first_business_date,
        last_business_date,
    ):

        start_bjt = datetime(
            business_date.year,
            business_date.month,
            business_date.day,
            0,
            0,
            0,
            tzinfo=BJT,
        )

        end_bjt = (
            start_bjt
            + timedelta(days=1)
        )

        start_utc = (
            start_bjt.astimezone(
                UTC
            )
        )

        end_utc = (
            end_bjt.astimezone(
                UTC
            )
        )

        # Dataset contains hourly observations.
        #
        # For a full business day, archive coverage must include
        # the entire [start_utc, end_utc) interval.
        #
        # Since final observation is discrete, require:
        # first archive observation <= day start
        # last archive observation >= day end - 1 hour
        #
        # Actual hourly completeness is audited separately below.
        expected_last_hour = (
            end_utc
            - timedelta(hours=1)
        )

        if (
            first_utc <= start_utc
            and
            last_utc >= expected_last_hour
        ):

            complete_dates.append(
                business_date
            )

        else:

            partial_dates.append(
                business_date
            )

    complete_date_set = set(
        complete_dates
    )

    # ========================================================
    # Audit complete days
    # ========================================================

    missing_hour_days = []

    abnormal_observation_count_days = []

    tmax_from_cor_days = []

    tmax_from_recovery_days = []

    tmax_from_nonhour_days = []

    tmax_multi_time_days = []

    daily_results = []

    observation_count_distribution = Counter()

    for business_date in complete_dates:

        items = by_business_date.get(
            business_date,
            [],
        )

        observation_count_distribution[
            len(items)
        ] += 1

        if len(items) != 24:

            abnormal_observation_count_days.append(
                (
                    business_date,
                    len(items),
                )
            )

        # ----------------------------------------------------
        # Expected 24 hourly timestamps
        # ----------------------------------------------------

        start_bjt = datetime(
            business_date.year,
            business_date.month,
            business_date.day,
            0,
            0,
            0,
            tzinfo=BJT,
        )

        expected_hours = {
            start_bjt
            + timedelta(hours=hour)
            for hour in range(24)
        }

        actual_hourly_times = {
            item["bjt"].replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            for item in items
            if (
                item["bjt"].minute == 0
                and
                item["bjt"].second == 0
            )
        }

        missing_hours = sorted(
            expected_hours
            - actual_hourly_times
        )

        if missing_hours:

            missing_hour_days.append(
                (
                    business_date,
                    missing_hours,
                )
            )

        # ----------------------------------------------------
        # Tmax
        # ----------------------------------------------------

        max_temp = max(
            item["temp"]
            for item in items
        )

        max_items = [
            item
            for item in items
            if item["temp"] == max_temp
        ]

        max_items.sort(
            key=lambda item: item["bjt"]
        )

        first_max = max_items[0]
        last_max = max_items[-1]

        if len(max_items) > 1:

            tmax_multi_time_days.append(
                (
                    business_date,
                    max_temp,
                    len(max_items),
                )
            )

        cor_support = [
            item
            for item in max_items
            if (
                item["row"]["message_class"]
                == "COR"
                or
                int(
                    item["row"]["is_correction"]
                ) == 1
            )
        ]

        recovery_support = [
            item
            for item in max_items
            if (
                item["row"][
                    "recovery_reason"
                ]
                is not None
            )
        ]

        nonhour_support = [
            item
            for item in max_items
            if (
                item["bjt"].minute != 0
                or
                item["bjt"].second != 0
            )
        ]

        if cor_support:

            tmax_from_cor_days.append(
                (
                    business_date,
                    max_temp,
                    cor_support,
                )
            )

        if recovery_support:

            tmax_from_recovery_days.append(
                (
                    business_date,
                    max_temp,
                    recovery_support,
                )
            )

        if nonhour_support:

            tmax_from_nonhour_days.append(
                (
                    business_date,
                    max_temp,
                    nonhour_support,
                )
            )

        daily_results.append(
            {
                "date": business_date,
                "count": len(items),
                "tmax": max_temp,
                "first_tmax_bjt":
                    first_max["bjt"],
                "last_tmax_bjt":
                    last_max["bjt"],
                "tmax_occurrences":
                    len(max_items),
            }
        )

    # ========================================================
    # Boundary details
    # ========================================================

    print()
    print("=" * 94)
    print("ARCHIVE BOUNDARY")
    print("=" * 94)

    print(
        f"First UTC Observation : {first_utc.isoformat()}"
    )

    print(
        f"Last UTC Observation  : {last_utc.isoformat()}"
    )

    print(
        f"First BJT Observation : {first_bjt.isoformat()}"
    )

    print(
        f"Last BJT Observation  : {last_bjt.isoformat()}"
    )

    print(
        f"First BJT Date        : {first_business_date}"
    )

    print(
        f"Last BJT Date         : {last_business_date}"
    )

    print()
    print(
        f"Complete BJT Days     : {len(complete_dates)}"
    )

    print(
        f"Partial Boundary Days : {len(partial_dates)}"
    )

    for d in partial_dates:

        count = len(
            by_business_date.get(
                d,
                [],
            )
        )

        print(
            f"    {d} : {count} observations"
        )

    # ========================================================
    # Observation distribution
    # ========================================================

    print()
    print("=" * 94)
    print("OBSERVATIONS PER COMPLETE BJT DAY")
    print("=" * 94)

    for count, days in sorted(
        observation_count_distribution.items()
    ):

        print(
            f"{count:>3} observations : {days} days"
        )

    print()
    print(
        "Complete Days With Count != 24 : "
        f"{len(abnormal_observation_count_days)}"
    )

    for d, count in (
        abnormal_observation_count_days[:20]
    ):

        print(
            f"    {d} : {count}"
        )

    # ========================================================
    # Missing hourly slots
    # ========================================================

    print()
    print("=" * 94)
    print("HOURLY COVERAGE")
    print("=" * 94)

    print(
        "Complete Days With Missing Hour Slots : "
        f"{len(missing_hour_days)}"
    )

    for d, missing in (
        missing_hour_days[:20]
    ):

        formatted = ", ".join(
            item.strftime(
                "%Y-%m-%d %H:%M BJT"
            )
            for item in missing
        )

        print(
            f"    {d} : {formatted}"
        )

    # ========================================================
    # Tmax provenance
    # ========================================================

    print()
    print("=" * 94)
    print("TMAX PROVENANCE")
    print("=" * 94)

    print(
        "Tmax Supported By COR Days      : "
        f"{len(tmax_from_cor_days)}"
    )

    for (
        d,
        temp,
        support,
    ) in tmax_from_cor_days:

        print(
            f"    {d} | Tmax={temp:g}C"
        )

        for item in support:

            print(
                "        "
                f"{item['bjt'].isoformat()} | "
                f"{item['row']['message_class']} | "
                f"Bronze ID="
                f"{item['row']['bronze_raw_id']}"
            )

    print()
    print(
        "Tmax Supported By Recovery Days : "
        f"{len(tmax_from_recovery_days)}"
    )

    for (
        d,
        temp,
        support,
    ) in tmax_from_recovery_days:

        print(
            f"    {d} | Tmax={temp:g}C"
        )

        for item in support:

            print(
                "        "
                f"{item['bjt'].isoformat()} | "
                f"{item['row']['source']} | "
                f"{item['row']['recovery_reason']}"
            )

    print()
    print(
        "Tmax Supported By Non-hour Days : "
        f"{len(tmax_from_nonhour_days)}"
    )

    for (
        d,
        temp,
        support,
    ) in tmax_from_nonhour_days:

        print(
            f"    {d} | Tmax={temp:g}C"
        )

        for item in support:

            print(
                "        "
                f"{item['bjt'].isoformat()} | "
                f"{item['row']['message_class']}"
            )

    print()
    print(
        "Days Tmax Occurs >1 Observation : "
        f"{len(tmax_multi_time_days)}"
    )

    # ========================================================
    # Tmax range / timing
    # ========================================================

    tmax_values = [
        item["tmax"]
        for item in daily_results
    ]

    print()
    print("=" * 94)
    print("DAILY TMAX SUMMARY")
    print("=" * 94)

    if daily_results:

        print(
            f"Ground Truth Candidate Days : {len(daily_results)}"
        )

        print(
            f"Minimum Daily Tmax          : {min(tmax_values):g} C"
        )

        print(
            f"Maximum Daily Tmax          : {max(tmax_values):g} C"
        )

        min_day = min(
            daily_results,
            key=lambda item: item["tmax"],
        )

        max_day = max(
            daily_results,
            key=lambda item: item["tmax"],
        )

        print(
            "Minimum Tmax Day            : "
            f"{min_day['date']} "
            f"({min_day['tmax']:g} C)"
        )

        print(
            "Maximum Tmax Day            : "
            f"{max_day['date']} "
            f"({max_day['tmax']:g} C)"
        )

    # ========================================================
    # Hard errors / review
    # ========================================================

    hard_errors = []

    review_items = []

    if not complete_dates:

        hard_errors.append(
            "NO_COMPLETE_BJT_DAYS"
        )

    if missing_hour_days:

        hard_errors.append(
            "MISSING_HOURLY_SLOTS_IN_COMPLETE_BJT_DAY"
        )

    # Count !=24 is not automatically an error:
    # a valid non-hour observation can make a day have >24 rows.
    for d, count in (
        abnormal_observation_count_days
    ):

        if count < 24:

            hard_errors.append(
                f"DAY_WITH_LT24_OBSERVATIONS:{d}"
            )

        elif count > 24:

            review_items.append(
                f"DAY_WITH_GT24_OBSERVATIONS:{d}:{count}"
            )

    if tmax_from_cor_days:

        review_items.append(
            "TMAX_HAS_COR_SUPPORT"
        )

    if tmax_from_recovery_days:

        review_items.append(
            "TMAX_HAS_RECOVERY_SUPPORT"
        )

    if tmax_from_nonhour_days:

        review_items.append(
            "TMAX_HAS_NONHOUR_SUPPORT"
        )

    hard_errors = list(
        dict.fromkeys(
            hard_errors
        )
    )

    review_items = list(
        dict.fromkeys(
            review_items
        )
    )

    # ========================================================
    # Final report
    # ========================================================

    print()
    print("=" * 94)
    print("FINAL REPORT")
    print("=" * 94)

    print(
        f"Silver Rows                    : {len(rows)}"
    )

    print(
        f"BJT Dates Present              : {len(by_business_date)}"
    )

    print(
        f"Complete BJT Days              : {len(complete_dates)}"
    )

    print(
        f"Partial Boundary Days          : {len(partial_dates)}"
    )

    print(
        "Complete Days Missing Hours    : "
        f"{len(missing_hour_days)}"
    )

    print(
        "Complete Days Count !=24       : "
        f"{len(abnormal_observation_count_days)}"
    )

    print(
        "Tmax Supported By COR Days     : "
        f"{len(tmax_from_cor_days)}"
    )

    print(
        "Tmax Supported By Recovery Days: "
        f"{len(tmax_from_recovery_days)}"
    )

    print(
        "Tmax Supported By Non-hour Days: "
        f"{len(tmax_from_nonhour_days)}"
    )

    print(
        f"Review Items                   : {len(review_items)}"
    )

    print(
        f"Hard Errors                    : {len(hard_errors)}"
    )

    if review_items:

        print()
        print("Review Items:")

        for item in review_items:

            print(
                f"    {item}"
            )

    if hard_errors:

        print()
        print("Hard Errors:")

        for item in hard_errors:

            print(
                f"    {item}"
            )

    print("=" * 94)

    if hard_errors:

        print(
            "RESULT: DAILY GROUND TRUTH AUDIT REVIEW REQUIRED"
        )

        return 2

    print(
        "RESULT: DAILY GROUND TRUTH AUDIT COMPLETE"
    )

    print(
        "NOTE: Review items must be resolved before TARGET_V1 freeze."
    )

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )