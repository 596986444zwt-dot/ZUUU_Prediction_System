"""Assemble Gold in memory from frozen sources; no storage side effects."""
import json
import math
from datetime import date, timedelta
from src.audit.ecmwf_issue_rule_final_audit import target_hours, EXPECTED_RULE_SHA
from .contracts import *
from .issue_mapping import build_mapping
from .solar_join import load_solar
from .daily_features import summarize


def build_rows(production, auxiliary, created_at):
    mapping = build_mapping(production)  # Gate first; abort on any coverage/rule mismatch.
    solar = load_solar(auxiliary)
    targets = {}
    for record in production.execute("SELECT * FROM zuuu_target_v1 ORDER BY business_date_bjt"):
        original = dict(record)
        row = {key: original[key] for key in TARGET_FIELDS}
        row["source_target_id"] = original["id"]
        row["source_provenance_json"] = json.dumps(original, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        targets[row["business_date_bjt"]] = row
    require(tuple(targets) == DATES, "Ground Truth dates mismatch")
    samples, trajectories, daily = [], [], []
    # Cache selected source vintages only, without raw JSON payloads.
    cache = {}
    for mapped in mapping:
        day, horizon, run, issue = mapped["day"], mapped["horizon"], mapped["run"], mapped["issue"]
        raw_id = run["canonical_raw_run_id"]
        if raw_id not in cache:
            raw = production.execute("""SELECT id, run_time_utc, content_sha256, source_available_time_utc,
                availability_type, model, model_cycle, api_model, data_spec_version, ingest_time_utc
                FROM ecmwf_raw_runs WHERE id=?""", (raw_id,)).fetchone()
            require(raw is not None, "Missing canonical Raw")
            for key in ("run_time_utc", "content_sha256", "source_available_time_utc", "availability_type",
                        "model", "model_cycle", "api_model", "data_spec_version", "ingest_time_utc"):
                require(raw[key] == run[key], f"Canonical / Raw mismatch: {raw_id}/{key}")
            cache[raw_id] = [dict(r) for r in production.execute(
                "SELECT * FROM ecmwf_hourly_forecasts WHERE raw_run_id=? ORDER BY target_time_utc", (raw_id,))]
        expected = set(target_hours(date.fromisoformat(day)))
        rows = []
        for source in cache[raw_id]:
            instant = utc(source["target_time_utc"])
            if instant not in expected:
                continue
            for key in ("run_time_utc", "source_available_time_utc", "availability_type", "model", "model_cycle", "ingest_time_utc"):
                require(source[key] == run[key], f"Cross-vintage/provenance mismatch: {day}/{horizon}/{key}")
            require(utc(run["run_time_utc"]) + timedelta(hours=source["lead_hours"]) == instant, "Lead/time mismatch")
            require(source["target_time_bjt"] == instant.astimezone(BJT).isoformat(), "Forecast BJT mismatch")
            require(instant in solar, "Missing Solar exact join")
            for field in WEATHER:
                require(source[field] is None or math.isfinite(source[field]), f"Nonfinite source field {field}")
            rows.append(dict(business_date_bjt=day, horizon=horizon,
                selected_ecmwf_run_time_utc=run["run_time_utc"], source_raw_run_id=raw_id,
                source_hourly_id=source["id"], target_time_utc=source["target_time_utc"],
                target_time_bjt=source["target_time_bjt"], lead_hours=source["lead_hours"],
                solar_target_time=solar[instant]["target_time"], source_available_time_utc=source["source_available_time_utc"],
                availability_type=source["availability_type"], model=source["model"], model_cycle=source["model_cycle"],
                grid_latitude=source["grid_latitude"], grid_longitude=source["grid_longitude"],
                grid_elevation_m=source["grid_elevation_m"], parse_success=source["parse_success"],
                qc_status=source["qc_status"], qc_warnings_json=source["qc_warnings_json"],
                processor_version=source["processor_version"], **{key: source[key] for key in WEATHER}))
        require(len({r["target_time_utc"] for r in rows}) == len(rows), "Duplicate trajectory hour")
        valid = sum(r["temperature_2m_c"] is not None for r in rows)
        require((len(rows), valid) == (mapped["present"], mapped["valid"]), "Mapping/trajectory coverage differs")
        complete = len(rows) == valid == 24
        sample = dict(business_date_bjt=day, horizon=horizon, target_tmax_c=targets[day]["daily_tmax_c"],
            issue_time_bjt=issue.astimezone(BJT).isoformat(), issue_time_utc=issue.isoformat(),
            selected_ecmwf_run_time_utc=run["run_time_utc"],
            selected_ecmwf_source_available_time_utc=run["source_available_time_utc"],
            ecmwf_availability_semantics=run["availability_semantics"], ecmwf_availability_type=run["availability_type"],
            ecmwf_availability_rule_version=run["availability_rule_version"],
            ecmwf_archive_version=run["archive_version"], ecmwf_issue_rule_version="ECMWF_ISSUE_RULE_V1",
            ecmwf_issue_rule_sha256=EXPECTED_RULE_SHA, ecmwf_canonical_rule_version=run["canonical_rule_version"],
            ecmwf_content_sha256=run["content_sha256"], ecmwf_api_model=run["api_model"],
            ecmwf_data_spec_version=run["data_spec_version"], source_ingest_time_utc=run["ingest_time_utc"],
            source_archive_id=run["id"], source_raw_run_id=raw_id,
            trajectory_expected_hours=24, trajectory_present_hours=len(rows), trajectory_valid_hours=valid,
            sample_status="ELIGIBLE" if complete else "EXCLUDED_INCOMPLETE_ECMWF",
            exclusion_reason=None if complete else "KNOWN_SOURCE_COVERAGE_GAP_2025_08_07_T2_14_OF_24",
            meteostat_training_admission="BLOCKED", dataset_version=VERSION, created_at_utc=utc(created_at).isoformat())
        samples.append(sample)
        trajectories.extend(rows)
        daily.append(summarize(sample, rows))
    return {"target": list(targets.values()), "sample": samples, "ecmwf_hourly": trajectories,
            "solar": list(solar.values()), "daily": daily}
