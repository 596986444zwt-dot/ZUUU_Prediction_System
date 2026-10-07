"""
ECMWF PHASE 2 ISSUE RESOLUTION AUDIT
====================================

Purpose
-------
One-shot READ-ONLY root-cause audit for all remaining ECMWF Phase-2 issues.

This script DOES NOT repair or mutate the database.

It investigates:

1.  temperature_2m_c NULL rows
2.  Whether NULL temperatures form complete 72-hour runs
3.  Raw JSON temperature availability
4.  NULLs by collector / data spec / API model / archive type
5.  Whether an alternative snapshot exists for the same run
6.  Multi-snapshot runs
7.  Exact duplicate Raw snapshots
8.  Different-content snapshots
9.  Hourly semantic equality between snapshots
10.  11 failed backfill tasks
11. Failed-run impact on target-day forecast coverage
12. RAW_NOT_TASK runs
13. Legacy-only runs
14. QC WARNING rows
15. CAPE negative warnings
16. Data-spec / variable-generation transitions
17. Forecast availability semantics
18. T0/T+1/T+2 target coverage
19. Per-target-day temperature completeness
20. Whether every target day has a usable ECMWF forecast
21. Final classification:
        HARD ERROR
        REPAIRABLE
        EXPECTED ARCHIVE GAP
        REDUNDANT SNAPSHOT
        LEGACY / INFORMATIONAL
        REVIEW

Safety
------
STRICT READ ONLY.
No CREATE.
No INSERT.
No UPDATE.
No DELETE.
No ALTER.
No VACUUM.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ======================================================================
# CONFIG
# ======================================================================

PROJECT_ROOT = Path(r"C:\ZUUU_Prediction_System")
DB_PATH = PROJECT_ROOT / "database" / "zuuu_prediction.db"

RAW_TABLE = "ecmwf_raw_runs"
HOURLY_TABLE = "ecmwf_hourly_forecasts"
TASK_TABLE = "ecmwf_backfill_tasks"
LEGACY_TABLE = "ecmwf_historical_run_status"
TARGET_TABLE = "zuuu_target_v1"

EXPECTED_TARGET_VERSION = "ZUUU_TARGET_V1"
EXPECTED_TARGET_STATUS = "FROZEN"

BJT = timezone(timedelta(hours=8))

EXPECTED_LEADS = set(range(72))

KNOWN_AVAILABILITY_TYPES = {
    "official_schedule_estimate",
    "observed_ingest_time",
}


# ======================================================================
# PRINT HELPERS
# ======================================================================

WIDTH = 120


def hr(char="="):
    print(char * WIDTH)


def title(text):
    print()
    hr("=")
    print(text)
    hr("=")


def section(text):
    print()
    hr("-")
    print(text)
    hr("-")


def qi(name):
    return '"' + name.replace('"', '""') + '"'


# ======================================================================
# DATE / JSON HELPERS
# ======================================================================

def parse_dt(value):

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def parse_json(value):

    if value is None:
        return None

    try:
        return json.loads(value)
    except Exception:
        return None


def compact(value, max_len=180):

    if value is None:
        return "None"

    value = str(value).replace("\n", " ").replace("\r", " ")

    if len(value) > max_len:
        return value[:max_len] + "..."

    return value


# ======================================================================
# DATABASE
# ======================================================================

def connect_read_only():

    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}"
        )

    uri = DB_PATH.resolve().as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA query_only = ON")

    return conn


def scalar(conn, sql, params=()):

    row = conn.execute(
        sql,
        params,
    ).fetchone()

    if row is None:
        return None

    return row[0]


# ======================================================================
# AUDIT STATE
# ======================================================================

class AuditState:

    def __init__(self):

        self.hard_errors = []
        self.repairable = []
        self.expected_gaps = []
        self.redundant_snapshots = []
        self.review_items = []
        self.information = []

    def hard(self, message):
        if message not in self.hard_errors:
            self.hard_errors.append(message)

    def repair(self, message):
        if message not in self.repairable:
            self.repairable.append(message)

    def expected(self, message):
        if message not in self.expected_gaps:
            self.expected_gaps.append(message)

    def redundant(self, message):
        if message not in self.redundant_snapshots:
            self.redundant_snapshots.append(message)

    def review(self, message):
        if message not in self.review_items:
            self.review_items.append(message)

    def info(self, message):
        if message not in self.information:
            self.information.append(message)


# ======================================================================
# RAW JSON SEARCH
# ======================================================================

TEMPERATURE_KEYS = {
    "temperature_2m",
    "temperature_2m_c",
    "temperature",
}


def recursive_find_temperature_keys(obj, path="root"):

    found = []

    if isinstance(obj, dict):

        for key, value in obj.items():

            new_path = f"{path}.{key}"

            if key in TEMPERATURE_KEYS:
                found.append(
                    (new_path, value)
                )

            found.extend(
                recursive_find_temperature_keys(
                    value,
                    new_path,
                )
            )

    elif isinstance(obj, list):

        # Avoid expanding every list element unnecessarily.
        for i, value in enumerate(obj[:5]):

            if isinstance(value, (dict, list)):
                found.extend(
                    recursive_find_temperature_keys(
                        value,
                        f"{path}[{i}]",
                    )
                )

    return found


def summarize_temperature_json(raw_json):

    parsed = parse_json(raw_json)

    if parsed is None:
        return {
            "json_valid": False,
            "keys": [],
            "temperature_found": False,
            "temperature_length": None,
            "temperature_null_count": None,
            "temperature_non_null_count": None,
        }

    keys = recursive_find_temperature_keys(parsed)

    if not keys:
        return {
            "json_valid": True,
            "keys": [],
            "temperature_found": False,
            "temperature_length": None,
            "temperature_null_count": None,
            "temperature_non_null_count": None,
        }

    path, value = keys[0]

    if isinstance(value, list):

        nulls = sum(
            1
            for x in value
            if x is None
        )

        non_nulls = len(value) - nulls

        length = len(value)

    else:

        nulls = None
        non_nulls = None
        length = None

    return {
        "json_valid": True,
        "keys": [
            item[0]
            for item in keys
        ],
        "temperature_found": True,
        "temperature_length": length,
        "temperature_null_count": nulls,
        "temperature_non_null_count": non_nulls,
    }


# ======================================================================
# 1. TEMPERATURE NULL ROOT CAUSE
# ======================================================================

def audit_temperature_nulls(conn, state):

    title("1. TEMPERATURE NULL ROOT-CAUSE AUDIT")

    total_null = scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {qi(HOURLY_TABLE)}
        WHERE temperature_2m_c IS NULL
        """
    )

    print(
        f"temperature_2m_c NULL rows : {total_null}"
    )

    rows = conn.execute(
        f"""
        SELECT
            h.raw_run_id,
            h.run_time_utc,
            COUNT(*) AS hourly_rows,
            SUM(
                CASE
                WHEN h.temperature_2m_c IS NULL
                THEN 1 ELSE 0
                END
            ) AS null_temperature_rows,

            MIN(h.lead_hours) AS min_lead,
            MAX(h.lead_hours) AS max_lead,

            r.model,
            r.api_model,
            r.archive_type,
            r.data_spec_version,
            r.collector_version,
            r.source,
            r.content_sha256,
            r.raw_json,
            r.bronze_file_path

        FROM {qi(HOURLY_TABLE)} h

        JOIN {qi(RAW_TABLE)} r
          ON r.id = h.raw_run_id

        GROUP BY
            h.raw_run_id,
            h.run_time_utc

        HAVING null_temperature_rows > 0

        ORDER BY h.run_time_utc, h.raw_run_id
        """
    ).fetchall()

    print(
        f"Affected Raw Runs          : {len(rows)}"
    )

    fully_missing_runs = 0
    partially_missing_runs = 0

    affected_run_times = set()

    result = []

    for row in rows:

        affected_run_times.add(
            row["run_time_utc"]
        )

        if (
            row["hourly_rows"] == 72
            and row["null_temperature_rows"] == 72
        ):
            classification = "FULL_RUN_TEMP_MISSING"
            fully_missing_runs += 1

        else:
            classification = "PARTIAL_TEMP_MISSING"
            partially_missing_runs += 1

        json_info = summarize_temperature_json(
            row["raw_json"]
        )

        print()
        print(
            f"Run       : {row['run_time_utc']}"
        )
        print(
            f"Raw ID    : {row['raw_run_id']}"
        )
        print(
            f"Rows      : {row['hourly_rows']}"
        )
        print(
            f"Temp NULL : {row['null_temperature_rows']}"
        )
        print(
            f"Lead      : {row['min_lead']}..{row['max_lead']}"
        )
        print(
            f"Class     : {classification}"
        )
        print(
            f"API Model : {row['api_model']}"
        )
        print(
            f"Archive   : {row['archive_type']}"
        )
        print(
            f"Spec      : {row['data_spec_version']}"
        )
        print(
            f"Collector : {row['collector_version']}"
        )
        print(
            f"SHA256    : {row['content_sha256']}"
        )
        print(
            f"Bronze    : {row['bronze_file_path']}"
        )

        print(
            f"JSON Valid       : {json_info['json_valid']}"
        )
        print(
            f"JSON Temp Found  : {json_info['temperature_found']}"
        )
        print(
            f"JSON Temp Length : {json_info['temperature_length']}"
        )
        print(
            f"JSON Temp NULL   : {json_info['temperature_null_count']}"
        )
        print(
            f"JSON Temp Values : {json_info['temperature_non_null_count']}"
        )

        if json_info["keys"]:
            print(
                "JSON Temp Keys   : "
                + ", ".join(
                    json_info["keys"][:5]
                )
            )

        result.append(
            {
                "raw_run_id": row["raw_run_id"],
                "run_time": row["run_time_utc"],
                "null_rows": row["null_temperature_rows"],
                "classification": classification,
                "json_info": json_info,
                "spec": row["data_spec_version"],
                "collector": row["collector_version"],
                "api_model": row["api_model"],
            }
        )

    print()
    print(
        f"Full 72h Temperature-Missing Runs : "
        f"{fully_missing_runs}"
    )
    print(
        f"Partial Temperature-Missing Runs  : "
        f"{partially_missing_runs}"
    )
    print(
        f"Distinct Affected Run Times       : "
        f"{len(affected_run_times)}"
    )

    if total_null != sum(
        x["null_rows"]
        for x in result
    ):
        state.hard(
            "TEMPERATURE_NULL_ACCOUNTING_MISMATCH"
        )

    return result


# ======================================================================
# 2. NULLS BY PROVENANCE
# ======================================================================

def audit_null_provenance(conn, state):

    title("2. NULL TEMPERATURE BY PROVENANCE")

    dimensions = [
        "data_spec_version",
        "collector_version",
        "api_model",
        "archive_type",
        "availability_type",
        "availability_rule_version",
    ]

    for col in dimensions:

        print()
        print(col)

        rows = conn.execute(
            f"""
            SELECT
                r.{qi(col)} AS value,
                COUNT(*) AS total_hourly,
                SUM(
                    CASE
                    WHEN h.temperature_2m_c IS NULL
                    THEN 1 ELSE 0
                    END
                ) AS temp_null,

                COUNT(
                    DISTINCT h.raw_run_id
                ) AS raw_runs,

                COUNT(
                    DISTINCT CASE
                    WHEN h.temperature_2m_c IS NULL
                    THEN h.raw_run_id
                    END
                ) AS affected_raw_runs

            FROM {qi(HOURLY_TABLE)} h

            JOIN {qi(RAW_TABLE)} r
              ON r.id = h.raw_run_id

            GROUP BY r.{qi(col)}

            ORDER BY temp_null DESC, total_hourly DESC
            """
        ).fetchall()

        for row in rows:

            print(
                f"    {str(row['value']):<55}"
                f"hourly={row['total_hourly']:<8}"
                f"null={row['temp_null']:<6}"
                f"runs={row['raw_runs']:<6}"
                f"affected_runs={row['affected_raw_runs']}"
            )


# ======================================================================
# 3. ALTERNATIVE SNAPSHOT ANALYSIS
# ======================================================================

def audit_alternative_snapshots(
    conn,
    state,
    null_runs,
):

    title("3. ALTERNATIVE SNAPSHOT FOR TEMPERATURE-NULL RUNS")

    affected_times = sorted(
        {
            x["run_time"]
            for x in null_runs
        }
    )

    alternative_usable = 0
    no_alternative = 0

    result = []

    for run_time in affected_times:

        snapshots = conn.execute(
            f"""
            SELECT
                r.id,
                r.run_time_utc,
                r.api_model,
                r.archive_type,
                r.data_spec_version,
                r.collector_version,
                r.content_sha256,

                COUNT(h.id) AS hourly_rows,

                SUM(
                    CASE
                    WHEN h.temperature_2m_c IS NULL
                    THEN 1 ELSE 0
                    END
                ) AS temp_null,

                SUM(
                    CASE
                    WHEN h.temperature_2m_c IS NOT NULL
                    THEN 1 ELSE 0
                    END
                ) AS temp_present

            FROM {qi(RAW_TABLE)} r

            LEFT JOIN {qi(HOURLY_TABLE)} h
              ON h.raw_run_id = r.id

            WHERE r.run_time_utc = ?

            GROUP BY r.id

            ORDER BY r.id
            """,
            (run_time,),
        ).fetchall()

        print()
        print(
            f"RUN: {run_time}"
        )

        usable = []

        for row in snapshots:

            print(
                f"    raw_id={row['id']} "
                f"| hourly={row['hourly_rows']} "
                f"| temp_null={row['temp_null']} "
                f"| temp_present={row['temp_present']} "
                f"| spec={row['data_spec_version']} "
                f"| collector={row['collector_version']} "
                f"| api={row['api_model']} "
                f"| sha={row['content_sha256'][:16]}..."
            )

            if (
                row["hourly_rows"] == 72
                and row["temp_present"] == 72
            ):
                usable.append(
                    row["id"]
                )

        if usable:

            alternative_usable += 1

            state.repair(
                f"TEMP_NULL_RUN_HAS_USABLE_ALTERNATIVE:"
                f"{run_time}:raw_ids={usable}"
            )

            classification = "USABLE_ALTERNATIVE_EXISTS"

        else:

            no_alternative += 1

            state.review(
                f"TEMP_NULL_RUN_NO_COMPLETE_ALTERNATIVE:"
                f"{run_time}"
            )

            classification = "NO_COMPLETE_ALTERNATIVE"

        result.append(
            (
                run_time,
                classification,
                usable,
            )
        )

    print()
    print(
        f"Runs With Complete Alternative : "
        f"{alternative_usable}"
    )

    print(
        f"Runs Without Alternative       : "
        f"{no_alternative}"
    )

    return result


# ======================================================================
# 4. MULTI-SNAPSHOT ROOT CAUSE
# ======================================================================

def audit_multi_snapshots(conn, state):

    title("4. MULTI-SNAPSHOT ROOT-CAUSE AUDIT")

    run_times = [
        row[0]
        for row in conn.execute(
            f"""
            SELECT run_time_utc
            FROM {qi(RAW_TABLE)}
            GROUP BY run_time_utc
            HAVING COUNT(*) > 1
            ORDER BY run_time_utc
            """
        )
    ]

    print(
        f"Multi-Snapshot Run Times: {len(run_times)}"
    )

    exact_content_duplicates = 0
    different_content = 0

    for run_time in run_times:

        rows = conn.execute(
            f"""
            SELECT
                id,
                model,
                api_model,
                run_time_utc,
                source_available_time_utc,
                availability_type,
                ingest_time_utc,
                archive_type,
                data_spec_version,
                collector_version,
                availability_rule_version,
                content_sha256,
                bronze_file_path
            FROM {qi(RAW_TABLE)}
            WHERE run_time_utc = ?
            ORDER BY id
            """,
            (run_time,),
        ).fetchall()

        hashes = {
            row["content_sha256"]
            for row in rows
        }

        print()
        print(
            f"RUN: {run_time}"
        )

        for row in rows:

            temp_stats = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS n,
                    SUM(
                        CASE
                        WHEN temperature_2m_c IS NULL
                        THEN 1 ELSE 0
                        END
                    ) AS temp_null,
                    MIN(temperature_2m_c) AS temp_min,
                    MAX(temperature_2m_c) AS temp_max
                FROM {qi(HOURLY_TABLE)}
                WHERE raw_run_id = ?
                """,
                (row["id"],),
            ).fetchone()

            print(
                f"    raw_id={row['id']} "
                f"| sha={row['content_sha256'][:18]}... "
                f"| api={row['api_model']} "
                f"| spec={row['data_spec_version']} "
                f"| collector={row['collector_version']} "
                f"| archive={row['archive_type']} "
                f"| hourly={temp_stats['n']} "
                f"| temp_null={temp_stats['temp_null']} "
                f"| T={temp_stats['temp_min']}..{temp_stats['temp_max']}"
            )

        if len(hashes) == 1:

            exact_content_duplicates += 1

            state.redundant(
                f"IDENTICAL_RAW_CONTENT_MULTI_SNAPSHOT:"
                f"{run_time}:rows={len(rows)}"
            )

        else:

            different_content += 1

            state.review(
                f"DIFFERENT_CONTENT_MULTI_SNAPSHOT:"
                f"{run_time}:rows={len(rows)}"
            )

    print()
    print(
        f"Same SHA Multi-Snapshot Runs      : "
        f"{exact_content_duplicates}"
    )

    print(
        f"Different SHA Multi-Snapshot Runs : "
        f"{different_content}"
    )


# ======================================================================
# 5. HOURLY SEMANTIC COMPARISON FOR MULTI-SNAPSHOT RUNS
# ======================================================================

def audit_multi_snapshot_hourly_semantics(conn, state):

    title("5. MULTI-SNAPSHOT HOURLY SEMANTIC COMPARISON")

    run_times = [
        row[0]
        for row in conn.execute(
            f"""
            SELECT run_time_utc
            FROM {qi(RAW_TABLE)}
            GROUP BY run_time_utc
            HAVING COUNT(*) > 1
            ORDER BY run_time_utc
            """
        )
    ]

    identical = 0
    different = 0

    compare_columns = [
        "target_time_utc",
        "lead_hours",
        "temperature_2m_c",
        "dew_point_2m_c",
        "relative_humidity_2m_pct",
        "surface_pressure_hpa",
        "pressure_msl_hpa",
        "cloud_cover_pct",
        "wind_speed_10m_ms",
        "wind_direction_10m_deg",
        "cloud_cover_low_pct",
        "cloud_cover_mid_pct",
        "cloud_cover_high_pct",
        "wind_gusts_10m_ms",
        "shortwave_radiation_wm2",
        "direct_radiation_wm2",
        "diffuse_radiation_wm2",
        "precipitation_mm",
        "rain_mm",
        "cape_jkg",
    ]

    for run_time in run_times:

        raw_ids = [
            row[0]
            for row in conn.execute(
                f"""
                SELECT id
                FROM {qi(RAW_TABLE)}
                WHERE run_time_utc=?
                ORDER BY id
                """,
                (run_time,),
            )
        ]

        if len(raw_ids) < 2:
            continue

        datasets = []

        for raw_id in raw_ids:

            rows = conn.execute(
                f"""
                SELECT
                    {", ".join(qi(c) for c in compare_columns)}
                FROM {qi(HOURLY_TABLE)}
                WHERE raw_run_id=?
                ORDER BY lead_hours
                """,
                (raw_id,),
            ).fetchall()

            normalized = [
                tuple(
                    row[col]
                    for col in compare_columns
                )
                for row in rows
            ]

            datasets.append(
                normalized
            )

        first = datasets[0]

        same = all(
            dataset == first
            for dataset in datasets[1:]
        )

        if same:

            identical += 1

            print(
                f"{run_time} : HOURLY_SEMANTICS_IDENTICAL "
                f"| raw_ids={raw_ids}"
            )

        else:

            different += 1

            print(
                f"{run_time} : HOURLY_SEMANTICS_DIFFERENT "
                f"| raw_ids={raw_ids}"
            )

            # Count differing leads.
            base = datasets[0]

            for idx in range(
                1,
                len(datasets),
            ):

                other = datasets[idx]

                diff_count = 0

                for a, b in zip(
                    base,
                    other,
                ):
                    if a != b:
                        diff_count += 1

                if len(base) != len(other):
                    diff_count += abs(
                        len(base) - len(other)
                    )

                print(
                    f"    compare "
                    f"{raw_ids[0]} vs {raw_ids[idx]} "
                    f"| differing_rows={diff_count}"
                )

            state.review(
                f"MULTI_SNAPSHOT_SEMANTIC_DIFFERENCE:"
                f"{run_time}"
            )

    print()
    print(
        f"Semantically Identical Runs : {identical}"
    )

    print(
        f"Semantically Different Runs : {different}"
    )


# ======================================================================
# 6. FAILED TASK CLASSIFICATION
# ======================================================================

def audit_failed_tasks(conn, state):

    title("6. FAILED BACKFILL TASK CLASSIFICATION")

    rows = conn.execute(
        f"""
        SELECT
            task_key,
            run_time_utc,
            status,
            attempt_count,
            last_http_status,
            last_error,
            raw_run_id,
            archive_type,
            data_spec_version,
            collector_version
        FROM {qi(TASK_TABLE)}
        WHERE status <> 'SUCCESS'
        ORDER BY run_time_utc
        """
    ).fetchall()

    print(
        f"Non-SUCCESS Tasks: {len(rows)}"
    )

    for row in rows:

        raw_count = scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {qi(RAW_TABLE)}
            WHERE run_time_utc=?
            """,
            (row["run_time_utc"],),
        )

        hourly_count = scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {qi(HOURLY_TABLE)}
            WHERE run_time_utc=?
            """,
            (row["run_time_utc"],),
        )

        error_text = (
            row["last_error"] or ""
        ).lower()

        unavailable = (
            "not available" in error_text
            or "archive_not_available" in error_text
        )

        if (
            row["last_http_status"] == 400
            and unavailable
            and raw_count == 0
        ):

            classification = "EXPECTED_ARCHIVE_UNAVAILABLE"

            state.expected(
                f"ARCHIVE_UNAVAILABLE:"
                f"{row['run_time_utc']}"
            )

        elif raw_count > 0:

            classification = "FAILED_TASK_BUT_RAW_EXISTS"

            state.review(
                f"FAILED_TASK_RAW_EXISTS:"
                f"{row['run_time_utc']}"
            )

        else:

            classification = "UNRESOLVED_FAILED_TASK"

            state.review(
                f"UNRESOLVED_FAILED_TASK:"
                f"{row['run_time_utc']}"
            )

        print()
        print(
            f"Run       : {row['run_time_utc']}"
        )
        print(
            f"Status    : {row['status']}"
        )
        print(
            f"Attempts  : {row['attempt_count']}"
        )
        print(
            f"HTTP      : {row['last_http_status']}"
        )
        print(
            f"Raw Rows  : {raw_count}"
        )
        print(
            f"Hourly    : {hourly_count}"
        )
        print(
            f"Class     : {classification}"
        )
        print(
            f"Error     : {compact(row['last_error'])}"
        )


# ======================================================================
# 7. RAW NOT TASK
# ======================================================================

def audit_raw_not_task(conn, state):

    title("7. RAW RUNS NOT PRESENT IN NEW BACKFILL TASK LEDGER")

    rows = conn.execute(
        f"""
        SELECT
            r.run_time_utc,
            COUNT(*) AS raw_rows,
            GROUP_CONCAT(DISTINCT r.collector_version) AS collectors,
            GROUP_CONCAT(DISTINCT r.data_spec_version) AS specs,
            GROUP_CONCAT(DISTINCT r.archive_type) AS archive_types,
            GROUP_CONCAT(DISTINCT r.api_model) AS api_models

        FROM {qi(RAW_TABLE)} r

        LEFT JOIN {qi(TASK_TABLE)} t
          ON t.run_time_utc = r.run_time_utc

        WHERE t.run_time_utc IS NULL

        GROUP BY r.run_time_utc

        ORDER BY r.run_time_utc
        """
    ).fetchall()

    print(
        f"RAW_NOT_TASK Runs: {len(rows)}"
    )

    for row in rows:

        print(
            f"    {row['run_time_utc']} "
            f"| raw_rows={row['raw_rows']} "
            f"| collector={row['collectors']} "
            f"| spec={row['specs']} "
            f"| archive={row['archive_types']} "
            f"| api={row['api_models']}"
        )

    if rows:
        state.info(
            f"RAW_NOT_TASK:{len(rows)}"
        )


# ======================================================================
# 8. LEGACY ONLY
# ======================================================================

def audit_legacy_only(conn, state):

    title("8. LEGACY-ONLY RUN AUDIT")

    rows = conn.execute(
        f"""
        SELECT
            l.run_time_utc,
            l.status,
            l.last_http_status,
            l.raw_run_id,
            COUNT(r.id) AS current_raw_rows

        FROM {qi(LEGACY_TABLE)} l

        LEFT JOIN {qi(TASK_TABLE)} t
          ON t.run_time_utc = l.run_time_utc

        LEFT JOIN {qi(RAW_TABLE)} r
          ON r.run_time_utc = l.run_time_utc

        WHERE t.run_time_utc IS NULL

        GROUP BY
            l.run_time_utc,
            l.status,
            l.last_http_status,
            l.raw_run_id

        ORDER BY l.run_time_utc
        """
    ).fetchall()

    print(
        f"Legacy Only Runs: {len(rows)}"
    )

    status_counter = Counter()

    for row in rows:

        status_counter[
            row["status"]
        ] += 1

        print(
            f"    {row['run_time_utc']} "
            f"| legacy_status={row['status']} "
            f"| http={row['last_http_status']} "
            f"| legacy_raw_id={row['raw_run_id']} "
            f"| current_raw_rows={row['current_raw_rows']}"
        )

    print()
    print("Legacy-only status distribution:")

    for key, n in status_counter.items():
        print(
            f"    {key:<30}{n:>8}"
        )

    if rows:
        state.info(
            f"LEGACY_ONLY:{len(rows)}"
        )


# ======================================================================
# 9. QC WARNING ROOT CAUSE
# ======================================================================

def audit_qc_warnings(conn, state):

    title("9. QC WARNING ROOT-CAUSE AUDIT")

    rows = conn.execute(
        f"""
        SELECT
            h.id,
            h.raw_run_id,
            h.run_time_utc,
            h.target_time_utc,
            h.lead_hours,
            h.temperature_2m_c,
            h.cape_jkg,
            h.qc_status,
            h.qc_warnings_json,
            r.collector_version,
            r.data_spec_version,
            r.content_sha256

        FROM {qi(HOURLY_TABLE)} h

        JOIN {qi(RAW_TABLE)} r
          ON r.id = h.raw_run_id

        WHERE h.qc_status <> 'OK'
           OR h.parse_success <> 1

        ORDER BY
            h.run_time_utc,
            h.target_time_utc,
            h.raw_run_id
        """
    ).fetchall()

    print(
        f"Warning / Parse-Failure Rows: {len(rows)}"
    )

    categories = Counter()

    for row in rows:

        warnings = parse_json(
            row["qc_warnings_json"]
        )

        if isinstance(warnings, list):

            for item in warnings:
                categories[
                    str(item).lower()
                ] += 1

        print(
            f"    id={row['id']} "
            f"| raw={row['raw_run_id']} "
            f"| run={row['run_time_utc']} "
            f"| target={row['target_time_utc']} "
            f"| lead={row['lead_hours']} "
            f"| T={row['temperature_2m_c']} "
            f"| CAPE={row['cape_jkg']} "
            f"| warning={row['qc_warnings_json']} "
            f"| collector={row['collector_version']}"
        )

    print()
    print("Warning Categories:")

    for key, n in categories.items():
        print(
            f"    {key:<70}{n:>8}"
        )

    # CAPE is an auxiliary predictor, not the target variable.
    # Negative CAPE is kept as review unless parsing/temperature is damaged.

    non_cape_warning = 0

    for key, n in categories.items():

        normalized = key.lower()

        if (
            "cape" not in normalized
            and "cape" not in normalized.upper()
        ):
            non_cape_warning += n

    if non_cape_warning:
        state.review(
            f"NON_CAPE_QC_WARNINGS:{non_cape_warning}"
        )
    elif rows:
        state.info(
            f"QC_WARNINGS_CAPE_ONLY:{len(rows)}"
        )


# ======================================================================
# 10. DATA SPEC TRANSITIONS
# ======================================================================

def audit_data_spec_transitions(conn, state):

    title("10. DATA SPEC / COLLECTOR TRANSITION AUDIT")

    rows = conn.execute(
        f"""
        SELECT
            data_spec_version,
            collector_version,
            api_model,
            archive_type,
            COUNT(*) AS raw_rows,
            COUNT(DISTINCT run_time_utc) AS runs,
            MIN(run_time_utc) AS first_run,
            MAX(run_time_utc) AS last_run

        FROM {qi(RAW_TABLE)}

        GROUP BY
            data_spec_version,
            collector_version,
            api_model,
            archive_type

        ORDER BY first_run
        """
    ).fetchall()

    for row in rows:

        print()
        print(
            f"Spec      : {row['data_spec_version']}"
        )
        print(
            f"Collector : {row['collector_version']}"
        )
        print(
            f"API Model : {row['api_model']}"
        )
        print(
            f"Archive   : {row['archive_type']}"
        )
        print(
            f"Raw Rows  : {row['raw_rows']}"
        )
        print(
            f"Runs      : {row['runs']}"
        )
        print(
            f"First     : {row['first_run']}"
        )
        print(
            f"Last      : {row['last_run']}"
        )


# ======================================================================
# 11. TARGET DAY FORECAST COVERAGE
# ======================================================================

def audit_target_day_coverage(conn, state):

    title("11. TARGET-DAY FORECAST COVERAGE / USABILITY")

    target_dates = [
        row["business_date_bjt"]
        for row in conn.execute(
            f"""
            SELECT business_date_bjt
            FROM {qi(TARGET_TABLE)}
            ORDER BY business_date_bjt
            """
        )
    ]

    raw_runs = conn.execute(
        f"""
        SELECT
            id,
            run_time_utc,
            source_available_time_utc
        FROM {qi(RAW_TABLE)}
        ORDER BY run_time_utc, id
        """
    ).fetchall()

    run_info = []

    for row in raw_runs:

        run_dt = parse_dt(
            row["run_time_utc"]
        )

        avail_dt = parse_dt(
            row["source_available_time_utc"]
        )

        if run_dt and avail_dt:

            temp_count = scalar(
                conn,
                f"""
                SELECT COUNT(*)
                FROM {qi(HOURLY_TABLE)}
                WHERE raw_run_id=?
                  AND temperature_2m_c IS NOT NULL
                """,
                (row["id"],),
            )

            run_info.append(
                {
                    "id": row["id"],
                    "run": run_dt,
                    "available": avail_dt,
                    "temp_count": temp_count,
                }
            )

    horizon_stats = {
        "T0": {
            "any": 0,
            "temp_complete": 0,
            "missing": [],
        },
        "T+1": {
            "any": 0,
            "temp_complete": 0,
            "missing": [],
        },
        "T+2": {
            "any": 0,
            "temp_complete": 0,
            "missing": [],
        },
    }

    for date_text in target_dates:

        d = datetime.strptime(
            date_text,
            "%Y-%m-%d",
        ).date()

        target_start_bjt = datetime(
            d.year,
            d.month,
            d.day,
            tzinfo=BJT,
        )

        cutoff = target_start_bjt.astimezone(
            timezone.utc
        )

        windows = {
            "T0": (
                cutoff - timedelta(hours=24),
                cutoff,
            ),
            "T+1": (
                cutoff - timedelta(hours=48),
                cutoff - timedelta(hours=24),
            ),
            "T+2": (
                cutoff - timedelta(hours=72),
                cutoff - timedelta(hours=48),
            ),
        }

        for label, (
            start,
            end,
        ) in windows.items():

            candidates = [
                x
                for x in run_info
                if (
                    start <= x["run"] < end
                    and x["available"] <= cutoff
                )
            ]

            if candidates:

                horizon_stats[
                    label
                ]["any"] += 1

                complete = [
                    x
                    for x in candidates
                    if x["temp_count"] == 72
                ]

                if complete:

                    horizon_stats[
                        label
                    ]["temp_complete"] += 1

                else:

                    horizon_stats[
                        label
                    ]["missing"].append(
                        (
                            date_text,
                            "NO_TEMP_COMPLETE_RUN",
                        )
                    )

            else:

                horizon_stats[
                    label
                ]["missing"].append(
                    (
                        date_text,
                        "NO_ARCHIVED_RUN",
                    )
                )

    total = len(target_dates)

    print(
        f"Target Days: {total}"
    )

    for label in (
        "T0",
        "T+1",
        "T+2",
    ):

        stats = horizon_stats[label]

        print()
        print(label)

        print(
            f"    Any archived run     : "
            f"{stats['any']}/{total}"
        )

        print(
            f"    Temp-complete run    : "
            f"{stats['temp_complete']}/{total}"
        )

        print(
            f"    Unusable target days : "
            f"{len(stats['missing'])}"
        )

        for date_text, reason in stats[
            "missing"
        ][:50]:

            print(
                f"        {date_text} "
                f"| {reason}"
            )

        if stats["missing"]:

            state.review(
                f"{label}_TARGET_DAYS_WITHOUT_TEMP_COMPLETE_RUN:"
                f"{len(stats['missing'])}"
            )

    return horizon_stats


# ======================================================================
# 12. NULL TEMPERATURE IMPACT ON TARGET PERIOD
# ======================================================================

def audit_null_target_impact(conn, state):

    title("12. TEMPERATURE-NULL IMPACT ON ZUUU TARGET PERIOD")

    target_first = scalar(
        conn,
        f"""
        SELECT MIN(business_date_bjt)
        FROM {qi(TARGET_TABLE)}
        """
    )

    target_last = scalar(
        conn,
        f"""
        SELECT MAX(business_date_bjt)
        FROM {qi(TARGET_TABLE)}
        """
    )

    first_date = datetime.strptime(
        target_first,
        "%Y-%m-%d",
    ).date()

    last_date = datetime.strptime(
        target_last,
        "%Y-%m-%d",
    ).date()

    rows = conn.execute(
        f"""
        SELECT DISTINCT
            h.raw_run_id,
            h.run_time_utc
        FROM {qi(HOURLY_TABLE)} h
        WHERE h.temperature_2m_c IS NULL
        ORDER BY h.run_time_utc
        """
    ).fetchall()

    inside_or_relevant = []

    for row in rows:

        run_dt = parse_dt(
            row["run_time_utc"]
        )

        if run_dt is None:
            continue

        bjt_date = run_dt.astimezone(
            BJT
        ).date()

        # Include two-day lead-in because T+2 may use runs
        # before the target period.
        if (
            first_date - timedelta(days=3)
            <= bjt_date
            <= last_date
        ):
            inside_or_relevant.append(
                (
                    row["raw_run_id"],
                    row["run_time_utc"],
                    str(bjt_date),
                )
            )

    print(
        f"Temperature-NULL Raw Runs Relevant "
        f"To Target Training Period: "
        f"{len(inside_or_relevant)}"
    )

    for item in inside_or_relevant:

        print(
            f"    raw_id={item[0]} "
            f"| run={item[1]} "
            f"| BJT date={item[2]}"
        )

    if inside_or_relevant:

        state.review(
            f"TEMP_NULL_RUNS_IN_TARGET_RELEVANT_PERIOD:"
            f"{len(inside_or_relevant)}"
        )


# ======================================================================
# 13. AVAILABILITY CONSISTENCY
# ======================================================================

def audit_availability(conn, state):

    title("13. AVAILABILITY SEMANTICS")

    rows = conn.execute(
        f"""
        SELECT
            availability_type,
            availability_rule_version,
            COUNT(*) AS n,
            MIN(run_time_utc) AS first_run,
            MAX(run_time_utc) AS last_run,
            MIN(source_available_time_utc) AS first_available,
            MAX(source_available_time_utc) AS last_available
        FROM {qi(RAW_TABLE)}
        GROUP BY
            availability_type,
            availability_rule_version
        ORDER BY n DESC
        """
    ).fetchall()

    for row in rows:

        print()
        print(
            f"Type       : {row['availability_type']}"
        )
        print(
            f"Rule       : {row['availability_rule_version']}"
        )
        print(
            f"Rows       : {row['n']}"
        )
        print(
            f"Run Range  : "
            f"{row['first_run']} -> {row['last_run']}"
        )
        print(
            f"Avail Range: "
            f"{row['first_available']} -> "
            f"{row['last_available']}"
        )

        if (
            row["availability_type"]
            not in KNOWN_AVAILABILITY_TYPES
        ):
            state.review(
                f"UNKNOWN_AVAILABILITY_TYPE:"
                f"{row['availability_type']}"
            )


# ======================================================================
# 14. FINAL DECISION ENGINE
# ======================================================================

def final_assessment(
    conn,
    state,
    null_runs,
    alternatives,
    target_coverage,
):

    title("14. PHASE 2 ISSUE RESOLUTION ASSESSMENT")

    total_hourly = scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {qi(HOURLY_TABLE)}
        """
    )

    temp_null = scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {qi(HOURLY_TABLE)}
        WHERE temperature_2m_c IS NULL
        """
    )

    raw_rows = scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {qi(RAW_TABLE)}
        """
    )

    distinct_runs = scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT run_time_utc)
        FROM {qi(RAW_TABLE)}
        """
    )

    failed_tasks = scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {qi(TASK_TABLE)}
        WHERE status <> 'SUCCESS'
        """
    )

    warning_rows = scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {qi(HOURLY_TABLE)}
        WHERE qc_status <> 'OK'
           OR parse_success <> 1
        """
    )

    print("CORE COUNTS")
    print(
        f"    Raw Rows                  : {raw_rows}"
    )
    print(
        f"    Distinct Run Times        : {distinct_runs}"
    )
    print(
        f"    Hourly Rows               : {total_hourly}"
    )
    print(
        f"    Temperature NULL Rows     : {temp_null}"
    )
    print(
        f"    Temp-NULL Raw Runs        : {len(null_runs)}"
    )
    print(
        f"    Failed Tasks              : {failed_tasks}"
    )
    print(
        f"    QC Warning Rows           : {warning_rows}"
    )

    print()
    print("TARGET USABILITY")

    for label in (
        "T0",
        "T+1",
        "T+2",
    ):

        stats = target_coverage[label]

        print(
            f"    {label:<4} "
            f"temp-complete="
            f"{stats['temp_complete']}/729 "
            f"| unusable={len(stats['missing'])}"
        )

    # --------------------------------------------------------------
    # Determine unresolved temperature problem
    # --------------------------------------------------------------

    alternative_map = {
        run_time: (
            classification,
            usable,
        )
        for (
            run_time,
            classification,
            usable,
        ) in alternatives
    }

    unresolved_temp_runs = []

    for item in null_runs:

        run_time = item["run_time"]

        alt = alternative_map.get(
            run_time
        )

        if (
            alt is None
            or alt[0] == "NO_COMPLETE_ALTERNATIVE"
        ):
            unresolved_temp_runs.append(
                run_time
            )

    if unresolved_temp_runs:

        # Important:
        # NULL temperature rows are not automatically corruption.
        # If the source raw JSON itself has no temperature values,
        # it may be a source/spec limitation.
        #
        # We therefore classify based on raw JSON evidence.

        for run_time in sorted(
            set(unresolved_temp_runs)
        ):

            matching = [
                x
                for x in null_runs
                if x["run_time"] == run_time
            ]

            source_has_temperature = any(
                x["json_info"][
                    "temperature_found"
                ]
                and (
                    x["json_info"][
                        "temperature_non_null_count"
                    ]
                    or 0
                ) > 0
                for x in matching
            )

            if source_has_temperature:

                state.hard(
                    f"PARSER_OR_PROCESSOR_LOST_TEMPERATURE:"
                    f"{run_time}"
                )

            else:

                state.review(
                    f"SOURCE_OR_SPEC_HAS_NO_TEMPERATURE:"
                    f"{run_time}"
                )

    # --------------------------------------------------------------
    # Hard errors only for actual integrity contradictions
    # --------------------------------------------------------------

    print()
    print("CLASSIFICATION COUNTS")

    print(
        f"    HARD ERROR              : "
        f"{len(state.hard_errors)}"
    )

    print(
        f"    REPAIRABLE              : "
        f"{len(state.repairable)}"
    )

    print(
        f"    EXPECTED ARCHIVE GAP    : "
        f"{len(state.expected_gaps)}"
    )

    print(
        f"    REDUNDANT SNAPSHOT      : "
        f"{len(state.redundant_snapshots)}"
    )

    print(
        f"    REVIEW                  : "
        f"{len(state.review_items)}"
    )

    print(
        f"    INFORMATIONAL           : "
        f"{len(state.information)}"
    )

    categories = [
        (
            "HARD ERRORS",
            state.hard_errors,
        ),
        (
            "REPAIRABLE",
            state.repairable,
        ),
        (
            "EXPECTED ARCHIVE GAPS",
            state.expected_gaps,
        ),
        (
            "REDUNDANT SNAPSHOTS",
            state.redundant_snapshots,
        ),
        (
            "REVIEW ITEMS",
            state.review_items,
        ),
        (
            "INFORMATIONAL",
            state.information,
        ),
    ]

    for heading, items in categories:

        print()
        print(heading)

        if not items:
            print("    NONE")
            continue

        for item in items:
            print(
                f"    {item}"
            )

    print()
    hr("=")

    if state.hard_errors:

        print(
            "RESULT: PHASE 2 HAS CONFIRMED INTEGRITY ERRORS"
        )

        print(
            "ACTION: DO NOT FREEZE ECMWF_ARCHIVE_V1"
        )

    elif state.repairable:

        print(
            "RESULT: PHASE 2 DATA STRUCTURE IS SOUND, "
            "BUT CANONICAL SNAPSHOT SELECTION / REPAIR IS REQUIRED"
        )

        print(
            "ACTION: DO NOT DELETE RAW DATA. "
            "BUILD A CANONICAL SELECTION LAYER."
        )

    elif state.review_items:

        print(
            "RESULT: PHASE 2 HAS NO CONFIRMED HARD INTEGRITY ERROR"
        )

        print(
            "ACTION: REVIEW REMAINING SOURCE/ARCHIVE LIMITATIONS "
            "BEFORE FREEZE"
        )

    else:

        print(
            "RESULT: PHASE 2 READY FOR FREEZE"
        )

    hr("=")


# ======================================================================
# MAIN
# ======================================================================

def main():

    hr("=")
    print("ECMWF PHASE 2 ISSUE RESOLUTION AUDIT")
    print("ZUUU PREDICTION SYSTEM")
    print(f"Database : {DB_PATH}")
    print("Mode     : STRICT READ ONLY")
    hr("=")

    state = AuditState()

    conn = connect_read_only()

    try:

        null_runs = audit_temperature_nulls(
            conn,
            state,
        )

        audit_null_provenance(
            conn,
            state,
        )

        alternatives = audit_alternative_snapshots(
            conn,
            state,
            null_runs,
        )

        audit_multi_snapshots(
            conn,
            state,
        )

        audit_multi_snapshot_hourly_semantics(
            conn,
            state,
        )

        audit_failed_tasks(
            conn,
            state,
        )

        audit_raw_not_task(
            conn,
            state,
        )

        audit_legacy_only(
            conn,
            state,
        )

        audit_qc_warnings(
            conn,
            state,
        )

        audit_data_spec_transitions(
            conn,
            state,
        )

        target_coverage = audit_target_day_coverage(
            conn,
            state,
        )

        audit_null_target_impact(
            conn,
            state,
        )

        audit_availability(
            conn,
            state,
        )

        final_assessment(
            conn=conn,
            state=state,
            null_runs=null_runs,
            alternatives=alternatives,
            target_coverage=target_coverage,
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()