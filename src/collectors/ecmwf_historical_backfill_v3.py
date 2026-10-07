"""Validated HRES archive backfill. Use python -m src.collectors.ecmwf_historical_backfill_v3."""
import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from config.settings import DATABASE_PATH, ECMWF_BRONZE_DIR, STATION_LATITUDE, STATION_LONGITUDE
from database.schema import backup_database, connect, ensure_schema
from src.ecmwf_contract import (
    DATA_SPEC_VERSION, FORECAST_HOURS, HOURLY_VARIABLES, MODEL, SOURCE,
    archive_cycle, iso_utc, parse_utc, validate_payload,
)

BACKFILL_VERSION = "ECMWF_HISTORICAL_BACKFILL_V3_1"
AVAILABILITY_RULE_VERSION = "HISTORICAL_ECMWF_DISSEMINATION_END_V1"
ARCHIVE_SCHEDULE_VERSION = "ECMWF_HISTORICAL_ARCHIVE_SCHEDULE_V1"
API_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
API_MODEL = "ecmwf_ifs"
ARCHIVE_TYPE = "historical"
BRONZE_DIR = ECMWF_BRONZE_DIR
LATITUDE, LONGITUDE = STATION_LATITUDE, STATION_LONGITUDE
MINIMUM_DATE = date(2024, 3, 14)
EARLY_END_DATE = date(2024, 8, 5)
TRANSITION_DATE = date(2024, 8, 6)
FULL_FOUR_RUN_START_DATE = date(2024, 8, 7)
TIMEOUT = 90
MAX_ATTEMPTS = 4
RETRY_DELAYS = (5, 15, 30)
REQUEST_INTERVAL_SECONDS = 0.8


def utc_now():
    return datetime.now(timezone.utc)


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def get_run_hours(day):
    if MINIMUM_DATE <= day <= EARLY_END_DATE:
        return (0, 12)
    if day == TRANSITION_DATE:
        return (0, 12, 18)
    if day >= FULL_FOUR_RUN_START_DATE:
        return (0, 6, 12, 18)
    return ()


def build_run_time(day, hour):
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def estimate_source_available_time(run_time):
    # Operational-style estimate, NOT actual historical archive publication.
    return run_time + timedelta(hours=6, minutes=12)


def build_run_plan(start_date, end_date, now=None):
    if start_date < MINIMUM_DATE or end_date < start_date:
        raise ValueError("Invalid archive date range")
    now = utc_now() if now is None else parse_utc(now)
    runs, day = [], start_date
    while day <= min(end_date, now.date()):
        for hour in get_run_hours(day):
            run_time = build_run_time(day, hour)
            if estimate_source_available_time(run_time) <= now:
                runs.append(run_time)
        day += timedelta(days=1)
    return runs


def get_archive_model_cycle(run_time):
    return archive_cycle(run_time, API_MODEL)


def connect_db(*, readonly=False, path=None):
    return connect(DATABASE_PATH if path is None else path, readonly=readonly)


def build_request_params(run_time):
    return {
        "latitude": LATITUDE, "longitude": LONGITUDE,
        "hourly": ",".join(HOURLY_VARIABLES), "forecast_hours": FORECAST_HOURS,
        "timezone": "UTC", "models": API_MODEL, "run": run_time.strftime("%Y-%m-%dT%H:%M"),
        "wind_speed_unit": "kmh", "temperature_unit": "celsius", "precipitation_unit": "mm",
    }


def task_key(run_time):
    identity = [MODEL, API_MODEL, iso_utc(run_time), LATITUDE, LONGITUDE, DATA_SPEC_VERSION, ARCHIVE_TYPE]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def find_existing_raw(connection, run_time):
    if connection is None:
        return None
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ecmwf_raw_runs'").fetchone():
        return None
    available = {row[1] for row in connection.execute("PRAGMA table_info(ecmwf_raw_runs)")}
    if not {"api_model", "data_spec_version", "archive_type"} <= available:
        return None
    rows = connection.execute("""SELECT * FROM ecmwf_raw_runs
        WHERE model=? AND run_time_utc=? AND api_model=?
          AND data_spec_version=? AND archive_type=? ORDER BY id DESC""",
        (MODEL, iso_utc(run_time), API_MODEL, DATA_SPEC_VERSION, ARCHIVE_TYPE))
    for row in rows:
        try:
            params = json.loads(row["request_parameters_json"])
            if float(params["latitude"]) != LATITUDE or float(params["longitude"]) != LONGITUDE:
                continue
            if params.get("models") != API_MODEL:
                continue
            if row["model_cycle"] != get_archive_model_cycle(run_time):
                raise ValueError("Archive cycle metadata requires migration")
            if hashlib.sha256(row["raw_json"].encode("utf-8")).hexdigest() != row["content_sha256"]:
                raise ValueError("Raw checksum mismatch")
            validate_payload(json.loads(row["raw_json"]), run_time)
            return row
        except (ValueError, TypeError, KeyError) as exc:
            print(f"Raw {row['id']} requires review: {exc}")
    return None


def ensure_status_task(connection, run_time):
    now = iso_utc(utc_now())
    connection.execute("""INSERT OR IGNORE INTO ecmwf_backfill_tasks
        (task_key,model,api_model,run_time_utc,latitude,longitude,data_spec_version,
         archive_type,status,collector_version,created_at_utc,updated_at_utc)
        VALUES (?,?,?,?,?,?,?,?,'PENDING',?,?,?)""",
        (task_key(run_time), MODEL, API_MODEL, iso_utc(run_time), LATITUDE, LONGITUDE,
         DATA_SPEC_VERSION, ARCHIVE_TYPE, BACKFILL_VERSION, now, now))
    connection.commit()


def mark_status(connection, run_time, status, *, http_status=None, error=None,
                raw_run_id=None, increment_attempt=False, commit=True):
    now = iso_utc(utc_now())
    connection.execute("""UPDATE ecmwf_backfill_tasks SET
        status=?, collector_version=?, updated_at_utc=?, last_http_status=?,
        last_error=?, raw_run_id=?, completed_time_utc=?, attempt_count=attempt_count+?,
        first_attempt_time_utc=CASE WHEN ? THEN COALESCE(first_attempt_time_utc,?) ELSE first_attempt_time_utc END,
        last_attempt_time_utc=CASE WHEN ? THEN ? ELSE last_attempt_time_utc END
        WHERE task_key=?""",
        (status, BACKFILL_VERSION, now, http_status, str(error)[:2000] if error else None,
         raw_run_id, now if status in ("SUCCESS", "SKIPPED_EXISTING") else None,
         int(increment_attempt), int(increment_attempt), now, int(increment_attempt), now,
         task_key(run_time)))
    if commit:
        connection.commit()


def download_run(session, run_time, on_attempt=None):
    last_error, last_http = None, None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if on_attempt:
            on_attempt()
        last_http = None
        try:
            response = session.get(API_URL, params=build_request_params(run_time), timeout=TIMEOUT)
            last_http = response.status_code
            if last_http == 200:
                try:
                    data = response.json()
                    validate_payload(data, run_time)
                except (ValueError, TypeError, KeyError) as exc:
                    return dict(success=False, retryable=False, attempts=attempt, http_status=200,
                                error=f"Invalid response: {exc}", data=None, request_url=response.url)
                return dict(success=True, retryable=False, attempts=attempt, http_status=200,
                            error=None, data=data, raw_text=response.text, request_url=response.url)
            last_error = f"HTTP {last_http}: {response.text[:1000]}"
            if last_http != 429 and not 500 <= last_http <= 599:
                return dict(success=False, retryable=False, attempts=attempt, http_status=last_http,
                            error=last_error, data=None, request_url=response.url)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except requests.exceptions.RequestException as exc:
            return dict(success=False, retryable=False, attempts=attempt, http_status=None,
                        error=str(exc), data=None, request_url=None)
        if attempt < MAX_ATTEMPTS:
            delay = RETRY_DELAYS[attempt - 1]
            if last_http == 429:
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = min(300, max(delay, int(retry_after)))
            print(f"Retry {run_time.isoformat()} in {delay}s: {last_error}", flush=True)
            time.sleep(delay)
    return dict(success=False, retryable=True, attempts=MAX_ATTEMPTS,
                http_status=last_http, error=last_error, data=None, request_url=None)


def save_bronze(run_time, ingest_time, raw_text):
    """Preserve response text and write a recovery provenance sidecar."""
    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ecmwf_ifs_hres_run_{run_time:%Y%m%dT%H%MZ}_ingest_{ingest_time:%Y%m%dT%H%M%S_%fZ}.json"
    path = BRONZE_DIR / name
    raw_bytes = raw_text.encode("utf-8")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    metadata = dict(run_time_utc=iso_utc(run_time), ingest_time_utc=iso_utc(ingest_time),
                    request_parameters=build_request_params(run_time), content_sha256=digest,
                    collector_version=BACKFILL_VERSION, archive_type=ARCHIVE_TYPE,
                    data_spec_version=DATA_SPEC_VERSION)
    for destination, content in (
        (path, raw_bytes),
        (path.with_suffix(".meta.json"), json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")),
    ):
        if destination.exists():
            raise FileExistsError(destination)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("xb") as output:
            output.write(content)
        temporary.replace(destination)
    return path, raw_text, digest


def insert_raw(connection, run_time, ingest_time, request_url, request_params,
               raw_text, bronze_path, sha256, *, commit=True, evidence=None):
    validate_payload(json.loads(raw_text), run_time)
    if request_params.get("models") != API_MODEL:
        raise ValueError("Request product does not match backfill product")
    if float(request_params.get("latitude", "nan")) != LATITUDE or float(request_params.get("longitude", "nan")) != LONGITUDE:
        raise ValueError("Request coordinates do not match station")
    if hashlib.sha256(raw_text.encode("utf-8")).hexdigest() != sha256:
        raise ValueError("Raw checksum mismatch")
    cursor = connection.execute("""INSERT INTO ecmwf_raw_runs (
        model,model_cycle,run_time_utc,source_available_time_utc,availability_type,
        ingest_time_utc,source,request_url,request_parameters_json,raw_json,
        bronze_file_path,content_sha256,created_at_utc,archive_type,data_spec_version,
        collector_version,availability_rule_version,api_model,requested_latitude,
        requested_longitude,recovery_evidence_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (MODEL, get_archive_model_cycle(run_time), iso_utc(run_time),
         iso_utc(estimate_source_available_time(run_time)), "official_schedule_estimate",
         iso_utc(ingest_time), SOURCE, request_url,
         json.dumps(request_params, ensure_ascii=False, sort_keys=True), raw_text,
         str(bronze_path), sha256, iso_utc(utc_now()), ARCHIVE_TYPE, DATA_SPEC_VERSION,
         BACKFILL_VERSION if evidence is None else "ECMWF_BRONZE_RECOVERY_V1",
         AVAILABILITY_RULE_VERSION, API_MODEL, LATITUDE, LONGITUDE,
         json.dumps(evidence, ensure_ascii=False) if evidence else None))
    if commit:
        connection.commit()
    return cursor.lastrowid


def process_run(connection, session, run_time, index, total):
    print(f"[{index}/{total}] {run_time.isoformat()}", flush=True)
    ensure_status_task(connection, run_time)
    existing = find_existing_raw(connection, run_time)
    if existing:
        mark_status(connection, run_time, "SKIPPED_EXISTING", raw_run_id=existing["id"])
        return "SKIPPED_EXISTING"
    result = download_run(session, run_time, on_attempt=lambda: mark_status(
        connection, run_time, "DOWNLOADING", increment_attempt=True))
    if not result["success"]:
        mark_status(connection, run_time, "FAILED", http_status=result["http_status"], error=result["error"])
        print(result["error"], flush=True)
        return "FAILED"
    ingest_time = utc_now()
    try:
        bronze_path, raw_text, digest = save_bronze(run_time, ingest_time, result["raw_text"])
        with connection:
            raw_id = insert_raw(connection, run_time, ingest_time, result["request_url"],
                                build_request_params(run_time), raw_text, bronze_path, digest, commit=False)
            mark_status(connection, run_time, "SUCCESS", http_status=200, raw_run_id=raw_id, commit=False)
    except Exception as exc:
        connection.rollback()
        mark_status(connection, run_time, "FAILED", http_status=200,
                    error=f"Persistence error: {type(exc).__name__}: {exc}")
        print(f"Persistence failed; Bronze retained: {exc}", flush=True)
        return "FAILED"
    return "SUCCESS"


def build_plan_stats(connection, runs):
    existing, missing = [], []
    for run_time in runs:
        row = find_existing_raw(connection, run_time)
        (existing if row else missing).append(run_time)
    return dict(existing=existing, missing=missing)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--limit", type=int, help="Maximum runs to download in this execution")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    return args


def main(argv=None):
    args = parse_args(argv)
    runs = build_run_plan(args.start, args.end)
    connection = None
    try:
        if args.plan:
            if args.database.exists():
                connection = connect_db(readonly=True, path=args.database)
        else:
            print(f"Database backup: {backup_database(args.database)}")
            connection = connect_db(path=args.database)
            ensure_schema(connection)
        stats = build_plan_stats(connection, runs)
        print(f"Product={API_MODEL}; runs={len(runs)}; existing={len(stats['existing'])}; missing={len(stats['missing'])}")
        print("Archive hindcasts are not historical operational forecasts. Availability is an estimate.")
        if args.plan:
            return 0
        selected = stats["missing"][:args.limit] if args.limit else stats["missing"]
        failed = 0
        with requests.Session() as session:
            for index, run_time in enumerate(selected, 1):
                failed += process_run(connection, session, run_time, index, len(selected)) == "FAILED"
                if index < len(selected):
                    time.sleep(REQUEST_INTERVAL_SECONDS)
        print(f"Completed={len(selected)-failed}; failed={failed}")
        return 1 if failed else 0
    except KeyboardInterrupt:
        print("Interrupted; committed Raw and Bronze retained.")
        return 130
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[FATAL] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

