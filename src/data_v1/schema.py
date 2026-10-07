"""Independent Gold schema and explicit model-input views."""
from .contracts import WEATHER, SOLAR_FEATURES, TARGET_FIELDS

PREFIX = "phase4_data_v1_"
KEYS = {"target": ("business_date_bjt",), "sample": ("business_date_bjt", "horizon"),
        "ecmwf_hourly": ("business_date_bjt", "horizon", "target_time_utc"),
        "solar": ("target_time",), "daily": ("business_date_bjt", "horizon")}

SPECS = {
    "target": {k: ("REAL NOT NULL" if k == "daily_tmax_c" else "INTEGER NOT NULL" if k in
        ("tmax_occurrence_count", "observation_count", "hourly_coverage_count", "has_correction",
         "tmax_has_correction", "has_recovery", "tmax_has_recovery") else "TEXT NOT NULL") for k in TARGET_FIELDS},
    "sample": {k: "TEXT NOT NULL" for k in (
        "business_date_bjt", "horizon", "issue_time_bjt", "issue_time_utc", "selected_ecmwf_run_time_utc",
        "selected_ecmwf_source_available_time_utc", "ecmwf_availability_semantics", "ecmwf_availability_type",
        "ecmwf_availability_rule_version", "ecmwf_archive_version", "ecmwf_issue_rule_version",
        "ecmwf_issue_rule_sha256", "ecmwf_canonical_rule_version", "ecmwf_content_sha256", "ecmwf_api_model",
        "ecmwf_data_spec_version", "source_ingest_time_utc", "sample_status", "meteostat_training_admission",
        "dataset_version", "created_at_utc")},
    "ecmwf_hourly": {k: "TEXT NOT NULL" for k in (
        "business_date_bjt", "horizon", "selected_ecmwf_run_time_utc", "target_time_utc", "target_time_bjt",
        "solar_target_time", "source_available_time_utc", "availability_type", "model", "model_cycle",
        "qc_status", "qc_warnings_json", "processor_version")},
    "solar": {k: "TEXT NOT NULL" for k in (
        "target_time", "availability_basis", "ingest_time", "timezone", "source", "source_version",
        "coordinate_version", "business_date_bjt", "season", "sunrise", "sunset")},
    "daily": {"business_date_bjt": "TEXT NOT NULL", "horizon": "TEXT NOT NULL", "trajectory_complete": "INTEGER NOT NULL"},
}
SPECS["target"].update(source_target_id="INTEGER NOT NULL", source_provenance_json="TEXT NOT NULL")
SPECS["sample"].update(target_tmax_c="REAL NOT NULL", source_archive_id="INTEGER NOT NULL", source_raw_run_id="INTEGER NOT NULL",
    trajectory_expected_hours="INTEGER NOT NULL CHECK(trajectory_expected_hours=24)",
    trajectory_present_hours="INTEGER NOT NULL CHECK(trajectory_present_hours BETWEEN 0 AND 24)",
    trajectory_valid_hours="INTEGER NOT NULL CHECK(trajectory_valid_hours BETWEEN 0 AND 24)", exclusion_reason="TEXT")
SPECS["ecmwf_hourly"].update({k: "REAL" for k in (*WEATHER, "grid_latitude", "grid_longitude", "grid_elevation_m")})
SPECS["ecmwf_hourly"].update({k: "INTEGER NOT NULL" for k in ("source_raw_run_id", "source_hourly_id", "lead_hours", "parse_success")})
SPECS["solar"].update({k: "REAL NOT NULL" for k in (*[f for f in SOLAR_FEATURES if f not in SPECS["solar"]], "latitude", "longitude")})
for k in ("hour_bjt", "day_of_year", "month"):
    SPECS["solar"][k] = "INTEGER NOT NULL"
SPECS["solar"].update(observation_time="TEXT", source_available_time="TEXT")
SPECS["daily"].update({k: "REAL" for k in ("ecmwf_daily_max_c", "ecmwf_daily_min_c", "ecmwf_daily_mean_c", "error_c", "absolute_error_c")})
SPECS["daily"].update(ecmwf_max_occurrence_hour_bjt="INTEGER", ecmwf_max_occurrence_count="INTEGER",
                       ecmwf_max_first_time_bjt="TEXT", ecmwf_max_last_time_bjt="TEXT")

CONSTRAINTS = {
    "target": ["CHECK(target_status='FROZEN' AND target_version='ZUUU_TARGET_V1' AND hourly_coverage_count=24)",
               "CHECK(json_valid(source_provenance_json))"],
    "solar": ["UNIQUE(target_time,business_date_bjt)", "CHECK(latitude=30.576 AND longitude=103.950)",
              "CHECK(source='NOAA_SOLAR_EQUATIONS' AND source_version='NOAA_FRACTIONAL_YEAR_ZUUU_V1')",
              "CHECK(coordinate_version='ZUUU_CONFIG_COORDINATES_V1' AND timezone='Asia/Shanghai')",
              "CHECK(availability_basis='DETERMINISTIC_NOT_APPLICABLE' AND observation_time IS NULL AND source_available_time IS NULL)"],
    "sample": [f"FOREIGN KEY(business_date_bjt) REFERENCES {PREFIX}target(business_date_bjt)",
        "UNIQUE(business_date_bjt,horizon,selected_ecmwf_run_time_utc,selected_ecmwf_source_available_time_utc)",
        "CHECK(horizon IN ('T0','T1','T2'))", "CHECK(dataset_version='DATA_V1' AND meteostat_training_admission='BLOCKED')",
        "CHECK(sample_status IN ('ELIGIBLE','EXCLUDED_INCOMPLETE_ECMWF','EXCLUDED_LEAKAGE','EXCLUDED_TARGET_MISSING','EXCLUDED_RULE_VIOLATION'))",
        "CHECK(sample_status!='ELIGIBLE' OR (trajectory_present_hours=24 AND trajectory_valid_hours=24 AND exclusion_reason IS NULL))",
        "CHECK(sample_status='ELIGIBLE' OR exclusion_reason IS NOT NULL)",
        "CHECK(julianday(selected_ecmwf_source_available_time_utc) IS NOT NULL AND julianday(issue_time_utc) IS NOT NULL)",
        "CHECK(julianday(selected_ecmwf_source_available_time_utc)<=julianday(issue_time_utc))",
        "CHECK(julianday(selected_ecmwf_run_time_utc)<=julianday(selected_ecmwf_source_available_time_utc))"],
    "ecmwf_hourly": [f"""FOREIGN KEY(business_date_bjt,horizon,selected_ecmwf_run_time_utc,source_available_time_utc)
        REFERENCES {PREFIX}sample(business_date_bjt,horizon,selected_ecmwf_run_time_utc,selected_ecmwf_source_available_time_utc)""",
        f"FOREIGN KEY(solar_target_time,business_date_bjt) REFERENCES {PREFIX}solar(target_time,business_date_bjt)",
        "CHECK(solar_target_time=target_time_utc)", "CHECK(lead_hours BETWEEN 0 AND 71)"],
    "daily": [f"FOREIGN KEY(business_date_bjt,horizon) REFERENCES {PREFIX}sample(business_date_bjt,horizon)",
              "CHECK(trajectory_complete IN (0,1))",
              "CHECK(trajectory_complete=1 OR (ecmwf_daily_max_c IS NULL AND error_c IS NULL AND absolute_error_c IS NULL))"],
}


def create_schema(conn):
    """All statements participate in caller's BEGIN IMMEDIATE; no executescript."""
    for name in ("target", "solar", "sample", "ecmwf_hourly", "daily"):
        clauses = [f"{k} {v}" for k, v in SPECS[name].items()]
        clauses += ["PRIMARY KEY(" + ",".join(KEYS[name]) + ")", *CONSTRAINTS[name]]
        conn.execute(f"CREATE TABLE {PREFIX}{name} (" + ",".join(clauses) + ")")
    conn.execute(f"""CREATE TABLE {PREFIX}manifest (
        dataset_version TEXT PRIMARY KEY CHECK(dataset_version='DATA_V1'),
        contract_version TEXT NOT NULL, dataset_semantic_sha256 TEXT NOT NULL CHECK(length(dataset_semantic_sha256)=64),
        source_production_sha256 TEXT NOT NULL, source_auxiliary_sha256 TEXT NOT NULL,
        issue_rule_sha256 TEXT NOT NULL, meteostat_training_admission TEXT NOT NULL CHECK(meteostat_training_admission='BLOCKED'),
        build_status TEXT NOT NULL CHECK(build_status='ACCEPTED'), created_at_utc TEXT NOT NULL,
        audit_json TEXT NOT NULL CHECK(json_valid(audit_json)), implementation_sha256_json TEXT NOT NULL CHECK(json_valid(implementation_sha256_json)))""")
    conn.execute(f"CREATE INDEX {PREFIX}hourly_target ON {PREFIX}ecmwf_hourly(target_time_utc)")
    # Explicit allowlist: never expose Ground Truth timing, lineage, errors or
    # excluded partial trajectories through the feature view.
    fields = ["h.business_date_bjt", "h.horizon", "h.target_time_utc", "h.target_time_bjt", "h.lead_hours",
              "s.issue_time_utc", "s.selected_ecmwf_run_time_utc", "s.selected_ecmwf_source_available_time_utc"]
    fields += ["h." + f for f in WEATHER] + ["z." + f for f in SOLAR_FEATURES]
    gate = f"s.sample_status='ELIGIBLE' AND EXISTS(SELECT 1 FROM {PREFIX}manifest WHERE build_status='ACCEPTED')"
    conn.execute(f"""CREATE VIEW {PREFIX}training_hourly AS SELECT {','.join(fields)}
        FROM {PREFIX}ecmwf_hourly h JOIN {PREFIX}sample s USING(business_date_bjt,horizon)
        JOIN {PREFIX}solar z ON z.target_time=h.solar_target_time WHERE {gate}""")
    conn.execute(f"""CREATE VIEW {PREFIX}training_labels AS SELECT business_date_bjt,horizon,target_tmax_c
        FROM {PREFIX}sample s WHERE {gate}""")
    for name in (*KEYS, "manifest"):
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {PREFIX}{name}_block_{action.lower()}
                BEFORE {action} ON {PREFIX}{name} BEGIN SELECT RAISE(ABORT,'DATA V1 is append-only'); END""")


def insert_rows(conn, table, rows):
    columns = tuple(SPECS[table])
    for row in rows:
        if set(row) != set(columns):
            raise RuntimeError(f"Schema contract mismatch: {table}: {set(row)^set(columns)}")
    conn.executemany(f"INSERT INTO {PREFIX}{table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                     (tuple(r[c] for c in columns) for r in rows))


def read_rows(conn):
    return {name: [dict(r) for r in conn.execute(f"SELECT * FROM {PREFIX}{name} ORDER BY {','.join(KEYS[name])}")]
            for name in KEYS}
