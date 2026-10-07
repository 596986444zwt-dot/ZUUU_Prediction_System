"""Read-only DATA V1 acceptance, including exact source reconstruction."""
import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date

from src.audit.ecmwf_issue_rule_final_audit import target_hours
from src.audit.phase4_leakage_audit import audit_leakage
from src.data_v1.contracts import *
from src.data_v1.daily_features import summarize
from src.data_v1.hashing import dataset_hash
from src.data_v1.schema import KEYS, SPECS, PREFIX, read_rows
from src.data_v1.source_io import open_snapshot, frozen_fingerprints


def validate_data(data):
    require(set(data) == set(KEYS), "Unexpected Gold tables")
    for table, rows in data.items():
        require(all(set(r) == set(SPECS[table]) for r in rows), f"Unexpected feature/column in {table}")
        require(len({tuple(r[k] for k in KEYS[table]) for r in rows}) == len(rows), f"Duplicate Gold key in {table}")
    targets = {r["business_date_bjt"]: r for r in data["target"]}
    require(tuple(sorted(targets)) == DATES, "Target calendar mismatch")
    for target in targets.values():
        require(target["target_status"] == "FROZEN" and target["target_version"] == "ZUUU_TARGET_V1", "Ground Truth version mismatch")
        require(math.isfinite(target["daily_tmax_c"]) and float(target["daily_tmax_c"]).is_integer(), "Target not report integer Tmax")
        provenance = json.loads(target["source_provenance_json"])
        require(all(provenance[k] == target[k] for k in TARGET_FIELDS), "Target lineage/value mismatch")
        require(provenance["id"] == target["source_target_id"], "Target source ID mismatch")
    sample_keys = {(r["business_date_bjt"], r["horizon"]) for r in data["sample"]}
    require(sample_keys == {(d,h) for d in DATES for h in HORIZONS}, "Expected 2187 day/horizon records")
    solar = {r["target_time"]: r for r in data["solar"]}
    expected_times = {t.isoformat() for d in DATES for t in target_hours(date.fromisoformat(d))}
    require(set(solar) == expected_times and len(solar) == 17496, "Solar exact coverage mismatch")
    for instant, row in solar.items():
        local = utc(instant).astimezone(BJT)
        require(row["business_date_bjt"] == local.date().isoformat() and row["hour_bjt"] == local.hour, "Solar BJT misalignment")
        require(row["availability_basis"] == "DETERMINISTIC_NOT_APPLICABLE" and row["source_available_time"] is None
                and row["observation_time"] is None, "Solar fake observation availability")
    hourly = defaultdict(list)
    for row in data["ecmwf_hourly"]:
        key = row["business_date_bjt"], row["horizon"]
        require(key in sample_keys, "Orphan trajectory")
        require(row["solar_target_time"] in solar and solar[row["solar_target_time"]]["business_date_bjt"] == key[0], "Solar exact join failed")
        hourly[key].append(row)
        require(all(row[f] is None or math.isfinite(row[f]) for f in WEATHER), "Nonfinite feature")
    eligible, excluded = Counter(), []
    daily = {(r["business_date_bjt"], r["horizon"]): r for r in data["daily"]}
    require(set(daily) == sample_keys, "Daily audit coverage mismatch")
    for sample in data["sample"]:
        day, horizon = key = sample["business_date_bjt"], sample["horizon"]
        trajectory = sorted(hourly[key], key=lambda r: r["target_time_utc"])
        valid = sum(r["temperature_2m_c"] is not None for r in trajectory)
        require(sample["trajectory_expected_hours"] == 24 and sample["trajectory_present_hours"] == len(trajectory)
                and sample["trajectory_valid_hours"] == valid, "Stored coverage differs from actual trajectory")
        require(sample["target_tmax_c"] == targets[day]["daily_tmax_c"], "Label differs from frozen target")
        require(sample["dataset_version"] == VERSION and sample["ecmwf_archive_version"] == "ECMWF_ARCHIVE_V1"
                and sample["ecmwf_issue_rule_version"] == "ECMWF_ISSUE_RULE_V1"
                and sample["ecmwf_canonical_rule_version"] == "ECMWF_CANONICAL_RULE_V1", "Rule version mismatch")
        if sample["sample_status"] == "ELIGIBLE":
            require(len(trajectory) == valid == 24 and sample["exclusion_reason"] is None, "Incomplete eligible trajectory")
            require({r["target_time_utc"] for r in trajectory} == {t.isoformat() for t in target_hours(date.fromisoformat(day))}, "Missing eligible hour")
            eligible[horizon] += 1
        else:
            excluded.append(sample)
        require(daily[key] == summarize(sample, trajectory), "Daily audit calculation mismatch")
    require(dict(eligible) == {"T0":729,"T1":729,"T2":728}, "Eligible counts mismatch")
    require(len(excluded) == 1, "Expected one excluded sample")
    gap = excluded[0]
    require((gap["business_date_bjt"],gap["horizon"],gap["sample_status"],gap["trajectory_valid_hours"],gap["trajectory_present_hours"])
            == ("2025-08-07","T2","EXCLUDED_INCOMPLETE_ECMWF",14,14), "Exact gap signature mismatch")
    require(utc(gap["selected_ecmwf_run_time_utc"]) == utc("2025-08-04T06:00:00Z"), "Gap run mismatch")
    gap_hours = sorted(r["target_time_utc"] for r in hourly[("2025-08-07","T2")])
    require(gap_hours == [t.isoformat() for t in target_hours(date(2025,8,7))[:14]], "Gap must retain only original 14 hours")
    leakage = audit_leakage(data)
    return dict(ground_truth_days=729, total_samples=2187, eligible=dict(eligible), eligible_total=2186,
        excluded_total=1, excluded_gap={k:gap[k] for k in ("business_date_bjt","horizon","sample_status",
            "selected_ecmwf_run_time_utc","trajectory_present_hours","trajectory_valid_hours","exclusion_reason")},
        gap_first_time_utc=gap_hours[0], gap_last_time_utc=gap_hours[-1], leakage_violations=leakage,
        solar_rows=len(solar), solar_join_count=len(data["ecmwf_hourly"]), eligible_solar_join_count=2186*24,
        meteostat_in_training="NO", optional_null_counts={f:sum(r[f] is None for r in data["ecmwf_hourly"]) for f in WEATHER},
        dataset_semantic_sha256=dataset_hash(data))


def audit_connection(conn, *, reconstruct=True):
    require([r[0] for r in conn.execute("PRAGMA integrity_check")] == ["ok"], "Gold integrity_check failed")
    require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "Gold foreign_key_check failed")
    data = read_rows(conn)
    report = validate_data(data)
    manifests = conn.execute(f"SELECT * FROM {PREFIX}manifest").fetchall()
    require(len(manifests) == 1, "Missing or duplicate accepted manifest")
    manifest = dict(manifests[0])
    require(manifest["build_status"] == "ACCEPTED" and manifest["dataset_version"] == VERSION
            and manifest["contract_version"] == CONTRACT_VERSION, "Manifest status/version mismatch")
    require(manifest["dataset_semantic_sha256"] == report["dataset_semantic_sha256"], "Dataset hash mismatch")
    require(manifest["source_production_sha256"] == PRODUCTION_SHA and manifest["source_auxiliary_sha256"] == AUXILIARY_SHA,
            "Manifest source SHA mismatch")
    require(json.loads(manifest["audit_json"]) == report, "Manifest audit differs from actual rows")
    feature_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({PREFIX}training_hourly)")}
    expected_cols = {"business_date_bjt","horizon","target_time_utc","target_time_bjt","lead_hours","issue_time_utc",
                     "selected_ecmwf_run_time_utc","selected_ecmwf_source_available_time_utc", *WEATHER, *SOLAR_FEATURES}
    require(feature_cols == expected_cols, "Training view is not the feature allowlist")
    require(conn.execute(f"SELECT count(*) FROM {PREFIX}training_hourly").fetchone()[0] == 52464, "Training trajectory count mismatch")
    require(conn.execute(f"SELECT count(*) FROM {PREFIX}training_labels").fetchone()[0] == 2186, "Training label count mismatch")
    if reconstruct:
        from src.data_v1.trajectory_builder import build_rows
        p = open_snapshot(PRODUCTION, PRODUCTION_SHA)
        a = open_snapshot(AUXILIARY, AUXILIARY_SHA)
        try:
            expected = build_rows(p, a, manifest["created_at_utc"])
            for name in KEYS:
                order = lambda r: tuple(r[k] for k in KEYS[name])
                require(sorted(data[name],key=order) == sorted(expected[name],key=order), f"Gold/source exact lineage mismatch: {name}")
        finally:
            p.close()
            a.close()
    return dict(report, integrity_check="ok", foreign_key_check=0, source_reconstruction="PASS" if reconstruct else "NOT_RUN")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=GOLD)
    args = parser.parse_args(argv)
    before = frozen_fingerprints()
    conn = open_snapshot(args.database)
    try:
        report = audit_connection(conn)
    finally:
        conn.close()
    require(frozen_fingerprints() == before, "Frozen sources changed during audit")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("PHASE 4 DATA V1 ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
