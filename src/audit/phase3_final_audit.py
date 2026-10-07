from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


# =============================================================================
# CONFIG
# =============================================================================

PROJECT_ROOT = Path(r"C:\ZUUU_Prediction_System")

MAIN_DB = PROJECT_ROOT / "database" / "zuuu_prediction.db"
AUX_DB = PROJECT_ROOT / "database" / "phase3_auxiliary_v1.db"

EXPECTED_MAIN_SHA256 = (
    "2e0149050ed11fe2d750b6f4798f7a51"
    "b313bf8f59bb671da5a076daeb124367"
)

EXPECTED_AUX_SHA256 = (
    "ec8defe1778a66ed9ea20dc9e3825579"
    "de685c1818862a3eef113bba9a624f22"
)

EXPECTED_TARGET_ROWS = 729
EXPECTED_ARCHIVE_RUNS = 3411

EXPECTED_UNAVAILABLE_RUNS = 7
EXPECTED_NULL_TEMPERATURE_ROWS = 504

EXPECTED_SOLAR_ROWS = 17496
EXPECTED_SOLAR_DAYS = 729

EXPECTED_METEOSTAT_HOURS = 17469
EXPECTED_METEOSTAT_MISSING_HOURS = 27
EXPECTED_METEOSTAT_PARAMETER_ROWS = 227097

EXPECTED_STATION_SNAPSHOTS = 1
EXPECTED_RAW_SNAPSHOTS = 3

EXPECTED_TEMP_OBSERVATION = 17349
EXPECTED_TEMP_MODEL = 120

EXPECTED_ISSUE_RULE_VERSION = "ECMWF_ISSUE_RULE_V1"
EXPECTED_BACKTEST_RULE = "BACKTEST_ISSUE_RULE_V1"
EXPECTED_REALTIME_RULE = "REALTIME_ISSUE_RULE_V1"
EXPECTED_ISSUE_HOUR_BJT = 21
EXPECTED_ISSUE_STATUS = "FROZEN"


# =============================================================================
# HELPERS
# =============================================================================

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()


def open_readonly(path: Path):
    uri = path.resolve().as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    conn.execute("PRAGMA query_only=ON")

    return conn


def scalar(conn, sql, params=()):
    row = conn.execute(
        sql,
        params,
    ).fetchone()

    if row is None:
        return None

    return row[0]


def table_exists(conn, table: str) -> bool:
    return scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM sqlite_master
        WHERE type='table'
          AND name=?
        """,
        (table,),
    ) == 1


def section(title):
    print()
    print("=" * 96)
    print(title)
    print("=" * 96)


def check(
    errors,
    name,
    actual,
    expected,
):
    passed = actual == expected

    print(
        f"{name:<52}"
        f"{'PASS' if passed else 'FAIL'}   "
        f"actual={actual!r}   "
        f"expected={expected!r}"
    )

    if not passed:
        errors.append(
            f"{name}: actual={actual!r}, expected={expected!r}"
        )

    return passed


def check_true(
    errors,
    name,
    condition,
    detail="",
):
    passed = bool(condition)

    suffix = f"   {detail}" if detail else ""

    print(
        f"{name:<52}"
        f"{'PASS' if passed else 'FAIL'}"
        f"{suffix}"
    )

    if not passed:
        errors.append(
            f"{name}: {detail}"
        )

    return passed


# =============================================================================
# MAIN DATABASE
# =============================================================================

def audit_main_database(
    conn,
    errors,
):
    section(
        "1. PRODUCTION DATABASE / FROZEN CORE"
    )

    check(
        errors,
        "Main integrity_check",
        scalar(
            conn,
            "PRAGMA integrity_check",
        ),
        "ok",
    )

    fk_errors = len(
        conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    )

    check(
        errors,
        "Main foreign_key_check",
        fk_errors,
        0,
    )

    required = (
        "zuuu_target_v1",
        "ecmwf_archive_v1",
        "ecmwf_issue_rule_v1",
        "ecmwf_hourly_forecasts",
    )

    for table in required:
        check_true(
            errors,
            f"Table exists: {table}",
            table_exists(
                conn,
                table,
            ),
        )

    check(
        errors,
        "ZUUU_TARGET_V1 rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM zuuu_target_v1
            """
        ),
        EXPECTED_TARGET_ROWS,
    )

    check(
        errors,
        "ECMWF_ARCHIVE_V1 canonical runs",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM ecmwf_archive_v1
            """
        ),
        EXPECTED_ARCHIVE_RUNS,
    )


# =============================================================================
# ECMWF ARCHIVE INVARIANTS
# =============================================================================

def audit_ecmwf(
    conn,
    errors,
):
    section(
        "2. ECMWF ARCHIVE V1 INVARIANTS"
    )

    # -------------------------------------------------------------------------
    # Known 7 source-temperature-unavailable canonical runs
    # -------------------------------------------------------------------------

    unavailable_runs = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM ecmwf_archive_v1
        WHERE canonical_status =
              'SOURCE_TEMPERATURE_UNAVAILABLE'
        """
    )

    check(
        errors,
        "SOURCE_TEMPERATURE_UNAVAILABLE runs",
        unavailable_runs,
        EXPECTED_UNAVAILABLE_RUNS,
    )

    # -------------------------------------------------------------------------
    # Archive-side NULL count
    # -------------------------------------------------------------------------

    archive_null_sum = scalar(
        conn,
        """
        SELECT COALESCE(
            SUM(temperature_null_count),
            0
        )
        FROM ecmwf_archive_v1
        """
    )

    check(
        errors,
        "Archive temperature_null_count sum",
        archive_null_sum,
        EXPECTED_NULL_TEMPERATURE_ROWS,
    )

    # -------------------------------------------------------------------------
    # Hourly source table independent cross-check
    # -------------------------------------------------------------------------

    hourly_null_rows = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM ecmwf_hourly_forecasts
        WHERE temperature_2m_c IS NULL
        """
    )

    check(
        errors,
        "Hourly temperature_2m_c NULL rows",
        hourly_null_rows,
        EXPECTED_NULL_TEMPERATURE_ROWS,
    )

    # -------------------------------------------------------------------------
    # Every unavailable canonical run should be a full 72-hour NULL run.
    # -------------------------------------------------------------------------

    bad_unavailable = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM ecmwf_archive_v1
        WHERE canonical_status =
              'SOURCE_TEMPERATURE_UNAVAILABLE'
          AND (
                temperature_null_count != 72
                OR temperature_valid_count != 0
              )
        """
    )

    check(
        errors,
        "Unavailable runs not exactly 72 NULL / 0 valid",
        bad_unavailable,
        0,
    )

    # -------------------------------------------------------------------------
    # Available canonical runs must not carry the unavailable status.
    # -------------------------------------------------------------------------

    status_counts = conn.execute(
        """
        SELECT
            canonical_status,
            COUNT(*)
        FROM ecmwf_archive_v1
        GROUP BY canonical_status
        ORDER BY canonical_status
        """
    ).fetchall()

    print()
    print("Canonical status counts:")

    for status, count in status_counts:
        print(
            f"  {status!r:<40} {count}"
        )

    # -------------------------------------------------------------------------
    # Frozen archive metadata
    # -------------------------------------------------------------------------

    archive_versions = conn.execute(
        """
        SELECT
            archive_version,
            archive_status,
            canonical_rule_version,
            COUNT(*)
        FROM ecmwf_archive_v1
        GROUP BY
            archive_version,
            archive_status,
            canonical_rule_version
        """
    ).fetchall()

    print()
    print("Archive freeze metadata:")

    for row in archive_versions:
        print(
            f"  version={row[0]!r} "
            f"status={row[1]!r} "
            f"rule={row[2]!r} "
            f"rows={row[3]}"
        )

    check(
        errors,
        "Distinct archive_version values",
        scalar(
            conn,
            """
            SELECT COUNT(
                DISTINCT archive_version
            )
            FROM ecmwf_archive_v1
            """
        ),
        1,
    )

    check(
        errors,
        "Distinct archive_status values",
        scalar(
            conn,
            """
            SELECT COUNT(
                DISTINCT archive_status
            )
            FROM ecmwf_archive_v1
            """
        ),
        1,
    )

    check(
        errors,
        "Archive non-FROZEN rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM ecmwf_archive_v1
            WHERE archive_status != 'FROZEN'
            """
        ),
        0,
    )


# =============================================================================
# ISSUE RULE
# =============================================================================

def audit_issue_rule(
    conn,
    errors,
):
    section(
        "3. ECMWF ISSUE RULE V1"
    )

    row_count = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM ecmwf_issue_rule_v1
        """
    )

    check(
        errors,
        "Issue-rule frozen rows",
        row_count,
        1,
    )

    row = conn.execute(
        """
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
        FROM ecmwf_issue_rule_v1
        LIMIT 1
        """
    ).fetchone()

    if row is None:
        errors.append(
            "ecmwf_issue_rule_v1 contains no row"
        )

        return

    (
        rule_version,
        backtest_rule,
        realtime_rule,
        timezone_name,
        issue_hour,
        payload_json,
        semantic_sha,
        rule_status,
        frozen_at,
    ) = row

    check(
        errors,
        "Issue rule version",
        rule_version,
        EXPECTED_ISSUE_RULE_VERSION,
    )

    check(
        errors,
        "Backtest rule name",
        backtest_rule,
        EXPECTED_BACKTEST_RULE,
    )

    check(
        errors,
        "Realtime rule name",
        realtime_rule,
        EXPECTED_REALTIME_RULE,
    )

    check(
        errors,
        "Issue-rule timezone",
        timezone_name,
        "Asia/Shanghai",
    )

    check(
        errors,
        "Historical issue hour BJT",
        issue_hour,
        EXPECTED_ISSUE_HOUR_BJT,
    )

    check(
        errors,
        "Issue-rule status",
        rule_status,
        EXPECTED_ISSUE_STATUS,
    )

    check_true(
        errors,
        "Issue-rule semantic SHA256 format",
        isinstance(
            semantic_sha,
            str,
        )
        and len(semantic_sha) == 64
        and all(
            c in "0123456789abcdef"
            for c in semantic_sha
        ),
        semantic_sha or "",
    )

    try:
        payload = json.loads(
            payload_json
        )

        payload_valid = isinstance(
            payload,
            dict,
        )

    except Exception:
        payload_valid = False

    check_true(
        errors,
        "Issue-rule payload valid JSON object",
        payload_valid,
    )

    check_true(
        errors,
        "Issue-rule frozen_at_utc present",
        isinstance(
            frozen_at,
            str,
        )
        and bool(
            frozen_at.strip()
        ),
        frozen_at or "",
    )

    print()
    print(
        "Previously frozen Phase 2 semantic acceptance:"
    )

    print(
        "  T0  : 729/729"
    )

    print(
        "  T+1 : 729/729"
    )

    print(
        "  T+2 : 728/729"
    )

    print(
        "  Known T+2 gap: 2025-08-07 = 14/24"
    )

    print(
        "  Leakage: 0"
    )

    print(
        "  NOTE: These mappings were validated by the "
        "dedicated Phase 2 final audit."
    )


# =============================================================================
# AUX DATABASE
# =============================================================================

def audit_aux_database(
    conn,
    errors,
):
    section(
        "4. PHASE 3 AUXILIARY DATABASE"
    )

    check(
        errors,
        "Aux integrity_check",
        scalar(
            conn,
            "PRAGMA integrity_check",
        ),
        "ok",
    )

    fk_errors = len(
        conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    )

    check(
        errors,
        "Aux foreign_key_check",
        fk_errors,
        0,
    )

    required = (
        "aux_v1_station_snapshot",
        "aux_v1_raw_snapshot",
        "aux_v1_meteostat_hourly",
        "aux_v1_solar_time",
    )

    for table in required:
        check_true(
            errors,
            f"Aux table exists: {table}",
            table_exists(
                conn,
                table,
            ),
        )


# =============================================================================
# SOLAR
# =============================================================================

def audit_solar(
    conn,
    errors,
):
    section(
        "5. SOLAR / TIME V1"
    )

    check(
        errors,
        "Solar rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_solar_time
            """
        ),
        EXPECTED_SOLAR_ROWS,
    )

    check(
        errors,
        "Solar BJT days",
        scalar(
            conn,
            """
            SELECT COUNT(
                DISTINCT business_date_bjt
            )
            FROM aux_v1_solar_time
            """
        ),
        EXPECTED_SOLAR_DAYS,
    )

    bad_days = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                business_date_bjt,
                COUNT(*) AS n,
                COUNT(
                    DISTINCT hour_bjt
                ) AS h
            FROM aux_v1_solar_time
            GROUP BY business_date_bjt
            HAVING n != 24
                OR h != 24
        )
        """
    )

    check(
        errors,
        "Solar days without exact 24 hours",
        bad_days,
        0,
    )

    duplicates = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                target_time,
                COUNT(*) AS n
            FROM aux_v1_solar_time
            GROUP BY target_time
            HAVING n != 1
        )
        """
    )

    check(
        errors,
        "Solar duplicate target times",
        duplicates,
        0,
    )

    first_row = conn.execute(
        """
        SELECT
            business_date_bjt,
            hour_bjt,
            target_time
        FROM aux_v1_solar_time
        ORDER BY
            business_date_bjt,
            hour_bjt
        LIMIT 1
        """
    ).fetchone()

    last_row = conn.execute(
        """
        SELECT
            business_date_bjt,
            hour_bjt,
            target_time
        FROM aux_v1_solar_time
        ORDER BY
            business_date_bjt DESC,
            hour_bjt DESC
        LIMIT 1
        """
    ).fetchone()

    check(
        errors,
        "Solar first row",
        first_row,
        (
            "2024-09-03",
            0,
            "2024-09-02T16:00:00+00:00",
        ),
    )

    check(
        errors,
        "Solar last row",
        last_row,
        (
            "2026-09-01",
            23,
            "2026-09-01T15:00:00+00:00",
        ),
    )

    contract_violations = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM aux_v1_solar_time
        WHERE
            observation_time IS NOT NULL
            OR source_available_time IS NOT NULL
            OR availability_basis
                != 'DETERMINISTIC_NOT_APPLICABLE'
            OR timezone
                != 'Asia/Shanghai'
            OR source
                != 'NOAA_SOLAR_EQUATIONS'
            OR source_version
                != 'NOAA_FRACTIONAL_YEAR_ZUUU_V1'
            OR coordinate_version
                != 'ZUUU_CONFIG_COORDINATES_V1'
            OR latitude != 30.576
            OR longitude != 103.950
        """
    )

    check(
        errors,
        "Solar contract violations",
        contract_violations,
        0,
    )

    range_violations = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM aux_v1_solar_time
        WHERE
            solar_elevation < -90
            OR solar_elevation > 90
            OR solar_azimuth < 0
            OR solar_azimuth >= 360
            OR daylight_duration < 0
            OR daylight_duration > 1440
            OR sin_hour < -1
            OR sin_hour > 1
            OR cos_hour < -1
            OR cos_hour > 1
            OR sin_doy < -1
            OR sin_doy > 1
            OR cos_doy < -1
            OR cos_doy > 1
        """
    )

    check(
        errors,
        "Solar physical/range violations",
        range_violations,
        0,
    )


# =============================================================================
# METEOSTAT
# =============================================================================

def audit_meteostat(
    conn,
    errors,
):
    section(
        "6. METEOSTAT AUXILIARY ARCHIVE"
    )

    check(
        errors,
        "Station snapshots",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_station_snapshot
            """
        ),
        EXPECTED_STATION_SNAPSHOTS,
    )

    check(
        errors,
        "Raw annual snapshots",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_raw_snapshot
            """
        ),
        EXPECTED_RAW_SNAPSHOTS,
    )

    check(
        errors,
        "Meteostat parameter rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            """
        ),
        EXPECTED_METEOSTAT_PARAMETER_ROWS,
    )

    distinct_hours = scalar(
        conn,
        """
        SELECT COUNT(
            DISTINCT observation_time
        )
        FROM aux_v1_meteostat_hourly
        """
    )

    check(
        errors,
        "Meteostat distinct source hours",
        distinct_hours,
        EXPECTED_METEOSTAT_HOURS,
    )

    check(
        errors,
        "Meteostat absent source hours",
        EXPECTED_SOLAR_ROWS
        - distinct_hours,
        EXPECTED_METEOSTAT_MISSING_HOURS,
    )

    check(
        errors,
        "Meteostat UNKNOWN availability rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE availability_basis='UNKNOWN'
            """
        ),
        EXPECTED_METEOSTAT_PARAMETER_ROWS,
    )

    check(
        errors,
        "Meteostat availability violations",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE
                availability_basis != 'UNKNOWN'
                OR source_available_time IS NOT NULL
            """
        ),
        0,
    )

    check(
        errors,
        "Meteostat out-of-window rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE
                observation_time
                    < '2024-09-02T16:00:00+00:00'
                OR observation_time
                    >= '2026-09-01T16:00:00+00:00'
            """
        ),
        0,
    )

    check(
        errors,
        "Raw snapshots not AUXILIARY_ONLY",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_raw_snapshot
            WHERE role != 'AUXILIARY_ONLY'
            """
        ),
        0,
    )

    check(
        errors,
        "Meteostat bad source rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE source != 'Meteostat'
            """
        ),
        0,
    )

    check(
        errors,
        "Meteostat bad timezone rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE timezone != 'UTC'
            """
        ),
        0,
    )

    check(
        errors,
        "Meteostat resolution violations",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE
                resolution_seconds != 3600
                OR interval_semantics
                    != 'SOURCE_HOUR_LABEL'
            """
        ),
        0,
    )

    check(
        errors,
        "VALID UNKNOWN-provider rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE
                value_kind='UNKNOWN'
                AND quality='VALID'
            """
        ),
        0,
    )

    check(
        errors,
        "Temperature OBSERVATION rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE
                parameter='temp'
                AND quality='VALID'
                AND value_kind='OBSERVATION'
            """
        ),
        EXPECTED_TEMP_OBSERVATION,
    )

    check(
        errors,
        "Temperature MODEL rows",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            WHERE
                parameter='temp'
                AND quality='VALID'
                AND value_kind='MODEL'
            """
        ),
        EXPECTED_TEMP_MODEL,
    )

    # Raw -> hourly provenance
    provenance_mismatch = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly h
        JOIN aux_v1_raw_snapshot r
          ON r.id = h.snapshot_id
        WHERE
            h.evidence_sha256
                != r.content_sha256
            OR h.source_version
                != r.source_version
            OR h.ingest_time
                != r.ingest_time
            OR h.availability_basis
                != r.availability_basis
            OR NOT (
                h.source_available_time
                IS r.source_available_time
            )
        """
    )

    check(
        errors,
        "Raw/hourly provenance mismatches",
        provenance_mismatch,
        0,
    )

    print()
    print(
        "Parameter summary:"
    )

    rows = conn.execute(
        """
        SELECT
            parameter,
            COUNT(*),

            SUM(
                CASE
                WHEN quality='VALID'
                 AND value_kind='OBSERVATION'
                THEN 1 ELSE 0 END
            ),

            SUM(
                CASE
                WHEN quality='VALID'
                 AND value_kind='MODEL'
                THEN 1 ELSE 0 END
            ),

            SUM(
                CASE
                WHEN quality='MISSING'
                THEN 1 ELSE 0 END
            ),

            SUM(
                CASE
                WHEN quality='UNSUPPORTED'
                THEN 1 ELSE 0 END
            ),

            SUM(
                CASE
                WHEN quality='INVALID'
                THEN 1 ELSE 0 END
            )

        FROM aux_v1_meteostat_hourly
        GROUP BY parameter
        ORDER BY parameter
        """
    ).fetchall()

    for row in rows:
        print(
            f"  {row[0]:5s} "
            f"total={row[1]:6d} "
            f"obs={row[2] or 0:6d} "
            f"model={row[3] or 0:6d} "
            f"missing={row[4] or 0:6d} "
            f"unsupported={row[5] or 0:6d} "
            f"invalid={row[6] or 0:6d}"
        )

    print()
    print(
        "Historical strict training eligibility: BLOCKED"
    )

    print(
        "Reason: historical availability is UNKNOWN."
    )


# =============================================================================
# APPEND-ONLY
# =============================================================================

def audit_triggers(
    conn,
    errors,
):
    section(
        "7. AUXILIARY APPEND-ONLY / PROVENANCE PROTECTION"
    )

    required = (
        "aux_v1_station_snapshot_block_update",
        "aux_v1_station_snapshot_block_delete",
        "aux_v1_raw_snapshot_block_update",
        "aux_v1_raw_snapshot_block_delete",
        "aux_v1_meteostat_hourly_block_update",
        "aux_v1_meteostat_hourly_block_delete",
        "aux_v1_solar_time_block_update",
        "aux_v1_solar_time_block_delete",
        "aux_v1_hourly_provenance",
    )

    existing = {
        row[0]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='trigger'
            """
        )
    }

    for trigger in required:
        check_true(
            errors,
            f"Trigger exists: {trigger}",
            trigger in existing,
        )


# =============================================================================
# MAIN
# =============================================================================

def main():
    errors = []

    if not MAIN_DB.exists():
        raise SystemExit(
            f"Missing production DB: {MAIN_DB}"
        )

    if not AUX_DB.exists():
        raise SystemExit(
            f"Missing auxiliary DB: {AUX_DB}"
        )

    main_sha_before = sha256_file(
        MAIN_DB
    )

    aux_sha_before = sha256_file(
        AUX_DB
    )

    print("=" * 96)
    print(
        "PHASE 3 AUXILIARY V1 — FINAL READ-ONLY AUDIT"
    )
    print("=" * 96)

    print(
        f"Production DB : {MAIN_DB}"
    )

    print(
        f"Auxiliary DB  : {AUX_DB}"
    )

    print(
        f"Main SHA256   : {main_sha_before}"
    )

    print(
        f"Aux SHA256    : {aux_sha_before}"
    )

    check(
        errors,
        "Production DB expected SHA256",
        main_sha_before,
        EXPECTED_MAIN_SHA256,
    )

    check(
        errors,
        "Auxiliary DB expected SHA256",
        aux_sha_before,
        EXPECTED_AUX_SHA256,
    )

    main_conn = open_readonly(
        MAIN_DB
    )

    aux_conn = open_readonly(
        AUX_DB
    )

    try:

        audit_main_database(
            main_conn,
            errors,
        )

        audit_ecmwf(
            main_conn,
            errors,
        )

        audit_issue_rule(
            main_conn,
            errors,
        )

        audit_aux_database(
            aux_conn,
            errors,
        )

        audit_solar(
            aux_conn,
            errors,
        )

        audit_meteostat(
            aux_conn,
            errors,
        )

        audit_triggers(
            aux_conn,
            errors,
        )

    finally:

        main_conn.close()
        aux_conn.close()

    # =========================================================================
    # SHA AFTER AUDIT
    # =========================================================================

    main_sha_after = sha256_file(
        MAIN_DB
    )

    aux_sha_after = sha256_file(
        AUX_DB
    )

    section(
        "8. READ-ONLY SHA PROTECTION"
    )

    check(
        errors,
        "Production SHA unchanged during audit",
        main_sha_after,
        main_sha_before,
    )

    check(
        errors,
        "Auxiliary SHA unchanged during audit",
        aux_sha_after,
        aux_sha_before,
    )

    # =========================================================================
    # FINAL
    # =========================================================================

    section(
        "FINAL RESULT"
    )

    if errors:

        print(
            "PHASE 3 FINAL AUDIT: FAIL"
        )

        print(
            f"Errors: {len(errors)}"
        )

        print()

        for i, error in enumerate(
            errors,
            start=1,
        ):
            print(
                f"{i:02d}. {error}"
            )

        raise SystemExit(1)

    print(
        "PHASE 3 FINAL AUDIT: PASS"
    )

    print()

    print(
        f"Ground Truth days      : "
        f"{EXPECTED_TARGET_ROWS}"
    )

    print(
        f"ECMWF canonical runs   : "
        f"{EXPECTED_ARCHIVE_RUNS}"
    )

    print(
        f"ECMWF unavailable runs : "
        f"{EXPECTED_UNAVAILABLE_RUNS}"
    )

    print(
        f"ECMWF source NULL rows : "
        f"{EXPECTED_NULL_TEMPERATURE_ROWS}"
    )

    print(
        f"Solar/Time rows        : "
        f"{EXPECTED_SOLAR_ROWS}"
    )

    print(
        f"Solar/Time days        : "
        f"{EXPECTED_SOLAR_DAYS}"
    )

    print(
        f"Meteostat source hours : "
        f"{EXPECTED_METEOSTAT_HOURS}"
    )

    print(
        f"Meteostat missing      : "
        f"{EXPECTED_METEOSTAT_MISSING_HOURS}"
    )

    print(
        f"Meteostat parameters   : "
        f"{EXPECTED_METEOSTAT_PARAMETER_ROWS}"
    )

    print()
    print(
        "Solar historical use   : ELIGIBLE"
    )

    print(
        "Meteostat historical   : ARCHIVE / RESEARCH ONLY"
    )

    print(
        "Meteostat training     : BLOCKED"
    )

    print(
        "Production DB modified : NO"
    )

    print(
        "Auxiliary DB modified  : NO"
    )

    print()

    print(
        "Production SHA256:"
    )

    print(
        main_sha_after
    )

    print()

    print(
        "Auxiliary SHA256:"
    )

    print(
        aux_sha_after
    )

    print()

    print(
        "RESULT: PHASE 3 AUXILIARY V1 ACCEPTED"
    )


if __name__ == "__main__":
    main()