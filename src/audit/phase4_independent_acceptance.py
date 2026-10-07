from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


# =============================================================================
# CONFIG
# =============================================================================

ROOT = Path(r"C:\ZUUU_Prediction_System")

MAIN_DB = ROOT / "database" / "zuuu_prediction.db"
AUX_DB = ROOT / "database" / "phase3_auxiliary_v1.db"
P4_DB = ROOT / "database" / "phase4_data_v1.db"

EXPECTED_MAIN_SHA = (
    "2e0149050ed11fe2d750b6f4798f7a51"
    "b313bf8f59bb671da5a076daeb124367"
)

EXPECTED_AUX_SHA = (
    "ec8defe1778a66ed9ea20dc9e3825579"
    "de685c1818862a3eef113bba9a624f22"
)

EXPECTED_DATASET_SEMANTIC_SHA = (
    "4eea080168f102f30314b0b8a64123af"
    "533923fd7f4b7e7c6e9312b5582835dd"
)

EXPECTED_ISSUE_RULE_SHA = (
    "7fdbf14f0f105870f06e9e4d29753718"
    "d8cfebfe98d7608c9f916ea441455504"
)

EXPECTED_TARGET_SEMANTIC_SHA = (
    "b8548609e64d10b787fc09d9fddb28e"
    "20acd1808b165f47e62b574580cc67f3e"
)

EXPECTED_TARGET_DAYS = 729
EXPECTED_SAMPLE_ROWS = 2187
EXPECTED_ELIGIBLE = 2186
EXPECTED_EXCLUDED = 1

EXPECTED_T0 = 729
EXPECTED_T1 = 729
EXPECTED_T2 = 728

EXPECTED_TRAJECTORY_ROWS = 52478
EXPECTED_ELIGIBLE_TRAJECTORY_ROWS = 52464

EXPECTED_SOLAR_ROWS = 17496

KNOWN_GAP_DATE = "2025-08-07"
KNOWN_GAP_HORIZON = "T2"
KNOWN_GAP_RUN = "2025-08-04T06:00:00+00:00"
KNOWN_GAP_VALID_HOURS = 14

errors: list[str] = []
warnings: list[str] = []


# =============================================================================
# HELPERS
# =============================================================================

def banner(title: str) -> None:
    print()
    print("=" * 110)
    print(title)
    print("=" * 110)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def connect_readonly(path: Path) -> sqlite3.Connection:
    """
    Immutable read-only SQLite connection.

    immutable=1 prevents SQLite from creating or modifying WAL/SHM sidecars.
    """
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"

    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")

    return conn


def scalar(conn: sqlite3.Connection, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()

    if row is None:
        return None

    return row[0]


def check(name: str, actual, expected) -> bool:
    ok = actual == expected

    print(
        f"{name:<72}"
        f"{'PASS' if ok else 'FAIL'}"
        f"   actual={actual!r} expected={expected!r}"
    )

    if not ok:
        errors.append(
            f"{name}: actual={actual!r}, expected={expected!r}"
        )

    return ok


def check_true(name: str, condition: bool, detail: str = "") -> bool:
    ok = bool(condition)

    suffix = f"   {detail}" if detail else ""

    print(
        f"{name:<72}"
        f"{'PASS' if ok else 'FAIL'}"
        f"{suffix}"
    )

    if not ok:
        errors.append(f"{name}: {detail}")

    return ok


def check_zero(name: str, actual: int) -> bool:
    return check(name, actual, 0)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type='table'
              AND name=?
            """,
            (name,),
        )
        == 1
    )


def view_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type='view'
              AND name=?
            """,
            (name,),
        )
        == 1
    )


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(
            f'PRAGMA table_info("{table}")'
        )
    ]


def normalize_sql(sql: str | None) -> str:
    if sql is None:
        return ""

    return " ".join(sql.lower().split())


# =============================================================================
# START
# =============================================================================

banner("PHASE 4 DATA V1 — INDEPENDENT ACCEPTANCE AUDIT V2")

print("Mode: STRICT READ-ONLY")
print("Purpose: independent verification of Phase 4 DATA V1")
print()


# =============================================================================
# 0. FILE EXISTENCE + SHA
# =============================================================================

banner("0. FROZEN INPUT FILES")

for db in (MAIN_DB, AUX_DB, P4_DB):
    check_true(
        f"Database exists: {db.name}",
        db.exists(),
        str(db),
    )

if errors:
    print()
    print("Required database missing. Audit aborted.")
    sys.exit(1)

main_sha_before = sha256_file(MAIN_DB)
aux_sha_before = sha256_file(AUX_DB)
p4_sha_before = sha256_file(P4_DB)

check(
    "Production DB frozen SHA256",
    main_sha_before,
    EXPECTED_MAIN_SHA,
)

check(
    "Phase3 auxiliary DB frozen SHA256",
    aux_sha_before,
    EXPECTED_AUX_SHA,
)

print(
    f"{'Phase4 physical DB SHA256':<72}"
    f"INFO   {p4_sha_before}"
)


# =============================================================================
# OPEN DATABASES READ-ONLY
# =============================================================================

main = connect_readonly(MAIN_DB)
aux = connect_readonly(AUX_DB)
p4 = connect_readonly(P4_DB)

try:

    # =========================================================================
    # 1. SQLITE INTEGRITY
    # =========================================================================

    banner("1. SQLITE INTEGRITY")

    for label, conn in (
        ("Production", main),
        ("Auxiliary", aux),
        ("Phase4", p4),
    ):
        check(
            f"{label} integrity_check",
            scalar(conn, "PRAGMA integrity_check"),
            "ok",
        )

        fk_errors = len(
            conn.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
        )

        check_zero(
            f"{label} foreign_key_check violations",
            fk_errors,
        )

    # =========================================================================
    # 2. REQUIRED PHASE 4 OBJECTS
    # =========================================================================

    banner("2. REQUIRED PHASE 4 OBJECTS")

    required_tables = (
        "phase4_data_v1_target",
        "phase4_data_v1_sample",
        "phase4_data_v1_ecmwf_hourly",
        "phase4_data_v1_solar",
        "phase4_data_v1_daily",
        "phase4_data_v1_manifest",
    )

    required_views = (
        "phase4_data_v1_training_hourly",
        "phase4_data_v1_training_labels",
    )

    for table in required_tables:
        check_true(
            f"Table exists: {table}",
            table_exists(p4, table),
        )

    for view in required_views:
        check_true(
            f"View exists: {view}",
            view_exists(p4, view),
        )

    if errors:
        raise RuntimeError(
            "Required Phase4 objects missing."
        )

    # =========================================================================
    # 3. REAL SCHEMA CONTRACT
    # =========================================================================

    banner("3. REAL SCHEMA CONTRACT")

    expected_target_columns = {
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
        "rule_version",
        "target_version",
        "target_status",
        "source_target_id",
        "source_provenance_json",
    }

    expected_sample_columns = {
        "business_date_bjt",
        "horizon",
        "issue_time_bjt",
        "issue_time_utc",
        "selected_ecmwf_run_time_utc",
        "selected_ecmwf_source_available_time_utc",
        "ecmwf_availability_semantics",
        "ecmwf_availability_type",
        "ecmwf_availability_rule_version",
        "ecmwf_archive_version",
        "ecmwf_issue_rule_version",
        "ecmwf_issue_rule_sha256",
        "ecmwf_canonical_rule_version",
        "ecmwf_content_sha256",
        "ecmwf_api_model",
        "ecmwf_data_spec_version",
        "source_ingest_time_utc",
        "sample_status",
        "meteostat_training_admission",
        "dataset_version",
        "created_at_utc",
        "target_tmax_c",
        "source_archive_id",
        "source_raw_run_id",
        "trajectory_expected_hours",
        "trajectory_present_hours",
        "trajectory_valid_hours",
        "exclusion_reason",
    }

    expected_hourly_key_columns = {
        "business_date_bjt",
        "horizon",
        "selected_ecmwf_run_time_utc",
        "target_time_utc",
        "target_time_bjt",
        "solar_target_time",
        "source_available_time_utc",
        "temperature_2m_c",
        "source_raw_run_id",
        "source_hourly_id",
        "lead_hours",
        "parse_success",
    }

    actual_target_columns = set(
        columns(p4, "phase4_data_v1_target")
    )

    actual_sample_columns = set(
        columns(p4, "phase4_data_v1_sample")
    )

    actual_hourly_columns = set(
        columns(p4, "phase4_data_v1_ecmwf_hourly")
    )

    check_true(
        "Target required columns present",
        expected_target_columns <= actual_target_columns,
        str(
            sorted(
                expected_target_columns
                - actual_target_columns
            )
        ),
    )

    check_true(
        "Sample required columns present",
        expected_sample_columns <= actual_sample_columns,
        str(
            sorted(
                expected_sample_columns
                - actual_sample_columns
            )
        ),
    )

    check_true(
        "Hourly required columns present",
        expected_hourly_key_columns <= actual_hourly_columns,
        str(
            sorted(
                expected_hourly_key_columns
                - actual_hourly_columns
            )
        ),
    )

    # =========================================================================
    # 4. CARDINALITY
    # =========================================================================

    banner("4. DATASET CARDINALITY")

    check(
        "Ground Truth target rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_target
            """
        ),
        EXPECTED_TARGET_DAYS,
    )

    check(
        "Horizon/day sample rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_sample
            """
        ),
        EXPECTED_SAMPLE_ROWS,
    )

    check(
        "ECMWF hourly trajectory rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_ecmwf_hourly
            """
        ),
        EXPECTED_TRAJECTORY_ROWS,
    )

    check(
        "Solar rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_solar
            """
        ),
        EXPECTED_SOLAR_ROWS,
    )

    check(
        "Daily audit rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_daily
            """
        ),
        EXPECTED_SAMPLE_ROWS,
    )

    check(
        "Manifest rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_manifest
            """
        ),
        1,
    )

    # =========================================================================
    # 5. SAMPLE IDENTITY / UNIQUENESS
    # =========================================================================

    banner("5. SAMPLE IDENTITY / UNIQUENESS")

    duplicate_samples = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                business_date_bjt,
                horizon,
                COUNT(*) AS n
            FROM phase4_data_v1_sample
            GROUP BY
                business_date_bjt,
                horizon
            HAVING n != 1
        )
        """
    )

    check_zero(
        "Duplicate date+horizon sample identities",
        duplicate_samples,
    )

    check(
        "Distinct business dates",
        scalar(
            p4,
            """
            SELECT COUNT(DISTINCT business_date_bjt)
            FROM phase4_data_v1_sample
            """
        ),
        729,
    )

    check(
        "Distinct horizons",
        scalar(
            p4,
            """
            SELECT COUNT(DISTINCT horizon)
            FROM phase4_data_v1_sample
            """
        ),
        3,
    )

    invalid_horizons = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample
        WHERE horizon NOT IN ('T0','T1','T2')
        """
    )

    check_zero(
        "Invalid horizon labels",
        invalid_horizons,
    )

    # =========================================================================
    # 6. ELIGIBILITY
    # =========================================================================

    banner("6. ELIGIBILITY")

    for horizon, expected in (
        ("T0", EXPECTED_T0),
        ("T1", EXPECTED_T1),
        ("T2", EXPECTED_T2),
    ):
        actual = scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_sample
            WHERE horizon=?
              AND sample_status='ELIGIBLE'
            """,
            (horizon,),
        )

        check(
            f"{horizon} eligible samples",
            actual,
            expected,
        )

    eligible_total = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample
        WHERE sample_status='ELIGIBLE'
        """
    )

    excluded_total = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample
        WHERE sample_status!='ELIGIBLE'
        """
    )

    check(
        "Total eligible samples",
        eligible_total,
        EXPECTED_ELIGIBLE,
    )

    check(
        "Total excluded samples",
        excluded_total,
        EXPECTED_EXCLUDED,
    )

    bad_eligible_coverage = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample
        WHERE sample_status='ELIGIBLE'
          AND (
                trajectory_expected_hours != 24
                OR trajectory_present_hours != 24
                OR trajectory_valid_hours != 24
                OR exclusion_reason IS NOT NULL
              )
        """
    )

    check_zero(
        "Eligible samples violating 24/24 contract",
        bad_eligible_coverage,
    )

    # =========================================================================
    # 7. KNOWN GAP
    # =========================================================================

    banner("7. KNOWN GAP — 2025-08-07 / T2")

    gap_rows = p4.execute(
        """
        SELECT *
        FROM phase4_data_v1_sample
        WHERE business_date_bjt=?
          AND horizon=?
        """,
        (
            KNOWN_GAP_DATE,
            KNOWN_GAP_HORIZON,
        ),
    ).fetchall()

    check(
        "Known gap sample count",
        len(gap_rows),
        1,
    )

    if len(gap_rows) == 1:
        gap = gap_rows[0]

        check(
            "Known gap status",
            gap["sample_status"],
            "EXCLUDED_INCOMPLETE_ECMWF",
        )

        check(
            "Known gap selected run",
            gap["selected_ecmwf_run_time_utc"],
            KNOWN_GAP_RUN,
        )

        check(
            "Known gap expected hours",
            gap["trajectory_expected_hours"],
            24,
        )

        check(
            "Known gap present hours",
            gap["trajectory_present_hours"],
            14,
        )

        check(
            "Known gap valid hours",
            gap["trajectory_valid_hours"],
            KNOWN_GAP_VALID_HOURS,
        )

        check_true(
            "Known gap has exclusion reason",
            bool(gap["exclusion_reason"]),
            repr(gap["exclusion_reason"]),
        )

    gap_hour_count = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_ecmwf_hourly
        WHERE business_date_bjt=?
          AND horizon=?
        """,
        (
            KNOWN_GAP_DATE,
            KNOWN_GAP_HORIZON,
        ),
    )

    check(
        "Known gap physical trajectory rows",
        gap_hour_count,
        14,
    )

    gap_lead_min = scalar(
        p4,
        """
        SELECT MIN(lead_hours)
        FROM phase4_data_v1_ecmwf_hourly
        WHERE business_date_bjt=?
          AND horizon=?
        """,
        (
            KNOWN_GAP_DATE,
            KNOWN_GAP_HORIZON,
        ),
    )

    gap_lead_max = scalar(
        p4,
        """
        SELECT MAX(lead_hours)
        FROM phase4_data_v1_ecmwf_hourly
        WHERE business_date_bjt=?
          AND horizon=?
        """,
        (
            KNOWN_GAP_DATE,
            KNOWN_GAP_HORIZON,
        ),
    )

    check(
        "Known gap minimum lead",
        gap_lead_min,
        58,
    )

    check(
        "Known gap maximum lead",
        gap_lead_max,
        71,
    )

    # =========================================================================
    # 8. LOOK-AHEAD / AVAILABILITY
    # =========================================================================

    banner("8. LOOK-AHEAD / AVAILABILITY")

    leakage = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample
        WHERE julianday(
            selected_ecmwf_source_available_time_utc
        ) > julianday(issue_time_utc)
        """
    )

    check_zero(
        "Samples using ECMWF available after issue time",
        leakage,
    )

    null_issue = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample
        WHERE issue_time_utc IS NULL
           OR selected_ecmwf_source_available_time_utc IS NULL
        """
    )

    check_zero(
        "Samples with NULL issue/availability time",
        null_issue,
    )

    run_after_availability = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample
        WHERE julianday(selected_ecmwf_run_time_utc)
            > julianday(
                selected_ecmwf_source_available_time_utc
              )
        """
    )

    check_zero(
        "Selected run later than its availability time",
        run_after_availability,
    )

    # =========================================================================
    # 9. ISSUE RULE HASH CONSISTENCY
    # =========================================================================

    banner("9. ISSUE RULE CONSISTENCY")

    issue_hashes = [
        row[0]
        for row in p4.execute(
            """
            SELECT DISTINCT ecmwf_issue_rule_sha256
            FROM phase4_data_v1_sample
            ORDER BY ecmwf_issue_rule_sha256
            """
        )
    ]

    check(
        "Distinct Issue Rule hashes",
        len(issue_hashes),
        1,
    )

    if len(issue_hashes) == 1:
        check(
            "Frozen Issue Rule SHA256",
            issue_hashes[0],
            EXPECTED_ISSUE_RULE_SHA,
        )

    # =========================================================================
    # 10. ECMWF TRAJECTORY INTEGRITY
    # =========================================================================

    banner("10. ECMWF TRAJECTORY INTEGRITY")

    duplicate_hour_keys = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                business_date_bjt,
                horizon,
                target_time_utc,
                COUNT(*) AS n
            FROM phase4_data_v1_ecmwf_hourly
            GROUP BY
                business_date_bjt,
                horizon,
                target_time_utc
            HAVING n != 1
        )
        """
    )

    check_zero(
        "Duplicate trajectory hour keys",
        duplicate_hour_keys,
    )

    orphan_hourly = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_ecmwf_hourly h
        LEFT JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=h.business_date_bjt
         AND s.horizon=h.horizon
        WHERE s.business_date_bjt IS NULL
        """
    )

    check_zero(
        "Trajectory rows without parent sample",
        orphan_hourly,
    )

    cross_run_samples = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                business_date_bjt,
                horizon,
                COUNT(
                    DISTINCT selected_ecmwf_run_time_utc
                ) AS n
            FROM phase4_data_v1_ecmwf_hourly
            GROUP BY
                business_date_bjt,
                horizon
            HAVING n != 1
        )
        """
    )

    check_zero(
        "Samples mixing multiple ECMWF runs",
        cross_run_samples,
    )

    run_mismatch = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_ecmwf_hourly h
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=h.business_date_bjt
         AND s.horizon=h.horizon
        WHERE h.selected_ecmwf_run_time_utc
              != s.selected_ecmwf_run_time_utc
        """
    )

    check_zero(
        "Hourly run differs from selected sample run",
        run_mismatch,
    )

    availability_mismatch = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_ecmwf_hourly h
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=h.business_date_bjt
         AND s.horizon=h.horizon
        WHERE h.source_available_time_utc
              != s.selected_ecmwf_source_available_time_utc
        """
    )

    check_zero(
        "Hourly availability differs from sample availability",
        availability_mismatch,
    )

    eligible_hourly = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_ecmwf_hourly h
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=h.business_date_bjt
         AND s.horizon=h.horizon
        WHERE s.sample_status='ELIGIBLE'
        """
    )

    check(
        "Eligible trajectory rows",
        eligible_hourly,
        EXPECTED_ELIGIBLE_TRAJECTORY_ROWS,
    )

    eligible_null_temp = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_ecmwf_hourly h
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=h.business_date_bjt
         AND s.horizon=h.horizon
        WHERE s.sample_status='ELIGIBLE'
          AND h.temperature_2m_c IS NULL
        """
    )

    check_zero(
        "Eligible trajectory NULL temperature",
        eligible_null_temp,
    )

    bad_parse = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_ecmwf_hourly h
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=h.business_date_bjt
         AND s.horizon=h.horizon
        WHERE s.sample_status='ELIGIBLE'
          AND h.parse_success != 1
        """
    )

    check_zero(
        "Eligible trajectory parse failures",
        bad_parse,
    )

    # =========================================================================
    # 11. GROUND TRUTH EXACT CROSS-CHECK
    # =========================================================================

    banner("11. GROUND TRUTH EXACT CROSS-CHECK")

    source_targets = {
        row["business_date_bjt"]: row
        for row in main.execute(
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
                rule_version,
                target_version,
                target_status
            FROM zuuu_target_v1
            ORDER BY business_date_bjt
            """
        )
    }

    phase4_targets = {
        row["business_date_bjt"]: row
        for row in p4.execute(
            """
            SELECT
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
                rule_version,
                target_version,
                target_status,
                source_target_id
            FROM phase4_data_v1_target
            ORDER BY business_date_bjt
            """
        )
    }

    check(
        "Source Ground Truth row count",
        len(source_targets),
        729,
    )

    check(
        "Phase4 Ground Truth row count",
        len(phase4_targets),
        729,
    )

    check(
        "Ground Truth date sets identical",
        set(phase4_targets),
        set(source_targets),
    )

    target_mismatches = []

    compare_fields = (
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
        "rule_version",
        "target_version",
        "target_status",
    )

    for date, src in source_targets.items():
        dst = phase4_targets.get(date)

        if dst is None:
            target_mismatches.append(
                (date, "MISSING")
            )
            continue

        if dst["source_target_id"] != src["id"]:
            target_mismatches.append(
                (
                    date,
                    "source_target_id",
                    dst["source_target_id"],
                    src["id"],
                )
            )

        for field in compare_fields:
            if dst[field] != src[field]:
                target_mismatches.append(
                    (
                        date,
                        field,
                        dst[field],
                        src[field],
                    )
                )

    check_zero(
        "Ground Truth field-level mismatches",
        len(target_mismatches),
    )

    if target_mismatches:
        print("First Ground Truth mismatches:")
        for item in target_mismatches[:10]:
            print("   ", item)

    # Sample labels must also match frozen target.

    sample_label_mismatch = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample s
        JOIN phase4_data_v1_target t
          ON t.business_date_bjt=s.business_date_bjt
        WHERE s.target_tmax_c != t.daily_tmax_c
        """
    )

    check_zero(
        "Sample label differs from frozen target",
        sample_label_mismatch,
    )

    # =========================================================================
    # 12. SOLAR EXACT CROSS-CHECK
    # =========================================================================

    banner("12. SOLAR EXACT CROSS-CHECK")

    check(
        "Phase3 Solar rows",
        scalar(
            aux,
            """
            SELECT COUNT(*)
            FROM aux_v1_solar_time
            """
        ),
        EXPECTED_SOLAR_ROWS,
    )

    check(
        "Phase4 Solar rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_solar
            """
        ),
        EXPECTED_SOLAR_ROWS,
    )

    aux_solar_cols = columns(
        aux,
        "aux_v1_solar_time",
    )

    p4_solar_cols = columns(
        p4,
        "phase4_data_v1_solar",
    )

    print("Phase3 Solar columns:", aux_solar_cols)
    print("Phase4 Solar columns:", p4_solar_cols)

    # Compare all common semantic columns except provenance/build metadata.
    excluded_solar_compare = {
        "id",
        "created_at_utc",
        "source_id",
    }

    common_solar_columns = [
        c
        for c in p4_solar_cols
        if c in aux_solar_cols
        and c not in excluded_solar_compare
    ]

    check_true(
        "Solar common semantic columns available",
        len(common_solar_columns) > 0,
        repr(common_solar_columns),
    )

    if common_solar_columns:
        order_col = (
            "target_time"
            if "target_time" in common_solar_columns
            else (
                "target_time_utc"
                if "target_time_utc" in common_solar_columns
                else common_solar_columns[0]
            )
        )

        cols_sql = ", ".join(
            f'"{c}"'
            for c in common_solar_columns
        )

        aux_solar_rows = [
            tuple(row)
            for row in aux.execute(
                f"""
                SELECT {cols_sql}
                FROM aux_v1_solar_time
                ORDER BY "{order_col}"
                """
            )
        ]

        p4_solar_rows = [
            tuple(row)
            for row in p4.execute(
                f"""
                SELECT {cols_sql}
                FROM phase4_data_v1_solar
                ORDER BY "{order_col}"
                """
            )
        ]

        check(
            "Solar semantic rows exact equality",
            p4_solar_rows,
            aux_solar_rows,
        )

    # Every trajectory row must join to exactly one solar row.

    missing_solar = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_ecmwf_hourly h
        LEFT JOIN phase4_data_v1_solar z
          ON z.target_time=h.solar_target_time
        WHERE z.target_time IS NULL
        """
    )

    check_zero(
        "Trajectory rows without Solar match",
        missing_solar,
    )

    # =========================================================================
    # 13. METEOSTAT MUST BE BLOCKED
    # =========================================================================

    banner("13. METEOSTAT TRAINING EXCLUSION")

    sample_meteostat_not_blocked = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_sample
        WHERE meteostat_training_admission != 'BLOCKED'
        """
    )

    check_zero(
        "Samples where Meteostat is not BLOCKED",
        sample_meteostat_not_blocked,
    )

    manifest_meteostat = scalar(
        p4,
        """
        SELECT meteostat_training_admission
        FROM phase4_data_v1_manifest
        LIMIT 1
        """
    )

    check(
        "Manifest Meteostat training admission",
        manifest_meteostat,
        "BLOCKED",
    )

    training_hourly_sql = scalar(
        p4,
        """
        SELECT sql
        FROM sqlite_master
        WHERE type='view'
          AND name='phase4_data_v1_training_hourly'
        """
    )

    training_labels_sql = scalar(
        p4,
        """
        SELECT sql
        FROM sqlite_master
        WHERE type='view'
          AND name='phase4_data_v1_training_labels'
        """
    )

    normalized_hourly_sql = normalize_sql(
        training_hourly_sql
    )

    normalized_label_sql = normalize_sql(
        training_labels_sql
    )

    check(
        "Meteostat referenced by hourly training view",
        "meteostat" in normalized_hourly_sql,
        False,
    )

    check(
        "Meteostat referenced by label training view",
        "meteostat" in normalized_label_sql,
        False,
    )

    # =========================================================================
    # 14. TRAINING FEATURE LEAKAGE SAFETY
    # =========================================================================

    banner("14. TRAINING FEATURE / LABEL SEPARATION")

    hourly_view_columns = columns(
        p4,
        "phase4_data_v1_training_hourly",
    )

    label_view_columns = columns(
        p4,
        "phase4_data_v1_training_labels",
    )

    print(
        "Training hourly columns:",
        hourly_view_columns,
    )

    print(
        "Training label columns:",
        label_view_columns,
    )

    forbidden_hourly_columns = {
        "target_tmax_c",
        "daily_tmax_c",
        "error_c",
        "absolute_error_c",
        "forecast_error",
        "abs_error",
    }

    leaked_columns = (
        set(hourly_view_columns)
        & forbidden_hourly_columns
    )

    check(
        "Forbidden target/error fields in training features",
        sorted(leaked_columns),
        [],
    )

    check(
        "Training label columns",
        label_view_columns,
        [
            "business_date_bjt",
            "horizon",
            "target_tmax_c",
        ],
    )

    training_hourly_rows = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_training_hourly
        """
    )

    check(
        "Training hourly rows",
        training_hourly_rows,
        EXPECTED_ELIGIBLE_TRAJECTORY_ROWS,
    )

    training_label_rows = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_training_labels
        """
    )

    check(
        "Training label rows",
        training_label_rows,
        EXPECTED_ELIGIBLE,
    )

    excluded_in_hourly_training = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_training_hourly v
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=v.business_date_bjt
         AND s.horizon=v.horizon
        WHERE s.sample_status!='ELIGIBLE'
        """
    )

    check_zero(
        "Excluded samples leaking into hourly training view",
        excluded_in_hourly_training,
    )

    excluded_in_label_training = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_training_labels v
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=v.business_date_bjt
         AND s.horizon=v.horizon
        WHERE s.sample_status!='ELIGIBLE'
        """
    )

    check_zero(
        "Excluded samples leaking into label training view",
        excluded_in_label_training,
    )

    known_gap_training_hourly = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_training_hourly
        WHERE business_date_bjt=?
          AND horizon=?
        """,
        (
            KNOWN_GAP_DATE,
            KNOWN_GAP_HORIZON,
        ),
    )

    known_gap_training_label = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_training_labels
        WHERE business_date_bjt=?
          AND horizon=?
        """,
        (
            KNOWN_GAP_DATE,
            KNOWN_GAP_HORIZON,
        ),
    )

    check_zero(
        "Known T2 gap rows in hourly training view",
        known_gap_training_hourly,
    )

    check_zero(
        "Known T2 gap rows in label training view",
        known_gap_training_label,
    )

    # =========================================================================
    # 15. DAILY AUDIT TABLE
    # =========================================================================

    banner("15. DAILY AUDIT TABLE")

    daily_duplicate = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                business_date_bjt,
                horizon,
                COUNT(*) AS n
            FROM phase4_data_v1_daily
            GROUP BY
                business_date_bjt,
                horizon
            HAVING n != 1
        )
        """
    )

    check_zero(
        "Duplicate daily audit identities",
        daily_duplicate,
    )

    daily_orphans = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_daily d
        LEFT JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=d.business_date_bjt
         AND s.horizon=d.horizon
        WHERE s.business_date_bjt IS NULL
        """
    )

    check_zero(
        "Daily audit rows without sample",
        daily_orphans,
    )

    incomplete_marked_complete = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_daily d
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=d.business_date_bjt
         AND s.horizon=d.horizon
        WHERE s.sample_status!='ELIGIBLE'
          AND d.trajectory_complete != 0
        """
    )

    check_zero(
        "Excluded sample incorrectly marked trajectory_complete",
        incomplete_marked_complete,
    )

    eligible_not_complete = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_daily d
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=d.business_date_bjt
         AND s.horizon=d.horizon
        WHERE s.sample_status='ELIGIBLE'
          AND d.trajectory_complete != 1
        """
    )

    check_zero(
        "Eligible sample not marked trajectory_complete",
        eligible_not_complete,
    )

    # Recalculate daily max from trajectory.

    daily_max_mismatch = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_daily d
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=d.business_date_bjt
         AND s.horizon=d.horizon
        JOIN (
            SELECT
                business_date_bjt,
                horizon,
                MAX(temperature_2m_c) AS calc_max
            FROM phase4_data_v1_ecmwf_hourly
            GROUP BY
                business_date_bjt,
                horizon
        ) x
          ON x.business_date_bjt=d.business_date_bjt
         AND x.horizon=d.horizon
        WHERE s.sample_status='ELIGIBLE'
          AND ABS(
                d.ecmwf_daily_max_c
                - x.calc_max
              ) > 0.0000001
        """
    )

    check_zero(
        "ECMWF daily max differs from hourly trajectory",
        daily_max_mismatch,
    )

    # Error convention must be internally consistent.
    # Determine convention rather than assume sign.

    error_plus = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_daily d
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=d.business_date_bjt
         AND s.horizon=d.horizon
        WHERE s.sample_status='ELIGIBLE'
          AND ABS(
                d.error_c
                - (
                    d.ecmwf_daily_max_c
                    - s.target_tmax_c
                  )
              ) > 0.0000001
        """
    )

    error_minus = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_daily d
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=d.business_date_bjt
         AND s.horizon=d.horizon
        WHERE s.sample_status='ELIGIBLE'
          AND ABS(
                d.error_c
                - (
                    s.target_tmax_c
                    - d.ecmwf_daily_max_c
                  )
              ) > 0.0000001
        """
    )

    check_true(
        "Daily error convention internally valid",
        error_plus == 0 or error_minus == 0,
        (
            f"forecast-target mismatches={error_plus}, "
            f"target-forecast mismatches={error_minus}"
        ),
    )

    absolute_error_mismatch = scalar(
        p4,
        """
        SELECT COUNT(*)
        FROM phase4_data_v1_daily d
        JOIN phase4_data_v1_sample s
          ON s.business_date_bjt=d.business_date_bjt
         AND s.horizon=d.horizon
        WHERE s.sample_status='ELIGIBLE'
          AND ABS(
                d.absolute_error_c
                - ABS(d.error_c)
              ) > 0.0000001
        """
    )

    check_zero(
        "absolute_error_c != abs(error_c)",
        absolute_error_mismatch,
    )

    # =========================================================================
    # 16. MANIFEST
    # =========================================================================

    banner("16. DATA V1 MANIFEST")

    manifest = p4.execute(
        """
        SELECT *
        FROM phase4_data_v1_manifest
        LIMIT 1
        """
    ).fetchone()

    check_true(
        "Manifest exists",
        manifest is not None,
    )

    if manifest is not None:
        check(
            "Manifest dataset version",
            manifest["dataset_version"],
            "DATA_V1",
        )

        check(
            "Manifest build status",
            manifest["build_status"],
            "ACCEPTED",
        )

        check(
            "Manifest semantic SHA256",
            manifest["dataset_semantic_sha256"],
            EXPECTED_DATASET_SEMANTIC_SHA,
        )

        check(
            "Manifest production source SHA256",
            manifest["source_production_sha256"],
            EXPECTED_MAIN_SHA,
        )

        check(
            "Manifest auxiliary source SHA256",
            manifest["source_auxiliary_sha256"],
            EXPECTED_AUX_SHA,
        )

        check(
            "Manifest Issue Rule SHA256",
            manifest["issue_rule_sha256"],
            EXPECTED_ISSUE_RULE_SHA,
        )

        check(
            "Manifest Meteostat admission",
            manifest["meteostat_training_admission"],
            "BLOCKED",
        )

        try:
            audit_json = json.loads(
                manifest["audit_json"]
            )

            check_true(
                "Manifest audit_json valid JSON",
                isinstance(audit_json, dict),
            )

        except Exception as exc:
            check_true(
                "Manifest audit_json valid JSON",
                False,
                repr(exc),
            )

        try:
            impl_json = json.loads(
                manifest[
                    "implementation_sha256_json"
                ]
            )

            check_true(
                "Manifest implementation hash JSON valid",
                isinstance(impl_json, dict),
            )

        except Exception as exc:
            check_true(
                "Manifest implementation hash JSON valid",
                False,
                repr(exc),
            )

    # =========================================================================
    # 17. BASIC VERSION CONSISTENCY
    # =========================================================================

    banner("17. VERSION CONSISTENCY")

    check(
        "Distinct dataset_version values",
        scalar(
            p4,
            """
            SELECT COUNT(DISTINCT dataset_version)
            FROM phase4_data_v1_sample
            """
        ),
        1,
    )

    check(
        "Non-DATA_V1 sample rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_sample
            WHERE dataset_version!='DATA_V1'
            """
        ),
        0,
    )

    check(
        "Non-BLOCKED Meteostat sample rows",
        scalar(
            p4,
            """
            SELECT COUNT(*)
            FROM phase4_data_v1_sample
            WHERE meteostat_training_admission!='BLOCKED'
            """
        ),
        0,
    )

finally:
    main.close()
    aux.close()
    p4.close()


# =============================================================================
# 18. READ-ONLY SHA GUARANTEE
# =============================================================================

banner("18. READ-ONLY SHA GUARANTEE")

main_sha_after = sha256_file(MAIN_DB)
aux_sha_after = sha256_file(AUX_DB)
p4_sha_after = sha256_file(P4_DB)

check(
    "Production DB unchanged during independent audit",
    main_sha_after,
    main_sha_before,
)

check(
    "Auxiliary DB unchanged during independent audit",
    aux_sha_after,
    aux_sha_before,
)

check(
    "Phase4 DB unchanged during independent audit",
    p4_sha_after,
    p4_sha_before,
)


# =============================================================================
# FINAL RESULT
# =============================================================================

banner("FINAL RESULT")

if warnings:
    print()
    print(f"WARNINGS: {len(warnings)}")

    for i, warning in enumerate(warnings, 1):
        print(f"{i:02d}. {warning}")

if errors:
    print()
    print("PHASE 4 INDEPENDENT ACCEPTANCE V2: FAIL")
    print()
    print(f"ERROR COUNT: {len(errors)}")
    print()

    for i, error in enumerate(errors, 1):
        print(f"{i:02d}. {error}")

    print()
    print("RESULT: PHASE 4 NOT YET FROZEN")

    sys.exit(1)


print()
print("PHASE 4 INDEPENDENT ACCEPTANCE V2: PASS")
print()
print("Ground Truth days              : 729")
print("Horizon/day samples            : 2187")
print("Eligible samples               : 2186")
print("Excluded samples               : 1")
print("T0 eligible                    : 729")
print("T1 eligible                    : 729")
print("T2 eligible                    : 728")
print("Known gap                      : 2025-08-07 / T2")
print("Known gap valid trajectory     : 14 / 24")
print("Eligible ECMWF hourly rows     : 52464")
print("Leakage violations             : 0")
print("Solar rows                     : 17496")
print("Meteostat training admission   : BLOCKED")
print("Training feature label leakage : 0")
print()
print("DATA V1 semantic SHA256:")
print(EXPECTED_DATASET_SEMANTIC_SHA)
print()
print("Production DB modified         : NO")
print("Auxiliary DB modified          : NO")
print("Phase4 DB modified             : NO")
print()
print("RESULT: PHASE 4 DATA V1 INDEPENDENTLY ACCEPTED")
print("STATUS: READY FOR PHASE 4 FREEZE / CLOSE")