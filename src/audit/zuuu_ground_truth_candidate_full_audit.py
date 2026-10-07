"""
ZUUU Ground Truth Candidate Full Audit V1.0
===========================================

Purpose:
    Independently audit the 729-day Ground Truth Candidate dataset
    against Silver and Bronze archives before TARGET_V1 freeze.

Principles:
    - READ ONLY
    - No database writes
    - Candidate calculations are independently recomputed from Silver
    - Every Candidate must preserve valid Silver/Bronze lineage
    - Business timezone = Asia/Shanghai
    - Business day = [00:00, next 00:00)
    - All valid observations participate in Daily Tmax

Expected Candidate Range:
    2024-09-03 -> 2026-09-01
    729 complete BJT business days
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH
from src.parsers.zuuu_time_normalizer import BJT


UTC = timezone.utc

EXPECTED_START_DATE = date(2024, 9, 3)
EXPECTED_END_DATE = date(2026, 9, 1)
EXPECTED_DAYS = 729

EXPECTED_TARGET_STATUS = "CANDIDATE"
EXPECTED_TARGET_VERSION = "DAILY_TMAX_RULE_V1"

# These thresholds create REVIEW items only.
# They do NOT automatically mean the target is wrong.
DAILY_JUMP_REVIEW_THRESHOLD_C = 8.0
EXTREME_HIGH_REVIEW_C = 40.0
EXTREME_LOW_REVIEW_C = 7.0

# Very early / late first Tmax occurrence.
EARLY_TMAX_HOUR = 8
LATE_TMAX_HOUR = 20


def parse_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        raise ValueError(
            f"NAIVE_DATETIME:{value}"
        )

    return dt


def parse_json_id_list(
    value: str,
) -> list[int]:
    result = json.loads(value)

    if not isinstance(result, list):
        raise ValueError(
            "LINEAGE_JSON_NOT_LIST"
        )

    return [
        int(item)
        for item in result
    ]


def date_range(
    start_date: date,
    end_date: date,
):
    current = start_date

    while current <= end_date:
        yield current
        current += timedelta(days=1)


def main() -> int:
    print("=" * 100)
    print(
        "ZUUU Ground Truth Candidate Full Audit V1.0"
    )
    print("=" * 100)

    print(
        f"Database : {DEFAULT_DB_PATH}"
    )

    print("=" * 100)

    conn = sqlite3.connect(
        DEFAULT_DB_PATH
    )

    conn.row_factory = sqlite3.Row

    try:
        bronze_rows = conn.execute(
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
            ORDER BY id
            """
        ).fetchall()

        silver_rows = conn.execute(
            """
            SELECT
                id,
                bronze_raw_id,
                station,
                observation_time_utc,
                observation_time_bjt,
                business_date_bjt,
                temperature_c,
                dewpoint_c,
                source,
                source_query_class,
                message_class,
                is_correction,
                supersedes_raw_id,
                recovery_reason,
                qc_status,
                qc_flags
            FROM zuuu_silver_observation
            ORDER BY
                observation_time_bjt,
                id
            """
        ).fetchall()

        candidate_rows = conn.execute(
            """
            SELECT
                id,
                business_date_bjt,
                daily_tmax_c,
                first_tmax_time_bjt,
                last_tmax_time_bjt,
                tmax_occurrence_count,
                observation_count,
                hourly_coverage_count,
                has_correction,
                tmax_has_correction,
                has_recovery,
                tmax_has_recovery,
                tmax_silver_ids,
                tmax_bronze_raw_ids,
                all_silver_ids,
                all_bronze_raw_ids,
                target_status,
                target_version,
                created_at_utc
            FROM zuuu_ground_truth_candidate
            ORDER BY business_date_bjt
            """
        ).fetchall()

    finally:
        conn.close()

    print(
        f"Bronze Rows    : {len(bronze_rows)}"
    )

    print(
        f"Silver Rows    : {len(silver_rows)}"
    )

    print(
        f"Candidate Rows : {len(candidate_rows)}"
    )

    hard_errors = []
    review_items = []
    issue_examples = []

    counters = Counter()

    # ========================================================
    # Indexes
    # ========================================================

    bronze_by_id = {
        int(row["id"]): row
        for row in bronze_rows
    }

    silver_by_id = {
        int(row["id"]): row
        for row in silver_rows
    }

    silver_by_date = defaultdict(list)

    for row in silver_rows:
        business_date = date.fromisoformat(
            row["business_date_bjt"]
        )

        silver_by_date[
            business_date
        ].append(
            row
        )

    candidate_by_date = {}

    duplicate_candidate_dates = []

    for row in candidate_rows:
        d = date.fromisoformat(
            row["business_date_bjt"]
        )

        if d in candidate_by_date:
            duplicate_candidate_dates.append(
                d
            )

        candidate_by_date[d] = row

    # ========================================================
    # Structural checks
    # ========================================================

    expected_dates = list(
        date_range(
            EXPECTED_START_DATE,
            EXPECTED_END_DATE,
        )
    )

    expected_date_set = set(
        expected_dates
    )

    actual_date_set = set(
        candidate_by_date
    )

    missing_candidate_dates = sorted(
        expected_date_set
        - actual_date_set
    )

    extra_candidate_dates = sorted(
        actual_date_set
        - expected_date_set
    )

    if len(candidate_rows) != EXPECTED_DAYS:
        hard_errors.append(
            "UNEXPECTED_CANDIDATE_COUNT"
        )

    if duplicate_candidate_dates:
        hard_errors.append(
            "DUPLICATE_CANDIDATE_DATE"
        )

    if missing_candidate_dates:
        hard_errors.append(
            "MISSING_CANDIDATE_DATES"
        )

    if extra_candidate_dates:
        hard_errors.append(
            "EXTRA_CANDIDATE_DATES"
        )

    # ========================================================
    # Independent daily recomputation
    # ========================================================

    daily_summary = []

    for business_date in expected_dates:
        candidate = candidate_by_date.get(
            business_date
        )

        if candidate is None:
            continue

        counters[
            "CANDIDATES_CHECKED"
        ] += 1

        silver_day_rows = list(
            silver_by_date.get(
                business_date,
                []
            )
        )

        if not silver_day_rows:
            hard_errors.append(
                f"NO_SILVER_ROWS:{business_date}"
            )
            continue

        # ----------------------------------------------------
        # Required Candidate fields
        # ----------------------------------------------------

        required_fields = [
            "business_date_bjt",
            "daily_tmax_c",
            "first_tmax_time_bjt",
            "last_tmax_time_bjt",
            "tmax_occurrence_count",
            "observation_count",
            "hourly_coverage_count",
            "has_correction",
            "tmax_has_correction",
            "has_recovery",
            "tmax_has_recovery",
            "tmax_silver_ids",
            "tmax_bronze_raw_ids",
            "all_silver_ids",
            "all_bronze_raw_ids",
            "target_status",
            "target_version",
            "created_at_utc",
        ]

        for field in required_fields:
            if candidate[field] is None:
                counters[
                    "NULL_REQUIRED_FIELD"
                ] += 1

                if len(issue_examples) < 30:
                    issue_examples.append(
                        f"{business_date}: "
                        f"NULL_FIELD:{field}"
                    )

        # ----------------------------------------------------
        # Candidate metadata
        # ----------------------------------------------------

        if (
            candidate["target_status"]
            != EXPECTED_TARGET_STATUS
        ):
            counters[
                "TARGET_STATUS_MISMATCH"
            ] += 1

        if (
            candidate["target_version"]
            != EXPECTED_TARGET_VERSION
        ):
            counters[
                "TARGET_VERSION_MISMATCH"
            ] += 1

        # ----------------------------------------------------
        # Independently parse Silver rows
        # ----------------------------------------------------

        observations = []

        for row in silver_day_rows:
            try:
                bjt_dt = parse_datetime(
                    row[
                        "observation_time_bjt"
                    ]
                ).astimezone(BJT)

            except Exception as exc:
                counters[
                    "SILVER_TIME_PARSE_ERROR"
                ] += 1

                if len(issue_examples) < 30:
                    issue_examples.append(
                        f"{business_date}: "
                        f"Silver ID {row['id']} "
                        f"time parse error: {exc}"
                    )

                continue

            # Strict business-day boundary check.
            if bjt_dt.date() != business_date:
                counters[
                    "SILVER_DAY_BOUNDARY_ERROR"
                ] += 1

            observations.append(
                {
                    "row": row,
                    "bjt": bjt_dt,
                    "temp": float(
                        row["temperature_c"]
                    ),
                }
            )

        if not observations:
            hard_errors.append(
                f"NO_VALID_OBSERVATIONS:{business_date}"
            )
            continue

        observations.sort(
            key=lambda item: (
                item["bjt"],
                int(item["row"]["id"]),
            )
        )

        # ----------------------------------------------------
        # Independent hourly coverage
        # ----------------------------------------------------

        day_start = datetime(
            business_date.year,
            business_date.month,
            business_date.day,
            0,
            0,
            0,
            tzinfo=BJT,
        )

        expected_hours = {
            day_start
            + timedelta(hours=i)
            for i in range(24)
        }

        actual_hours = {
            item["bjt"]
            .replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            for item in observations
            if (
                item["bjt"].minute == 0
                and
                item["bjt"].second == 0
            )
        }

        recomputed_hourly_coverage = len(
            expected_hours
            & actual_hours
        )

        # ----------------------------------------------------
        # Independent Tmax
        # ----------------------------------------------------

        recomputed_tmax = max(
            item["temp"]
            for item in observations
        )

        recomputed_tmax_rows = [
            item
            for item in observations
            if (
                item["temp"]
                == recomputed_tmax
            )
        ]

        recomputed_tmax_rows.sort(
            key=lambda item: (
                item["bjt"],
                int(item["row"]["id"]),
            )
        )

        recomputed_first_tmax = (
            recomputed_tmax_rows[0]["bjt"]
        )

        recomputed_last_tmax = (
            recomputed_tmax_rows[-1]["bjt"]
        )

        recomputed_occurrences = len(
            recomputed_tmax_rows
        )

        # ----------------------------------------------------
        # Independent flags
        # ----------------------------------------------------

        recomputed_has_correction = int(
            any(
                int(
                    item["row"][
                        "is_correction"
                    ]
                ) == 1
                or
                item["row"][
                    "message_class"
                ] == "COR"
                for item in observations
            )
        )

        recomputed_tmax_has_correction = int(
            any(
                int(
                    item["row"][
                        "is_correction"
                    ]
                ) == 1
                or
                item["row"][
                    "message_class"
                ] == "COR"
                for item
                in recomputed_tmax_rows
            )
        )

        recomputed_has_recovery = int(
            any(
                item["row"][
                    "recovery_reason"
                ] is not None
                for item in observations
            )
        )

        recomputed_tmax_has_recovery = int(
            any(
                item["row"][
                    "recovery_reason"
                ] is not None
                for item
                in recomputed_tmax_rows
            )
        )

        # ----------------------------------------------------
        # Independent lineage
        # ----------------------------------------------------

        expected_all_silver_ids = [
            int(
                item["row"]["id"]
            )
            for item in observations
        ]

        expected_all_bronze_ids = [
            int(
                item["row"][
                    "bronze_raw_id"
                ]
            )
            for item in observations
        ]

        expected_tmax_silver_ids = [
            int(
                item["row"]["id"]
            )
            for item
            in recomputed_tmax_rows
        ]

        expected_tmax_bronze_ids = [
            int(
                item["row"][
                    "bronze_raw_id"
                ]
            )
            for item
            in recomputed_tmax_rows
        ]

        # ----------------------------------------------------
        # Compare Candidate vs recomputation
        # ----------------------------------------------------

        if (
            float(
                candidate["daily_tmax_c"]
            )
            != recomputed_tmax
        ):
            counters[
                "TMAX_MISMATCH"
            ] += 1

        try:
            candidate_first_tmax = (
                parse_datetime(
                    candidate[
                        "first_tmax_time_bjt"
                    ]
                ).astimezone(BJT)
            )

            if (
                candidate_first_tmax
                != recomputed_first_tmax
            ):
                counters[
                    "FIRST_TMAX_TIME_MISMATCH"
                ] += 1

        except Exception:
            counters[
                "FIRST_TMAX_TIME_PARSE_ERROR"
            ] += 1

        try:
            candidate_last_tmax = (
                parse_datetime(
                    candidate[
                        "last_tmax_time_bjt"
                    ]
                ).astimezone(BJT)
            )

            if (
                candidate_last_tmax
                != recomputed_last_tmax
            ):
                counters[
                    "LAST_TMAX_TIME_MISMATCH"
                ] += 1

        except Exception:
            counters[
                "LAST_TMAX_TIME_PARSE_ERROR"
            ] += 1

        if (
            int(
                candidate[
                    "tmax_occurrence_count"
                ]
            )
            != recomputed_occurrences
        ):
            counters[
                "TMAX_OCCURRENCE_MISMATCH"
            ] += 1

        if (
            int(
                candidate[
                    "observation_count"
                ]
            )
            != len(observations)
        ):
            counters[
                "OBSERVATION_COUNT_MISMATCH"
            ] += 1

        if (
            int(
                candidate[
                    "hourly_coverage_count"
                ]
            )
            != recomputed_hourly_coverage
        ):
            counters[
                "HOURLY_COVERAGE_MISMATCH"
            ] += 1

        if recomputed_hourly_coverage != 24:
            counters[
                "INCOMPLETE_HOURLY_COVERAGE"
            ] += 1

        if (
            int(candidate["has_correction"])
            != recomputed_has_correction
        ):
            counters[
                "HAS_CORRECTION_MISMATCH"
            ] += 1

        if (
            int(
                candidate[
                    "tmax_has_correction"
                ]
            )
            != recomputed_tmax_has_correction
        ):
            counters[
                "TMAX_CORRECTION_MISMATCH"
            ] += 1

        if (
            int(candidate["has_recovery"])
            != recomputed_has_recovery
        ):
            counters[
                "HAS_RECOVERY_MISMATCH"
            ] += 1

        if (
            int(
                candidate[
                    "tmax_has_recovery"
                ]
            )
            != recomputed_tmax_has_recovery
        ):
            counters[
                "TMAX_RECOVERY_MISMATCH"
            ] += 1

        # ----------------------------------------------------
        # Parse stored lineage
        # ----------------------------------------------------

        try:
            stored_all_silver_ids = (
                parse_json_id_list(
                    candidate[
                        "all_silver_ids"
                    ]
                )
            )

            stored_all_bronze_ids = (
                parse_json_id_list(
                    candidate[
                        "all_bronze_raw_ids"
                    ]
                )
            )

            stored_tmax_silver_ids = (
                parse_json_id_list(
                    candidate[
                        "tmax_silver_ids"
                    ]
                )
            )

            stored_tmax_bronze_ids = (
                parse_json_id_list(
                    candidate[
                        "tmax_bronze_raw_ids"
                    ]
                )
            )

        except Exception as exc:
            counters[
                "LINEAGE_JSON_PARSE_ERROR"
            ] += 1

            if len(issue_examples) < 30:
                issue_examples.append(
                    f"{business_date}: "
                    f"LINEAGE_JSON_PARSE_ERROR:"
                    f"{exc}"
                )

            continue

        # ----------------------------------------------------
        # Compare stored lineage exactly
        # ----------------------------------------------------

        if (
            stored_all_silver_ids
            != expected_all_silver_ids
        ):
            counters[
                "ALL_SILVER_LINEAGE_MISMATCH"
            ] += 1

        if (
            stored_all_bronze_ids
            != expected_all_bronze_ids
        ):
            counters[
                "ALL_BRONZE_LINEAGE_MISMATCH"
            ] += 1

        if (
            stored_tmax_silver_ids
            != expected_tmax_silver_ids
        ):
            counters[
                "TMAX_SILVER_LINEAGE_MISMATCH"
            ] += 1

        if (
            stored_tmax_bronze_ids
            != expected_tmax_bronze_ids
        ):
            counters[
                "TMAX_BRONZE_LINEAGE_MISMATCH"
            ] += 1

        # ----------------------------------------------------
        # Verify every referenced Silver exists
        # ----------------------------------------------------

        for silver_id in stored_all_silver_ids:
            row = silver_by_id.get(
                silver_id
            )

            if row is None:
                counters[
                    "MISSING_REFERENCED_SILVER"
                ] += 1
                continue

            if (
                row["business_date_bjt"]
                != business_date.isoformat()
            ):
                counters[
                    "CROSS_DAY_SILVER_LINEAGE"
                ] += 1

        # ----------------------------------------------------
        # Verify every referenced Bronze exists
        # ----------------------------------------------------

        for bronze_id in stored_all_bronze_ids:
            if bronze_id not in bronze_by_id:
                counters[
                    "MISSING_REFERENCED_BRONZE"
                ] += 1

        # ----------------------------------------------------
        # Verify Tmax Silver IDs actually have Tmax
        # ----------------------------------------------------

        for silver_id in stored_tmax_silver_ids:
            row = silver_by_id.get(
                silver_id
            )

            if row is None:
                continue

            if (
                float(
                    row["temperature_c"]
                )
                != recomputed_tmax
            ):
                counters[
                    "TMAX_LINEAGE_NOT_TMAX"
                ] += 1

            if (
                row["business_date_bjt"]
                != business_date.isoformat()
            ):
                counters[
                    "TMAX_LINEAGE_CROSS_DAY"
                ] += 1

        # ----------------------------------------------------
        # Review / anomaly information
        # ----------------------------------------------------

        if len(observations) > 24:
            review_items.append(
                f"GT24_OBSERVATIONS:"
                f"{business_date}:"
                f"{len(observations)}"
            )

        if recomputed_tmax_has_correction:
            review_items.append(
                f"TMAX_HAS_CORRECTION:"
                f"{business_date}:"
                f"{recomputed_tmax:g}C"
            )

        if recomputed_tmax_has_recovery:
            review_items.append(
                f"TMAX_HAS_RECOVERY:"
                f"{business_date}:"
                f"{recomputed_tmax:g}C"
            )

        if recomputed_tmax >= (
            EXTREME_HIGH_REVIEW_C
        ):
            review_items.append(
                f"EXTREME_HIGH_TMAX:"
                f"{business_date}:"
                f"{recomputed_tmax:g}C"
            )

        if recomputed_tmax <= (
            EXTREME_LOW_REVIEW_C
        ):
            review_items.append(
                f"EXTREME_LOW_TMAX:"
                f"{business_date}:"
                f"{recomputed_tmax:g}C"
            )

        first_hour = (
            recomputed_first_tmax.hour
        )

        if first_hour <= EARLY_TMAX_HOUR:
            review_items.append(
                f"EARLY_TMAX:"
                f"{business_date}:"
                f"{recomputed_first_tmax.isoformat()}:"
                f"{recomputed_tmax:g}C"
            )

        if first_hour >= LATE_TMAX_HOUR:
            review_items.append(
                f"LATE_TMAX:"
                f"{business_date}:"
                f"{recomputed_first_tmax.isoformat()}:"
                f"{recomputed_tmax:g}C"
            )

        daily_summary.append(
            {
                "date":
                    business_date,
                "tmax":
                    recomputed_tmax,
                "first":
                    recomputed_first_tmax,
                "last":
                    recomputed_last_tmax,
                "occurrences":
                    recomputed_occurrences,
                "observations":
                    len(observations),
            }
        )

    # ========================================================
    # Daily continuity / jump audit
    # ========================================================

    daily_summary.sort(
        key=lambda item: item["date"]
    )

    daily_jumps = []

    for previous, current in zip(
        daily_summary,
        daily_summary[1:],
    ):
        expected_next_date = (
            previous["date"]
            + timedelta(days=1)
        )

        if current["date"] != expected_next_date:
            counters[
                "DAILY_SEQUENCE_GAP"
            ] += 1

        jump = (
            current["tmax"]
            - previous["tmax"]
        )

        if abs(jump) >= (
            DAILY_JUMP_REVIEW_THRESHOLD_C
        ):
            daily_jumps.append(
                (
                    previous["date"],
                    previous["tmax"],
                    current["date"],
                    current["tmax"],
                    jump,
                )
            )

            review_items.append(
                f"LARGE_DAILY_TMAX_JUMP:"
                f"{previous['date']}:"
                f"{previous['tmax']:g}C->"
                f"{current['date']}:"
                f"{current['tmax']:g}C:"
                f"{jump:+g}C"
            )

    # ========================================================
    # Determine extrema
    # ========================================================

    minimum_record = None
    maximum_record = None

    if daily_summary:
        minimum_record = min(
            daily_summary,
            key=lambda item: item["tmax"],
        )

        maximum_record = max(
            daily_summary,
            key=lambda item: item["tmax"],
        )

    # ========================================================
    # Hard-error counters
    # ========================================================

    hard_counter_names = [
        "NULL_REQUIRED_FIELD",
        "TARGET_STATUS_MISMATCH",
        "TARGET_VERSION_MISMATCH",
        "SILVER_TIME_PARSE_ERROR",
        "SILVER_DAY_BOUNDARY_ERROR",
        "TMAX_MISMATCH",
        "FIRST_TMAX_TIME_MISMATCH",
        "FIRST_TMAX_TIME_PARSE_ERROR",
        "LAST_TMAX_TIME_MISMATCH",
        "LAST_TMAX_TIME_PARSE_ERROR",
        "TMAX_OCCURRENCE_MISMATCH",
        "OBSERVATION_COUNT_MISMATCH",
        "HOURLY_COVERAGE_MISMATCH",
        "INCOMPLETE_HOURLY_COVERAGE",
        "HAS_CORRECTION_MISMATCH",
        "TMAX_CORRECTION_MISMATCH",
        "HAS_RECOVERY_MISMATCH",
        "TMAX_RECOVERY_MISMATCH",
        "LINEAGE_JSON_PARSE_ERROR",
        "ALL_SILVER_LINEAGE_MISMATCH",
        "ALL_BRONZE_LINEAGE_MISMATCH",
        "TMAX_SILVER_LINEAGE_MISMATCH",
        "TMAX_BRONZE_LINEAGE_MISMATCH",
        "MISSING_REFERENCED_SILVER",
        "MISSING_REFERENCED_BRONZE",
        "CROSS_DAY_SILVER_LINEAGE",
        "TMAX_LINEAGE_NOT_TMAX",
        "TMAX_LINEAGE_CROSS_DAY",
        "DAILY_SEQUENCE_GAP",
    ]

    for key in hard_counter_names:
        if counters[key] != 0:
            hard_errors.append(
                f"{key}:{counters[key]}"
            )

    # ========================================================
    # Output: Structure
    # ========================================================

    print()
    print("=" * 100)
    print("STRUCTURE")
    print("=" * 100)

    print(
        f"Expected Candidate Days : {EXPECTED_DAYS}"
    )

    print(
        f"Actual Candidate Rows   : {len(candidate_rows)}"
    )

    print(
        f"Candidates Checked      : "
        f"{counters['CANDIDATES_CHECKED']}"
    )

    print(
        f"Missing Dates           : "
        f"{len(missing_candidate_dates)}"
    )

    print(
        f"Extra Dates             : "
        f"{len(extra_candidate_dates)}"
    )

    print(
        f"Duplicate Dates         : "
        f"{len(duplicate_candidate_dates)}"
    )

    # ========================================================
    # Output: Independent recomputation
    # ========================================================

    print()
    print("=" * 100)
    print("INDEPENDENT RECOMPUTATION")
    print("=" * 100)

    print(
        f"Tmax Mismatch               : "
        f"{counters['TMAX_MISMATCH']}"
    )

    print(
        f"First Tmax Time Mismatch    : "
        f"{counters['FIRST_TMAX_TIME_MISMATCH']}"
    )

    print(
        f"Last Tmax Time Mismatch     : "
        f"{counters['LAST_TMAX_TIME_MISMATCH']}"
    )

    print(
        f"Tmax Occurrence Mismatch    : "
        f"{counters['TMAX_OCCURRENCE_MISMATCH']}"
    )

    print(
        f"Observation Count Mismatch  : "
        f"{counters['OBSERVATION_COUNT_MISMATCH']}"
    )

    print(
        f"Hourly Coverage Mismatch    : "
        f"{counters['HOURLY_COVERAGE_MISMATCH']}"
    )

    print(
        f"Incomplete Hourly Days      : "
        f"{counters['INCOMPLETE_HOURLY_COVERAGE']}"
    )

    # ========================================================
    # Output: Lineage
    # ========================================================

    print()
    print("=" * 100)
    print("LINEAGE")
    print("=" * 100)

    print(
        f"All Silver Lineage Mismatch : "
        f"{counters['ALL_SILVER_LINEAGE_MISMATCH']}"
    )

    print(
        f"All Bronze Lineage Mismatch : "
        f"{counters['ALL_BRONZE_LINEAGE_MISMATCH']}"
    )

    print(
        f"Tmax Silver Lineage Mismatch: "
        f"{counters['TMAX_SILVER_LINEAGE_MISMATCH']}"
    )

    print(
        f"Tmax Bronze Lineage Mismatch: "
        f"{counters['TMAX_BRONZE_LINEAGE_MISMATCH']}"
    )

    print(
        f"Missing Referenced Silver    : "
        f"{counters['MISSING_REFERENCED_SILVER']}"
    )

    print(
        f"Missing Referenced Bronze    : "
        f"{counters['MISSING_REFERENCED_BRONZE']}"
    )

    print(
        f"Cross-Day Silver Lineage     : "
        f"{counters['CROSS_DAY_SILVER_LINEAGE']}"
    )

    print(
        f"Tmax Lineage Not Tmax        : "
        f"{counters['TMAX_LINEAGE_NOT_TMAX']}"
    )

    # ========================================================
    # Output: Extrema
    # ========================================================

    print()
    print("=" * 100)
    print("EXTREMA")
    print("=" * 100)

    if minimum_record:
        print(
            f"Minimum Daily Tmax : "
            f"{minimum_record['date']} | "
            f"{minimum_record['tmax']:g} C | "
            f"First={minimum_record['first'].isoformat()} | "
            f"Occurrences={minimum_record['occurrences']}"
        )

    if maximum_record:
        print(
            f"Maximum Daily Tmax : "
            f"{maximum_record['date']} | "
            f"{maximum_record['tmax']:g} C | "
            f"First={maximum_record['first'].isoformat()} | "
            f"Occurrences={maximum_record['occurrences']}"
        )

    # ========================================================
    # Output: Large jumps
    # ========================================================

    print()
    print("=" * 100)
    print(
        f"DAILY TMAX JUMPS >= "
        f"{DAILY_JUMP_REVIEW_THRESHOLD_C:g} C"
    )
    print("=" * 100)

    print(
        f"Large Jump Count : {len(daily_jumps)}"
    )

    for (
        prev_date,
        prev_temp,
        curr_date,
        curr_temp,
        jump,
    ) in daily_jumps:
        print(
            f"    {prev_date} "
            f"{prev_temp:g}C -> "
            f"{curr_date} "
            f"{curr_temp:g}C "
            f"({jump:+g}C)"
        )

    # ========================================================
    # Known review dates
    # ========================================================

    print()
    print("=" * 100)
    print("KNOWN REVIEW DATES")
    print("=" * 100)

    for review_date in [
        date(2025, 1, 5),
        date(2025, 7, 17),
        date(2025, 9, 24),
        date(2024, 12, 23),
        date(2026, 7, 25),
    ]:
        row = candidate_by_date.get(
            review_date
        )

        if row is None:
            print(
                f"{review_date} : MISSING"
            )
            continue

        print(
            f"{review_date} | "
            f"Tmax={float(row['daily_tmax_c']):g}C | "
            f"Obs={row['observation_count']} | "
            f"COR={row['has_correction']} | "
            f"TmaxCOR={row['tmax_has_correction']} | "
            f"Recovery={row['has_recovery']} | "
            f"TmaxRecovery={row['tmax_has_recovery']} | "
            f"First={row['first_tmax_time_bjt']} | "
            f"Last={row['last_tmax_time_bjt']} | "
            f"Occurrences={row['tmax_occurrence_count']}"
        )

    # ========================================================
    # Examples
    # ========================================================

    if issue_examples:
        print()
        print("=" * 100)
        print("ISSUE EXAMPLES")
        print("=" * 100)

        for item in issue_examples:
            print(
                f"    {item}"
            )

    # ========================================================
    # Deduplicate review / errors
    # ========================================================

    review_items = list(
        dict.fromkeys(
            review_items
        )
    )

    hard_errors = list(
        dict.fromkeys(
            hard_errors
        )
    )

    # ========================================================
    # Final report
    # ========================================================

    print()
    print("=" * 100)
    print("FINAL REPORT")
    print("=" * 100)

    print(
        f"Bronze Rows               : "
        f"{len(bronze_rows)}"
    )

    print(
        f"Silver Rows               : "
        f"{len(silver_rows)}"
    )

    print(
        f"Candidate Rows            : "
        f"{len(candidate_rows)}"
    )

    print(
        f"Candidates Checked        : "
        f"{counters['CANDIDATES_CHECKED']}"
    )

    print(
        f"Missing Candidate Dates   : "
        f"{len(missing_candidate_dates)}"
    )

    print(
        f"Extra Candidate Dates     : "
        f"{len(extra_candidate_dates)}"
    )

    print(
        f"Daily Sequence Gaps       : "
        f"{counters['DAILY_SEQUENCE_GAP']}"
    )

    print(
        f"Tmax Mismatches           : "
        f"{counters['TMAX_MISMATCH']}"
    )

    print(
        f"Time Mismatches           : "
        f"{counters['FIRST_TMAX_TIME_MISMATCH'] + counters['LAST_TMAX_TIME_MISMATCH']}"
    )

    print(
        f"Count Mismatches          : "
        f"{counters['TMAX_OCCURRENCE_MISMATCH'] + counters['OBSERVATION_COUNT_MISMATCH']}"
    )

    print(
        f"Coverage Mismatches       : "
        f"{counters['HOURLY_COVERAGE_MISMATCH']}"
    )

    lineage_mismatches = (
        counters[
            "ALL_SILVER_LINEAGE_MISMATCH"
        ]
        + counters[
            "ALL_BRONZE_LINEAGE_MISMATCH"
        ]
        + counters[
            "TMAX_SILVER_LINEAGE_MISMATCH"
        ]
        + counters[
            "TMAX_BRONZE_LINEAGE_MISMATCH"
        ]
    )

    print(
        f"Lineage Mismatches        : "
        f"{lineage_mismatches}"
    )

    print(
        f"Large Daily Tmax Jumps    : "
        f"{len(daily_jumps)}"
    )

    print(
        f"Review Items              : "
        f"{len(review_items)}"
    )

    print(
        f"Hard Errors               : "
        f"{len(hard_errors)}"
    )

    if hard_errors:
        print()
        print("Hard Errors:")

        for item in hard_errors:
            print(
                f"    {item}"
            )

    print("=" * 100)

    if hard_errors:
        print(
            "RESULT: GROUND TRUTH CANDIDATE FULL AUDIT REVIEW REQUIRED"
        )

        return 2

    print(
        "RESULT: GROUND TRUTH CANDIDATE FULL AUDIT PASS"
    )

    print(
        "NOTE: Review Items still require evidence review before TARGET_V1 freeze."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )