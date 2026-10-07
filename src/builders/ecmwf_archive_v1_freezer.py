"""
ECMWF ARCHIVE V1 FREEZER
========================

Phase 2 Canonical Archive Manifest V1

原则：
1. 不修改 ecmwf_raw_runs
2. 不修改 ecmwf_hourly_forecasts
3. 不删除重复 Raw Snapshot
4. 同一 run_time 的完全相同内容只选择一个 canonical raw_run
5. 不同内容/规格并存时优先 ECMWF_18_VARIABLE_V2
6. temperature_2m 全 NULL 的 Run 标记 SOURCE_TEMPERATURE_UNAVAILABLE
7. historical official_schedule_estimate 只保存 provenance，
   不宣称它是真实 observed historical availability
8. Plain INSERT，禁止 REPLACE / UPSERT
9. 支持 --dry-run / --commit
10. 冻结后计算 deterministic SHA256
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "zuuu_prediction.db"

TARGET_TABLE = "ecmwf_archive_v1"

ARCHIVE_VERSION = "ECMWF_ARCHIVE_V1"
CANONICAL_RULE_VERSION = "ECMWF_CANONICAL_RULE_V1"

PREFERRED_SPEC = "ECMWF_18_VARIABLE_V2"

WIDTH = 110


def hr(char="="):
    print(char * WIDTH)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def connect_rw():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(conn, table_name):
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name=?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def create_table(conn):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            run_time_utc TEXT NOT NULL UNIQUE,

            canonical_raw_run_id INTEGER,

            model TEXT,
            api_model TEXT,
            model_cycle TEXT,

            archive_type TEXT,
            data_spec_version TEXT,
            collector_version TEXT,

            source_available_time_utc TEXT,
            availability_type TEXT,
            availability_rule_version TEXT,

            ingest_time_utc TEXT,

            requested_latitude REAL,
            requested_longitude REAL,

            content_sha256 TEXT,

            raw_snapshot_count INTEGER NOT NULL,
            distinct_content_count INTEGER NOT NULL,

            hourly_row_count INTEGER NOT NULL,
            temperature_valid_count INTEGER NOT NULL,
            temperature_null_count INTEGER NOT NULL,

            canonical_status TEXT NOT NULL,

            availability_semantics TEXT NOT NULL,

            canonical_rule_version TEXT NOT NULL,
            archive_version TEXT NOT NULL,
            archive_status TEXT NOT NULL,

            frozen_at_utc TEXT NOT NULL,

            FOREIGN KEY(canonical_raw_run_id)
                REFERENCES ecmwf_raw_runs(id)
        )
        """
    )

    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_status
        ON {TARGET_TABLE}(canonical_status)
        """
    )

    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TARGET_TABLE}_raw
        ON {TARGET_TABLE}(canonical_raw_run_id)
        """
    )


def raw_rows(conn):
    return conn.execute(
        """
        SELECT
            id,
            model,
            api_model,
            model_cycle,
            run_time_utc,
            source_available_time_utc,
            availability_type,
            ingest_time_utc,
            archive_type,
            data_spec_version,
            collector_version,
            availability_rule_version,
            requested_latitude,
            requested_longitude,
            content_sha256
        FROM ecmwf_raw_runs
        ORDER BY run_time_utc, id
        """
    ).fetchall()


def hourly_stats(conn):
    rows = conn.execute(
        """
        SELECT
            raw_run_id,
            COUNT(*) AS row_count,
            SUM(
                CASE WHEN temperature_2m_c IS NULL
                THEN 1 ELSE 0 END
            ) AS null_count,
            SUM(
                CASE WHEN temperature_2m_c IS NOT NULL
                THEN 1 ELSE 0 END
            ) AS valid_count
        FROM ecmwf_hourly_forecasts
        GROUP BY raw_run_id
        """
    ).fetchall()

    return {
        row["raw_run_id"]: {
            "row_count": row["row_count"],
            "null_count": row["null_count"],
            "valid_count": row["valid_count"],
        }
        for row in rows
    }


def rank_snapshot(row, stats):
    """
    数值越小优先级越高。

    先保证：
    1. 温度可用
    2. V2 spec
    3. ecmwf_ifs
    4. 较新的 collector
    5. 最小 raw id 作为 deterministic tie-break
    """

    st = stats.get(
        row["id"],
        {
            "row_count": 0,
            "null_count": 0,
            "valid_count": 0,
        },
    )

    temperature_penalty = (
        0 if st["valid_count"] > 0 else 1
    )

    spec_penalty = (
        0
        if row["data_spec_version"] == PREFERRED_SPEC
        else 1
    )

    api_penalty = (
        0
        if row["api_model"] == "ecmwf_ifs"
        else 1
    )

    collector = row["collector_version"] or ""

    collector_penalty = {
        "ECMWF_HISTORICAL_BACKFILL_V3_2": 0,
        "ECMWF_HISTORICAL_BACKFILL_V3_1": 1,
        "ECMWF_HISTORICAL_COLLECTOR_V1_1": 2,
        "ECMWF_HISTORICAL_BATCH_COLLECTOR_V2": 3,
        "ECMWF_BRONZE_RECOVERY_V1": 4,
        "ECMWF_IFS_COLLECTOR_V2": 0,
        "ECMWF_IFS_COLLECTOR_V1": 5,
    }.get(collector, 10)

    return (
        temperature_penalty,
        spec_penalty,
        api_penalty,
        collector_penalty,
        row["id"],
    )


def choose_canonical(rows, stats):
    return sorted(
        rows,
        key=lambda x: rank_snapshot(x, stats),
    )[0]


def availability_semantics(row):
    if row["availability_type"] == "observed_ingest_time":
        return "OBSERVED_INGEST_TIME"

    if row["availability_type"] == "official_schedule_estimate":
        return "ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED"

    return "UNRESOLVED_AVAILABILITY_SEMANTICS"


def canonical_status(row, stat):
    if stat["row_count"] == 0:
        return "NO_HOURLY_DATA"

    if stat["valid_count"] == 0:
        return "SOURCE_TEMPERATURE_UNAVAILABLE"

    if stat["null_count"] > 0:
        return "PARTIAL_TEMPERATURE_DATA"

    return "AVAILABLE"


def build_manifest(conn):
    raws = raw_rows(conn)
    stats = hourly_stats(conn)

    grouped = defaultdict(list)

    for row in raws:
        grouped[row["run_time_utc"]].append(row)

    manifest = []

    for run_time in sorted(grouped):
        snapshots = grouped[run_time]

        canonical = choose_canonical(
            snapshots,
            stats,
        )

        st = stats.get(
            canonical["id"],
            {
                "row_count": 0,
                "null_count": 0,
                "valid_count": 0,
            },
        )

        hashes = {
            row["content_sha256"]
            for row in snapshots
            if row["content_sha256"]
        }

        manifest.append(
            {
                "run_time_utc":
                    run_time,

                "canonical_raw_run_id":
                    canonical["id"],

                "model":
                    canonical["model"],

                "api_model":
                    canonical["api_model"],

                "model_cycle":
                    canonical["model_cycle"],

                "archive_type":
                    canonical["archive_type"],

                "data_spec_version":
                    canonical["data_spec_version"],

                "collector_version":
                    canonical["collector_version"],

                "source_available_time_utc":
                    canonical["source_available_time_utc"],

                "availability_type":
                    canonical["availability_type"],

                "availability_rule_version":
                    canonical["availability_rule_version"],

                "ingest_time_utc":
                    canonical["ingest_time_utc"],

                "requested_latitude":
                    canonical["requested_latitude"],

                "requested_longitude":
                    canonical["requested_longitude"],

                "content_sha256":
                    canonical["content_sha256"],

                "raw_snapshot_count":
                    len(snapshots),

                "distinct_content_count":
                    len(hashes),

                "hourly_row_count":
                    st["row_count"],

                "temperature_valid_count":
                    st["valid_count"],

                "temperature_null_count":
                    st["null_count"],

                "canonical_status":
                    canonical_status(
                        canonical,
                        st,
                    ),

                "availability_semantics":
                    availability_semantics(
                        canonical
                    ),
            }
        )

    return manifest


def semantic_hash(manifest):
    """
    不包含：
    - DB local manifest ID
    - frozen timestamp

    包含真正决定 Archive V1 语义的字段。
    """

    semantic_rows = []

    fields = [
        "run_time_utc",
        "canonical_raw_run_id",
        "model",
        "api_model",
        "model_cycle",
        "archive_type",
        "data_spec_version",
        "collector_version",
        "source_available_time_utc",
        "availability_type",
        "availability_rule_version",
        "requested_latitude",
        "requested_longitude",
        "content_sha256",
        "raw_snapshot_count",
        "distinct_content_count",
        "hourly_row_count",
        "temperature_valid_count",
        "temperature_null_count",
        "canonical_status",
        "availability_semantics",
    ]

    for row in manifest:
        semantic_rows.append(
            {
                field: row.get(field)
                for field in fields
            }
        )

    blob = json.dumps(
        semantic_rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(blob).hexdigest()


def print_summary(manifest):
    statuses = defaultdict(int)
    availability = defaultdict(int)

    multi_snapshot = 0
    different_content = 0

    for row in manifest:
        statuses[
            row["canonical_status"]
        ] += 1

        availability[
            row["availability_semantics"]
        ] += 1

        if row["raw_snapshot_count"] > 1:
            multi_snapshot += 1

        if row["distinct_content_count"] > 1:
            different_content += 1

    print(f"Canonical Runs          : {len(manifest)}")

    if manifest:
        print(
            f"First Run               : "
            f"{manifest[0]['run_time_utc']}"
        )
        print(
            f"Last Run                : "
            f"{manifest[-1]['run_time_utc']}"
        )

    print(
        f"Multi-Snapshot Runs     : "
        f"{multi_snapshot}"
    )

    print(
        f"Different-Content Runs  : "
        f"{different_content}"
    )

    print()
    print("CANONICAL STATUS")

    for key in sorted(statuses):
        print(
            f"    {key:<40} "
            f"{statuses[key]}"
        )

    print()
    print("AVAILABILITY SEMANTICS")

    for key in sorted(availability):
        print(
            f"    {key:<45} "
            f"{availability[key]}"
        )

    print()
    print("SOURCE TEMPERATURE UNAVAILABLE")

    missing = [
        x
        for x in manifest
        if x["canonical_status"]
        == "SOURCE_TEMPERATURE_UNAVAILABLE"
    ]

    for row in missing:
        print(
            f"    {row['run_time_utc']} "
            f"| raw_id={row['canonical_raw_run_id']} "
            f"| rows={row['hourly_row_count']} "
            f"| null={row['temperature_null_count']}"
        )

    print(
        f"    TOTAL = {len(missing)}"
    )

    print()
    print("MULTI-SNAPSHOT CANONICAL SELECTION")

    for row in manifest:
        if row["raw_snapshot_count"] > 1:
            print(
                f"    {row['run_time_utc']} "
                f"| snapshots={row['raw_snapshot_count']} "
                f"| distinct_content={row['distinct_content_count']} "
                f"| selected_raw={row['canonical_raw_run_id']} "
                f"| spec={row['data_spec_version']} "
                f"| collector={row['collector_version']}"
            )


def validate_manifest(manifest):
    errors = []

    if not manifest:
        errors.append("MANIFEST_EMPTY")
        return errors

    run_times = [
        row["run_time_utc"]
        for row in manifest
    ]

    if len(run_times) != len(set(run_times)):
        errors.append(
            "DUPLICATE_CANONICAL_RUN_TIME"
        )

    for row in manifest:
        if row["canonical_raw_run_id"] is None:
            errors.append(
                f"NO_CANONICAL_RAW:"
                f"{row['run_time_utc']}"
            )

        if row["canonical_status"] == "AVAILABLE":
            if row["temperature_valid_count"] <= 0:
                errors.append(
                    f"AVAILABLE_WITHOUT_TEMP:"
                    f"{row['run_time_utc']}"
                )

        if (
            row["canonical_status"]
            == "SOURCE_TEMPERATURE_UNAVAILABLE"
            and row["temperature_valid_count"] != 0
        ):
            errors.append(
                f"MISSING_STATUS_WITH_VALID_TEMP:"
                f"{row['run_time_utc']}"
            )

    return errors


def existing_count(conn):
    if not table_exists(
        conn,
        TARGET_TABLE,
    ):
        return 0

    return conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {TARGET_TABLE}
        """
    ).fetchone()[0]


def commit_manifest(conn, manifest):
    if existing_count(conn) != 0:
        raise RuntimeError(
            f"{TARGET_TABLE} already contains rows. "
            "Refusing to overwrite frozen archive."
        )

    frozen_at = utc_now()

    sql = f"""
        INSERT INTO {TARGET_TABLE} (
            run_time_utc,
            canonical_raw_run_id,
            model,
            api_model,
            model_cycle,
            archive_type,
            data_spec_version,
            collector_version,
            source_available_time_utc,
            availability_type,
            availability_rule_version,
            ingest_time_utc,
            requested_latitude,
            requested_longitude,
            content_sha256,
            raw_snapshot_count,
            distinct_content_count,
            hourly_row_count,
            temperature_valid_count,
            temperature_null_count,
            canonical_status,
            availability_semantics,
            canonical_rule_version,
            archive_version,
            archive_status,
            frozen_at_utc
        )
        VALUES (
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?
        )
    """

    for row in manifest:
        conn.execute(
            sql,
            (
                row["run_time_utc"],
                row["canonical_raw_run_id"],
                row["model"],
                row["api_model"],
                row["model_cycle"],
                row["archive_type"],
                row["data_spec_version"],
                row["collector_version"],
                row["source_available_time_utc"],
                row["availability_type"],
                row["availability_rule_version"],
                row["ingest_time_utc"],
                row["requested_latitude"],
                row["requested_longitude"],
                row["content_sha256"],
                row["raw_snapshot_count"],
                row["distinct_content_count"],
                row["hourly_row_count"],
                row["temperature_valid_count"],
                row["temperature_null_count"],
                row["canonical_status"],
                row["availability_semantics"],
                CANONICAL_RULE_VERSION,
                ARCHIVE_VERSION,
                "FROZEN",
                frozen_at,
            ),
        )

    return frozen_at


def post_commit_audit(conn, expected_manifest, expected_hash):
    rows = conn.execute(
        f"""
        SELECT *
        FROM {TARGET_TABLE}
        ORDER BY run_time_utc
        """
    ).fetchall()

    reconstructed = []

    for row in rows:
        reconstructed.append(
            {
                "run_time_utc":
                    row["run_time_utc"],
                "canonical_raw_run_id":
                    row["canonical_raw_run_id"],
                "model":
                    row["model"],
                "api_model":
                    row["api_model"],
                "model_cycle":
                    row["model_cycle"],
                "archive_type":
                    row["archive_type"],
                "data_spec_version":
                    row["data_spec_version"],
                "collector_version":
                    row["collector_version"],
                "source_available_time_utc":
                    row["source_available_time_utc"],
                "availability_type":
                    row["availability_type"],
                "availability_rule_version":
                    row["availability_rule_version"],
                "ingest_time_utc":
                    row["ingest_time_utc"],
                "requested_latitude":
                    row["requested_latitude"],
                "requested_longitude":
                    row["requested_longitude"],
                "content_sha256":
                    row["content_sha256"],
                "raw_snapshot_count":
                    row["raw_snapshot_count"],
                "distinct_content_count":
                    row["distinct_content_count"],
                "hourly_row_count":
                    row["hourly_row_count"],
                "temperature_valid_count":
                    row["temperature_valid_count"],
                "temperature_null_count":
                    row["temperature_null_count"],
                "canonical_status":
                    row["canonical_status"],
                "availability_semantics":
                    row["availability_semantics"],
            }
        )

    actual_hash = semantic_hash(
        reconstructed
    )

    errors = []

    if len(rows) != len(expected_manifest):
        errors.append(
            "POST_FREEZE_ROW_COUNT_MISMATCH"
        )

    if actual_hash != expected_hash:
        errors.append(
            "POST_FREEZE_SHA256_MISMATCH"
        )

    bad_version = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {TARGET_TABLE}
        WHERE archive_version != ?
           OR canonical_rule_version != ?
           OR archive_status != 'FROZEN'
        """,
        (
            ARCHIVE_VERSION,
            CANONICAL_RULE_VERSION,
        ),
    ).fetchone()[0]

    if bad_version:
        errors.append(
            f"BAD_VERSION_OR_STATUS:{bad_version}"
        )

    return (
        len(rows),
        actual_hash,
        errors,
    )


def main():
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

    hr()
    print("ECMWF ARCHIVE V1 FREEZER")
    print(f"Database : {DB_PATH}")
    print(
        f"Mode     : "
        f"{'COMMIT' if args.commit else 'DRY-RUN'}"
    )
    hr()

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    conn = connect_rw()

    try:
        manifest = build_manifest(conn)

        errors = validate_manifest(
            manifest
        )

        digest = semantic_hash(
            manifest
        )

        print()
        print_summary(manifest)

        print()
        print(
            f"Semantic SHA256         : "
            f"{digest}"
        )

        print(
            f"Hard Errors             : "
            f"{len(errors)}"
        )

        for error in errors:
            print(
                f"    ERROR: {error}"
            )

        if errors:
            print()
            hr()
            print(
                "RESULT: FREEZE BLOCKED"
            )
            hr()
            raise SystemExit(2)

        if args.dry_run:
            print()
            hr()
            print(
                "RESULT: ECMWF_ARCHIVE_V1 "
                "FREEZE DRY-RUN PASS"
            )
            print(
                "Database Modification: NONE"
            )
            hr()
            return

        # ----------------------------------------------------------
        # COMMIT
        # ----------------------------------------------------------

        if table_exists(
            conn,
            TARGET_TABLE,
        ):
            count = existing_count(conn)

            if count != 0:
                raise RuntimeError(
                    f"{TARGET_TABLE} already frozen "
                    f"with {count} rows. "
                    "Refusing overwrite."
                )

        try:
            conn.execute("BEGIN IMMEDIATE")

            create_table(conn)

            frozen_at = commit_manifest(
                conn,
                manifest,
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        (
            actual_count,
            actual_hash,
            post_errors,
        ) = post_commit_audit(
            conn,
            manifest,
            digest,
        )

        print()
        hr()
        print("POST-FREEZE AUDIT")
        hr()

        print(
            f"Inserted Rows           : "
            f"{actual_count}"
        )

        print(
            f"Archive Version         : "
            f"{ARCHIVE_VERSION}"
        )

        print(
            f"Canonical Rule          : "
            f"{CANONICAL_RULE_VERSION}"
        )

        print(
            f"Frozen At UTC           : "
            f"{frozen_at}"
        )

        print(
            f"Expected SHA256         : "
            f"{digest}"
        )

        print(
            f"Actual SHA256           : "
            f"{actual_hash}"
        )

        print(
            f"SHA256 Match            : "
            f"{actual_hash == digest}"
        )

        print(
            f"Post-Freeze Errors      : "
            f"{len(post_errors)}"
        )

        for error in post_errors:
            print(
                f"    ERROR: {error}"
            )

        if post_errors:
            hr()
            print(
                "RESULT: POST-FREEZE AUDIT FAILED"
            )
            hr()
            raise SystemExit(3)

        print()
        hr()
        print(
            "RESULT: ECMWF_ARCHIVE_V1 FREEZE COMPLETE"
        )
        print(
            "RAW / HOURLY SOURCE TABLES WERE NOT MODIFIED"
        )
        hr()

    finally:
        conn.close()


if __name__ == "__main__":
    main()