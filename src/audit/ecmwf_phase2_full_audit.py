"""
ECMWF PHASE 2 FULL AUDIT
========================

ZUUU Prediction System

Purpose
-------
Perform one comprehensive READ-ONLY audit of the existing ECMWF archive.

Checks
------
1. Required table/schema presence
2. Backfill task ledger
3. Legacy historical status ledger
4. Raw run archive integrity
5. Hourly forecast integrity
6. Run reconciliation across layers
7. Duplicate/multi-snapshot analysis
8. Run cycle distribution
9. Forecast lead structure
10. target_time == run_time + lead_hours
11. Hourly/raw lineage
12. Forecast variable completeness
13. parse_success / QC warnings
14. Availability semantics
15. Anti-look-ahead structural checks
16. Ground Truth alignment
17. T0 / T+1 / T+2 availability coverage
18. Final Phase-2 readiness assessment

Safety
------
READ ONLY.
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
from statistics import median


# ======================================================================
# CONFIG
# ======================================================================

PROJECT_ROOT = Path(r"C:\ZUUU_Prediction_System")
DB_PATH = PROJECT_ROOT / "database" / "zuuu_prediction.db"

TARGET_TABLE = "zuuu_target_v1"

RAW_TABLE = "ecmwf_raw_runs"
HOURLY_TABLE = "ecmwf_hourly_forecasts"
TASK_TABLE = "ecmwf_backfill_tasks"
LEGACY_STATUS_TABLE = "ecmwf_historical_run_status"

EXPECTED_TARGET_VERSION = "ZUUU_TARGET_V1"
EXPECTED_TARGET_STATUS = "FROZEN"

EXPECTED_TARGET_START = "2024-09-03"
EXPECTED_TARGET_END = "2026-09-01"
EXPECTED_TARGET_DAYS = 729

VALID_RUN_HOURS = {0, 6, 12, 18}

REQUIRED_TABLES = {
    RAW_TABLE,
    HOURLY_TABLE,
    TASK_TABLE,
    LEGACY_STATUS_TABLE,
    TARGET_TABLE,
}

CORE_FORECAST_COLUMNS = {
    "temperature_2m_c",
    "dew_point_2m_c",
    "relative_humidity_2m_pct",
    "surface_pressure_hpa",
    "pressure_msl_hpa",
    "cloud_cover_pct",
    "wind_speed_10m_kmh",
    "wind_speed_10m_ms",
    "wind_direction_10m_deg",
}

EXTENDED_FORECAST_COLUMNS = {
    "cloud_cover_low_pct",
    "cloud_cover_mid_pct",
    "cloud_cover_high_pct",
    "wind_gusts_10m_kmh",
    "wind_gusts_10m_ms",
    "shortwave_radiation_wm2",
    "direct_radiation_wm2",
    "diffuse_radiation_wm2",
    "precipitation_mm",
    "rain_mm",
    "cape_jkg",
}


# ======================================================================
# UTILITIES
# ======================================================================

def hr(char="=", width=118):
    print(char * width)


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


def qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_dt(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
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

    # Extra protection.
    conn.execute("PRAGMA query_only = ON")

    return conn


def get_tables(conn):
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
        """
    ).fetchall()

    return {
        row["name"]
        for row in rows
        if not row["name"].startswith("sqlite_")
    }


def get_columns(conn, table):
    rows = conn.execute(
        f"PRAGMA table_info({qi(table)})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def count_rows(conn, table):
    return conn.execute(
        f"SELECT COUNT(*) FROM {qi(table)}"
    ).fetchone()[0]


def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()

    if row is None:
        return None

    return row[0]


def fmt_pct(numerator, denominator):
    if not denominator:
        return "N/A"

    return f"{numerator / denominator * 100:.3f}%"


def safe_json(value):
    if value is None:
        return None

    try:
        return json.loads(value)
    except Exception:
        return None


# ======================================================================
# AUDIT STATE
# ======================================================================

class AuditState:

    def __init__(self):
        self.hard_errors = []
        self.review_items = []
        self.info_items = []

    def hard(self, message):
        self.hard_errors.append(message)

    def review(self, message):
        self.review_items.append(message)

    def info(self, message):
        self.info_items.append(message)


# ======================================================================
# SCHEMA AUDIT
# ======================================================================

def audit_schema(conn, state):

    title("1. DATABASE / SCHEMA AUDIT")

    tables = get_tables(conn)

    print(f"Database : {DB_PATH}")
    print("Mode     : READ ONLY + PRAGMA query_only")
    print(f"Tables   : {len(tables)}")

    missing = REQUIRED_TABLES - tables

    print()
    print("Required Tables:")

    for table in sorted(REQUIRED_TABLES):
        exists = table in tables
        print(f"    {table:<40} {'OK' if exists else 'MISSING'}")

    if missing:
        for table in sorted(missing):
            state.hard(f"MISSING_TABLE:{table}")

        return False

    required_columns = {
        RAW_TABLE: {
            "id",
            "model",
            "run_time_utc",
            "source_available_time_utc",
            "availability_type",
            "ingest_time_utc",
            "source",
            "raw_json",
            "bronze_file_path",
            "content_sha256",
            "archive_type",
            "data_spec_version",
            "collector_version",
            "availability_rule_version",
        },
        HOURLY_TABLE: {
            "id",
            "raw_run_id",
            "model",
            "run_time_utc",
            "source_available_time_utc",
            "availability_type",
            "ingest_time_utc",
            "target_time_utc",
            "target_time_bjt",
            "lead_hours",
            "temperature_2m_c",
            "parse_success",
            "qc_status",
            "qc_warnings_json",
            "processor_version",
        },
        TASK_TABLE: {
            "task_key",
            "model",
            "api_model",
            "run_time_utc",
            "archive_type",
            "status",
            "attempt_count",
            "last_http_status",
            "last_error",
            "raw_run_id",
            "collector_version",
        },
        LEGACY_STATUS_TABLE: {
            "model",
            "run_time_utc",
            "status",
            "attempt_count",
            "last_http_status",
            "last_error",
            "raw_run_id",
        },
        TARGET_TABLE: {
            "business_date_bjt",
            "daily_tmax_c",
            "target_version",
            "target_status",
        },
    }

    print()
    print("Required Columns:")

    for table, required in required_columns.items():

        actual = get_columns(conn, table)
        missing_cols = required - actual

        print(
            f"    {table:<40} "
            f"columns={len(actual):>3} "
            f"missing_required={len(missing_cols)}"
        )

        for col in sorted(missing_cols):
            state.hard(
                f"MISSING_COLUMN:{table}.{col}"
            )

    return len(state.hard_errors) == 0


# ======================================================================
# BACKFILL TASK AUDIT
# ======================================================================

def audit_tasks(conn, state):

    title("2. BACKFILL TASK LEDGER")

    total = count_rows(conn, TASK_TABLE)

    distinct_runs = scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT run_time_utc)
        FROM {qi(TASK_TABLE)}
        """
    )

    min_run, max_run = conn.execute(
        f"""
        SELECT MIN(run_time_utc), MAX(run_time_utc)
        FROM {qi(TASK_TABLE)}
        """
    ).fetchone()

    print(f"Rows          : {total}")
    print(f"Distinct Runs : {distinct_runs}")
    print(f"First Run     : {min_run}")
    print(f"Last Run      : {max_run}")

    rows = conn.execute(
        f"""
        SELECT status, COUNT(*) AS n
        FROM {qi(TASK_TABLE)}
        GROUP BY status
        ORDER BY n DESC
        """
    ).fetchall()

    print()
    print("Status Distribution:")

    for row in rows:
        print(
            f"    {str(row['status']):<30}"
            f"{row['n']:>10}"
        )

    failed = conn.execute(
        f"""
        SELECT
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

    print()
    print(f"Non-SUCCESS Tasks: {len(failed)}")

    for row in failed:

        error = (
            str(row["last_error"])
            .replace("\n", " ")
            if row["last_error"]
            else None
        )

        if error and len(error) > 180:
            error = error[:180] + "..."

        print(
            f"    {row['run_time_utc']} "
            f"| status={row['status']} "
            f"| attempts={row['attempt_count']} "
            f"| http={row['last_http_status']} "
            f"| raw_id={row['raw_run_id']} "
            f"| error={error}"
        )

        state.review(
            f"TASK_{row['status']}:{row['run_time_utc']}"
        )

    return {
        "rows": total,
        "distinct_runs": distinct_runs,
        "first": min_run,
        "last": max_run,
        "non_success": len(failed),
    }


# ======================================================================
# LEGACY STATUS AUDIT
# ======================================================================

def audit_legacy_status(conn, state):

    title("3. LEGACY HISTORICAL STATUS LEDGER")

    total = count_rows(
        conn,
        LEGACY_STATUS_TABLE,
    )

    print(f"Rows: {total}")

    rows = conn.execute(
        f"""
        SELECT status, COUNT(*) AS n
        FROM {qi(LEGACY_STATUS_TABLE)}
        GROUP BY status
        ORDER BY n DESC
        """
    ).fetchall()

    for row in rows:
        print(
            f"    {str(row['status']):<30}"
            f"{row['n']:>10}"
        )

    # Do NOT convert legacy FAILED into hard errors.
    # It is a historical operational ledger and may have been superseded
    # by later collectors/backfills.

    legacy_runs = {
        row[0]
        for row in conn.execute(
            f"""
            SELECT DISTINCT run_time_utc
            FROM {qi(LEGACY_STATUS_TABLE)}
            """
        )
    }

    task_runs = {
        row[0]
        for row in conn.execute(
            f"""
            SELECT DISTINCT run_time_utc
            FROM {qi(TASK_TABLE)}
            """
        )
    }

    overlap = legacy_runs & task_runs
    legacy_only = legacy_runs - task_runs
    task_only = task_runs - legacy_runs

    print()
    print(f"Legacy Distinct Runs : {len(legacy_runs)}")
    print(f"New Task Runs        : {len(task_runs)}")
    print(f"Overlap              : {len(overlap)}")
    print(f"Legacy Only          : {len(legacy_only)}")
    print(f"New Task Only        : {len(task_only)}")

    if legacy_only:
        state.info(
            f"LEGACY_ONLY_RUNS:{len(legacy_only)}"
        )

    return {
        "rows": total,
        "legacy_runs": len(legacy_runs),
        "overlap": len(overlap),
        "legacy_only": len(legacy_only),
        "task_only": len(task_only),
    }


# ======================================================================
# RAW ARCHIVE AUDIT
# ======================================================================

def audit_raw(conn, state):

    title("4. RAW ECMWF ARCHIVE")

    total = count_rows(conn, RAW_TABLE)

    distinct_runs = scalar(
        conn,
        f"""
        SELECT COUNT(DISTINCT run_time_utc)
        FROM {qi(RAW_TABLE)}
        """
    )

    min_run, max_run = conn.execute(
        f"""
        SELECT MIN(run_time_utc), MAX(run_time_utc)
        FROM {qi(RAW_TABLE)}
        """
    ).fetchone()

    print(f"Raw Rows      : {total}")
    print(f"Distinct Runs : {distinct_runs}")
    print(f"First Run     : {min_run}")
    print(f"Last Run      : {max_run}")
    print(f"Extra Snapshots vs Distinct Run : {total - distinct_runs}")

    # --------------------------------------------------------------
    # Run cycle
    # --------------------------------------------------------------

    cycle_counts = Counter()
    invalid_run_times = []

    rows = conn.execute(
        f"""
        SELECT DISTINCT run_time_utc
        FROM {qi(RAW_TABLE)}
        ORDER BY run_time_utc
        """
    ).fetchall()

    for row in rows:
        dt = parse_dt(row["run_time_utc"])

        if dt is None:
            invalid_run_times.append(
                row["run_time_utc"]
            )
            continue

        cycle_counts[dt.hour] += 1

        if dt.minute != 0 or dt.second != 0:
            state.hard(
                f"NON_HOURLY_RUN_TIME:{row['run_time_utc']}"
            )

        if dt.hour not in VALID_RUN_HOURS:
            state.hard(
                f"INVALID_RUN_CYCLE:{row['run_time_utc']}"
            )

    print()
    print("Distinct Run Cycle Distribution:")

    for hour in sorted(cycle_counts):
        print(
            f"    {hour:02d}Z : "
            f"{cycle_counts[hour]}"
        )

    if invalid_run_times:
        for value in invalid_run_times:
            state.hard(
                f"UNPARSEABLE_RUN_TIME:{value}"
            )

    # --------------------------------------------------------------
    # Multi snapshot
    # --------------------------------------------------------------

    multi = conn.execute(
        f"""
        SELECT
            run_time_utc,
            COUNT(*) AS n,
            COUNT(DISTINCT content_sha256) AS sha_count,
            COUNT(DISTINCT COALESCE(archive_type,'')) AS archive_count,
            COUNT(DISTINCT COALESCE(data_spec_version,'')) AS spec_count
        FROM {qi(RAW_TABLE)}
        GROUP BY run_time_utc
        HAVING COUNT(*) > 1
        ORDER BY run_time_utc
        """
    ).fetchall()

    print()
    print(f"Run Times With Multiple Raw Snapshots: {len(multi)}")

    for row in multi:

        print(
            f"    {row['run_time_utc']} "
            f"| rows={row['n']} "
            f"| sha={row['sha_count']} "
            f"| archive_types={row['archive_count']} "
            f"| specs={row['spec_count']}"
        )

        state.review(
            f"MULTI_RAW_SNAPSHOT:"
            f"{row['run_time_utc']}:"
            f"{row['n']}"
        )

    # --------------------------------------------------------------
    # Exact SHA reuse
    # --------------------------------------------------------------

    sha_duplicates = conn.execute(
        f"""
        SELECT
            content_sha256,
            COUNT(*) AS n,
            COUNT(DISTINCT run_time_utc) AS run_count
        FROM {qi(RAW_TABLE)}
        GROUP BY content_sha256
        HAVING COUNT(*) > 1
        ORDER BY n DESC
        """
    ).fetchall()

    print()
    print(f"SHA256 Values Used By >1 Raw Row: {len(sha_duplicates)}")

    cross_run_sha = 0

    for row in sha_duplicates:

        if row["run_count"] > 1:
            cross_run_sha += 1
            state.review(
                f"SHA_REUSED_ACROSS_RUNS:"
                f"{row['content_sha256']}:"
                f"{row['run_count']}"
            )

    print(f"SHA Reused Across Different Runs: {cross_run_sha}")

    # --------------------------------------------------------------
    # Provenance distributions
    # --------------------------------------------------------------

    for col in (
        "model",
        "source",
        "archive_type",
        "data_spec_version",
        "collector_version",
        "availability_type",
        "availability_rule_version",
        "api_model",
    ):

        print()
        print(f"{col}:")

        rows = conn.execute(
            f"""
            SELECT
                {qi(col)} AS value,
                COUNT(*) AS n
            FROM {qi(RAW_TABLE)}
            GROUP BY {qi(col)}
            ORDER BY n DESC
            """
        ).fetchall()

        for row in rows:
            print(
                f"    {str(row['value']):<55}"
                f"{row['n']:>8}"
            )

    # --------------------------------------------------------------
    # Required raw fields
    # --------------------------------------------------------------

    raw_required_non_null = [
        "model",
        "run_time_utc",
        "source_available_time_utc",
        "availability_type",
        "ingest_time_utc",
        "source",
        "raw_json",
        "bronze_file_path",
        "content_sha256",
        "availability_rule_version",
    ]

    print()
    print("Required Raw NULL Audit:")

    for col in raw_required_non_null:

        n = scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {qi(RAW_TABLE)}
            WHERE {qi(col)} IS NULL
               OR TRIM(CAST({qi(col)} AS TEXT)) = ''
            """
        )

        print(f"    {col:<40} {n}")

        if n:
            state.hard(
                f"RAW_REQUIRED_NULL:{col}:{n}"
            )

    return {
        "rows": total,
        "distinct_runs": distinct_runs,
        "multi_run_times": len(multi),
        "first": min_run,
        "last": max_run,
    }


# ======================================================================
# RUN RECONCILIATION
# ======================================================================

def audit_run_reconciliation(conn, state):

    title("5. RUN RECONCILIATION")

    def run_set(table):
        return {
            row[0]
            for row in conn.execute(
                f"""
                SELECT DISTINCT run_time_utc
                FROM {qi(table)}
                WHERE run_time_utc IS NOT NULL
                """
            )
        }

    task = run_set(TASK_TABLE)
    raw = run_set(RAW_TABLE)
    hourly = run_set(HOURLY_TABLE)
    legacy = run_set(LEGACY_STATUS_TABLE)

    print(f"Task Runs   : {len(task)}")
    print(f"Raw Runs    : {len(raw)}")
    print(f"Hourly Runs : {len(hourly)}")
    print(f"Legacy Runs : {len(legacy)}")

    comparisons = {
        "TASK_NOT_RAW": task - raw,
        "RAW_NOT_TASK": raw - task,
        "RAW_NOT_HOURLY": raw - hourly,
        "HOURLY_NOT_RAW": hourly - raw,
        "TASK_NOT_HOURLY": task - hourly,
    }

    print()

    for name, values in comparisons.items():

        print(f"{name:<30}: {len(values)}")

        for value in sorted(values)[:100]:
            print(f"    {value}")

        if len(values) > 100:
            print(
                f"    ... {len(values) - 100} more"
            )

    # A task missing raw is not automatically a hard error if the task
    # explicitly failed/archive-unavailable. We reconcile status.

    task_not_raw = comparisons["TASK_NOT_RAW"]

    unresolved_success = []

    for run_time in task_not_raw:

        row = conn.execute(
            f"""
            SELECT status, last_http_status, last_error
            FROM {qi(TASK_TABLE)}
            WHERE run_time_utc=?
            """,
            (run_time,),
        ).fetchone()

        if row and row["status"] == "SUCCESS":
            unresolved_success.append(run_time)

    if unresolved_success:

        for run_time in unresolved_success:
            state.hard(
                f"TASK_SUCCESS_WITHOUT_RAW:{run_time}"
            )

    for run_time in comparisons["RAW_NOT_HOURLY"]:
        state.hard(
            f"RAW_WITHOUT_HOURLY:{run_time}"
        )

    for run_time in comparisons["HOURLY_NOT_RAW"]:
        state.hard(
            f"HOURLY_WITHOUT_RAW_RUN_TIME:{run_time}"
        )

    return {
        name: len(values)
        for name, values in comparisons.items()
    }


# ======================================================================
# HOURLY LINEAGE
# ======================================================================

def audit_hourly_lineage(conn, state):

    title("6. RAW -> HOURLY LINEAGE")

    total = count_rows(
        conn,
        HOURLY_TABLE,
    )

    print(f"Hourly Rows: {total}")

    missing_raw = conn.execute(
        f"""
        SELECT
            h.id,
            h.raw_run_id,
            h.run_time_utc
        FROM {qi(HOURLY_TABLE)} h
        LEFT JOIN {qi(RAW_TABLE)} r
          ON r.id = h.raw_run_id
        WHERE r.id IS NULL
        ORDER BY h.id
        """
    ).fetchall()

    print(f"Missing raw_run_id References : {len(missing_raw)}")

    for row in missing_raw[:50]:
        print(
            f"    hourly_id={row['id']} "
            f"raw_run_id={row['raw_run_id']} "
            f"run={row['run_time_utc']}"
        )

    if missing_raw:
        state.hard(
            f"HOURLY_MISSING_RAW_LINEAGE:{len(missing_raw)}"
        )

    mismatch = conn.execute(
        f"""
        SELECT
            h.id,
            h.raw_run_id,
            h.run_time_utc AS hourly_run,
            r.run_time_utc AS raw_run
        FROM {qi(HOURLY_TABLE)} h
        JOIN {qi(RAW_TABLE)} r
          ON r.id = h.raw_run_id
        WHERE h.run_time_utc <> r.run_time_utc
        ORDER BY h.id
        """
    ).fetchall()

    print(f"Hourly/Raw Run-Time Mismatches : {len(mismatch)}")

    if mismatch:
        state.hard(
            f"HOURLY_RAW_RUN_TIME_MISMATCH:{len(mismatch)}"
        )

    duplicate_identity = conn.execute(
        f"""
        SELECT
            raw_run_id,
            target_time_utc,
            COUNT(*) AS n
        FROM {qi(HOURLY_TABLE)}
        GROUP BY raw_run_id, target_time_utc
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    print(
        f"Duplicate (raw_run_id,target_time) : "
        f"{len(duplicate_identity)}"
    )

    if duplicate_identity:
        state.hard(
            f"HOURLY_DUPLICATE_RAW_TARGET:"
            f"{len(duplicate_identity)}"
        )


# ======================================================================
# LEAD / TARGET TIME AUDIT
# ======================================================================

def audit_leads(conn, state):

    title("7. FORECAST LEAD / TARGET-TIME AUDIT")

    rows = conn.execute(
        f"""
        SELECT
            id,
            raw_run_id,
            run_time_utc,
            target_time_utc,
            lead_hours
        FROM {qi(HOURLY_TABLE)}
        ORDER BY raw_run_id, lead_hours, target_time_utc
        """
    )

    bad_parse = 0
    bad_target_math = 0
    negative_leads = 0
    lead_counter = Counter()

    run_leads = defaultdict(list)
    raw_run_times = {}

    examples = []

    for row in rows:

        run_dt = parse_dt(
            row["run_time_utc"]
        )

        target_dt = parse_dt(
            row["target_time_utc"]
        )

        lead = row["lead_hours"]

        if (
            run_dt is None
            or target_dt is None
            or lead is None
        ):
            bad_parse += 1
            continue

        lead = int(lead)

        lead_counter[lead] += 1
        run_leads[row["raw_run_id"]].append(lead)
        raw_run_times[row["raw_run_id"]] = row["run_time_utc"]

        if lead < 0:
            negative_leads += 1

        expected = run_dt + timedelta(
            hours=lead
        )

        if target_dt != expected:

            bad_target_math += 1

            if len(examples) < 30:
                examples.append(
                    (
                        row["id"],
                        row["run_time_utc"],
                        lead,
                        row["target_time_utc"],
                        expected.isoformat(),
                    )
                )

    print(f"Unparseable Time Rows       : {bad_parse}")
    print(f"Negative Lead Rows          : {negative_leads}")
    print(f"Target-Time Math Mismatches : {bad_target_math}")

    if bad_parse:
        state.hard(
            f"HOURLY_TIME_PARSE_ERRORS:{bad_parse}"
        )

    if negative_leads:
        state.hard(
            f"NEGATIVE_LEADS:{negative_leads}"
        )

    if bad_target_math:
        state.hard(
            f"TARGET_TIME_MATH_MISMATCH:{bad_target_math}"
        )

    for item in examples:
        print(
            "    "
            f"id={item[0]} "
            f"run={item[1]} "
            f"lead={item[2]} "
            f"target={item[3]} "
            f"expected={item[4]}"
        )

    if lead_counter:

        print()
        print(
            f"Lead Range: "
            f"{min(lead_counter)} .. "
            f"{max(lead_counter)} hours"
        )

        print(
            f"Distinct Lead Values: "
            f"{len(lead_counter)}"
        )

    # --------------------------------------------------------------
    # Per raw run structure
    # --------------------------------------------------------------

    counts = [
        len(values)
        for values in run_leads.values()
    ]

    if counts:

        print()
        print("Hourly Rows Per Raw Run:")
        print(f"    Raw Runs : {len(counts)}")
        print(f"    Min      : {min(counts)}")
        print(f"    Median   : {median(counts)}")
        print(f"    Max      : {max(counts)}")

        distribution = Counter(counts)

        print()
        print("Rows-per-Raw-Run Distribution:")

        for n, count in sorted(
            distribution.items()
        ):
            print(
                f"    {n:>4} rows : "
                f"{count:>6} raw runs"
            )

    duplicate_leads = []
    lead_gaps = []

    for raw_id, leads in run_leads.items():

        sorted_leads = sorted(leads)

        if len(sorted_leads) != len(set(sorted_leads)):
            duplicate_leads.append(raw_id)

        unique = sorted(set(sorted_leads))

        if len(unique) >= 2:

            gaps = [
                b - a
                for a, b in zip(
                    unique,
                    unique[1:],
                )
            ]

            # We do NOT assume every product must be strictly hourly.
            # But gaps > 6h are worth reviewing for this project.
            max_gap = max(gaps)

            if max_gap > 6:
                lead_gaps.append(
                    (
                        raw_id,
                        raw_run_times.get(raw_id),
                        min(unique),
                        max(unique),
                        max_gap,
                        len(unique),
                    )
                )

    print()
    print(
        f"Raw Runs With Duplicate lead_hours : "
        f"{len(duplicate_leads)}"
    )

    print(
        f"Raw Runs With Lead Gap >6h         : "
        f"{len(lead_gaps)}"
    )

    if duplicate_leads:
        state.hard(
            f"DUPLICATE_LEADS_WITHIN_RAW:"
            f"{len(duplicate_leads)}"
        )

    for item in lead_gaps[:50]:
        print(
            f"    raw_id={item[0]} "
            f"run={item[1]} "
            f"lead={item[2]}..{item[3]} "
            f"max_gap={item[4]} "
            f"points={item[5]}"
        )

    if lead_gaps:
        state.review(
            f"RUNS_WITH_LEAD_GAP_GT6H:"
            f"{len(lead_gaps)}"
        )

    return {
        "time_parse_errors": bad_parse,
        "negative_leads": negative_leads,
        "target_math_errors": bad_target_math,
        "duplicate_lead_runs": len(duplicate_leads),
        "large_gap_runs": len(lead_gaps),
    }


# ======================================================================
# FORECAST VARIABLES
# ======================================================================

def audit_variables(conn, state):

    title("8. FORECAST VARIABLE COMPLETENESS")

    columns = get_columns(
        conn,
        HOURLY_TABLE,
    )

    total = count_rows(
        conn,
        HOURLY_TABLE,
    )

    all_vars = (
        sorted(CORE_FORECAST_COLUMNS)
        + sorted(EXTENDED_FORECAST_COLUMNS)
    )

    result = {}

    print(
        f"{'Variable':<38}"
        f"{'NULL':>12}"
        f"{'NON-NULL':>12}"
        f"{'Coverage':>14}"
    )

    hr("-", 80)

    for col in all_vars:

        if col not in columns:
            print(
                f"{col:<38}"
                f"{'MISSING':>12}"
            )

            if col in CORE_FORECAST_COLUMNS:
                state.hard(
                    f"MISSING_CORE_VARIABLE_COLUMN:{col}"
                )
            else:
                state.review(
                    f"MISSING_EXTENDED_VARIABLE_COLUMN:{col}"
                )

            continue

        nulls = scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM {qi(HOURLY_TABLE)}
            WHERE {qi(col)} IS NULL
            """
        )

        non_null = total - nulls
        coverage = (
            non_null / total * 100
            if total
            else 0
        )

        result[col] = coverage

        print(
            f"{col:<38}"
            f"{nulls:>12}"
            f"{non_null:>12}"
            f"{coverage:>13.3f}%"
        )

        # Temperature is mandatory.
        if col == "temperature_2m_c" and nulls:
            state.hard(
                f"TEMPERATURE_2M_NULL:{nulls}"
            )

        # Other variables can be product-dependent.
        elif col in CORE_FORECAST_COLUMNS and coverage < 95:
            state.review(
                f"LOW_CORE_VARIABLE_COVERAGE:"
                f"{col}:{coverage:.3f}%"
            )

    return result


# ======================================================================
# QC AUDIT
# ======================================================================

def audit_qc(conn, state):

    title("9. PARSE / QC AUDIT")

    total = count_rows(
        conn,
        HOURLY_TABLE,
    )

    parse_rows = conn.execute(
        f"""
        SELECT parse_success, COUNT(*) AS n
        FROM {qi(HOURLY_TABLE)}
        GROUP BY parse_success
        ORDER BY parse_success
        """
    ).fetchall()

    print("parse_success:")

    parse_fail = 0

    for row in parse_rows:
        print(
            f"    {row['parse_success']} : "
            f"{row['n']}"
        )

        if not row["parse_success"]:
            parse_fail += row["n"]

    if parse_fail:
        state.hard(
            f"PARSE_FAILURE_ROWS:{parse_fail}"
        )

    qc_rows = conn.execute(
        f"""
        SELECT qc_status, COUNT(*) AS n
        FROM {qi(HOURLY_TABLE)}
        GROUP BY qc_status
        ORDER BY n DESC
        """
    ).fetchall()

    print()
    print("qc_status:")

    warning_count = 0

    for row in qc_rows:
        print(
            f"    {str(row['qc_status']):<20}"
            f"{row['n']:>10}"
        )

        if row["qc_status"] != "OK":
            warning_count += row["n"]

    warning_rows = conn.execute(
        f"""
        SELECT
            id,
            raw_run_id,
            run_time_utc,
            target_time_utc,
            lead_hours,
            temperature_2m_c,
            qc_status,
            qc_warnings_json
        FROM {qi(HOURLY_TABLE)}
        WHERE qc_status <> 'OK'
           OR parse_success <> 1
        ORDER BY run_time_utc, target_time_utc
        """
    ).fetchall()

    print()
    print(
        f"Non-OK / Parse-Failure Rows: "
        f"{len(warning_rows)}"
    )

    warning_types = Counter()

    for row in warning_rows:

        parsed = safe_json(
            row["qc_warnings_json"]
        )

        if isinstance(parsed, list):
            for warning in parsed:
                warning_types[str(warning)] += 1

        elif isinstance(parsed, dict):
            for key in parsed:
                warning_types[str(key)] += 1

        elif row["qc_warnings_json"]:
            warning_types[
                str(row["qc_warnings_json"])
            ] += 1

        print(
            f"    id={row['id']} "
            f"run={row['run_time_utc']} "
            f"target={row['target_time_utc']} "
            f"lead={row['lead_hours']} "
            f"T={row['temperature_2m_c']} "
            f"qc={row['qc_status']} "
            f"warnings={row['qc_warnings_json']}"
        )

    print()
    print("Warning Type Summary:")

    if not warning_types:
        print("    NONE")
    else:
        for key, n in warning_types.most_common():
            print(
                f"    {key:<70}{n:>8}"
            )

    if warning_count:
        state.review(
            f"QC_NON_OK_ROWS:{warning_count}"
        )

    return {
        "parse_fail": parse_fail,
        "warning_rows": len(warning_rows),
    }


# ======================================================================
# AVAILABILITY / LOOK-AHEAD AUDIT
# ======================================================================

def audit_availability(conn, state):

    title("10. AVAILABILITY / ANTI-LOOK-AHEAD AUDIT")

    rows = conn.execute(
        f"""
        SELECT
            id,
            run_time_utc,
            source_available_time_utc,
            ingest_time_utc,
            availability_type,
            availability_rule_version,
            archive_type
        FROM {qi(RAW_TABLE)}
        ORDER BY run_time_utc, id
        """
    ).fetchall()

    bad_time_parse = 0
    available_before_run = []
    observed_after_ingest_mismatch = []
    historical_ingest_before_available = []

    availability_types = Counter()
    rule_types = Counter()

    delays_by_rule = defaultdict(list)

    for row in rows:

        run_dt = parse_dt(
            row["run_time_utc"]
        )

        avail_dt = parse_dt(
            row["source_available_time_utc"]
        )

        ingest_dt = parse_dt(
            row["ingest_time_utc"]
        )

        availability_types[
            str(row["availability_type"])
        ] += 1

        rule_types[
            str(row["availability_rule_version"])
        ] += 1

        if (
            run_dt is None
            or avail_dt is None
            or ingest_dt is None
        ):
            bad_time_parse += 1
            continue

        delay_hours = (
            avail_dt - run_dt
        ).total_seconds() / 3600

        delays_by_rule[
            str(row["availability_rule_version"])
        ].append(delay_hours)

        if avail_dt < run_dt:
            available_before_run.append(
                row["id"]
            )

        if (
            row["availability_type"]
            == "observed_ingest_time"
            and avail_dt != ingest_dt
        ):
            observed_after_ingest_mismatch.append(
                row["id"]
            )

        # Historical archives are downloaded later than the historical
        # forecast availability time. That is expected.
        #
        # ingest_time < historical estimated availability would be
        # logically suspicious.
        if (
            row["availability_type"]
            == "official_schedule_estimate"
            and ingest_dt < avail_dt
        ):
            historical_ingest_before_available.append(
                row["id"]
            )

    print("Availability Types:")

    for key, n in availability_types.items():
        print(
            f"    {key:<45}{n:>8}"
        )

    print()
    print("Availability Rules:")

    for key, n in rule_types.items():
        print(
            f"    {key:<55}{n:>8}"
        )

    print()
    print("Availability Delay From Run Time:")

    for rule, values in delays_by_rule.items():

        if not values:
            continue

        print(
            f"    {rule}"
        )

        print(
            f"        min    = "
            f"{min(values):.3f} h"
        )

        print(
            f"        median = "
            f"{median(values):.3f} h"
        )

        print(
            f"        max    = "
            f"{max(values):.3f} h"
        )

    print()
    print(
        f"Unparseable Availability Times       : "
        f"{bad_time_parse}"
    )

    print(
        f"source_available_time < run_time     : "
        f"{len(available_before_run)}"
    )

    print(
        f"Observed Availability != Ingest Time : "
        f"{len(observed_after_ingest_mismatch)}"
    )

    print(
        f"Historical Ingest < Estimated Avail  : "
        f"{len(historical_ingest_before_available)}"
    )

    if bad_time_parse:
        state.hard(
            f"AVAILABILITY_TIME_PARSE_ERRORS:"
            f"{bad_time_parse}"
        )

    if available_before_run:
        state.hard(
            f"AVAILABLE_BEFORE_RUN:"
            f"{len(available_before_run)}"
        )

    if observed_after_ingest_mismatch:
        state.review(
            f"OBSERVED_AVAILABLE_INGEST_MISMATCH:"
            f"{len(observed_after_ingest_mismatch)}"
        )

    if historical_ingest_before_available:
        state.hard(
            f"HISTORICAL_INGEST_BEFORE_AVAILABILITY:"
            f"{len(historical_ingest_before_available)}"
        )

    # Important methodological distinction.
    historical_estimate_count = availability_types[
        "official_schedule_estimate"
    ]

    if historical_estimate_count:
        state.info(
            "HISTORICAL_AVAILABILITY_IS_ESTIMATED:"
            f"{historical_estimate_count}"
        )

    return {
        "parse_errors": bad_time_parse,
        "available_before_run": len(
            available_before_run
        ),
        "observed_mismatch": len(
            observed_after_ingest_mismatch
        ),
        "historical_ingest_before_available": len(
            historical_ingest_before_available
        ),
    }


# ======================================================================
# TARGET V1 AUDIT
# ======================================================================

def audit_target(conn, state):

    title("11. ZUUU_TARGET_V1 ALIGNMENT")

    total = count_rows(
        conn,
        TARGET_TABLE,
    )

    row = conn.execute(
        f"""
        SELECT
            MIN(business_date_bjt),
            MAX(business_date_bjt),
            COUNT(DISTINCT business_date_bjt)
        FROM {qi(TARGET_TABLE)}
        """
    ).fetchone()

    min_date = row[0]
    max_date = row[1]
    distinct_dates = row[2]

    print(f"Rows           : {total}")
    print(f"Distinct Dates : {distinct_dates}")
    print(f"First Date     : {min_date}")
    print(f"Last Date      : {max_date}")

    versions = conn.execute(
        f"""
        SELECT
            target_version,
            target_status,
            COUNT(*) AS n
        FROM {qi(TARGET_TABLE)}
        GROUP BY target_version, target_status
        """
    ).fetchall()

    print()
    print("Target Version / Status:")

    for row in versions:
        print(
            f"    {row['target_version']} "
            f"| {row['target_status']} "
            f"| {row['n']}"
        )

    if total != EXPECTED_TARGET_DAYS:
        state.hard(
            f"TARGET_ROW_COUNT:"
            f"{total}!={EXPECTED_TARGET_DAYS}"
        )

    if distinct_dates != EXPECTED_TARGET_DAYS:
        state.hard(
            f"TARGET_DISTINCT_DATE_COUNT:"
            f"{distinct_dates}"
        )

    if min_date != EXPECTED_TARGET_START:
        state.hard(
            f"TARGET_START:"
            f"{min_date}!={EXPECTED_TARGET_START}"
        )

    if max_date != EXPECTED_TARGET_END:
        state.hard(
            f"TARGET_END:"
            f"{max_date}!={EXPECTED_TARGET_END}"
        )

    invalid_versions = scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {qi(TARGET_TABLE)}
        WHERE target_version <> ?
           OR target_status <> ?
        """,
        (
            EXPECTED_TARGET_VERSION,
            EXPECTED_TARGET_STATUS,
        ),
    )

    if invalid_versions:
        state.hard(
            f"TARGET_VERSION_STATUS_MISMATCH:"
            f"{invalid_versions}"
        )

    return {
        "rows": total,
        "distinct_dates": distinct_dates,
        "first": min_date,
        "last": max_date,
    }


# ======================================================================
# T0 / T+1 / T+2 COVERAGE
# ======================================================================

def audit_target_forecast_coverage(conn, state):

    title("12. T0 / T+1 / T+2 FORECAST COVERAGE")

    target_dates = [
        row[0]
        for row in conn.execute(
            f"""
            SELECT business_date_bjt
            FROM {qi(TARGET_TABLE)}
            ORDER BY business_date_bjt
            """
        )
    ]

    # Read raw run availability once.
    raw_rows = conn.execute(
        f"""
        SELECT
            id,
            run_time_utc,
            source_available_time_utc
        FROM {qi(RAW_TABLE)}
        ORDER BY run_time_utc
        """
    ).fetchall()

    available_runs = []

    for row in raw_rows:

        run_dt = parse_dt(
            row["run_time_utc"]
        )

        avail_dt = parse_dt(
            row["source_available_time_utc"]
        )

        if run_dt and avail_dt:
            available_runs.append(
                (
                    row["id"],
                    run_dt,
                    avail_dt,
                )
            )

    # For this audit, T+N means:
    # Was there at least one archived forecast run available BEFORE
    # the beginning of the BJT target day, issued N calendar days
    # before that target day?
    #
    # This is a structural coverage audit only.
    # It does NOT yet select the final modeling cutoff/run policy.

    bjt = timezone(
        timedelta(hours=8)
    )

    coverage = {
        "T0": 0,
        "T+1": 0,
        "T+2": 0,
    }

    missing = {
        "T0": [],
        "T+1": [],
        "T+2": [],
    }

    for date_text in target_dates:

        target_date = datetime.strptime(
            date_text,
            "%Y-%m-%d",
        ).date()

        target_start_bjt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            0,
            0,
            tzinfo=bjt,
        )

        target_start_utc = (
            target_start_bjt
            .astimezone(timezone.utc)
        )

        # Structural windows.
        #
        # T0:
        # run calendar date UTC close to target day and available
        # before target-day start.
        #
        # T+1 / T+2:
        # require run time approximately 1 or 2 days earlier.
        #
        # We intentionally use windows rather than hard-code a single
        # cycle because the final operational policy is not frozen yet.

        horizons = {
            "T0": (
                target_start_utc - timedelta(hours=24),
                target_start_utc,
            ),
            "T+1": (
                target_start_utc - timedelta(hours=48),
                target_start_utc - timedelta(hours=24),
            ),
            "T+2": (
                target_start_utc - timedelta(hours=72),
                target_start_utc - timedelta(hours=48),
            ),
        }

        for label, (
            run_start,
            run_end,
        ) in horizons.items():

            found = False

            for _, run_dt, avail_dt in available_runs:

                if not (
                    run_start <= run_dt < run_end
                ):
                    continue

                if avail_dt <= target_start_utc:
                    found = True
                    break

            if found:
                coverage[label] += 1
            else:
                missing[label].append(
                    date_text
                )

    total = len(target_dates)

    for label in (
        "T0",
        "T+1",
        "T+2",
    ):

        print(
            f"{label:<5} "
            f"covered={coverage[label]:>4}/{total} "
            f"({fmt_pct(coverage[label], total)}) "
            f"missing={len(missing[label])}"
        )

        if missing[label]:

            print(
                "    Missing dates:"
            )

            for value in missing[label][:30]:
                print(
                    f"        {value}"
                )

            if len(missing[label]) > 30:
                print(
                    f"        ... "
                    f"{len(missing[label]) - 30} more"
                )

            state.review(
                f"{label}_STRUCTURAL_COVERAGE_MISSING:"
                f"{len(missing[label])}"
            )

    print()
    print(
        "NOTE: This is structural archive coverage, "
        "not the final frozen T0/T+1/T+2 run-selection policy."
    )

    return {
        "coverage": coverage,
        "missing": {
            k: len(v)
            for k, v in missing.items()
        },
    }


# ======================================================================
# TASK -> RAW LINKAGE
# ======================================================================

def audit_task_raw_links(conn, state):

    title("13. TASK -> RAW LINKAGE")

    rows = conn.execute(
        f"""
        SELECT
            t.task_key,
            t.run_time_utc,
            t.status,
            t.raw_run_id,
            r.id AS found_raw_id,
            r.run_time_utc AS raw_run_time
        FROM {qi(TASK_TABLE)} t
        LEFT JOIN {qi(RAW_TABLE)} r
          ON r.id = t.raw_run_id
        ORDER BY t.run_time_utc
        """
    ).fetchall()

    success_missing_id = 0
    success_missing_raw = 0
    run_mismatch = 0

    for row in rows:

        if row["status"] == "SUCCESS":

            if row["raw_run_id"] is None:
                success_missing_id += 1

            elif row["found_raw_id"] is None:
                success_missing_raw += 1

            elif (
                row["raw_run_time"]
                != row["run_time_utc"]
            ):
                run_mismatch += 1

    print(
        f"SUCCESS Without raw_run_id : "
        f"{success_missing_id}"
    )

    print(
        f"SUCCESS raw_run_id Missing : "
        f"{success_missing_raw}"
    )

    print(
        f"Task/Raw Run-Time Mismatch : "
        f"{run_mismatch}"
    )

    if success_missing_id:
        state.hard(
            f"TASK_SUCCESS_WITHOUT_RAW_ID:"
            f"{success_missing_id}"
        )

    if success_missing_raw:
        state.hard(
            f"TASK_SUCCESS_RAW_ID_NOT_FOUND:"
            f"{success_missing_raw}"
        )

    if run_mismatch:
        state.hard(
            f"TASK_RAW_RUN_TIME_MISMATCH:"
            f"{run_mismatch}"
        )


# ======================================================================
# HOURLY AVAILABILITY CONSISTENCY
# ======================================================================

def audit_hourly_raw_metadata(conn, state):

    title("14. RAW -> HOURLY METADATA CONSISTENCY")

    rows = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total,

            SUM(
                CASE
                WHEN h.run_time_utc <> r.run_time_utc
                THEN 1 ELSE 0 END
            ) AS run_mismatch,

            SUM(
                CASE
                WHEN h.source_available_time_utc
                     <> r.source_available_time_utc
                THEN 1 ELSE 0 END
            ) AS available_mismatch,

            SUM(
                CASE
                WHEN h.availability_type
                     <> r.availability_type
                THEN 1 ELSE 0 END
            ) AS availability_type_mismatch,

            SUM(
                CASE
                WHEN h.ingest_time_utc
                     <> r.ingest_time_utc
                THEN 1 ELSE 0 END
            ) AS ingest_mismatch

        FROM {qi(HOURLY_TABLE)} h
        JOIN {qi(RAW_TABLE)} r
          ON r.id = h.raw_run_id
        """
    ).fetchone()

    print(
        f"Joined Rows                 : "
        f"{rows['total']}"
    )

    print(
        f"Run-Time Mismatch           : "
        f"{rows['run_mismatch']}"
    )

    print(
        f"Available-Time Mismatch     : "
        f"{rows['available_mismatch']}"
    )

    print(
        f"Availability-Type Mismatch  : "
        f"{rows['availability_type_mismatch']}"
    )

    print(
        f"Ingest-Time Mismatch        : "
        f"{rows['ingest_mismatch']}"
    )

    checks = {
        "RAW_HOURLY_RUN_TIME_MISMATCH":
            rows["run_mismatch"],

        "RAW_HOURLY_AVAILABLE_TIME_MISMATCH":
            rows["available_mismatch"],

        "RAW_HOURLY_AVAILABILITY_TYPE_MISMATCH":
            rows["availability_type_mismatch"],

        "RAW_HOURLY_INGEST_TIME_MISMATCH":
            rows["ingest_mismatch"],
    }

    for key, value in checks.items():

        if value:
            state.hard(
                f"{key}:{value}"
            )


# ======================================================================
# TEMPERATURE SANITY
# ======================================================================

def audit_temperature(conn, state):

    title("15. TEMPERATURE SANITY AUDIT")

    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            COUNT(temperature_2m_c) AS non_null,
            MIN(temperature_2m_c) AS min_temp,
            MAX(temperature_2m_c) AS max_temp
        FROM {qi(HOURLY_TABLE)}
        """
    ).fetchone()

    print(f"Rows       : {row['total']}")
    print(f"Non-NULL T : {row['non_null']}")
    print(f"Min T      : {row['min_temp']}")
    print(f"Max T      : {row['max_temp']}")

    suspicious = conn.execute(
        f"""
        SELECT
            id,
            run_time_utc,
            target_time_utc,
            temperature_2m_c
        FROM {qi(HOURLY_TABLE)}
        WHERE temperature_2m_c IS NOT NULL
          AND (
              temperature_2m_c < -50
              OR temperature_2m_c > 60
          )
        ORDER BY run_time_utc, target_time_utc
        """
    ).fetchall()

    print(
        f"Extreme Sanity Violations "
        f"(<-50C or >60C): {len(suspicious)}"
    )

    if suspicious:
        state.hard(
            f"TEMPERATURE_SANITY_FAILURE:"
            f"{len(suspicious)}"
        )


# ======================================================================
# FINAL REPORT
# ======================================================================

def final_report(
    state,
    task_result,
    raw_result,
    reconciliation,
    lead_result,
    qc_result,
    availability_result,
    target_result,
    coverage_result,
):

    title("PHASE 2 FINAL ASSESSMENT")

    print("ARCHIVE")
    print(
        f"    Backfill Task Runs     : "
        f"{task_result['distinct_runs']}"
    )

    print(
        f"    Raw Rows               : "
        f"{raw_result['rows']}"
    )

    print(
        f"    Raw Distinct Runs      : "
        f"{raw_result['distinct_runs']}"
    )

    print(
        f"    Multi-Snapshot Runs    : "
        f"{raw_result['multi_run_times']}"
    )

    print()
    print("RECONCILIATION")

    for key, value in reconciliation.items():
        print(
            f"    {key:<28}: {value}"
        )

    print()
    print("FORECAST STRUCTURE")

    print(
        f"    Target-Time Errors     : "
        f"{lead_result['target_math_errors']}"
    )

    print(
        f"    Negative Leads         : "
        f"{lead_result['negative_leads']}"
    )

    print(
        f"    Duplicate Lead Runs    : "
        f"{lead_result['duplicate_lead_runs']}"
    )

    print(
        f"    Lead-Gap Review Runs   : "
        f"{lead_result['large_gap_runs']}"
    )

    print()
    print("QC")

    print(
        f"    Parse Failures         : "
        f"{qc_result['parse_fail']}"
    )

    print(
        f"    Warning Rows           : "
        f"{qc_result['warning_rows']}"
    )

    print()
    print("ANTI-LOOK-AHEAD")

    print(
        f"    Time Parse Errors      : "
        f"{availability_result['parse_errors']}"
    )

    print(
        f"    Available Before Run   : "
        f"{availability_result['available_before_run']}"
    )

    print(
        f"    Observed Time Mismatch : "
        f"{availability_result['observed_mismatch']}"
    )

    print()
    print("GROUND TRUTH")

    print(
        f"    Target Rows            : "
        f"{target_result['rows']}"
    )

    print(
        f"    Target Range           : "
        f"{target_result['first']} -> "
        f"{target_result['last']}"
    )

    print()
    print("STRUCTURAL FORECAST COVERAGE")

    total_target = target_result["rows"]

    for label in ("T0", "T+1", "T+2"):

        n = coverage_result[
            "coverage"
        ][label]

        missing = coverage_result[
            "missing"
        ][label]

        print(
            f"    {label:<4}: "
            f"{n}/{total_target} "
            f"({fmt_pct(n, total_target)}) "
            f"| missing={missing}"
        )

    print()
    print("INFORMATIONAL ITEMS")

    if state.info_items:

        for item in state.info_items:
            print(
                f"    {item}"
            )
    else:
        print("    NONE")

    print()
    print("REVIEW ITEMS")

    if state.review_items:

        for item in state.review_items:
            print(
                f"    {item}"
            )
    else:
        print("    NONE")

    print()
    print("HARD ERRORS")

    if state.hard_errors:

        for item in state.hard_errors:
            print(
                f"    {item}"
            )
    else:
        print("    NONE")

    print()
    hr("=")

    print(
        f"Review Items : "
        f"{len(state.review_items)}"
    )

    print(
        f"Hard Errors  : "
        f"{len(state.hard_errors)}"
    )

    # Important:
    # Review items do not automatically fail Phase 2.
    # Hard errors do.

    if state.hard_errors:

        print(
            "RESULT: PHASE 2 NOT READY"
        )

        print(
            "STATUS: HARD ERRORS MUST BE RESOLVED "
            "BEFORE ECMWF_ARCHIVE_V1 FREEZE"
        )

    else:

        print(
            "RESULT: PHASE 2 TECHNICAL AUDIT PASS"
        )

        if state.review_items:

            print(
                "STATUS: NO HARD ERRORS; "
                "REVIEW ITEMS REQUIRE CLASSIFICATION "
                "BEFORE ECMWF_ARCHIVE_V1 FREEZE"
            )

        else:

            print(
                "STATUS: PHASE 2 READY FOR "
                "ECMWF_ARCHIVE_V1 FREEZE"
            )

    hr("=")


# ======================================================================
# MAIN
# ======================================================================

def main():

    hr("=")
    print("ECMWF PHASE 2 FULL AUDIT")
    print("ZUUU PREDICTION SYSTEM")
    print(f"Database : {DB_PATH}")
    print("Mode     : STRICT READ ONLY")
    hr("=")

    state = AuditState()

    conn = connect_read_only()

    try:

        schema_ok = audit_schema(
            conn,
            state,
        )

        if not schema_ok:

            title("AUDIT ABORTED")

            print(
                "Required schema is incomplete."
            )

            print(
                "No database modifications were made."
            )

            print()
            print("Hard Errors:")

            for item in state.hard_errors:
                print(
                    f"    {item}"
                )

            return 2

        task_result = audit_tasks(
            conn,
            state,
        )

        audit_legacy_status(
            conn,
            state,
        )

        raw_result = audit_raw(
            conn,
            state,
        )

        reconciliation = (
            audit_run_reconciliation(
                conn,
                state,
            )
        )

        audit_hourly_lineage(
            conn,
            state,
        )

        lead_result = audit_leads(
            conn,
            state,
        )

        audit_variables(
            conn,
            state,
        )

        qc_result = audit_qc(
            conn,
            state,
        )

        availability_result = (
            audit_availability(
                conn,
                state,
            )
        )

        target_result = audit_target(
            conn,
            state,
        )

        coverage_result = (
            audit_target_forecast_coverage(
                conn,
                state,
            )
        )

        audit_task_raw_links(
            conn,
            state,
        )

        audit_hourly_raw_metadata(
            conn,
            state,
        )

        audit_temperature(
            conn,
            state,
        )

        final_report(
            state=state,
            task_result=task_result,
            raw_result=raw_result,
            reconciliation=reconciliation,
            lead_result=lead_result,
            qc_result=qc_result,
            availability_result=availability_result,
            target_result=target_result,
            coverage_result=coverage_result,
        )

        return (
            0
            if not state.hard_errors
            else 1
        )

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )