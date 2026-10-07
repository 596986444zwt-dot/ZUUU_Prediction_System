"""
ZUUU TARGET V1 Final Audit
==========================

Final read-only audit for the frozen ZUUU_TARGET_V1 asset.

This audit verifies:

1. TARGET table exists.
2. Exactly 729 frozen target rows exist.
3. Date range is exactly 2024-09-03 -> 2026-09-01.
4. No missing / duplicate business dates.
5. Every TARGET row matches its source Candidate.
6. Candidate -> Silver lineage remains valid.
7. Silver -> Bronze lineage remains valid.
8. Daily Tmax is independently recomputed from Silver.
9. Tmax lineage really points to Tmax observations.
10. Frozen metadata is internally consistent.
11. Dataset SHA256 matches the pre-freeze audited fingerprint.

IMPORTANT:
    READ ONLY.
    This script performs no CREATE / INSERT / UPDATE / DELETE.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH


TARGET_TABLE = "zuuu_target_v1"
CANDIDATE_TABLE = "zuuu_ground_truth_candidate"
SILVER_TABLE = "zuuu_silver_observation"
BRONZE_TABLE = "zuuu_raw_metar"

EXPECTED_START_DATE = date(2024, 9, 3)
EXPECTED_END_DATE = date(2026, 9, 1)
EXPECTED_DAYS = 729

EXPECTED_RULE_VERSION = "DAILY_TMAX_RULE_V1"
EXPECTED_TARGET_VERSION = "ZUUU_TARGET_V1"
EXPECTED_TARGET_STATUS = "FROZEN"

EXPECTED_SHA256 = (
    "b8548609e64d10b787fc09d9fddb28e20acd1808b165f47e62b574580cc67f3e"
)


def connect_read_only() -> sqlite3.Connection:
    """
    Open SQLite in true read-only mode.
    """

    db_uri = DEFAULT_DB_PATH.resolve().as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        db_uri,
        uri=True,
    )

    conn.row_factory = sqlite3.Row

    return conn


def table_exists(
    conn: sqlite3.Connection,
    table_name: str,
) -> bool:

    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def expected_dates() -> list[str]:

    result = []

    current = EXPECTED_START_DATE

    while current <= EXPECTED_END_DATE:

        result.append(
            current.isoformat()
        )

        current += timedelta(days=1)

    return result


def parse_json_ids(
    value: str,
) -> list[int]:

    parsed = json.loads(value)

    if not isinstance(parsed, list):
        raise ValueError(
            "LINEAGE_NOT_LIST"
        )

    return [
        int(item)
        for item in parsed
    ]


def canonical_target_payload(
    row: sqlite3.Row,
) -> dict:

    """
    Must match the canonical semantic payload used by the freezer.

    Database-local IDs, frozen timestamp and Candidate creation
    timestamp are intentionally excluded.
    """

    return {
        "business_date_bjt":
            row["business_date_bjt"],

        "daily_tmax_c":
            float(row["daily_tmax_c"]),

        "first_tmax_time_bjt":
            row["first_tmax_time_bjt"],

        "last_tmax_time_bjt":
            row["last_tmax_time_bjt"],

        "tmax_occurrence_count":
            int(row["tmax_occurrence_count"]),

        "observation_count":
            int(row["observation_count"]),

        "hourly_coverage_count":
            int(row["hourly_coverage_count"]),

        "has_correction":
            int(row["has_correction"]),

        "tmax_has_correction":
            int(row["tmax_has_correction"]),

        "has_recovery":
            int(row["has_recovery"]),

        "tmax_has_recovery":
            int(row["tmax_has_recovery"]),

        "tmax_silver_ids":
            parse_json_ids(
                row["tmax_silver_ids"]
            ),

        "tmax_bronze_raw_ids":
            parse_json_ids(
                row["tmax_bronze_raw_ids"]
            ),

        "all_silver_ids":
            parse_json_ids(
                row["all_silver_ids"]
            ),

        "all_bronze_raw_ids":
            parse_json_ids(
                row["all_bronze_raw_ids"]
            ),

        "rule_version":
            row["rule_version"],

        "target_version":
            row["target_version"],
    }


def compute_target_sha256(
    rows,
) -> str:

    payload = [
        canonical_target_payload(row)
        for row in rows
    ]

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(
        encoded
    ).hexdigest()


def main() -> int:

    print("=" * 110)
    print("ZUUU TARGET V1 Final Audit")
    print("=" * 110)

    print(
        f"Database       : {DEFAULT_DB_PATH}"
    )

    print(
        f"Expected Target: {EXPECTED_TARGET_VERSION}"
    )

    print(
        f"Expected SHA256: {EXPECTED_SHA256}"
    )

    print("=" * 110)

    conn = connect_read_only()

    hard_errors = []
    review_items = []

    try:

        # ====================================================
        # TABLE EXISTENCE
        # ====================================================

        required_tables = [
            TARGET_TABLE,
            CANDIDATE_TABLE,
            SILVER_TABLE,
            BRONZE_TABLE,
        ]

        missing_tables = []

        for table_name in required_tables:

            if not table_exists(
                conn,
                table_name,
            ):
                missing_tables.append(
                    table_name
                )

        print()
        print("=" * 110)
        print("TABLE CHECK")
        print("=" * 110)

        for table_name in required_tables:

            status = (
                "OK"
                if table_name not in missing_tables
                else "MISSING"
            )

            print(
                f"{table_name:<35} : {status}"
            )

        if missing_tables:

            for table_name in missing_tables:

                hard_errors.append(
                    f"MISSING_TABLE:{table_name}"
                )

            print()
            print("=" * 110)
            print("RESULT: TARGET V1 FINAL AUDIT FAIL")
            print("=" * 110)

            for item in hard_errors:
                print(
                    f"    {item}"
                )

            return 2

        # ====================================================
        # LOAD DATA
        # ====================================================

        target_rows = conn.execute(
            f"""
            SELECT *
            FROM {TARGET_TABLE}
            ORDER BY business_date_bjt
            """
        ).fetchall()

        candidate_rows = conn.execute(
            f"""
            SELECT *
            FROM {CANDIDATE_TABLE}
            ORDER BY business_date_bjt
            """
        ).fetchall()

        bronze_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {BRONZE_TABLE}
                """
            ).fetchone()[0]
        )

        silver_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {SILVER_TABLE}
                """
            ).fetchone()[0]
        )

        print()
        print("=" * 110)
        print("DATASET COUNTS")
        print("=" * 110)

        print(
            f"Bronze Rows    : {bronze_count}"
        )

        print(
            f"Silver Rows    : {silver_count}"
        )

        print(
            f"Candidate Rows : {len(candidate_rows)}"
        )

        print(
            f"TARGET Rows    : {len(target_rows)}"
        )

        if len(target_rows) != EXPECTED_DAYS:

            hard_errors.append(
                f"TARGET_COUNT:"
                f"{len(target_rows)}:"
                f"EXPECTED={EXPECTED_DAYS}"
            )

        if len(candidate_rows) != EXPECTED_DAYS:

            hard_errors.append(
                f"CANDIDATE_COUNT:"
                f"{len(candidate_rows)}:"
                f"EXPECTED={EXPECTED_DAYS}"
            )

        # ====================================================
        # DATE SEQUENCE
        # ====================================================

        expected_date_sequence = (
            expected_dates()
        )

        actual_target_dates = [
            row["business_date_bjt"]
            for row in target_rows
        ]

        duplicate_dates = (
            len(actual_target_dates)
            - len(set(actual_target_dates))
        )

        missing_dates = sorted(
            set(expected_date_sequence)
            - set(actual_target_dates)
        )

        extra_dates = sorted(
            set(actual_target_dates)
            - set(expected_date_sequence)
        )

        sequence_match = (
            actual_target_dates
            == expected_date_sequence
        )

        print()
        print("=" * 110)
        print("DATE RANGE")
        print("=" * 110)

        print(
            f"Expected Start   : {EXPECTED_START_DATE}"
        )

        print(
            f"Expected End     : {EXPECTED_END_DATE}"
        )

        print(
            f"Expected Days    : {EXPECTED_DAYS}"
        )

        print(
            f"Actual Start     : "
            f"{actual_target_dates[0] if actual_target_dates else 'N/A'}"
        )

        print(
            f"Actual End       : "
            f"{actual_target_dates[-1] if actual_target_dates else 'N/A'}"
        )

        print(
            f"Duplicate Dates  : {duplicate_dates}"
        )

        print(
            f"Missing Dates    : {len(missing_dates)}"
        )

        print(
            f"Extra Dates      : {len(extra_dates)}"
        )

        print(
            f"Sequence Match   : {sequence_match}"
        )

        if duplicate_dates != 0:
            hard_errors.append(
                f"DUPLICATE_DATES:{duplicate_dates}"
            )

        if missing_dates:
            hard_errors.append(
                f"MISSING_DATES:{len(missing_dates)}"
            )

        if extra_dates:
            hard_errors.append(
                f"EXTRA_DATES:{len(extra_dates)}"
            )

        if not sequence_match:
            hard_errors.append(
                "DATE_SEQUENCE_MISMATCH"
            )

        # ====================================================
        # SHA256
        # ====================================================

        actual_sha256 = (
            compute_target_sha256(
                target_rows
            )
            if target_rows
            else None
        )

        sha_match = (
            actual_sha256
            == EXPECTED_SHA256
        )

        print()
        print("=" * 110)
        print("FROZEN DATASET FINGERPRINT")
        print("=" * 110)

        print(
            f"Expected SHA256 : {EXPECTED_SHA256}"
        )

        print(
            f"Actual SHA256   : {actual_sha256}"
        )

        print(
            f"SHA256 Match    : {sha_match}"
        )

        if not sha_match:
            hard_errors.append(
                "TARGET_SHA256_MISMATCH"
            )

        # ====================================================
        # METADATA
        # ====================================================

        rule_versions = sorted(
            {
                row["rule_version"]
                for row in target_rows
            }
        )

        target_versions = sorted(
            {
                row["target_version"]
                for row in target_rows
            }
        )

        target_statuses = sorted(
            {
                row["target_status"]
                for row in target_rows
            }
        )

        frozen_times = sorted(
            {
                row["frozen_at_utc"]
                for row in target_rows
            }
        )

        print()
        print("=" * 110)
        print("FROZEN METADATA")
        print("=" * 110)

        print(
            f"Rule Versions   : {rule_versions}"
        )

        print(
            f"Target Versions : {target_versions}"
        )

        print(
            f"Target Statuses : {target_statuses}"
        )

        print(
            f"Freeze Times    : {frozen_times}"
        )

        if rule_versions != [
            EXPECTED_RULE_VERSION
        ]:
            hard_errors.append(
                "RULE_VERSION_MISMATCH"
            )

        if target_versions != [
            EXPECTED_TARGET_VERSION
        ]:
            hard_errors.append(
                "TARGET_VERSION_MISMATCH"
            )

        if target_statuses != [
            EXPECTED_TARGET_STATUS
        ]:
            hard_errors.append(
                "TARGET_STATUS_MISMATCH"
            )

        if len(frozen_times) != 1:
            hard_errors.append(
                f"FREEZE_TIMESTAMP_COUNT:"
                f"{len(frozen_times)}"
            )

        for value in frozen_times:

            try:
                dt = datetime.fromisoformat(
                    value
                )

                if dt.tzinfo is None:
                    hard_errors.append(
                        "FROZEN_AT_NAIVE"
                    )

            except Exception:

                hard_errors.append(
                    f"INVALID_FROZEN_AT:{value}"
                )

        # ====================================================
        # CANDIDATE LOOKUP
        # ====================================================

        candidate_by_id = {
            int(row["id"]): row
            for row in candidate_rows
        }

        candidate_by_date = {
            row["business_date_bjt"]: row
            for row in candidate_rows
        }

        # ====================================================
        # FULL TARGET AUDIT
        # ====================================================

        candidate_mismatch = 0
        source_candidate_missing = 0

        silver_lineage_mismatch = 0
        bronze_lineage_mismatch = 0

        missing_silver = 0
        missing_bronze = 0

        cross_day_silver = 0

        tmax_recompute_mismatch = 0
        first_tmax_mismatch = 0
        last_tmax_mismatch = 0
        occurrence_mismatch = 0

        tmax_silver_lineage_mismatch = 0
        tmax_bronze_lineage_mismatch = 0

        observation_count_mismatch = 0
        hourly_coverage_mismatch = 0

        days_gt24 = 0
        days_with_cor = 0
        tmax_cor_days = 0
        recovery_days = 0
        tmax_recovery_days = 0

        min_tmax = None
        max_tmax = None
        min_tmax_date = None
        max_tmax_date = None

        for target in target_rows:

            business_date = (
                target[
                    "business_date_bjt"
                ]
            )

            # ------------------------------------------------
            # Candidate provenance
            # ------------------------------------------------

            source_candidate_id = int(
                target[
                    "source_candidate_id"
                ]
            )

            candidate = (
                candidate_by_id.get(
                    source_candidate_id
                )
            )

            if candidate is None:

                source_candidate_missing += 1

                hard_errors.append(
                    f"MISSING_SOURCE_CANDIDATE:"
                    f"{business_date}:"
                    f"{source_candidate_id}"
                )

                continue

            if (
                candidate[
                    "business_date_bjt"
                ]
                != business_date
            ):

                candidate_mismatch += 1

                hard_errors.append(
                    f"CANDIDATE_DATE_MISMATCH:"
                    f"{business_date}"
                )

            date_candidate = (
                candidate_by_date.get(
                    business_date
                )
            )

            if (
                date_candidate is None
                or int(date_candidate["id"])
                != source_candidate_id
            ):

                candidate_mismatch += 1

                hard_errors.append(
                    f"CANDIDATE_ID_DATE_LINK_MISMATCH:"
                    f"{business_date}"
                )

            # ------------------------------------------------
            # Exact Candidate -> Target semantic comparison
            # ------------------------------------------------

            fields = [
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
            ]

            for field in fields:

                target_value = (
                    target[field]
                )

                candidate_value = (
                    candidate[field]
                )

                if field == "daily_tmax_c":

                    target_value = float(
                        target_value
                    )

                    candidate_value = float(
                        candidate_value
                    )

                elif field in {
                    "tmax_occurrence_count",
                    "observation_count",
                    "hourly_coverage_count",
                    "has_correction",
                    "tmax_has_correction",
                    "has_recovery",
                    "tmax_has_recovery",
                }:

                    target_value = int(
                        target_value
                    )

                    candidate_value = int(
                        candidate_value
                    )

                if (
                    target_value
                    != candidate_value
                ):

                    candidate_mismatch += 1

                    hard_errors.append(
                        f"CANDIDATE_TARGET_MISMATCH:"
                        f"{business_date}:"
                        f"{field}"
                    )

            # ------------------------------------------------
            # Parse lineage
            # ------------------------------------------------

            try:

                all_silver_ids = (
                    parse_json_ids(
                        target[
                            "all_silver_ids"
                        ]
                    )
                )

                all_bronze_ids = (
                    parse_json_ids(
                        target[
                            "all_bronze_raw_ids"
                        ]
                    )
                )

                tmax_silver_ids = (
                    parse_json_ids(
                        target[
                            "tmax_silver_ids"
                        ]
                    )
                )

                tmax_bronze_ids = (
                    parse_json_ids(
                        target[
                            "tmax_bronze_raw_ids"
                        ]
                    )
                )

            except Exception as exc:

                hard_errors.append(
                    f"LINEAGE_PARSE_ERROR:"
                    f"{business_date}:"
                    f"{exc}"
                )

                continue

            # ------------------------------------------------
            # Observation count
            # ------------------------------------------------

            expected_obs_count = int(
                target[
                    "observation_count"
                ]
            )

            if (
                len(all_silver_ids)
                != expected_obs_count
            ):

                observation_count_mismatch += 1

                hard_errors.append(
                    f"SILVER_LINEAGE_COUNT:"
                    f"{business_date}"
                )

            if (
                len(all_bronze_ids)
                != expected_obs_count
            ):

                observation_count_mismatch += 1

                hard_errors.append(
                    f"BRONZE_LINEAGE_COUNT:"
                    f"{business_date}"
                )

            if expected_obs_count > 24:

                days_gt24 += 1

                review_items.append(
                    f"GT24_OBSERVATIONS:"
                    f"{business_date}:"
                    f"{expected_obs_count}"
                )

            # ------------------------------------------------
            # Fetch Silver evidence independently
            # ------------------------------------------------

            silver_rows = conn.execute(
                f"""
                SELECT
                    id,
                    bronze_raw_id,
                    observation_time_bjt,
                    business_date_bjt,
                    temperature_c,
                    message_class,
                    recovery_reason

                FROM {SILVER_TABLE}

                WHERE business_date_bjt = ?

                ORDER BY
                    observation_time_bjt,
                    id
                """,
                (business_date,),
            ).fetchall()

            actual_silver_ids = [
                int(row["id"])
                for row in silver_rows
            ]

            actual_bronze_ids = [
                int(row["bronze_raw_id"])
                for row in silver_rows
            ]

            if (
                actual_silver_ids
                != all_silver_ids
            ):

                silver_lineage_mismatch += 1

                hard_errors.append(
                    f"ALL_SILVER_LINEAGE_MISMATCH:"
                    f"{business_date}"
                )

            if (
                actual_bronze_ids
                != all_bronze_ids
            ):

                bronze_lineage_mismatch += 1

                hard_errors.append(
                    f"ALL_BRONZE_LINEAGE_MISMATCH:"
                    f"{business_date}"
                )

            # ------------------------------------------------
            # Validate each Silver -> Bronze reference
            # ------------------------------------------------

            for silver in silver_rows:

                silver_id = int(
                    silver["id"]
                )

                bronze_id = int(
                    silver[
                        "bronze_raw_id"
                    ]
                )

                if (
                    silver[
                        "business_date_bjt"
                    ]
                    != business_date
                ):

                    cross_day_silver += 1

                    hard_errors.append(
                        f"CROSS_DAY_SILVER:"
                        f"{business_date}:"
                        f"{silver_id}"
                    )

                bronze = conn.execute(
                    f"""
                    SELECT id
                    FROM {BRONZE_TABLE}
                    WHERE id = ?
                    """,
                    (bronze_id,),
                ).fetchone()

                if bronze is None:

                    missing_bronze += 1

                    hard_errors.append(
                        f"MISSING_BRONZE:"
                        f"{business_date}:"
                        f"{bronze_id}"
                    )

            # Check referenced Silver IDs exist.
            for silver_id in all_silver_ids:

                silver_exists = conn.execute(
                    f"""
                    SELECT 1
                    FROM {SILVER_TABLE}
                    WHERE id = ?
                    LIMIT 1
                    """,
                    (silver_id,),
                ).fetchone()

                if silver_exists is None:

                    missing_silver += 1

                    hard_errors.append(
                        f"MISSING_SILVER:"
                        f"{business_date}:"
                        f"{silver_id}"
                    )

            # ------------------------------------------------
            # Independent Tmax recomputation
            # ------------------------------------------------

            if not silver_rows:

                hard_errors.append(
                    f"NO_SILVER_ROWS:"
                    f"{business_date}"
                )

                continue

            temperatures = [
                float(
                    row[
                        "temperature_c"
                    ]
                )
                for row in silver_rows
            ]

            recomputed_tmax = max(
                temperatures
            )

            stored_tmax = float(
                target[
                    "daily_tmax_c"
                ]
            )

            if (
                recomputed_tmax
                != stored_tmax
            ):

                tmax_recompute_mismatch += 1

                hard_errors.append(
                    f"TMAX_RECOMPUTE_MISMATCH:"
                    f"{business_date}:"
                    f"{stored_tmax}:"
                    f"{recomputed_tmax}"
                )

            tmax_rows = [
                row
                for row in silver_rows
                if float(
                    row[
                        "temperature_c"
                    ]
                ) == recomputed_tmax
            ]

            recomputed_tmax_silver_ids = [
                int(row["id"])
                for row in tmax_rows
            ]

            recomputed_tmax_bronze_ids = [
                int(row["bronze_raw_id"])
                for row in tmax_rows
            ]

            if (
                recomputed_tmax_silver_ids
                != tmax_silver_ids
            ):

                tmax_silver_lineage_mismatch += 1

                hard_errors.append(
                    f"TMAX_SILVER_LINEAGE_MISMATCH:"
                    f"{business_date}"
                )

            if (
                recomputed_tmax_bronze_ids
                != tmax_bronze_ids
            ):

                tmax_bronze_lineage_mismatch += 1

                hard_errors.append(
                    f"TMAX_BRONZE_LINEAGE_MISMATCH:"
                    f"{business_date}"
                )

            first_tmax_time = (
                tmax_rows[0][
                    "observation_time_bjt"
                ]
            )

            last_tmax_time = (
                tmax_rows[-1][
                    "observation_time_bjt"
                ]
            )

            occurrence_count = len(
                tmax_rows
            )

            if (
                first_tmax_time
                != target[
                    "first_tmax_time_bjt"
                ]
            ):

                first_tmax_mismatch += 1

                hard_errors.append(
                    f"FIRST_TMAX_TIME_MISMATCH:"
                    f"{business_date}"
                )

            if (
                last_tmax_time
                != target[
                    "last_tmax_time_bjt"
                ]
            ):

                last_tmax_mismatch += 1

                hard_errors.append(
                    f"LAST_TMAX_TIME_MISMATCH:"
                    f"{business_date}"
                )

            if (
                occurrence_count
                != int(
                    target[
                        "tmax_occurrence_count"
                    ]
                )
            ):

                occurrence_mismatch += 1

                hard_errors.append(
                    f"TMAX_OCCURRENCE_MISMATCH:"
                    f"{business_date}"
                )

            # ------------------------------------------------
            # Hourly coverage independent recomputation
            # ------------------------------------------------

            hour_slots = set()

            for row in silver_rows:

                dt = datetime.fromisoformat(
                    row[
                        "observation_time_bjt"
                    ]
                )

                if dt.minute == 0:

                    hour_slots.add(
                        dt.hour
                    )

            actual_hourly_coverage = len(
                hour_slots
            )

            stored_hourly_coverage = int(
                target[
                    "hourly_coverage_count"
                ]
            )

            if (
                actual_hourly_coverage
                != stored_hourly_coverage
            ):

                hourly_coverage_mismatch += 1

                hard_errors.append(
                    f"HOURLY_COVERAGE_MISMATCH:"
                    f"{business_date}:"
                    f"{actual_hourly_coverage}:"
                    f"{stored_hourly_coverage}"
                )

            if actual_hourly_coverage != 24:

                hard_errors.append(
                    f"INCOMPLETE_HOURLY_DAY:"
                    f"{business_date}:"
                    f"{actual_hourly_coverage}"
                )

            # ------------------------------------------------
            # Provenance statistics
            # ------------------------------------------------

            has_cor = any(
                row["message_class"] == "COR"
                for row in silver_rows
            )

            tmax_has_cor = any(
                row["message_class"] == "COR"
                for row in tmax_rows
            )

            has_recovery = any(
                row["recovery_reason"]
                is not None
                for row in silver_rows
            )

            tmax_has_recovery = any(
                row["recovery_reason"]
                is not None
                for row in tmax_rows
            )

            if has_cor:
                days_with_cor += 1

            if tmax_has_cor:

                tmax_cor_days += 1

                review_items.append(
                    f"TMAX_HAS_CORRECTION:"
                    f"{business_date}"
                )

            if has_recovery:
                recovery_days += 1

            if tmax_has_recovery:

                tmax_recovery_days += 1

                review_items.append(
                    f"TMAX_HAS_RECOVERY:"
                    f"{business_date}"
                )

            # ------------------------------------------------
            # Extrema
            # ------------------------------------------------

            if (
                min_tmax is None
                or stored_tmax < min_tmax
            ):

                min_tmax = stored_tmax
                min_tmax_date = (
                    business_date
                )

            if (
                max_tmax is None
                or stored_tmax > max_tmax
            ):

                max_tmax = stored_tmax
                max_tmax_date = (
                    business_date
                )

        # ====================================================
        # Remove duplicate diagnostic strings
        # ====================================================

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

        # ====================================================
        # AUDIT SUMMARY
        # ====================================================

        print()
        print("=" * 110)
        print("INDEPENDENT TARGET RECOMPUTATION")
        print("=" * 110)

        print(
            f"Candidate Mismatches       : "
            f"{candidate_mismatch}"
        )

        print(
            f"Missing Source Candidates  : "
            f"{source_candidate_missing}"
        )

        print(
            f"Tmax Recompute Mismatches  : "
            f"{tmax_recompute_mismatch}"
        )

        print(
            f"First Tmax Time Mismatches : "
            f"{first_tmax_mismatch}"
        )

        print(
            f"Last Tmax Time Mismatches  : "
            f"{last_tmax_mismatch}"
        )

        print(
            f"Tmax Occurrence Mismatches : "
            f"{occurrence_mismatch}"
        )

        print(
            f"Observation Count Mismatch : "
            f"{observation_count_mismatch}"
        )

        print(
            f"Hourly Coverage Mismatch   : "
            f"{hourly_coverage_mismatch}"
        )

        print()
        print("=" * 110)
        print("LINEAGE")
        print("=" * 110)

        print(
            f"Silver Lineage Mismatch    : "
            f"{silver_lineage_mismatch}"
        )

        print(
            f"Bronze Lineage Mismatch    : "
            f"{bronze_lineage_mismatch}"
        )

        print(
            f"Tmax Silver Lineage Error  : "
            f"{tmax_silver_lineage_mismatch}"
        )

        print(
            f"Tmax Bronze Lineage Error  : "
            f"{tmax_bronze_lineage_mismatch}"
        )

        print(
            f"Missing Silver             : "
            f"{missing_silver}"
        )

        print(
            f"Missing Bronze             : "
            f"{missing_bronze}"
        )

        print(
            f"Cross-Day Silver           : "
            f"{cross_day_silver}"
        )

        print()
        print("=" * 110)
        print("TARGET STATISTICS")
        print("=" * 110)

        print(
            f"Days >24 Observations : "
            f"{days_gt24}"
        )

        print(
            f"Days With COR         : "
            f"{days_with_cor}"
        )

        print(
            f"Tmax COR Days         : "
            f"{tmax_cor_days}"
        )

        print(
            f"Recovery Days         : "
            f"{recovery_days}"
        )

        print(
            f"Tmax Recovery Days    : "
            f"{tmax_recovery_days}"
        )

        print(
            f"Minimum Daily Tmax    : "
            f"{min_tmax:g} C "
            f"({min_tmax_date})"
        )

        print(
            f"Maximum Daily Tmax    : "
            f"{max_tmax:g} C "
            f"({max_tmax_date})"
        )

        # ====================================================
        # FINAL REPORT
        # ====================================================

        print()
        print("=" * 110)
        print("FINAL REPORT")
        print("=" * 110)

        print(
            f"Bronze Rows               : "
            f"{bronze_count}"
        )

        print(
            f"Silver Rows               : "
            f"{silver_count}"
        )

        print(
            f"Candidate Rows            : "
            f"{len(candidate_rows)}"
        )

        print(
            f"Frozen TARGET Rows        : "
            f"{len(target_rows)}"
        )

        print(
            f"Target Version            : "
            f"{EXPECTED_TARGET_VERSION}"
        )

        print(
            f"Target Status             : "
            f"{EXPECTED_TARGET_STATUS}"
        )

        print(
            f"Expected SHA256           : "
            f"{EXPECTED_SHA256}"
        )

        print(
            f"Actual SHA256             : "
            f"{actual_sha256}"
        )

        print(
            f"SHA256 Match              : "
            f"{sha_match}"
        )

        print(
            f"Review Items              : "
            f"{len(review_items)}"
        )

        print(
            f"Hard Errors               : "
            f"{len(hard_errors)}"
        )

        if review_items:

            print()
            print("Known Review Items:")

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

        print("=" * 110)

        if hard_errors:

            print(
                "RESULT: ZUUU_TARGET_V1 FINAL AUDIT FAIL"
            )

            return 2

        print(
            "RESULT: ZUUU_TARGET_V1 FINAL AUDIT PASS"
        )

        print(
            "STATUS: PHASE 1 GROUND TRUTH READY FOR FINAL CLOSE."
        )

        return 0

    finally:

        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )