"""
ZUUU Ground Truth Candidate Builder V1.0
========================================

Purpose:
    Build daily Ground Truth CANDIDATES from the audited Silver archive.

IMPORTANT:
    - This is NOT ZUUU_TARGET_V1 yet.
    - TARGET_V1 is NOT frozen by this script.
    - Bronze and Silver are READ ONLY.
    - Business timezone is Asia/Shanghai.
    - Only complete BJT natural days are eligible.
    - Daily Tmax uses ALL valid Silver observations in the BJT day.
    - COR / Recovery observations are retained with explicit provenance.
    - Historical lineage is preserved.

Current audited candidate range:
    2024-09-03 -> 2026-09-01
    Expected complete BJT days: 729
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH
from src.parsers.zuuu_time_normalizer import BJT


UTC = timezone.utc

TABLE_NAME = "zuuu_ground_truth_candidate"

EXPECTED_START_DATE = date(2024, 9, 3)
EXPECTED_END_DATE = date(2026, 9, 1)
EXPECTED_DAYS = 729


def parse_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        raise ValueError(
            f"NAIVE_DATETIME:{value}"
        )

    return dt


def date_range(
    start_date: date,
    end_date: date,
):
    current = start_date

    while current <= end_date:
        yield current
        current += timedelta(days=1)


def connect(
    db_path: Path | str = DEFAULT_DB_PATH,
) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_candidate_table(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            business_date_bjt TEXT NOT NULL UNIQUE,

            daily_tmax_c REAL NOT NULL,

            first_tmax_time_bjt TEXT NOT NULL,
            last_tmax_time_bjt TEXT NOT NULL,
            tmax_occurrence_count INTEGER NOT NULL,

            observation_count INTEGER NOT NULL,
            hourly_coverage_count INTEGER NOT NULL,

            has_correction INTEGER NOT NULL
                CHECK (has_correction IN (0, 1)),

            tmax_has_correction INTEGER NOT NULL
                CHECK (tmax_has_correction IN (0, 1)),

            has_recovery INTEGER NOT NULL
                CHECK (has_recovery IN (0, 1)),

            tmax_has_recovery INTEGER NOT NULL
                CHECK (tmax_has_recovery IN (0, 1)),

            tmax_silver_ids TEXT NOT NULL,
            tmax_bronze_raw_ids TEXT NOT NULL,

            all_silver_ids TEXT NOT NULL,
            all_bronze_raw_ids TEXT NOT NULL,

            target_status TEXT NOT NULL,
            target_version TEXT NOT NULL,

            created_at_utc TEXT NOT NULL
        )
        """
    )

    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS
        idx_{TABLE_NAME}_date
        ON {TABLE_NAME}(business_date_bjt)
        """
    )

    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS
        idx_{TABLE_NAME}_tmax
        ON {TABLE_NAME}(daily_tmax_c)
        """
    )

    conn.commit()


def read_silver_rows(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            id,
            bronze_raw_id,
            station,
            observation_time_utc,
            observation_time_bjt,
            business_date_bjt,
            temperature_c,
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


def build_candidate_for_day(
    business_date: date,
    rows: list[sqlite3.Row],
) -> dict:
    if not rows:
        raise ValueError(
            f"NO_ROWS:{business_date}"
        )

    parsed_rows = []

    for row in rows:
        bjt_dt = parse_datetime(
            row["observation_time_bjt"]
        ).astimezone(BJT)

        parsed_rows.append(
            {
                "row": row,
                "bjt": bjt_dt,
                "temp": float(
                    row["temperature_c"]
                ),
            }
        )

    parsed_rows.sort(
        key=lambda item: (
            item["bjt"],
            int(item["row"]["id"]),
        )
    )

    # --------------------------------------------------------
    # Hourly coverage
    # --------------------------------------------------------

    start_bjt = datetime(
        business_date.year,
        business_date.month,
        business_date.day,
        0,
        0,
        0,
        tzinfo=BJT,
    )

    expected_hour_slots = {
        start_bjt + timedelta(hours=i)
        for i in range(24)
    }

    actual_hour_slots = {
        item["bjt"].replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        for item in parsed_rows
        if (
            item["bjt"].minute == 0
            and
            item["bjt"].second == 0
        )
    }

    hourly_coverage_count = len(
        expected_hour_slots
        & actual_hour_slots
    )

    # --------------------------------------------------------
    # Tmax
    # --------------------------------------------------------

    daily_tmax_c = max(
        item["temp"]
        for item in parsed_rows
    )

    tmax_rows = [
        item
        for item in parsed_rows
        if item["temp"] == daily_tmax_c
    ]

    tmax_rows.sort(
        key=lambda item: (
            item["bjt"],
            int(item["row"]["id"]),
        )
    )

    first_tmax = tmax_rows[0]
    last_tmax = tmax_rows[-1]

    # --------------------------------------------------------
    # Provenance flags
    # --------------------------------------------------------

    has_correction = any(
        int(item["row"]["is_correction"]) == 1
        or item["row"]["message_class"] == "COR"
        for item in parsed_rows
    )

    tmax_has_correction = any(
        int(item["row"]["is_correction"]) == 1
        or item["row"]["message_class"] == "COR"
        for item in tmax_rows
    )

    has_recovery = any(
        item["row"]["recovery_reason"]
        is not None
        for item in parsed_rows
    )

    tmax_has_recovery = any(
        item["row"]["recovery_reason"]
        is not None
        for item in tmax_rows
    )

    # --------------------------------------------------------
    # Lineage
    # --------------------------------------------------------

    all_silver_ids = [
        int(item["row"]["id"])
        for item in parsed_rows
    ]

    all_bronze_raw_ids = [
        int(item["row"]["bronze_raw_id"])
        for item in parsed_rows
    ]

    tmax_silver_ids = [
        int(item["row"]["id"])
        for item in tmax_rows
    ]

    tmax_bronze_raw_ids = [
        int(item["row"]["bronze_raw_id"])
        for item in tmax_rows
    ]

    return {
        "business_date_bjt":
            business_date.isoformat(),

        "daily_tmax_c":
            daily_tmax_c,

        "first_tmax_time_bjt":
            first_tmax["bjt"].isoformat(),

        "last_tmax_time_bjt":
            last_tmax["bjt"].isoformat(),

        "tmax_occurrence_count":
            len(tmax_rows),

        "observation_count":
            len(parsed_rows),

        "hourly_coverage_count":
            hourly_coverage_count,

        "has_correction":
            int(has_correction),

        "tmax_has_correction":
            int(tmax_has_correction),

        "has_recovery":
            int(has_recovery),

        "tmax_has_recovery":
            int(tmax_has_recovery),

        "tmax_silver_ids":
            json.dumps(
                tmax_silver_ids,
                separators=(",", ":"),
            ),

        "tmax_bronze_raw_ids":
            json.dumps(
                tmax_bronze_raw_ids,
                separators=(",", ":"),
            ),

        "all_silver_ids":
            json.dumps(
                all_silver_ids,
                separators=(",", ":"),
            ),

        "all_bronze_raw_ids":
            json.dumps(
                all_bronze_raw_ids,
                separators=(",", ":"),
            ),

        "target_status":
            "CANDIDATE",

        "target_version":
            "DAILY_TMAX_RULE_V1",
    }


def insert_candidate(
    conn: sqlite3.Connection,
    candidate: dict,
) -> bool:
    created_at_utc = (
        datetime.now(UTC).isoformat()
    )

    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_NAME} (
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
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            candidate["business_date_bjt"],
            candidate["daily_tmax_c"],
            candidate["first_tmax_time_bjt"],
            candidate["last_tmax_time_bjt"],
            candidate["tmax_occurrence_count"],
            candidate["observation_count"],
            candidate["hourly_coverage_count"],
            candidate["has_correction"],
            candidate["tmax_has_correction"],
            candidate["has_recovery"],
            candidate["tmax_has_recovery"],
            candidate["tmax_silver_ids"],
            candidate["tmax_bronze_raw_ids"],
            candidate["all_silver_ids"],
            candidate["all_bronze_raw_ids"],
            candidate["target_status"],
            candidate["target_version"],
            created_at_utc,
        ),
    )

    return cursor.rowcount == 1


def main() -> int:
    parser = argparse.ArgumentParser()

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--dry-run",
        action="store_true",
    )

    mode.add_argument(
        "--commit",
        action="store_true",
    )

    args = parser.parse_args()

    commit = bool(args.commit)

    print("=" * 94)
    print(
        "ZUUU Ground Truth Candidate Builder V1.0"
    )
    print("=" * 94)

    print(
        "Mode     : "
        + (
            "COMMIT"
            if commit
            else "DRY-RUN"
        )
    )

    print(
        f"Database : {DEFAULT_DB_PATH}"
    )

    print("=" * 94)

    conn = connect()

    try:
        silver_rows = read_silver_rows(
            conn
        )

        print(
            f"Silver Rows : {len(silver_rows)}"
        )

        # ----------------------------------------------------
        # Group by BJT business date
        # ----------------------------------------------------

        grouped = defaultdict(list)

        for row in silver_rows:
            business_date = date.fromisoformat(
                row["business_date_bjt"]
            )

            grouped[
                business_date
            ].append(
                row
            )

        candidates = []

        hard_errors = []
        review_items = []

        # ----------------------------------------------------
        # Build only frozen complete range
        # ----------------------------------------------------

        for business_date in date_range(
            EXPECTED_START_DATE,
            EXPECTED_END_DATE,
        ):
            rows = grouped.get(
                business_date,
                [],
            )

            if not rows:
                hard_errors.append(
                    f"NO_OBSERVATIONS:{business_date}"
                )
                continue

            candidate = (
                build_candidate_for_day(
                    business_date,
                    rows,
                )
            )

            if (
                candidate[
                    "hourly_coverage_count"
                ]
                != 24
            ):
                hard_errors.append(
                    "INCOMPLETE_HOURLY_COVERAGE:"
                    f"{business_date}:"
                    f"{candidate['hourly_coverage_count']}"
                )

            if (
                candidate[
                    "observation_count"
                ]
                < 24
            ):
                hard_errors.append(
                    "LT24_OBSERVATIONS:"
                    f"{business_date}:"
                    f"{candidate['observation_count']}"
                )

            if (
                candidate[
                    "observation_count"
                ]
                > 24
            ):
                review_items.append(
                    "GT24_OBSERVATIONS:"
                    f"{business_date}:"
                    f"{candidate['observation_count']}"
                )

            if (
                candidate[
                    "tmax_has_correction"
                ]
                == 1
            ):
                review_items.append(
                    "TMAX_HAS_CORRECTION:"
                    f"{business_date}"
                )

            if (
                candidate[
                    "tmax_has_recovery"
                ]
                == 1
            ):
                review_items.append(
                    "TMAX_HAS_RECOVERY:"
                    f"{business_date}"
                )

            candidates.append(
                candidate
            )

        # ----------------------------------------------------
        # Global validation
        # ----------------------------------------------------

        if len(candidates) != EXPECTED_DAYS:
            hard_errors.append(
                "UNEXPECTED_CANDIDATE_COUNT:"
                f"{len(candidates)}"
            )

        candidate_dates = [
            item["business_date_bjt"]
            for item in candidates
        ]

        if len(candidate_dates) != len(
            set(candidate_dates)
        ):
            hard_errors.append(
                "DUPLICATE_CANDIDATE_DATE"
            )

        if candidates:
            if (
                candidates[0][
                    "business_date_bjt"
                ]
                != EXPECTED_START_DATE.isoformat()
            ):
                hard_errors.append(
                    "START_DATE_MISMATCH"
                )

            if (
                candidates[-1][
                    "business_date_bjt"
                ]
                != EXPECTED_END_DATE.isoformat()
            ):
                hard_errors.append(
                    "END_DATE_MISMATCH"
                )

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        correction_days = sum(
            item["has_correction"]
            for item in candidates
        )

        tmax_correction_days = sum(
            item["tmax_has_correction"]
            for item in candidates
        )

        recovery_days = sum(
            item["has_recovery"]
            for item in candidates
        )

        tmax_recovery_days = sum(
            item["tmax_has_recovery"]
            for item in candidates
        )

        gt24_days = sum(
            1
            for item in candidates
            if item["observation_count"] > 24
        )

        incomplete_days = sum(
            1
            for item in candidates
            if item["hourly_coverage_count"] != 24
        )

        tmax_values = [
            item["daily_tmax_c"]
            for item in candidates
        ]

        # ----------------------------------------------------
        # Print selected evidence
        # ----------------------------------------------------

        print()
        print("=" * 94)
        print("CANDIDATE RANGE")
        print("=" * 94)

        print(
            f"Start Date      : {EXPECTED_START_DATE}"
        )

        print(
            f"End Date        : {EXPECTED_END_DATE}"
        )

        print(
            f"Candidate Days  : {len(candidates)}"
        )

        print()
        print("=" * 94)
        print("COVERAGE")
        print("=" * 94)

        print(
            f"24-Hour Complete Days : "
            f"{len(candidates) - incomplete_days}"
        )

        print(
            f"Incomplete Days       : "
            f"{incomplete_days}"
        )

        print(
            f"Days >24 Observations : "
            f"{gt24_days}"
        )

        print()
        print("=" * 94)
        print("PROVENANCE")
        print("=" * 94)

        print(
            f"Days With COR         : "
            f"{correction_days}"
        )

        print(
            f"Tmax Has COR Days     : "
            f"{tmax_correction_days}"
        )

        print(
            f"Days With Recovery    : "
            f"{recovery_days}"
        )

        print(
            f"Tmax Recovery Days    : "
            f"{tmax_recovery_days}"
        )

        if tmax_values:
            print()
            print("=" * 94)
            print("TMAX RANGE")
            print("=" * 94)

            print(
                f"Minimum Tmax : "
                f"{min(tmax_values):g} C"
            )

            print(
                f"Maximum Tmax : "
                f"{max(tmax_values):g} C"
            )

        # ----------------------------------------------------
        # COMMIT
        # ----------------------------------------------------

        inserted = 0
        already_existing = 0

        if commit:
            if hard_errors:
                print()
                print(
                    "COMMIT BLOCKED: "
                    "hard errors exist."
                )
            else:
                create_candidate_table(
                    conn
                )

                for candidate in candidates:
                    if insert_candidate(
                        conn,
                        candidate,
                    ):
                        inserted += 1
                    else:
                        already_existing += 1

                conn.commit()

        # ----------------------------------------------------
        # Existing DB total
        # ----------------------------------------------------

        db_total = None

        if commit and not hard_errors:
            db_total = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {TABLE_NAME}
                """
            ).fetchone()[0]

            if db_total != EXPECTED_DAYS:
                hard_errors.append(
                    "DATABASE_TOTAL_MISMATCH:"
                    f"{db_total}"
                )

        # ----------------------------------------------------
        # Final
        # ----------------------------------------------------

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

        print()
        print("=" * 94)
        print("FINAL REPORT")
        print("=" * 94)

        print(
            f"Mode                    : "
            f"{'COMMIT' if commit else 'DRY-RUN'}"
        )

        print(
            f"Silver Rows             : "
            f"{len(silver_rows)}"
        )

        print(
            f"Candidate Days          : "
            f"{len(candidates)}"
        )

        print(
            f"Expected Candidate Days : "
            f"{EXPECTED_DAYS}"
        )

        print(
            f"Incomplete Days         : "
            f"{incomplete_days}"
        )

        print(
            f"Days >24 Observations   : "
            f"{gt24_days}"
        )

        print(
            f"Days With COR           : "
            f"{correction_days}"
        )

        print(
            f"Tmax Has COR Days       : "
            f"{tmax_correction_days}"
        )

        print(
            f"Days With Recovery      : "
            f"{recovery_days}"
        )

        print(
            f"Tmax Recovery Days      : "
            f"{tmax_recovery_days}"
        )

        if commit:
            print(
                f"Inserted                : "
                f"{inserted}"
            )

            print(
                f"Already Existing        : "
                f"{already_existing}"
            )

            if db_total is not None:
                print(
                    f"Candidate DB Total      : "
                    f"{db_total}"
                )

        print(
            f"Review Items            : "
            f"{len(review_items)}"
        )

        print(
            f"Hard Errors             : "
            f"{len(hard_errors)}"
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
                "RESULT: GROUND TRUTH CANDIDATE BUILD REVIEW REQUIRED"
            )
            return 2

        if commit:
            print(
                "RESULT: GROUND TRUTH CANDIDATE BUILD COMPLETE"
            )
        else:
            print(
                "RESULT: GROUND TRUTH CANDIDATE DRY-RUN PASS"
            )

        print(
            "NOTE: Candidate data is NOT ZUUU_TARGET_V1 yet."
        )

        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )