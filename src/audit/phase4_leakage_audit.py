"""Hard failures for future-vintage and cross-vintage DATA V1 contamination."""
from datetime import date, timedelta
from src.audit.ecmwf_issue_rule_final_audit import issue_time_backtest, target_hours
from src.data_v1.contracts import BJT, utc, require


def audit_leakage(data):
    samples = {(r["business_date_bjt"], r["horizon"]): r for r in data["sample"]}
    for sample in samples.values():
        issue = utc(sample["issue_time_utc"])
        run = utc(sample["selected_ecmwf_run_time_utc"])
        available = utc(sample["selected_ecmwf_source_available_time_utc"])
        require(run <= available <= issue, "LEAKAGE: run/availability after historical cutoff")
        require(issue == issue_time_backtest(date.fromisoformat(sample["business_date_bjt"]), int(sample["horizon"][1])),
                "Issue rule/cutoff changed")
        require(sample["issue_time_bjt"] == issue.astimezone(BJT).isoformat(), "Issue BJT mismatch")
        require(sample["meteostat_training_admission"] == "BLOCKED", "Meteostat feature admission forbidden")
        if sample["ecmwf_availability_type"] == "official_schedule_estimate":
            require(sample["ecmwf_availability_semantics"] == "ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED",
                    "Historical availability cannot be represented as observed ingestion")
    for row in data["ecmwf_hourly"]:
        sample = samples[(row["business_date_bjt"], row["horizon"])]
        require(row["selected_ecmwf_run_time_utc"] == sample["selected_ecmwf_run_time_utc"]
                and row["source_raw_run_id"] == sample["source_raw_run_id"]
                and row["source_available_time_utc"] == sample["selected_ecmwf_source_available_time_utc"],
                "Cross-vintage trajectory detected")
        target = utc(row["target_time_utc"])
        require(target in target_hours(date.fromisoformat(row["business_date_bjt"])), "Trajectory outside BJT target day")
        require(target == utc(row["selected_ecmwf_run_time_utc"]) + timedelta(hours=row["lead_hours"]), "Invalid lead")
        require(row["target_time_bjt"] == target.astimezone(BJT).isoformat(), "Target BJT mismatch")
        require(row["target_time_utc"] == row["solar_target_time"], "Solar time misalignment")
    return 0
