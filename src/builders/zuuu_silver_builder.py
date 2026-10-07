"""
ZUUU Silver Observation Builder V1.0
====================================

Purpose:
    Build structured Silver observations from Bronze Raw archive.

Data flow:
    zuuu_raw_metar
        ->
    Temperature Parser
        ->
    Time Normalizer
        ->
    Initial QC / Provenance
        ->
    zuuu_silver_observation

Principles:
    1. Bronze is read-only.
    2. Raw METAR is never modified.
    3. Silver is rebuildable.
    4. Every Silver row must trace back to bronze_raw_id.
    5. COR is retained and explicitly flagged.
    6. Recovery provenance is retained.
    7. Parser failures are not silently ignored.
    8. No network access.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH
from src.database.zuuu_silver_archive import ZUUUSilverArchive

from src.parsers.zuuu_metar_temperature_parser import (
    parse_temperature,
)

from src.parsers.zuuu_time_normalizer import (
    normalize_observation_time,
)


UTC = timezone.utc


# ============================================================
# Helpers
# ============================================================

def parse_iso_datetime(
    value: str,
) -> datetime:
    """
    Parse Bronze observation_time_utc.

    Bronze time must already be timezone-aware.
    """

    dt = datetime.fromisoformat(
        value
    )

    if dt.tzinfo is None:
        raise ValueError(
            "NAIVE_BRONZE_OBSERVATION_TIME"
        )

    return dt.astimezone(UTC)


def build_initial_qc(
    *,
    message_class: str,
    recovery_reason: str | None,
) -> tuple[str, str | None]:
    """
    Initial Silver QC.

    Important:
        This is NOT the final Ground Truth QC.

    PASS:
        Structurally valid observation.

    Flags:
        CORRECTION
        RECOVERY_SOURCE

    Multiple flags are stored with "|" separator.
    """

    flags: list[str] = []

    if message_class == "COR":
        flags.append(
            "CORRECTION"
        )

    if recovery_reason:
        flags.append(
            "RECOVERY_SOURCE"
        )

    qc_status = "PASS"

    qc_flags = (
        "|".join(flags)
        if flags
        else None
    )

    return (
        qc_status,
        qc_flags,
    )


# ============================================================
# Bronze Reader
# ============================================================

def read_bronze_rows():
    """
    Read complete Bronze archive.

    READ ONLY.
    """

    conn = sqlite3.connect(
        DEFAULT_DB_PATH
    )

    conn.row_factory = sqlite3.Row

    try:

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
            ORDER BY
                observation_time_utc,
                id
            """
        ).fetchall()

        return rows

    finally:

        conn.close()


# ============================================================
# Builder
# ============================================================

def build_silver(
    *,
    commit: bool,
) -> int:

    mode = (
        "COMMIT"
        if commit
        else "DRY-RUN"
    )

    print("=" * 78)
    print(
        "ZUUU Silver Observation Builder V1.0"
    )
    print("=" * 78)

    print(
        f"Mode     : {mode}"
    )

    print(
        f"Database : {DEFAULT_DB_PATH}"
    )

    print("=" * 78)

    bronze_rows = (
        read_bronze_rows()
    )

    print(
        f"Bronze Rows : {len(bronze_rows)}"
    )

    if not bronze_rows:

        print()
        print(
            "RESULT: NO BRONZE DATA"
        )

        return 2

    archive = ZUUUSilverArchive(
        DEFAULT_DB_PATH
    )

    # Only initialize/write Silver table in COMMIT mode.
    if commit:
        archive.initialize()

    parser_success = 0
    parser_failed = 0

    time_success = 0
    time_failed = 0

    inserted = 0
    already_existing = 0

    message_classes = Counter()
    sources = Counter()
    query_classes = Counter()
    qc_flags_counter = Counter()

    parser_errors = Counter()
    time_errors = Counter()

    failure_examples = []

    expected_silver_records = 0

    # --------------------------------------------------------
    # Process Bronze
    # --------------------------------------------------------

    for row in bronze_rows:

        bronze_raw_id = int(
            row["id"]
        )

        station = row["station"]

        source = row["source"]

        source_query_class = (
            row["source_query_class"]
        )

        message_class = (
            row["message_class"]
        )

        observation_time_raw = (
            row["observation_time_utc"]
        )

        raw_metar = (
            row["raw_metar"]
        )

        recovery_reason = (
            row["recovery_reason"]
        )

        sources[source] += 1

        message_classes[
            message_class
        ] += 1

        query_classes[
            source_query_class
            if source_query_class is not None
            else "NULL"
        ] += 1

        # ----------------------------------------------------
        # Temperature / Dew Point
        # ----------------------------------------------------

        temp_result = (
            parse_temperature(
                raw_metar
            )
        )

        if not temp_result.success:

            parser_failed += 1

            parser_errors[
                temp_result.error
                or "UNKNOWN_PARSER_ERROR"
            ] += 1

            if len(failure_examples) < 20:

                failure_examples.append(
                    (
                        bronze_raw_id,
                        "PARSER",
                        temp_result.error,
                        raw_metar,
                    )
                )

            continue

        parser_success += 1

        # ----------------------------------------------------
        # Time
        # ----------------------------------------------------

        try:

            observation_dt = (
                parse_iso_datetime(
                    observation_time_raw
                )
            )

            normalized_time = (
                normalize_observation_time(
                    observation_dt
                )
            )

        except Exception as exc:

            time_failed += 1

            error_name = (
                f"{type(exc).__name__}:"
                f"{exc}"
            )

            time_errors[
                error_name
            ] += 1

            if len(failure_examples) < 20:

                failure_examples.append(
                    (
                        bronze_raw_id,
                        "TIME",
                        error_name,
                        raw_metar,
                    )
                )

            continue

        time_success += 1

        # ----------------------------------------------------
        # Initial QC
        # ----------------------------------------------------

        qc_status, qc_flags = (
            build_initial_qc(
                message_class=message_class,
                recovery_reason=recovery_reason,
            )
        )

        if qc_flags:

            for flag in qc_flags.split("|"):
                qc_flags_counter[
                    flag
                ] += 1

        is_correction = (
            message_class == "COR"
        )

        # We currently do not have evidence linking
        # these COR records to an archived original Raw.
        supersedes_raw_id = None

        expected_silver_records += 1

        # ----------------------------------------------------
        # Dry Run
        # ----------------------------------------------------

        if not commit:
            continue

        # ----------------------------------------------------
        # Commit
        # ----------------------------------------------------

        created_at_utc = (
            datetime.now(
                UTC
            ).isoformat()
        )

        was_inserted = (
            archive.insert(
                bronze_raw_id=bronze_raw_id,
                station=station,
                observation_time_utc=(
                    normalized_time
                    .observation_time_utc
                    .isoformat()
                ),
                observation_time_bjt=(
                    normalized_time
                    .observation_time_bjt
                    .isoformat()
                ),
                business_date_bjt=(
                    normalized_time
                    .business_date_bjt
                    .isoformat()
                ),
                temperature_c=(
                    temp_result.temperature_c
                ),
                dewpoint_c=(
                    temp_result.dewpoint_c
                ),
                source=source,
                source_query_class=(
                    source_query_class
                ),
                message_class=(
                    message_class
                ),
                is_correction=(
                    is_correction
                ),
                supersedes_raw_id=(
                    supersedes_raw_id
                ),
                recovery_reason=(
                    recovery_reason
                ),
                qc_status=(
                    qc_status
                ),
                qc_flags=(
                    qc_flags
                ),
                created_at_utc=(
                    created_at_utc
                ),
            )
        )

        if was_inserted:
            inserted += 1
        else:
            already_existing += 1

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    print()
    print("=" * 78)
    print("PARSER")
    print("=" * 78)

    print(
        f"Success : {parser_success}"
    )

    print(
        f"Failed  : {parser_failed}"
    )

    if parser_errors:

        print()
        print("Parser Errors:")

        for key, count in sorted(
            parser_errors.items()
        ):

            print(
                f"    {key:<45} : {count}"
            )

    print()
    print("=" * 78)
    print("TIME NORMALIZATION")
    print("=" * 78)

    print(
        f"Success : {time_success}"
    )

    print(
        f"Failed  : {time_failed}"
    )

    if time_errors:

        print()
        print("Time Errors:")

        for key, count in sorted(
            time_errors.items()
        ):

            print(
                f"    {key:<45} : {count}"
            )

    print()
    print("=" * 78)
    print("SOURCE")
    print("=" * 78)

    for key, count in sorted(
        sources.items()
    ):

        print(
            f"{key:<20} : {count}"
        )

    print()
    print("=" * 78)
    print("SOURCE QUERY CLASS")
    print("=" * 78)

    for key, count in sorted(
        query_classes.items()
    ):

        print(
            f"{key:<20} : {count}"
        )

    print()
    print("=" * 78)
    print("MESSAGE CLASS")
    print("=" * 78)

    for key, count in sorted(
        message_classes.items()
    ):

        print(
            f"{key:<20} : {count}"
        )

    print()
    print("=" * 78)
    print("INITIAL QC FLAGS")
    print("=" * 78)

    if qc_flags_counter:

        for key, count in sorted(
            qc_flags_counter.items()
        ):

            print(
                f"{key:<20} : {count}"
            )

    else:

        print("NONE")

    if failure_examples:

        print()
        print("=" * 78)
        print("FAILURE EXAMPLES")
        print("=" * 78)

        for (
            raw_id,
            stage,
            error,
            raw,
        ) in failure_examples:

            print()
            print(
                f"Bronze ID : {raw_id}"
            )

            print(
                f"Stage     : {stage}"
            )

            print(
                f"Error     : {error}"
            )

            print(
                f"Raw       : {raw}"
            )

    # --------------------------------------------------------
    # Final counts
    # --------------------------------------------------------

    silver_total = None

    if commit:
        silver_total = (
            archive.count()
        )

    print()
    print("=" * 78)
    print("FINAL REPORT")
    print("=" * 78)

    print(
        f"Mode                    : {mode}"
    )

    print(
        f"Bronze Rows             : {len(bronze_rows)}"
    )

    print(
        f"Parser Success          : {parser_success}"
    )

    print(
        f"Parser Failed           : {parser_failed}"
    )

    print(
        f"Time Success            : {time_success}"
    )

    print(
        f"Time Failed             : {time_failed}"
    )

    print(
        "Expected Silver Records : "
        f"{expected_silver_records}"
    )

    if commit:

        print(
            f"Inserted                : {inserted}"
        )

        print(
            "Already Existing        : "
            f"{already_existing}"
        )

        print(
            f"Silver Database Total   : {silver_total}"
        )

    # --------------------------------------------------------
    # Hard validation
    # --------------------------------------------------------

    hard_errors = 0

    if parser_failed != 0:
        hard_errors += 1

    if time_failed != 0:
        hard_errors += 1

    if (
        expected_silver_records
        != len(bronze_rows)
    ):
        hard_errors += 1

    if commit:

        if (
            silver_total
            != len(bronze_rows)
        ):
            hard_errors += 1

    print(
        f"Hard Errors             : {hard_errors}"
    )

    print("=" * 78)

    if hard_errors:

        print(
            "RESULT: SILVER BUILD REVIEW REQUIRED"
        )

        return 2

    if commit:

        print(
            "RESULT: SILVER BUILD COMPLETE"
        )

    else:

        print(
            "RESULT: SILVER DRY-RUN PASS"
        )

    return 0


# ============================================================
# CLI
# ============================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Build ZUUU Silver observations "
            "from Bronze archive."
        )
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Parse and validate all Bronze rows "
            "without writing Silver."
        ),
    )

    mode.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Build Silver observations."
        ),
    )

    args = parser.parse_args()

    return build_silver(
        commit=args.commit
    )


if __name__ == "__main__":

    raise SystemExit(
        main()
    )