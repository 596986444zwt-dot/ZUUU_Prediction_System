from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "zuuu_prediction.db"

TABLE_NAME = "ecmwf_issue_rule_v1"

RULE_VERSION = "ECMWF_ISSUE_RULE_V1"
BACKTEST_RULE = "BACKTEST_ISSUE_RULE_V1"
REALTIME_RULE = "REALTIME_ISSUE_RULE_V1"

BACKTEST_ISSUE_HOUR_BJT = 21
TIMEZONE_NAME = "Asia/Shanghai"

KNOWN_GAP_DATE = "2025-08-07"
KNOWN_GAP_HORIZON = "T+2"
KNOWN_GAP_PRESENT_HOURS = 14
KNOWN_GAP_VALID_TEMP_HOURS = 14


RULE_PAYLOAD = {
    "rule_version": RULE_VERSION,

    "backtest": {
        "rule_name": BACKTEST_RULE,
        "timezone": TIMEZONE_NAME,
        "fixed_issue_time_bjt": "21:00",
        "selection_policy": (
            "For each T0/T+1/T+2 independently, "
            "select the newest canonical AVAILABLE ECMWF run "
            "whose source_available_time_utc is not later than "
            "the historical issue cutoff and which provides "
            "24/24 target-day hours with 24/24 valid "
            "temperature_2m values."
        ),
        "fallback_policy": (
            "If the newest legally available run does not provide "
            "complete target-day coverage, search backward through "
            "older legally available canonical runs until the newest "
            "complete run is found."
        ),
        "no_lookahead": True,
        "no_fabrication": True,
    },

    "realtime": {
        "rule_name": REALTIME_RULE,
        "issue_time": "NOW",
        "selection_policy": (
            "For each T0/T+1/T+2 independently, "
            "select the newest canonical AVAILABLE ECMWF run "
            "whose source_available_time_utc is not later than NOW "
            "and which provides 24/24 target-day hours with "
            "24/24 valid temperature_2m values."
        ),
        "fallback_policy": (
            "If the newest legally available run is incomplete for "
            "a horizon, search backward for the newest complete legal run."
        ),
        "different_horizons_may_use_different_runs": True,
        "refresh_on_new_ecmwf_arrival": True,
        "no_lookahead": True,
        "no_fabrication": True,
    },

    "historical_audit": {
        "target_days": 729,

        "T0": {
            "complete": 729,
            "partial": 0,
            "no_run": 0,
            "leakage": 0,
        },

        "T+1": {
            "complete": 729,
            "partial": 0,
            "no_run": 0,
            "leakage": 0,
        },

        "T+2": {
            "complete": 728,
            "partial": 1,
            "no_run": 0,
            "leakage": 0,
        },

        "known_gap": {
            "business_date_bjt": KNOWN_GAP_DATE,
            "horizon": KNOWN_GAP_HORIZON,
            "present_hours": KNOWN_GAP_PRESENT_HOURS,
            "valid_temperature_hours": KNOWN_GAP_VALID_TEMP_HOURS,
            "policy": "EXCLUDE_FROM_TPLUS2_BACKTEST_SAMPLE",
        },

        "hard_errors": 0,
    },

    "availability_semantics": {
        "historical": (
            "Historical official_schedule_estimate remains an "
            "estimated dissemination time and is not represented "
            "as observed historical ingest time."
        ),
        "realtime": (
            "Observed ingest availability may be used when actually observed."
        ),
    },

    "frozen_dependencies": {
        "ground_truth": "ZUUU_TARGET_V1",
        "ecmwf_archive": "ECMWF_ARCHIVE_V1",
    },
}


def semantic_json() -> str:
    return json.dumps(
        RULE_PAYLOAD,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def semantic_sha256() -> str:
    return hashlib.sha256(
        semantic_json().encode("utf-8")
    ).hexdigest()


def connect():
    return sqlite3.connect(DB_PATH)


def create_table(conn):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            rule_version TEXT NOT NULL UNIQUE,

            backtest_rule_name TEXT NOT NULL,
            realtime_rule_name TEXT NOT NULL,

            timezone_name TEXT NOT NULL,
            backtest_issue_hour_bjt INTEGER NOT NULL,

            rule_payload_json TEXT NOT NULL,
            semantic_sha256 TEXT NOT NULL,

            rule_status TEXT NOT NULL,
            frozen_at_utc TEXT NOT NULL,

            CHECK(rule_status = 'FROZEN')
        )
        """
    )


def check_dependencies(conn):
    errors = []

    # --------------------------------------------------------
    # ZUUU_TARGET_V1
    # --------------------------------------------------------
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                MIN(business_date_bjt),
                MAX(business_date_bjt)
            FROM zuuu_target_v1
            WHERE target_status = 'FROZEN'
            """
        ).fetchone()

        if row[0] != 729:
            errors.append(
                f"ZUUU_TARGET_V1 expected 729 rows, got {row[0]}"
            )

        if row[1] != "2024-09-03":
            errors.append(
                f"Unexpected target start date: {row[1]}"
            )

        if row[2] != "2026-09-01":
            errors.append(
                f"Unexpected target end date: {row[2]}"
            )

    except sqlite3.Error as exc:
        errors.append(
            f"ZUUU_TARGET_V1 dependency error: {exc}"
        )

    # --------------------------------------------------------
    # ECMWF_ARCHIVE_V1
    # --------------------------------------------------------
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM ecmwf_archive_v1
            WHERE archive_status = 'FROZEN'
            """
        ).fetchone()

        if row[0] != 3411:
            errors.append(
                f"ECMWF_ARCHIVE_V1 expected 3411 rows, got {row[0]}"
            )

    except sqlite3.Error as exc:
        errors.append(
            f"ECMWF_ARCHIVE_V1 dependency error: {exc}"
        )

    return errors


def existing_rule_count(conn):
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {TABLE_NAME}
            WHERE rule_version = ?
            """,
            (RULE_VERSION,),
        ).fetchone()

        return row[0]

    except sqlite3.OperationalError:
        return 0


def print_rule():
    print("=" * 90)
    print("ECMWF ISSUE RULE V1 FREEZER")
    print("=" * 90)

    print(f"Database              : {DB_PATH}")
    print(f"Rule Version          : {RULE_VERSION}")
    print(f"Backtest Rule         : {BACKTEST_RULE}")
    print(f"Realtime Rule         : {REALTIME_RULE}")
    print(f"Timezone              : {TIMEZONE_NAME}")
    print(
        f"Backtest Snapshot     : "
        f"{BACKTEST_ISSUE_HOUR_BJT:02d}:00 BJT"
    )

    print()
    print("Historical Audit:")
    print("    T0   : 729/729")
    print("    T+1  : 729/729")
    print("    T+2  : 728/729")
    print("    Leakage Errors : 0")
    print("    Hard Errors    : 0")

    print()
    print("Known Historical Gap:")
    print(
        f"    Date        : {KNOWN_GAP_DATE}"
    )
    print(
        f"    Horizon     : {KNOWN_GAP_HORIZON}"
    )
    print(
        f"    Coverage    : "
        f"{KNOWN_GAP_PRESENT_HOURS}/24"
    )
    print(
        f"    Valid Temp  : "
        f"{KNOWN_GAP_VALID_TEMP_HOURS}/24"
    )
    print(
        "    Policy      : "
        "EXCLUDE_FROM_TPLUS2_BACKTEST_SAMPLE"
    )

    print()
    print("Realtime:")
    print("    issue_time = NOW")
    print(
        "    latest legal + complete canonical run"
    )
    print(
        "    each horizon selected independently"
    )
    print(
        "    fallback backward if newest run incomplete"
    )
    print("    no fabrication")
    print("    no lookahead")

    print()
    print(
        f"Semantic SHA256       : {semantic_sha256()}"
    )


def dry_run():
    conn = connect()

    try:
        print_rule()

        errors = check_dependencies(conn)

        exists = existing_rule_count(conn)

        print()
        print(f"Existing Rule Rows    : {exists}")
        print(
            f"Dependency Errors     : {len(errors)}"
        )

        if errors:
            for error in errors:
                print(f"ERROR: {error}")

        print("Database Modification : NONE")
        print()

        if exists:
            print(
                "RESULT: REFUSED - RULE VERSION ALREADY EXISTS"
            )
            return 1

        if errors:
            print(
                "RESULT: ISSUE RULE V1 FREEZE DRY-RUN FAILED"
            )
            return 1

        print(
            "RESULT: ISSUE RULE V1 FREEZE DRY-RUN PASS"
        )
        print(
            "READY FOR --commit"
        )

        return 0

    finally:
        conn.close()


def commit():
    conn = connect()

    try:
        conn.execute("BEGIN IMMEDIATE")

        create_table(conn)

        errors = check_dependencies(conn)

        if errors:
            raise RuntimeError(
                "Dependency validation failed: "
                + " | ".join(errors)
            )

        exists = existing_rule_count(conn)

        if exists:
            raise RuntimeError(
                f"{RULE_VERSION} already exists. "
                "Frozen rules must never be overwritten."
            )

        frozen_at = datetime.now(
            timezone.utc
        ).isoformat()

        payload = semantic_json()
        sha = semantic_sha256()

        # Plain INSERT only.
        # No REPLACE / UPSERT / IGNORE.
        conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                rule_version,
                backtest_rule_name,
                realtime_rule_name,
                timezone_name,
                backtest_issue_hour_bjt,
                rule_payload_json,
                semantic_sha256,
                rule_status,
                frozen_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                RULE_VERSION,
                BACKTEST_RULE,
                REALTIME_RULE,
                TIMEZONE_NAME,
                BACKTEST_ISSUE_HOUR_BJT,
                payload,
                sha,
                "FROZEN",
                frozen_at,
            ),
        )

        # ----------------------------------------------------
        # POST-FREEZE AUDIT BEFORE COMMIT
        # ----------------------------------------------------
        row = conn.execute(
            f"""
            SELECT
                rule_version,
                backtest_rule_name,
                realtime_rule_name,
                timezone_name,
                backtest_issue_hour_bjt,
                rule_payload_json,
                semantic_sha256,
                rule_status,
                frozen_at_utc
            FROM {TABLE_NAME}
            WHERE rule_version = ?
            """,
            (RULE_VERSION,),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "Post-freeze row missing."
            )

        actual_payload = row[5]

        actual_sha = hashlib.sha256(
            actual_payload.encode("utf-8")
        ).hexdigest()

        errors = []

        if row[0] != RULE_VERSION:
            errors.append("rule_version mismatch")

        if row[1] != BACKTEST_RULE:
            errors.append("backtest rule mismatch")

        if row[2] != REALTIME_RULE:
            errors.append("realtime rule mismatch")

        if row[3] != TIMEZONE_NAME:
            errors.append("timezone mismatch")

        if row[4] != BACKTEST_ISSUE_HOUR_BJT:
            errors.append("issue hour mismatch")

        if row[6] != sha:
            errors.append(
                "stored semantic SHA mismatch"
            )

        if actual_sha != sha:
            errors.append(
                "payload semantic SHA mismatch"
            )

        if row[7] != "FROZEN":
            errors.append("status mismatch")

        if errors:
            raise RuntimeError(
                "Post-freeze audit failed: "
                + " | ".join(errors)
            )

        conn.commit()

        print_rule()

        print()
        print("=" * 90)
        print("POST-FREEZE AUDIT")
        print("=" * 90)

        print("Inserted Rows          : 1")
        print(f"Rule Version           : {row[0]}")
        print(f"Rule Status            : {row[7]}")
        print(f"Frozen At UTC          : {row[8]}")
        print(f"Expected SHA256        : {sha}")
        print(f"Actual SHA256          : {actual_sha}")
        print(
            f"SHA256 Match           : "
            f"{actual_sha == sha}"
        )
        print("Post-Freeze Errors     : 0")

        print()
        print(
            "SOURCE TABLES MODIFIED : NO"
        )
        print(
            "ZUUU_TARGET_V1 MODIFIED: NO"
        )
        print(
            "ECMWF_ARCHIVE_V1 MODIFIED: NO"
        )

        print()
        print(
            "RESULT: ECMWF_ISSUE_RULE_V1 FREEZE COMPLETE"
        )

        return 0

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--dry-run",
        action="store_true"
    )

    group.add_argument(
        "--commit",
        action="store_true"
    )

    args = parser.parse_args()

    if args.dry_run:
        raise SystemExit(dry_run())

    if args.commit:
        raise SystemExit(commit())


if __name__ == "__main__":
    main()