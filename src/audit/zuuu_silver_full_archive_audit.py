"""
ZUUU Silver Full Archive Audit V1.0
===================================

Purpose:
    Full read-only audit of Bronze -> Silver transformation.

Checks:
    1. Bronze / Silver row counts
    2. Bronze -> Silver strict 1:1 lineage
    3. Orphan Silver rows
    4. Missing Silver rows
    5. Station / source / query class / message class consistency
    6. UTC time consistency
    7. UTC -> Asia/Shanghai conversion
    8. BJT business date correctness
    9. Temperature / dew point consistency
    10. COR semantics
    11. Recovery provenance
    12. QC status / flags
    13. Known Ogimet recovery timestamps
    14. Duplicate bronze_raw_id protection

Principles:
    - READ ONLY
    - No database writes
    - No network access
    - Bronze remains authoritative Raw lineage
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH

from src.parsers.zuuu_metar_temperature_parser import (
    parse_temperature,
)

from src.parsers.zuuu_time_normalizer import (
    normalize_observation_time,
)


UTC = timezone.utc


EXPECTED_BRONZE_ROWS = 17521
EXPECTED_SILVER_ROWS = 17521

EXPECTED_COR_COUNT = 5
EXPECTED_RECOVERY_COUNT = 2

EXPECTED_RECOVERY_TIMES = {
    "2025-09-24T03:00:00+00:00",
    "2025-09-24T04:00:00+00:00",
}


def parse_iso_utc(value: str) -> datetime:

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        raise ValueError(
            f"NAIVE_DATETIME:{value}"
        )

    return dt.astimezone(UTC)


def flags_to_set(
    value: str | None,
) -> set[str]:

    if not value:
        return set()

    return {
        item
        for item in value.split("|")
        if item
    }


def main() -> int:

    print("=" * 90)
    print(
        "ZUUU Silver Full Archive Audit V1.0"
    )
    print("=" * 90)

    print(
        f"Database : {DEFAULT_DB_PATH}"
    )

    print("=" * 90)

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
                qc_flags,
                created_at_utc
            FROM zuuu_silver_observation
            ORDER BY bronze_raw_id
            """
        ).fetchall()

    finally:

        conn.close()

    print(
        f"Bronze Rows : {len(bronze_rows)}"
    )

    print(
        f"Silver Rows : {len(silver_rows)}"
    )

    bronze_by_id = {
        int(row["id"]): row
        for row in bronze_rows
    }

    silver_by_bronze_id = {
        int(row["bronze_raw_id"]): row
        for row in silver_rows
    }

    hard_errors: list[str] = []
    review_items: list[str] = []

    issue_examples: list[str] = []

    counters = Counter()

    # ========================================================
    # Basic counts
    # ========================================================

    if len(bronze_rows) != EXPECTED_BRONZE_ROWS:

        hard_errors.append(
            "UNEXPECTED_BRONZE_ROW_COUNT"
        )

    if len(silver_rows) != EXPECTED_SILVER_ROWS:

        hard_errors.append(
            "UNEXPECTED_SILVER_ROW_COUNT"
        )

    # ========================================================
    # Duplicate lineage
    # ========================================================

    bronze_ids = [
        int(row["bronze_raw_id"])
        for row in silver_rows
    ]

    duplicate_lineage_count = (
        len(bronze_ids)
        - len(set(bronze_ids))
    )

    if duplicate_lineage_count:

        hard_errors.append(
            "DUPLICATE_BRONZE_RAW_ID_IN_SILVER"
        )

    # ========================================================
    # Missing / orphan
    # ========================================================

    missing_silver_ids = (
        set(bronze_by_id)
        - set(silver_by_bronze_id)
    )

    orphan_silver_ids = (
        set(silver_by_bronze_id)
        - set(bronze_by_id)
    )

    if missing_silver_ids:

        hard_errors.append(
            "BRONZE_ROWS_MISSING_FROM_SILVER"
        )

    if orphan_silver_ids:

        hard_errors.append(
            "ORPHAN_SILVER_ROWS"
        )

    # ========================================================
    # Row-by-row audit
    # ========================================================

    for bronze_id, bronze in bronze_by_id.items():

        silver = silver_by_bronze_id.get(
            bronze_id
        )

        if silver is None:
            continue

        counters["ROWS_CHECKED"] += 1

        # ----------------------------------------------------
        # Provenance fields
        # ----------------------------------------------------

        if (
            bronze["station"]
            != silver["station"]
        ):
            counters["STATION_MISMATCH"] += 1

        if (
            bronze["source"]
            != silver["source"]
        ):
            counters["SOURCE_MISMATCH"] += 1

        if (
            bronze["source_query_class"]
            != silver["source_query_class"]
        ):
            counters[
                "QUERY_CLASS_MISMATCH"
            ] += 1

        if (
            bronze["message_class"]
            != silver["message_class"]
        ):
            counters[
                "MESSAGE_CLASS_MISMATCH"
            ] += 1

        if (
            bronze["recovery_reason"]
            != silver["recovery_reason"]
        ):
            counters[
                "RECOVERY_REASON_MISMATCH"
            ] += 1

        # ----------------------------------------------------
        # Bronze time
        # ----------------------------------------------------

        try:

            bronze_dt = parse_iso_utc(
                bronze[
                    "observation_time_utc"
                ]
            )

            normalized = (
                normalize_observation_time(
                    bronze_dt
                )
            )

        except Exception as exc:

            counters[
                "TIME_RECOMPUTE_ERROR"
            ] += 1

            if len(issue_examples) < 20:

                issue_examples.append(
                    f"Bronze ID {bronze_id}: "
                    f"TIME_RECOMPUTE_ERROR "
                    f"{type(exc).__name__}:{exc}"
                )

            continue

        # ----------------------------------------------------
        # Silver UTC
        # ----------------------------------------------------

        try:

            silver_utc = parse_iso_utc(
                silver[
                    "observation_time_utc"
                ]
            )

        except Exception as exc:

            counters[
                "SILVER_UTC_PARSE_ERROR"
            ] += 1

            if len(issue_examples) < 20:

                issue_examples.append(
                    f"Bronze ID {bronze_id}: "
                    f"SILVER_UTC_PARSE_ERROR "
                    f"{type(exc).__name__}:{exc}"
                )

            continue

        if (
            silver_utc
            != normalized.observation_time_utc
        ):
            counters[
                "UTC_MISMATCH"
            ] += 1

        # ----------------------------------------------------
        # Silver BJT
        # ----------------------------------------------------

        try:

            silver_bjt = (
                datetime.fromisoformat(
                    silver[
                        "observation_time_bjt"
                    ]
                )
            )

        except Exception as exc:

            counters[
                "SILVER_BJT_PARSE_ERROR"
            ] += 1

            if len(issue_examples) < 20:

                issue_examples.append(
                    f"Bronze ID {bronze_id}: "
                    f"SILVER_BJT_PARSE_ERROR "
                    f"{type(exc).__name__}:{exc}"
                )

            continue

        if silver_bjt.tzinfo is None:

            counters[
                "SILVER_BJT_NAIVE"
            ] += 1

        elif (
            silver_bjt
            != normalized.observation_time_bjt
        ):

            counters[
                "BJT_MISMATCH"
            ] += 1

        expected_business_date = (
            normalized
            .business_date_bjt
            .isoformat()
        )

        if (
            silver["business_date_bjt"]
            != expected_business_date
        ):
            counters[
                "BUSINESS_DATE_MISMATCH"
            ] += 1

        # ----------------------------------------------------
        # Temperature / dew point
        # ----------------------------------------------------

        parsed_temp = parse_temperature(
            bronze["raw_metar"]
        )

        if not parsed_temp.success:

            counters[
                "REPARSE_FAILURE"
            ] += 1

            if len(issue_examples) < 20:

                issue_examples.append(
                    f"Bronze ID {bronze_id}: "
                    f"REPARSE_FAILURE "
                    f"{parsed_temp.error}"
                )

        else:

            if (
                float(
                    parsed_temp.temperature_c
                )
                != float(
                    silver["temperature_c"]
                )
            ):
                counters[
                    "TEMPERATURE_MISMATCH"
                ] += 1

            if parsed_temp.dewpoint_c is None:

                if (
                    silver["dewpoint_c"]
                    is not None
                ):
                    counters[
                        "DEWPOINT_MISMATCH"
                    ] += 1

            else:

                if (
                    silver["dewpoint_c"]
                    is None
                    or float(
                        parsed_temp.dewpoint_c
                    )
                    != float(
                        silver["dewpoint_c"]
                    )
                ):
                    counters[
                        "DEWPOINT_MISMATCH"
                    ] += 1

        # ----------------------------------------------------
        # COR semantics
        # ----------------------------------------------------

        expected_is_correction = (
            1
            if bronze["message_class"] == "COR"
            else 0
        )

        if (
            int(silver["is_correction"])
            != expected_is_correction
        ):
            counters[
                "CORRECTION_FLAG_MISMATCH"
            ] += 1

        flags = flags_to_set(
            silver["qc_flags"]
        )

        if bronze["message_class"] == "COR":

            counters[
                "COR_ROWS"
            ] += 1

            if (
                "CORRECTION"
                not in flags
            ):
                counters[
                    "CORRECTION_QC_FLAG_MISSING"
                ] += 1

            # Current V1 evidence does not establish
            # a superseded archived Raw.
            if (
                silver["supersedes_raw_id"]
                is not None
            ):
                counters[
                    "UNEXPECTED_SUPERSEDES_RAW_ID"
                ] += 1

        else:

            if (
                "CORRECTION"
                in flags
            ):
                counters[
                    "FALSE_CORRECTION_QC_FLAG"
                ] += 1

        # ----------------------------------------------------
        # Recovery semantics
        # ----------------------------------------------------

        if bronze["recovery_reason"]:

            counters[
                "RECOVERY_ROWS"
            ] += 1

            if (
                "RECOVERY_SOURCE"
                not in flags
            ):
                counters[
                    "RECOVERY_QC_FLAG_MISSING"
                ] += 1

        else:

            if (
                "RECOVERY_SOURCE"
                in flags
            ):
                counters[
                    "FALSE_RECOVERY_QC_FLAG"
                ] += 1

        # ----------------------------------------------------
        # QC status
        # ----------------------------------------------------

        if (
            silver["qc_status"]
            != "PASS"
        ):
            counters[
                "INVALID_INITIAL_QC_STATUS"
            ] += 1

        # ----------------------------------------------------
        # Required fields
        # ----------------------------------------------------

        required_values = {
            "station":
                silver["station"],
            "observation_time_utc":
                silver["observation_time_utc"],
            "observation_time_bjt":
                silver["observation_time_bjt"],
            "business_date_bjt":
                silver["business_date_bjt"],
            "temperature_c":
                silver["temperature_c"],
            "source":
                silver["source"],
            "message_class":
                silver["message_class"],
            "qc_status":
                silver["qc_status"],
            "created_at_utc":
                silver["created_at_utc"],
        }

        for field, value in (
            required_values.items()
        ):

            if value is None:

                counters[
                    f"NULL_REQUIRED_{field.upper()}"
                ] += 1

    # ========================================================
    # Known Recovery verification
    # ========================================================

    actual_recovery_times = set()

    recovery_rows = []

    for row in silver_rows:

        if (
            row["recovery_reason"]
            == "IEM_MISSING"
        ):

            recovery_rows.append(
                row
            )

            dt = parse_iso_utc(
                row[
                    "observation_time_utc"
                ]
            )

            actual_recovery_times.add(
                dt.isoformat()
            )

    if (
        actual_recovery_times
        != EXPECTED_RECOVERY_TIMES
    ):

        hard_errors.append(
            "RECOVERY_TIMESTAMP_SET_MISMATCH"
        )

    # ========================================================
    # Convert counters into hard errors
    # ========================================================

    mismatch_keys = [
        "STATION_MISMATCH",
        "SOURCE_MISMATCH",
        "QUERY_CLASS_MISMATCH",
        "MESSAGE_CLASS_MISMATCH",
        "RECOVERY_REASON_MISMATCH",
        "TIME_RECOMPUTE_ERROR",
        "SILVER_UTC_PARSE_ERROR",
        "UTC_MISMATCH",
        "SILVER_BJT_PARSE_ERROR",
        "SILVER_BJT_NAIVE",
        "BJT_MISMATCH",
        "BUSINESS_DATE_MISMATCH",
        "REPARSE_FAILURE",
        "TEMPERATURE_MISMATCH",
        "DEWPOINT_MISMATCH",
        "CORRECTION_FLAG_MISMATCH",
        "CORRECTION_QC_FLAG_MISSING",
        "UNEXPECTED_SUPERSEDES_RAW_ID",
        "FALSE_CORRECTION_QC_FLAG",
        "RECOVERY_QC_FLAG_MISSING",
        "FALSE_RECOVERY_QC_FLAG",
        "INVALID_INITIAL_QC_STATUS",
    ]

    for key in mismatch_keys:

        if counters[key] != 0:

            hard_errors.append(
                key
            )

    if (
        counters["COR_ROWS"]
        != EXPECTED_COR_COUNT
    ):

        hard_errors.append(
            "UNEXPECTED_COR_COUNT"
        )

    if (
        counters["RECOVERY_ROWS"]
        != EXPECTED_RECOVERY_COUNT
    ):

        hard_errors.append(
            "UNEXPECTED_RECOVERY_COUNT"
        )

    # ========================================================
    # Output
    # ========================================================

    print()
    print("=" * 90)
    print("LINEAGE")
    print("=" * 90)

    print(
        f"Bronze Rows              : {len(bronze_rows)}"
    )

    print(
        f"Silver Rows              : {len(silver_rows)}"
    )

    print(
        f"Rows Checked             : {counters['ROWS_CHECKED']}"
    )

    print(
        f"Missing Silver           : {len(missing_silver_ids)}"
    )

    print(
        f"Orphan Silver            : {len(orphan_silver_ids)}"
    )

    print(
        f"Duplicate Bronze Lineage : {duplicate_lineage_count}"
    )

    print()
    print("=" * 90)
    print("TIME")
    print("=" * 90)

    print(
        f"UTC Mismatch             : {counters['UTC_MISMATCH']}"
    )

    print(
        f"BJT Mismatch             : {counters['BJT_MISMATCH']}"
    )

    print(
        "Business Date Mismatch   : "
        f"{counters['BUSINESS_DATE_MISMATCH']}"
    )

    print(
        f"BJT Naive                : {counters['SILVER_BJT_NAIVE']}"
    )

    print()
    print("=" * 90)
    print("METEOROLOGY")
    print("=" * 90)

    print(
        "Temperature Mismatch     : "
        f"{counters['TEMPERATURE_MISMATCH']}"
    )

    print(
        f"Dewpoint Mismatch        : {counters['DEWPOINT_MISMATCH']}"
    )

    print(
        f"Reparse Failure          : {counters['REPARSE_FAILURE']}"
    )

    print()
    print("=" * 90)
    print("COR / RECOVERY")
    print("=" * 90)

    print(
        f"COR Rows                 : {counters['COR_ROWS']}"
    )

    print(
        f"Recovery Rows            : {counters['RECOVERY_ROWS']}"
    )

    print(
        "Correction Flag Mismatch : "
        f"{counters['CORRECTION_FLAG_MISMATCH']}"
    )

    print(
        "Correction QC Missing    : "
        f"{counters['CORRECTION_QC_FLAG_MISSING']}"
    )

    print(
        "Recovery QC Missing      : "
        f"{counters['RECOVERY_QC_FLAG_MISSING']}"
    )

    print()
    print("Recovery Records:")

    for row in recovery_rows:

        print(
            "    "
            f"Bronze ID={row['bronze_raw_id']} | "
            f"UTC={row['observation_time_utc']} | "
            f"BJT={row['observation_time_bjt']} | "
            f"T={row['temperature_c']} | "
            f"Source={row['source']} | "
            f"Flags={row['qc_flags']}"
        )

    print()
    print("=" * 90)
    print("PROVENANCE")
    print("=" * 90)

    provenance_keys = [
        "STATION_MISMATCH",
        "SOURCE_MISMATCH",
        "QUERY_CLASS_MISMATCH",
        "MESSAGE_CLASS_MISMATCH",
        "RECOVERY_REASON_MISMATCH",
    ]

    for key in provenance_keys:

        print(
            f"{key:<30} : "
            f"{counters[key]}"
        )

    if issue_examples:

        print()
        print("=" * 90)
        print("ISSUE EXAMPLES")
        print("=" * 90)

        for item in issue_examples:

            print(item)

    # Deduplicate hard error labels.
    hard_errors = list(
        dict.fromkeys(
            hard_errors
        )
    )

    print()
    print("=" * 90)
    print("FINAL REPORT")
    print("=" * 90)

    print(
        f"Bronze Rows              : {len(bronze_rows)}"
    )

    print(
        f"Silver Rows              : {len(silver_rows)}"
    )

    print(
        f"Rows Checked             : {counters['ROWS_CHECKED']}"
    )

    print(
        f"Missing Silver Rows      : {len(missing_silver_ids)}"
    )

    print(
        f"Orphan Silver Rows       : {len(orphan_silver_ids)}"
    )

    print(
        f"Duplicate Lineage        : {duplicate_lineage_count}"
    )

    print(
        f"COR Rows                 : {counters['COR_ROWS']}"
    )

    print(
        f"Recovery Rows            : {counters['RECOVERY_ROWS']}"
    )

    print(
        f"Review Items             : {len(review_items)}"
    )

    print(
        f"Hard Errors              : {len(hard_errors)}"
    )

    if hard_errors:

        print()
        print("Hard Error Types:")

        for error in hard_errors:

            print(
                f"    {error}"
            )

    print("=" * 90)

    if hard_errors:

        print(
            "RESULT: SILVER FULL ARCHIVE AUDIT REVIEW REQUIRED"
        )

        return 2

    print(
        "RESULT: SILVER FULL ARCHIVE AUDIT PASS"
    )

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )