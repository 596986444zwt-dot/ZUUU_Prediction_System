from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path


# ============================================================
# Phase 5 Independent Acceptance V2
#
# ECMWF RAW BASELINE V1 独立验收
#
# 原则：
#   1. Phase 1~5 数据库全部只读
#   2. 不相信 Phase 5 已保存的预测，重新从 Phase 4 hourly 计算
#   3. 不相信 Phase 5 已保存的误差，重新计算
#   4. 不相信 Phase 5 已保存的总体指标，重新计算
#   5. 不重新选择 ECMWF run
#   6. 不补值、不插值、不跨 run 拼接
#   7. 验收前后所有数据库物理 SHA 必须一致
# ============================================================


ROOT = Path(r"C:\ZUUU_Prediction_System")

PRODUCTION_DB = ROOT / "database" / "zuuu_prediction.db"
AUX_DB = ROOT / "database" / "phase3_auxiliary_v1.db"
PHASE4_DB = ROOT / "database" / "phase4_data_v1.db"
PHASE5_DB = ROOT / "database" / "phase5_ecmwf_raw_baseline_v1.db"


EXPECTED_PRODUCTION_SHA = (
    "2e0149050ed11fe2d750b6f4798f7a51"
    "b313bf8f59bb671da5a076daeb124367"
)

EXPECTED_AUX_SHA = (
    "ec8defe1778a66ed9ea20dc9e3825579"
    "de685c1818862a3eef113bba9a624f22"
)

EXPECTED_PHASE4_PHYSICAL_SHA = (
    "9d1901aa0f6c9c3106450cb9d41d885"
    "797745f087a80c497cff082f67094a892"
)

EXPECTED_PHASE4_SEMANTIC_SHA = (
    "4eea080168f102f30314b0b8a64123af"
    "533923fd7f4b7e7c6e9312b5582835dd"
)

EXPECTED_PHASE5_SEMANTIC_SHA = (
    "4f31fa80c704eed5100b1add4d37cebf"
    "120b1e56fe1c4e2ba65a140d9d8fd353"
)


EXPECTED_SOURCE_SAMPLES = 2187
EXPECTED_ELIGIBLE = 2186

EXPECTED_COUNTS = {
    "T0": 729,
    "T1": 729,
    "T2": 728,
}

EXPECTED_GAP_DATE = "2025-08-07"
EXPECTED_GAP_HORIZON = "T2"
EXPECTED_GAP_HOURS = 14

TOL = 1e-8


# ============================================================
# Global counters
# ============================================================

PASS_COUNT = 0
WARN_COUNT = 0
FAIL_COUNT = 0


def section(title: str):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def pass_(message: str):
    global PASS_COUNT
    PASS_COUNT += 1
    print(f"[PASS] {message}")


def warn(message: str):
    global WARN_COUNT
    WARN_COUNT += 1
    print(f"[WARN] {message}")


def fail(message: str):
    global FAIL_COUNT
    FAIL_COUNT += 1
    print(f"[FAIL] {message}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def ro_connect(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    conn.row_factory = sqlite3.Row

    return conn


def table_exists(
    conn: sqlite3.Connection,
    name: str,
) -> bool:

    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name = ?
        """,
        (name,),
    ).fetchone()

    return row is not None


def get_columns(
    conn: sqlite3.Connection,
    table: str,
) -> list[str]:

    return [
        row["name"]
        for row in conn.execute(
            f'PRAGMA table_info("{table}")'
        )
    ]


def first_existing(
    columns: list[str],
    candidates: list[str],
):

    for candidate in candidates:
        if candidate in columns:
            return candidate

    return None


def almost_equal(
    a,
    b,
    tolerance=TOL,
) -> bool:

    if a is None or b is None:
        return a is None and b is None

    try:
        return math.isclose(
            float(a),
            float(b),
            rel_tol=0.0,
            abs_tol=tolerance,
        )
    except Exception:
        return False


def percentile_nearest_rank(
    values: list[float],
    p: float,
) -> float:

    if not values:
        return float("nan")

    values = sorted(values)

    rank = max(
        1,
        math.ceil(p * len(values)),
    )

    return values[rank - 1]


def calc_metrics(rows):

    errors = [
        float(r["error"])
        for r in rows
    ]

    aes = [
        abs(x)
        for x in errors
    ]

    forecasts = [
        float(r["forecast"])
        for r in rows
    ]

    observed = [
        float(r["observed"])
        for r in rows
    ]

    n = len(rows)

    bias = sum(errors) / n

    mae = sum(aes) / n

    rmse = math.sqrt(
        sum(x * x for x in errors) / n
    )

    median_ae = statistics.median(aes)

    return {
        "n": n,
        "bias": bias,
        "mae": mae,
        "rmse": rmse,
        "median_ae": median_ae,
        "p90_nearest_rank": percentile_nearest_rank(
            aes,
            0.90,
        ),
        "max_ae": max(aes),
        "mean_forecast": sum(forecasts) / n,
        "mean_observed": sum(observed) / n,
        "ae_05_count": sum(
            x <= 0.5 + TOL
            for x in aes
        ),
        "ae_10_count": sum(
            x <= 1.0 + TOL
            for x in aes
        ),
        "ae_20_count": sum(
            x <= 2.0 + TOL
            for x in aes
        ),
    }


# ============================================================
# Header
# ============================================================

print()
print("=" * 78)
print("PHASE 5 INDEPENDENT ACCEPTANCE V2")
print("ECMWF RAW BASELINE V1")
print("=" * 78)
print()
print("MODE: STRICT READ-ONLY")
print("No database will be modified.")


# ============================================================
# 1. Required DB
# ============================================================

section("1. REQUIRED DATABASE FILES")

required_files = [
    PRODUCTION_DB,
    AUX_DB,
    PHASE4_DB,
    PHASE5_DB,
]

missing = []

for path in required_files:

    if path.exists():
        pass_(f"Exists: {path}")
    else:
        fail(f"Missing: {path}")
        missing.append(path)


if missing:

    print()
    print("Required database missing.")
    print("Acceptance aborted.")

    sys.exit(2)


# ============================================================
# 2. Physical SHA before
# ============================================================

section("2. DATABASE SHA256 — BEFORE AUDIT")

before_sha = {
    "production": sha256_file(PRODUCTION_DB),
    "auxiliary": sha256_file(AUX_DB),
    "phase4": sha256_file(PHASE4_DB),
    "phase5": sha256_file(PHASE5_DB),
}


for name, value in before_sha.items():
    print(f"{name:12s}: {value}")


if before_sha["production"] == EXPECTED_PRODUCTION_SHA:
    pass_("Production DB physical SHA matches frozen value")
else:
    fail(
        "Production DB physical SHA mismatch"
    )


if before_sha["auxiliary"] == EXPECTED_AUX_SHA:
    pass_("Phase 3 auxiliary DB physical SHA matches frozen value")
else:
    fail(
        "Phase 3 auxiliary DB physical SHA mismatch"
    )


if before_sha["phase4"] == EXPECTED_PHASE4_PHYSICAL_SHA:
    pass_("Phase 4 DB physical SHA matches frozen value")
else:
    fail(
        "Phase 4 DB physical SHA mismatch"
    )


# ============================================================
# 3. Open strictly read-only
# ============================================================

section("3. OPEN DATABASES READ-ONLY")

prod = ro_connect(PRODUCTION_DB)
aux = ro_connect(AUX_DB)
p4 = ro_connect(PHASE4_DB)
p5 = ro_connect(PHASE5_DB)

pass_(
    "All databases opened with SQLite mode=ro"
)


# ============================================================
# 4. SQLite integrity
# ============================================================

section("4. SQLITE INTEGRITY")

connections = [
    ("production", prod),
    ("auxiliary", aux),
    ("phase4", p4),
    ("phase5", p5),
]


for name, conn in connections:

    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if integrity == "ok":
        pass_(
            f"{name}: integrity_check=ok"
        )
    else:
        fail(
            f"{name}: integrity_check={integrity}"
        )

    fk = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if len(fk) == 0:
        pass_(
            f"{name}: foreign_key_check=0"
        )
    else:
        fail(
            f"{name}: foreign_key_check={len(fk)}"
        )


# ============================================================
# 5. Required objects
# ============================================================

section("5. REQUIRED DATABASE OBJECTS")

required_phase4 = [
    "phase4_data_v1_sample",
    "phase4_data_v1_ecmwf_hourly",
    "phase4_data_v1_manifest",
]

required_phase5 = [
    "phase5_baseline_v1_prediction",
    "phase5_baseline_v1_exclusion",
    "phase5_baseline_v1_metrics",
    "phase5_baseline_v1_group_metrics",
    "phase5_baseline_v1_manifest",
]


for table in required_phase4:

    if table_exists(p4, table):
        pass_(f"Phase 4 object exists: {table}")
    else:
        fail(f"Missing Phase 4 object: {table}")


for table in required_phase5:

    if table_exists(p5, table):
        pass_(f"Phase 5 object exists: {table}")
    else:
        fail(f"Missing Phase 5 object: {table}")


# ============================================================
# 6. Phase 4 manifest
# ============================================================

section("6. PHASE 4 MANIFEST")

p4_manifest_rows = p4.execute(
    """
    SELECT *
    FROM phase4_data_v1_manifest
    """
).fetchall()


if len(p4_manifest_rows) == 1:

    pass_("Exactly one Phase 4 manifest row")

    p4_manifest = dict(
        p4_manifest_rows[0]
    )

    actual_semantic = p4_manifest.get(
        "dataset_semantic_sha256"
    )

    print(
        "Phase 4 semantic SHA:",
        actual_semantic,
    )

    if (
        actual_semantic
        == EXPECTED_PHASE4_SEMANTIC_SHA
    ):
        pass_(
            "Phase 4 semantic SHA matches frozen value"
        )
    else:
        fail(
            "Phase 4 semantic SHA mismatch"
        )

else:

    fail(
        "Phase 4 manifest must contain exactly one row"
    )


# ============================================================
# 7. Phase 4 sample counts
# ============================================================

section("7. PHASE 4 SOURCE SAMPLE COUNTS")

sample_rows = p4.execute(
    """
    SELECT
        business_date_bjt,
        horizon,
        issue_time_bjt,
        issue_time_utc,
        selected_ecmwf_run_time_utc,
        selected_ecmwf_source_available_time_utc,
        sample_status,
        target_tmax_c,
        source_archive_id,
        source_raw_run_id,
        trajectory_expected_hours,
        trajectory_present_hours,
        trajectory_valid_hours,
        exclusion_reason
    FROM phase4_data_v1_sample
    ORDER BY
        business_date_bjt,
        horizon
    """
).fetchall()


if len(sample_rows) == EXPECTED_SOURCE_SAMPLES:

    pass_(
        f"Phase 4 source samples = "
        f"{EXPECTED_SOURCE_SAMPLES}"
    )

else:

    fail(
        f"Phase 4 source sample count "
        f"expected={EXPECTED_SOURCE_SAMPLES}, "
        f"actual={len(sample_rows)}"
    )


eligible_source = [
    row
    for row in sample_rows
    if row["sample_status"] == "ELIGIBLE"
]


excluded_source = [
    row
    for row in sample_rows
    if row["sample_status"] != "ELIGIBLE"
]


if len(eligible_source) == EXPECTED_ELIGIBLE:

    pass_(
        f"Phase 4 eligible samples = "
        f"{EXPECTED_ELIGIBLE}"
    )

else:

    fail(
        f"Eligible sample count "
        f"expected={EXPECTED_ELIGIBLE}, "
        f"actual={len(eligible_source)}"
    )


if len(excluded_source) == 1:

    pass_("Exactly one Phase 4 sample excluded")

else:

    fail(
        f"Expected 1 excluded sample, "
        f"actual={len(excluded_source)}"
    )


if len(excluded_source) == 1:

    gap = excluded_source[0]

    print(
        "Excluded sample:",
        gap["business_date_bjt"],
        gap["horizon"],
        f"{gap['trajectory_valid_hours']}/"
        f"{gap['trajectory_expected_hours']}",
        gap["exclusion_reason"],
    )

    if (
        gap["business_date_bjt"]
        == EXPECTED_GAP_DATE
        and gap["horizon"]
        == EXPECTED_GAP_HORIZON
        and int(
            gap["trajectory_valid_hours"]
        )
        == EXPECTED_GAP_HOURS
        and int(
            gap["trajectory_expected_hours"]
        )
        == 24
    ):

        pass_(
            "Known 2025-08-07 / T2 "
            "14/24 gap confirmed"
        )

    else:

        fail(
            "Excluded sample does not match "
            "frozen known T2 gap"
        )


horizon_counts = defaultdict(int)

for row in eligible_source:
    horizon_counts[row["horizon"]] += 1


for horizon, expected in EXPECTED_COUNTS.items():

    actual = horizon_counts[horizon]

    if actual == expected:

        pass_(
            f"{horizon} eligible count = {expected}"
        )

    else:

        fail(
            f"{horizon}: expected={expected}, "
            f"actual={actual}"
        )


# ============================================================
# 8. Eligible Phase 4 trajectory metadata
# ============================================================

section("8. PHASE 4 TRAJECTORY METADATA")

bad_metadata = []


for row in eligible_source:

    if int(row["trajectory_expected_hours"]) != 24:

        bad_metadata.append(
            (
                row["business_date_bjt"],
                row["horizon"],
                "expected_hours",
                row["trajectory_expected_hours"],
            )
        )

    if int(row["trajectory_present_hours"]) != 24:

        bad_metadata.append(
            (
                row["business_date_bjt"],
                row["horizon"],
                "present_hours",
                row["trajectory_present_hours"],
            )
        )

    if int(row["trajectory_valid_hours"]) != 24:

        bad_metadata.append(
            (
                row["business_date_bjt"],
                row["horizon"],
                "valid_hours",
                row["trajectory_valid_hours"],
            )
        )


if not bad_metadata:

    pass_(
        "All eligible Phase 4 samples declare "
        "24 expected/present/valid hours"
    )

else:

    fail(
        f"Invalid Phase 4 trajectory metadata "
        f"count={len(bad_metadata)}"
    )

    for item in bad_metadata[:20]:
        print("      ", item)


# ============================================================
# 9. Load Phase 4 hourly
# ============================================================

section("9. INDEPENDENT 24-HOUR ECMWF RECONSTRUCTION")

hourly_rows = p4.execute(
    """
    SELECT
        business_date_bjt,
        horizon,
        selected_ecmwf_run_time_utc,
        target_time_utc,
        target_time_bjt,
        source_available_time_utc,
        temperature_2m_c,
        source_raw_run_id,
        source_hourly_id,
        lead_hours,
        parse_success
    FROM phase4_data_v1_ecmwf_hourly
    ORDER BY
        business_date_bjt,
        horizon,
        target_time_utc
    """
).fetchall()


print(
    "Phase 4 hourly rows:",
    len(hourly_rows),
)


groups = defaultdict(list)


for row in hourly_rows:

    key = (
        row["business_date_bjt"],
        row["horizon"],
    )

    groups[key].append(row)


reconstructed = {}
trajectory_failures = []


for sample in eligible_source:

    key = (
        sample["business_date_bjt"],
        sample["horizon"],
    )

    rows = groups.get(
        key,
        [],
    )

    # ---------------------------------------------
    # Exactly 24 rows
    # ---------------------------------------------

    if len(rows) != 24:

        trajectory_failures.append(
            (
                key,
                f"hour_count={len(rows)}",
            )
        )

        continue

    # ---------------------------------------------
    # Unique target times
    # ---------------------------------------------

    target_times = [
        row["target_time_utc"]
        for row in rows
    ]

    if len(set(target_times)) != 24:

        trajectory_failures.append(
            (
                key,
                "duplicate target_time_utc",
            )
        )

        continue

    # ---------------------------------------------
    # All temperature values valid
    # ---------------------------------------------

    temperatures = [
        row["temperature_2m_c"]
        for row in rows
    ]

    if any(
        value is None
        for value in temperatures
    ):

        trajectory_failures.append(
            (
                key,
                "NULL temperature_2m_c",
            )
        )

        continue

    # ---------------------------------------------
    # Same selected ECMWF run
    # ---------------------------------------------

    runs = {
        row["selected_ecmwf_run_time_utc"]
        for row in rows
    }

    if len(runs) != 1:

        trajectory_failures.append(
            (
                key,
                f"cross-vintage runs={runs}",
            )
        )

        continue

    selected_run = next(
        iter(runs)
    )

    if (
        selected_run
        != sample["selected_ecmwf_run_time_utc"]
    ):

        trajectory_failures.append(
            (
                key,
                "hourly selected run != "
                "sample selected run",
            )
        )

        continue

    # ---------------------------------------------
    # Same source raw run
    # ---------------------------------------------

    raw_runs = {
        row["source_raw_run_id"]
        for row in rows
    }

    if len(raw_runs) != 1:

        trajectory_failures.append(
            (
                key,
                f"multiple source_raw_run_id={raw_runs}",
            )
        )

        continue

    # ---------------------------------------------
    # Parse success
    # ---------------------------------------------

    if any(
        not bool(row["parse_success"])
        for row in rows
    ):

        trajectory_failures.append(
            (
                key,
                "parse_success failure",
            )
        )

        continue

    # ---------------------------------------------
    # Independent max
    # ---------------------------------------------

    float_temps = [
        float(x)
        for x in temperatures
    ]

    max_temp = max(
        float_temps
    )

    max_rows = [
        row
        for row in rows
        if almost_equal(
            row["temperature_2m_c"],
            max_temp,
        )
    ]

    reconstructed[key] = {
        "forecast": max_temp,
        "hour_count": 24,
        "run": selected_run,
        "raw_run_id": next(
            iter(raw_runs)
        ),
        "max_occurrence_count": len(
            max_rows
        ),
        "max_first_time_utc":
            max_rows[0]["target_time_utc"],
        "max_last_time_utc":
            max_rows[-1]["target_time_utc"],
    }


if not trajectory_failures:

    pass_(
        "All eligible trajectories independently validated"
    )

    pass_(
        "All eligible trajectories contain exactly 24 hours"
    )

    pass_(
        "Eligible temperature NULL count = 0"
    )

    pass_(
        "Cross-vintage violations = 0"
    )

    pass_(
        "Duplicate target hour violations = 0"
    )

else:

    fail(
        f"Trajectory validation failures = "
        f"{len(trajectory_failures)}"
    )

    for item in trajectory_failures[:20]:
        print("      ", item)


if len(reconstructed) == EXPECTED_ELIGIBLE:

    pass_(
        "Independent reconstructed baseline "
        "count = 2186"
    )

else:

    fail(
        f"Expected reconstructed=2186, "
        f"actual={len(reconstructed)}"
    )


# ============================================================
# 10. Known excluded gap
# ============================================================

section("10. KNOWN T2 GAP")

gap_key = (
    EXPECTED_GAP_DATE,
    EXPECTED_GAP_HORIZON,
)

gap_hourly = groups.get(
    gap_key,
    [],
)

gap_valid = [
    row
    for row in gap_hourly
    if row["temperature_2m_c"] is not None
]


print(
    "2025-08-07 / T2 hourly rows:",
    len(gap_hourly),
)

print(
    "2025-08-07 / T2 valid temperatures:",
    len(gap_valid),
)


if len(gap_valid) == EXPECTED_GAP_HOURS:

    pass_(
        "Known T2 gap independently confirms "
        "14 valid temperature hours"
    )

else:

    fail(
        f"Known T2 gap expected 14 valid hours, "
        f"actual={len(gap_valid)}"
    )


# ============================================================
# 11. Phase 5 prediction schema
# ============================================================

section("11. PHASE 5 PREDICTION SCHEMA")

PRED_TABLE = "phase5_baseline_v1_prediction"

pred_cols = get_columns(
    p5,
    PRED_TABLE,
)


for column in pred_cols:
    print(" ", column)


date_col = first_existing(
    pred_cols,
    [
        "business_date_bjt",
    ],
)

horizon_col = first_existing(
    pred_cols,
    [
        "horizon",
    ],
)

forecast_col = first_existing(
    pred_cols,
    [
        "ecmwf_raw_tmax_c",
        "forecast_tmax_c",
        "prediction_tmax_c",
    ],
)

target_col = first_existing(
    pred_cols,
    [
        "target_tmax_c",
        "observed_tmax_c",
    ],
)

error_col = first_existing(
    pred_cols,
    [
        "error_c",
    ],
)

absolute_error_col = first_existing(
    pred_cols,
    [
        "absolute_error_c",
    ],
)

squared_error_col = first_existing(
    pred_cols,
    [
        "squared_error_c",
    ],
)

run_col = first_existing(
    pred_cols,
    [
        "selected_ecmwf_run_time_utc",
        "selected_run_time_utc",
    ],
)

hour_count_col = first_existing(
    pred_cols,
    [
        "hour_count",
        "trajectory_hour_count",
        "valid_hour_count",
    ],
)


required_mapping = {
    "business_date": date_col,
    "horizon": horizon_col,
    "forecast": forecast_col,
    "target": target_col,
    "error": error_col,
    "absolute_error": absolute_error_col,
    "squared_error": squared_error_col,
    "selected_run": run_col,
    "hour_count": hour_count_col,
}


mapping_failure = False


for logical_name, actual_name in required_mapping.items():

    if actual_name:

        pass_(
            f"{logical_name} -> {actual_name}"
        )

    else:

        fail(
            f"Cannot map required prediction field: "
            f"{logical_name}"
        )

        mapping_failure = True


if mapping_failure:

    print()
    print(
        "Prediction schema mapping failed."
    )

    print(
        "Database remains unchanged."
    )

    sys.exit(4)


# ============================================================
# 12. Load Phase 5 predictions
# ============================================================

section("12. PHASE 5 PREDICTION ROWS")

prediction_sql = f"""
SELECT
    "{date_col}" AS business_date_bjt,
    "{horizon_col}" AS horizon,
    "{forecast_col}" AS forecast,
    "{target_col}" AS observed,
    "{error_col}" AS error,
    "{absolute_error_col}" AS absolute_error,
    "{squared_error_col}" AS squared_error,
    "{run_col}" AS selected_run,
    "{hour_count_col}" AS hour_count
FROM "{PRED_TABLE}"
ORDER BY
    business_date_bjt,
    horizon
"""


prediction_rows = p5.execute(
    prediction_sql
).fetchall()


if len(prediction_rows) == EXPECTED_ELIGIBLE:

    pass_(
        "Phase 5 prediction rows = 2186"
    )

else:

    fail(
        f"Expected 2186 Phase 5 predictions, "
        f"actual={len(prediction_rows)}"
    )


prediction_keys = [
    (
        row["business_date_bjt"],
        row["horizon"],
    )
    for row in prediction_rows
]


if (
    len(prediction_keys)
    == len(set(prediction_keys))
):

    pass_(
        "Phase 5 prediction key uniqueness confirmed"
    )

else:

    fail(
        "Duplicate Phase 5 "
        "business_date/horizon keys"
    )


if gap_key not in set(prediction_keys):

    pass_(
        "2025-08-07 / T2 correctly absent "
        "from Phase 5 predictions"
    )

else:

    fail(
        "Known excluded T2 gap entered "
        "Phase 5 predictions"
    )


# ============================================================
# 13. Phase 5 exclusion table
# ============================================================

section("13. PHASE 5 EXCLUSION TABLE")

exclusion_rows = p5.execute(
    """
    SELECT *
    FROM phase5_baseline_v1_exclusion
    """
).fetchall()


print(
    "Exclusion rows:",
    len(exclusion_rows),
)


if len(exclusion_rows) == 1:

    pass_(
        "Exactly one Phase 5 exclusion row"
    )

    exclusion_text = json.dumps(
        dict(exclusion_rows[0]),
        ensure_ascii=False,
        default=str,
    )

    if (
        EXPECTED_GAP_DATE in exclusion_text
        and EXPECTED_GAP_HORIZON in exclusion_text
    ):

        pass_(
            "Phase 5 exclusion table records "
            "2025-08-07 / T2"
        )

    else:

        fail(
            "Phase 5 exclusion row does not "
            "match known T2 gap"
        )

else:

    fail(
        f"Expected 1 exclusion row, "
        f"actual={len(exclusion_rows)}"
    )


# ============================================================
# 14. Independent target map
# ============================================================

section("14. INDEPENDENT TARGET MAP")

target_map = {
    (
        row["business_date_bjt"],
        row["horizon"],
    ):
        float(row["target_tmax_c"])

    for row in eligible_source
}


if len(target_map) == EXPECTED_ELIGIBLE:

    pass_(
        "2186 frozen target values loaded "
        "independently"
    )

else:

    fail(
        f"Target map expected=2186, "
        f"actual={len(target_map)}"
    )


# ============================================================
# 15. Row-by-row independent validation
# ============================================================

section("15. ROW-BY-ROW BASELINE VALIDATION")

forecast_mismatch = []
target_mismatch = []
error_mismatch = []
absolute_error_mismatch = []
squared_error_mismatch = []
run_mismatch = []
hour_mismatch = []

independent_metric_rows = defaultdict(
    list
)


for row in prediction_rows:

    key = (
        row["business_date_bjt"],
        row["horizon"],
    )

    recon = reconstructed.get(
        key
    )

    if recon is None:

        forecast_mismatch.append(
            (
                key,
                "missing independent reconstruction",
            )
        )

        continue

    independent_forecast = recon[
        "forecast"
    ]

    independent_target = target_map.get(
        key
    )

    if independent_target is None:

        target_mismatch.append(
            (
                key,
                "missing target",
            )
        )

        continue

    # Forecast

    if not almost_equal(
        row["forecast"],
        independent_forecast,
    ):

        forecast_mismatch.append(
            (
                key,
                row["forecast"],
                independent_forecast,
            )
        )

    # Target

    if not almost_equal(
        row["observed"],
        independent_target,
    ):

        target_mismatch.append(
            (
                key,
                row["observed"],
                independent_target,
            )
        )

    # Error convention:
    # forecast - observed

    independent_error = (
        independent_forecast
        - independent_target
    )

    independent_absolute_error = abs(
        independent_error
    )

    independent_squared_error = (
        independent_error ** 2
    )

    if not almost_equal(
        row["error"],
        independent_error,
    ):

        error_mismatch.append(
            (
                key,
                row["error"],
                independent_error,
            )
        )

    if not almost_equal(
        row["absolute_error"],
        independent_absolute_error,
    ):

        absolute_error_mismatch.append(
            (
                key,
                row["absolute_error"],
                independent_absolute_error,
            )
        )

    if not almost_equal(
        row["squared_error"],
        independent_squared_error,
    ):

        squared_error_mismatch.append(
            (
                key,
                row["squared_error"],
                independent_squared_error,
            )
        )

    # Selected run

    if (
        row["selected_run"]
        != recon["run"]
    ):

        run_mismatch.append(
            (
                key,
                row["selected_run"],
                recon["run"],
            )
        )

    # Hour count

    if int(row["hour_count"]) != 24:

        hour_mismatch.append(
            (
                key,
                row["hour_count"],
            )
        )

    independent_metric_rows[
        row["horizon"]
    ].append(
        {
            "forecast":
                independent_forecast,
            "observed":
                independent_target,
            "error":
                independent_error,
        }
    )


validation_checks = [
    (
        "Forecast Tmax mismatches",
        forecast_mismatch,
    ),
    (
        "Target mismatches",
        target_mismatch,
    ),
    (
        "Error mismatches",
        error_mismatch,
    ),
    (
        "Absolute error mismatches",
        absolute_error_mismatch,
    ),
    (
        "Squared error mismatches",
        squared_error_mismatch,
    ),
    (
        "Selected run mismatches",
        run_mismatch,
    ),
    (
        "Hour-count mismatches",
        hour_mismatch,
    ),
]


for name, items in validation_checks:

    if not items:

        pass_(
            f"{name} = 0"
        )

    else:

        fail(
            f"{name} = {len(items)}"
        )

        for item in items[:10]:
            print("      ", item)


# ============================================================
# 16. Horizon counts
# ============================================================

section("16. PHASE 5 HORIZON COUNTS")

phase5_horizon_counts = defaultdict(int)


for row in prediction_rows:

    phase5_horizon_counts[
        row["horizon"]
    ] += 1


for horizon, expected in EXPECTED_COUNTS.items():

    actual = phase5_horizon_counts[
        horizon
    ]

    if actual == expected:

        pass_(
            f"{horizon} prediction count = {expected}"
        )

    else:

        fail(
            f"{horizon}: expected={expected}, "
            f"actual={actual}"
        )


# ============================================================
# 17. Independent metrics
# ============================================================

section("17. INDEPENDENT METRIC RECOMPUTATION")

calculated_metrics = {}


for horizon in [
    "T0",
    "T1",
    "T2",
]:

    rows = independent_metric_rows[
        horizon
    ]

    metrics = calc_metrics(
        rows
    )

    calculated_metrics[
        horizon
    ] = metrics

    print()
    print(
        f"----- {horizon} -----"
    )

    print(
        f"N              : "
        f"{metrics['n']}"
    )

    print(
        f"Bias           : "
        f"{metrics['bias']:.9f}"
    )

    print(
        f"MAE            : "
        f"{metrics['mae']:.9f}"
    )

    print(
        f"RMSE           : "
        f"{metrics['rmse']:.9f}"
    )

    print(
        f"Median AE      : "
        f"{metrics['median_ae']:.9f}"
    )

    print(
        f"P90 AE*        : "
        f"{metrics['p90_nearest_rank']:.9f}"
    )

    print(
        f"Max AE         : "
        f"{metrics['max_ae']:.9f}"
    )

    print(
        f"Mean Forecast  : "
        f"{metrics['mean_forecast']:.9f}"
    )

    print(
        f"Mean Observed  : "
        f"{metrics['mean_observed']:.9f}"
    )

    print(
        f"AE <= 0.5      : "
        f"{metrics['ae_05_count']} / "
        f"{metrics['n']} "
        f"({metrics['ae_05_count'] / metrics['n'] * 100:.2f}%)"
    )

    print(
        f"AE <= 1.0      : "
        f"{metrics['ae_10_count']} / "
        f"{metrics['n']} "
        f"({metrics['ae_10_count'] / metrics['n'] * 100:.2f}%)"
    )

    print(
        f"AE <= 2.0      : "
        f"{metrics['ae_20_count']} / "
        f"{metrics['n']} "
        f"({metrics['ae_20_count'] / metrics['n'] * 100:.2f}%)"
    )

    expected_n = EXPECTED_COUNTS[
        horizon
    ]

    if metrics["n"] == expected_n:

        pass_(
            f"{horizon} independent metric N="
            f"{expected_n}"
        )

    else:

        fail(
            f"{horizon} metric N expected="
            f"{expected_n}, "
            f"actual={metrics['n']}"
        )


print()
print(
    "* P90 shown above uses nearest-rank "
    "as independent diagnostic."
)


# ============================================================
# 18. Compare known Phase 5 headline report
# ============================================================

section("18. HEADLINE RESULT CROSS-CHECK")

reported = {

    "T0": {
        "bias": -0.532922,
        "mae": 1.498080,
        "rmse": 1.865832,
        "median_ae": 1.3,
        "ae05": 163,
        "ae10": 308,
        "ae20": 510,
    },

    "T1": {
        "bias": -0.545816,
        "mae": 1.551029,
        "rmse": 1.935245,
        "median_ae": 1.4,
        "ae05": 164,
        "ae10": 293,
        "ae20": 506,
    },

    "T2": {
        "bias": -0.408242,
        "mae": 1.741758,
        "rmse": 2.173820,
        "median_ae": 1.4,
        "ae05": 122,
        "ae10": 252,
        "ae20": 485,
    },
}


for horizon in [
    "T0",
    "T1",
    "T2",
]:

    actual = calculated_metrics[
        horizon
    ]

    expected = reported[
        horizon
    ]

    numeric_checks = [
        (
            "Bias",
            actual["bias"],
            expected["bias"],
        ),
        (
            "MAE",
            actual["mae"],
            expected["mae"],
        ),
        (
            "RMSE",
            actual["rmse"],
            expected["rmse"],
        ),
        (
            "Median AE",
            actual["median_ae"],
            expected["median_ae"],
        ),
    ]

    for (
        label,
        actual_value,
        expected_value,
    ) in numeric_checks:

        if math.isclose(
            actual_value,
            expected_value,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):

            pass_(
                f"{horizon} {label} "
                f"matches Phase 5 report"
            )

        else:

            fail(
                f"{horizon} {label} mismatch: "
                f"independent={actual_value}, "
                f"reported={expected_value}"
            )

    count_checks = [
        (
            "AE<=0.5",
            actual["ae_05_count"],
            expected["ae05"],
        ),
        (
            "AE<=1.0",
            actual["ae_10_count"],
            expected["ae10"],
        ),
        (
            "AE<=2.0",
            actual["ae_20_count"],
            expected["ae20"],
        ),
    ]

    for (
        label,
        actual_count,
        expected_count,
    ) in count_checks:

        if actual_count == expected_count:

            pass_(
                f"{horizon} {label} count "
                f"matches Phase 5 report"
            )

        else:

            fail(
                f"{horizon} {label}: "
                f"independent={actual_count}, "
                f"reported={expected_count}"
            )


# ============================================================
# 19. Stored metrics table
# ============================================================

section("19. STORED METRICS TABLE")

stored_metric_rows = p5.execute(
    """
    SELECT *
    FROM phase5_baseline_v1_metrics
    """
).fetchall()


print(
    "Stored metric rows:",
    len(stored_metric_rows),
)


if len(stored_metric_rows) == 3:

    pass_(
        "Stored headline metric rows = 3"
    )

else:

    warn(
        "Stored metric rows != 3; "
        "inspect schema if long-format was intended"
    )


# ============================================================
# 20. Group metrics basic integrity
# ============================================================

section("20. GROUP METRICS")

group_metric_rows = p5.execute(
    """
    SELECT *
    FROM phase5_baseline_v1_group_metrics
    """
).fetchall()


print(
    "Stored group metric rows:",
    len(group_metric_rows),
)


if len(group_metric_rows) == 75:

    pass_(
        "Stored group metric rows = 75"
    )

else:

    warn(
        f"Expected agent report group metrics=75, "
        f"actual={len(group_metric_rows)}"
    )


# ============================================================
# 21. Phase 5 manifest
# ============================================================

section("21. PHASE 5 MANIFEST")

manifest_rows = p5.execute(
    """
    SELECT *
    FROM phase5_baseline_v1_manifest
    """
).fetchall()


if len(manifest_rows) == 1:

    pass_(
        "Exactly one Phase 5 manifest row"
    )

else:

    fail(
        f"Expected one Phase 5 manifest row, "
        f"actual={len(manifest_rows)}"
    )


if manifest_rows:

    manifest = dict(
        manifest_rows[0]
    )

    print()
    print("Manifest:")

    for key, value in manifest.items():
        print(
            f"  {key}: {value}"
        )

    manifest_json = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )

    # Phase 5 semantic SHA

    semantic_matches = [
        value
        for key, value in manifest.items()
        if (
            "semantic" in key.lower()
            and "sha" in key.lower()
            and value
            == EXPECTED_PHASE5_SEMANTIC_SHA
        )
    ]

    if semantic_matches:

        pass_(
            "Phase 5 semantic SHA matches "
            "expected frozen build result"
        )

    else:

        fail(
            "Expected Phase 5 semantic SHA "
            "not found in manifest"
        )

    # Phase 4 semantic lineage

    if (
        EXPECTED_PHASE4_SEMANTIC_SHA
        in manifest_json
    ):

        pass_(
            "Phase 5 manifest references "
            "Phase 4 semantic SHA"
        )

    else:

        fail(
            "Phase 5 manifest does not reference "
            "frozen Phase 4 semantic SHA"
        )

    # Error convention

    if (
        "forecast_minus_observed"
        in manifest_json
    ):

        pass_(
            "Manifest freezes error convention: "
            "forecast_minus_observed"
        )

    else:

        warn(
            "Literal forecast_minus_observed "
            "not found in manifest; "
            "row-level convention was independently verified"
        )

    # Meteostat

    lower_manifest = (
        manifest_json.lower()
    )

    if (
        "meteostat" in lower_manifest
        and "blocked" in lower_manifest
    ):

        pass_(
            "Manifest records Meteostat as blocked/non-training input"
        )

    elif "meteostat" not in lower_manifest:

        pass_(
            "Manifest contains no Meteostat dependency"
        )

    else:

        warn(
            "Meteostat appears in manifest without "
            "obvious BLOCKED marker; inspect documentation"
        )


# ============================================================
# 22. Prediction schema prohibited input check
# ============================================================

section("22. PROHIBITED INPUT CHECK")

prediction_schema_row = p5.execute(
    """
    SELECT sql
    FROM sqlite_master
    WHERE type = 'table'
      AND name = 'phase5_baseline_v1_prediction'
    """
).fetchone()


prediction_schema_sql = ""

if prediction_schema_row:
    prediction_schema_sql = (
        prediction_schema_row["sql"]
        or ""
    )


if (
    "meteostat"
    not in prediction_schema_sql.lower()
):

    pass_(
        "Prediction table has no Meteostat feature dependency"
    )

else:

    fail(
        "Prediction table contains Meteostat dependency"
    )


# ============================================================
# 23. T0 semantic safety
# ============================================================

section("23. T0 SEMANTIC SAFETY")

t0_rows = [
    row
    for row in eligible_source
    if row["horizon"] == "T0"
]


t0_issue_hours = defaultdict(int)


for row in t0_rows:

    value = row["issue_time_bjt"]

    if value is not None:

        text_value = str(value)

        if "21:00" in text_value:
            t0_issue_hours["21:00"] += 1
        else:
            t0_issue_hours[text_value] += 1


print(
    "T0 issue-time distribution:",
    dict(t0_issue_hours),
)


if (
    len(t0_rows) == 729
    and t0_issue_hours.get("21:00", 0) == 729
):

    pass_(
        "All T0 samples use frozen 21:00 BJT issue semantics"
    )

else:

    # 时间字符串有可能是完整 ISO timestamp，
    # 所以进一步直接检查字符串中 21:00。
    matching = sum(
        1
        for row in t0_rows
        if (
            row["issue_time_bjt"] is not None
            and "21:00"
            in str(row["issue_time_bjt"])
        )
    )

    if matching == 729:

        pass_(
            "All T0 samples contain 21:00 BJT "
            "issue semantics"
        )

    else:

        fail(
            f"T0 21:00 BJT semantics expected=729, "
            f"actual={matching}"
        )


# ============================================================
# 24. Error direction sanity
# ============================================================

section("24. ERROR SIGN CONVENTION")

forward_mismatch = 0
reverse_mismatch = 0


for row in prediction_rows:

    forecast = float(
        row["forecast"]
    )

    observed = float(
        row["observed"]
    )

    stored_error = float(
        row["error"]
    )

    forward = (
        forecast - observed
    )

    reverse = (
        observed - forecast
    )

    if not almost_equal(
        stored_error,
        forward,
    ):
        forward_mismatch += 1

    if not almost_equal(
        stored_error,
        reverse,
    ):
        reverse_mismatch += 1


print(
    "forecast_minus_observed mismatches:",
    forward_mismatch,
)

print(
    "observed_minus_forecast mismatches:",
    reverse_mismatch,
)


if forward_mismatch == 0:

    pass_(
        "Error convention independently confirmed: "
        "forecast - observed"
    )

else:

    fail(
        "Stored errors do not follow "
        "forecast - observed"
    )


# ============================================================
# 25. Close DB and after SHA
# ============================================================

section("25. DATABASE SHA256 — AFTER AUDIT")

prod.close()
aux.close()
p4.close()
p5.close()


after_sha = {
    "production": sha256_file(
        PRODUCTION_DB
    ),
    "auxiliary": sha256_file(
        AUX_DB
    ),
    "phase4": sha256_file(
        PHASE4_DB
    ),
    "phase5": sha256_file(
        PHASE5_DB
    ),
}


for name, value in after_sha.items():
    print(
        f"{name:12s}: {value}"
    )


for name in [
    "production",
    "auxiliary",
    "phase4",
    "phase5",
]:

    if (
        before_sha[name]
        == after_sha[name]
    ):

        pass_(
            f"{name} DB unchanged "
            f"by independent audit"
        )

    else:

        fail(
            f"{name} DB changed "
            f"during independent audit"
        )


# ============================================================
# 26. Final summary
# ============================================================

section("26. FINAL ACCEPTANCE SUMMARY")

print(
    f"PASS checks : {PASS_COUNT}"
)

print(
    f"WARN checks : {WARN_COUNT}"
)

print(
    f"FAIL checks : {FAIL_COUNT}"
)

print()

print(
    "Expected Phase 5 semantic SHA:"
)

print(
    EXPECTED_PHASE5_SEMANTIC_SHA
)

print()

print(
    "Phase 5 physical DB SHA:"
)

print(
    after_sha["phase5"]
)

print()


# ============================================================
# Final verdict
# ============================================================

if FAIL_COUNT == 0:

    print(
        "=" * 78
    )

    print(
        "PHASE 5 INDEPENDENT ACCEPTANCE V2: PASS"
    )

    print(
        "=" * 78
    )

    print()

    print(
        "RESULT: PHASE 5 ECMWF RAW BASELINE V1 "
        "INDEPENDENTLY ACCEPTED"
    )

    print()

    print(
        "STATUS: READY FOR PHASE 5 FREEZE / CLOSE"
    )

    print()

    print(
        "NEXT:"
    )

    print(
        "INTRADAY HISTORICAL DATA V1"
    )

    print(
        "Goal: predict final ZUUU daily Tmax "
        "BEFORE the actual daily maximum occurs."
    )

    sys.exit(0)

else:

    print(
        "=" * 78
    )

    print(
        "PHASE 5 INDEPENDENT ACCEPTANCE V2: FAIL"
    )

    print(
        "=" * 78
    )

    print()

    print(
        "DO NOT FREEZE PHASE 5."
    )

    print(
        "Investigate all FAIL items first."
    )

    sys.exit(1)