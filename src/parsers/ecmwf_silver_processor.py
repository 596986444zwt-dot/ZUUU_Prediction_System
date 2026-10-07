"""Append-only ECMWF Raw -> Silver processor; historical rebuilding is blocked."""
import argparse
import json
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import DATABASE_PATH
from database.schema import backup_database, connect, ensure_schema
from src.ecmwf_write_safety import assert_new_silver_run, is_frozen_raw_run
from src.ecmwf_contract import (
    EXPECTED_UNITS, FIELD_MAP, HOURLY_VARIABLES, iso_utc, parse_utc, validate_payload,
)

PROCESSOR_VERSION = "ECMWF_SILVER_PROCESSOR_V3"
V2_REQUIRED_FIELDS = ["time", *HOURLY_VARIABLES]
UTC_TZ = timezone.utc
BJT_TZ = ZoneInfo("Asia/Shanghai")
parse_iso_datetime = parse_utc
parse_target_time = parse_utc


def kmh_to_ms(value):
    return None if value is None else value / 3.6


def is_v2_raw(data):
    return isinstance(data, dict) and isinstance(data.get("hourly"), dict) and all(
        field in data["hourly"] for field in V2_REQUIRED_FIELDS)


def validate_hourly_structure(data, run_time=None):
    if run_time is None:
        try:
            run_time = data["hourly"]["time"][0]
        except (KeyError, TypeError, IndexError):
            raise ValueError("Missing hourly timeline") from None
    return validate_payload(data, run_time, require_usable=False), []


def iter_pending_raw_runs(connection, batch_size=100):
    last_id = 0
    while True:
        rows = connection.execute("""SELECT r.* FROM ecmwf_raw_runs r WHERE r.id>?
            AND NOT EXISTS (SELECT 1 FROM ecmwf_hourly_forecasts s WHERE s.raw_run_id=r.id)
            ORDER BY r.id LIMIT ?""", (last_id, batch_size)).fetchall()
        if not rows:
            return
        for row in rows:
            last_id = row["id"]
            if not is_frozen_raw_run(connection, row["id"]):
                yield row


def get_unprocessed_raw_runs(connection):
    runs, skipped = [], []
    for row in iter_pending_raw_runs(connection):
        try:
            if is_v2_raw(json.loads(row["raw_json"])):
                runs.append(row)
            else:
                skipped.append((row["id"], "Not an 18-variable Raw snapshot"))
        except (ValueError, TypeError):
            skipped.append((row["id"], "Malformed Raw JSON"))
    return runs, skipped


def row_quality(hourly, index):
    values = {field: hourly[field][index] for field in HOURLY_VARIABLES}
    warnings = []
    if all(value is None for value in values.values()):
        return "ERROR", ["All 18 weather variables are null"]
    for field in ("temperature_2m", "dew_point_2m", "wind_speed_10m"):
        if values[field] is None:
            warnings.append(f"Missing core variable: {field}")
    for field, value in values.items():
        if value is None:
            continue
        if (field.startswith("cloud_cover") or field == "relative_humidity_2m") and not 0 <= value <= 100:
            warnings.append(f"{field} outside 0..100")
        if field == "wind_direction_10m" and not 0 <= value <= 360:
            warnings.append("Wind direction outside 0..360")
        if field in ("wind_speed_10m", "wind_gusts_10m", "shortwave_radiation",
                     "direct_radiation", "diffuse_radiation", "precipitation", "rain", "cape") and value < 0:
            warnings.append(f"Negative {field}")
    return ("WARNING" if warnings else "OK"), warnings


def process_raw_run(connection, raw_row):
    """Validate first; append a new run only and verify before commit."""
    data = json.loads(raw_row["raw_json"])
    run_time = parse_utc(raw_row["run_time_utc"])
    hourly = validate_payload(data, run_time, require_usable=False)
    rows = []
    for lead, target in enumerate(hourly["time"]):
        target_time = parse_utc(target)
        qc, warnings = row_quality(hourly, lead)
        row = {
            "raw_run_id": raw_row["id"], "model": raw_row["model"],
            "model_cycle": raw_row["model_cycle"], "run_time_utc": iso_utc(run_time),
            "source_available_time_utc": raw_row["source_available_time_utc"],
            "availability_type": raw_row["availability_type"], "ingest_time_utc": raw_row["ingest_time_utc"],
            "target_time_utc": iso_utc(target_time), "target_time_bjt": target_time.astimezone(BJT_TZ).isoformat(),
            "lead_hours": lead, "grid_latitude": data.get("latitude"),
            "grid_longitude": data.get("longitude"), "grid_elevation_m": data.get("elevation"),
            **{column: hourly[field][lead] for field, column in FIELD_MAP.items()},
            "wind_speed_10m_ms": kmh_to_ms(hourly["wind_speed_10m"][lead]),
            "wind_gusts_10m_ms": kmh_to_ms(hourly["wind_gusts_10m"][lead]),
            "parse_success": 1, "qc_status": qc, "qc_warnings_json": json.dumps(warnings),
            "processor_version": PROCESSOR_VERSION,
            "created_at_utc": iso_utc(__import__("datetime").datetime.now(UTC_TZ)),
        }
        rows.append(row)
    connection.execute("SAVEPOINT silver_run")
    try:
        assert_new_silver_run(connection, raw_row["id"])
        names = list(rows[0])
        sql = "INSERT INTO ecmwf_hourly_forecasts (" + ",".join(names) + ") VALUES (" + ",".join("?" for _ in names) + ")"
        connection.executemany(sql, [tuple(row[name] for name in names) for row in rows])
        actual = connection.execute("SELECT lead_hours FROM ecmwf_hourly_forecasts WHERE raw_run_id=? ORDER BY lead_hours", (raw_row["id"],)).fetchall()
        if [row[0] for row in actual] != list(range(72)):
            raise RuntimeError("Incomplete Silver run")
        connection.execute("RELEASE SAVEPOINT silver_run")
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT silver_run")
        connection.execute("RELEASE SAVEPOINT silver_run")
        raise
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args(argv)
    print(f"Database backup: {backup_database(args.database)}")
    connection = connect(args.database)
    failed = processed = skipped = 0
    try:
        ensure_schema(connection)
        for raw_row in iter_pending_raw_runs(connection):
            try:
                data = json.loads(raw_row["raw_json"])
                if not is_v2_raw(data):
                    if raw_row["data_spec_version"] == "ECMWF_18_VARIABLE_V2":
                        raise ValueError("Declared V2 Raw is missing required fields")
                    skipped += 1
                    continue
                processed += process_raw_run(connection, raw_row)
            except Exception as exc:
                failed += 1
                print(f"Raw {raw_row['id']} failed: {exc}", flush=True)
        print(f"Rows={processed}; legacy snapshots skipped={skipped}; failed runs={failed}")
        return 1 if failed else 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())

