from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from src.auxiliary.meteostat.parser import parse_hourly


# =============================================================================
# CONFIG
# =============================================================================

PROJECT_ROOT = Path(r"C:\ZUUU_Prediction_System")

MAIN_DB = PROJECT_ROOT / "database" / "zuuu_prediction.db"
AUX_DB = PROJECT_ROOT / "database" / "phase3_auxiliary_v1.db"

STATION_ID = "56294"
YEARS = (2024, 2025, 2026)

BASE_URL = "https://data.meteostat.net"

STATION_URL = f"{BASE_URL}/stations/{STATION_ID}.json"

MAX_COMPRESSED_BYTES = 2_000_000
MAX_EXPANDED_BYTES = 10_000_000

UTC = timezone.utc

TARGET_START_UTC = datetime(
    2024, 9, 2, 16, 0, 0,
    tzinfo=UTC,
)

TARGET_END_UTC = datetime(
    2026, 9, 1, 16, 0, 0,
    tzinfo=UTC,
)

EXPECTED_TARGET_HOURS = 17_496
EXPECTED_SOLAR_ROWS = 17_496

USER_AGENT = "ZUUU-PHASE3-AUXILIARY-V1"


# =============================================================================
# HASH
# =============================================================================

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            h.update(chunk)

    return h.hexdigest()


# =============================================================================
# HTTP
# =============================================================================

def fetch(url: str):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
        },
    )

    with urlopen(
        request,
        timeout=30,
    ) as response:

        status = getattr(
            response,
            "status",
            200,
        )

        payload = response.read(
            MAX_COMPRESSED_BYTES + 1
        )

        if len(payload) > MAX_COMPRESSED_BYTES:
            raise RuntimeError(
                f"Compressed byte limit exceeded: {url}"
            )

        headers = dict(
            response.headers.items()
        )

    ingest_time = datetime.now(
        UTC
    ).isoformat()

    return (
        status,
        payload,
        headers,
        ingest_time,
    )


def decompress_gzip(payload: bytes) -> bytes:
    with gzip.GzipFile(
        fileobj=io.BytesIO(payload)
    ) as f:

        expanded = f.read(
            MAX_EXPANDED_BYTES + 1
        )

    if len(expanded) > MAX_EXPANDED_BYTES:
        raise RuntimeError(
            "Expanded byte limit exceeded"
        )

    return expanded


# =============================================================================
# STATION METADATA
# =============================================================================

def validate_station_metadata(data):
    if not isinstance(data, dict):
        raise RuntimeError(
            "Station metadata is not an object"
        )

    location = data.get("location")

    if not isinstance(location, dict):
        raise RuntimeError(
            "Station metadata missing location"
        )

    latitude = location.get("latitude")
    longitude = location.get("longitude")

    if (
        isinstance(latitude, bool)
        or not isinstance(latitude, (int, float))
    ):
        raise RuntimeError(
            "Invalid station latitude"
        )

    if (
        isinstance(longitude, bool)
        or not isinstance(longitude, (int, float))
    ):
        raise RuntimeError(
            "Invalid station longitude"
        )

    if not -90 <= float(latitude) <= 90:
        raise RuntimeError(
            "Station latitude out of range"
        )

    if not -180 <= float(longitude) <= 180:
        raise RuntimeError(
            "Station longitude out of range"
        )

    return (
        float(latitude),
        float(longitude),
    )


def normalize_station_name(data) -> str:
    """
    Meteostat station name may be a string or a multilingual dictionary.
    aux_v1_station_snapshot.name requires TEXT.
    """

    raw_name = data.get("name")

    if isinstance(raw_name, str):
        value = raw_name.strip()

        if value:
            return value

    if isinstance(raw_name, dict):

        # Prefer English, then Chinese.
        for key in (
            "en",
            "zh",
            "zh-cn",
            "zh_CN",
            "de",
        ):
            value = raw_name.get(key)

            if isinstance(value, str):
                value = value.strip()

                if value:
                    return value

        # Deterministic fallback.
        for key in sorted(raw_name):
            value = raw_name[key]

            if isinstance(value, str):
                value = value.strip()

                if value:
                    return value

    return f"Meteostat {STATION_ID}"


def normalize_station_timezone(data) -> str:
    raw_timezone = data.get("timezone")

    if isinstance(raw_timezone, str):
        value = raw_timezone.strip()

        if value:
            return value

    if isinstance(raw_timezone, dict):

        for key in (
            "name",
            "timezone",
            "id",
        ):
            value = raw_timezone.get(key)

            if isinstance(value, str):
                value = value.strip()

                if value:
                    return value

    return "Asia/Shanghai"


def normalize_identifiers(data):
    identifiers = data.get(
        "identifiers",
        {},
    )

    if identifiers is None:
        return {}

    if isinstance(
        identifiers,
        (dict, list, str, int, float, bool),
    ):
        return identifiers

    return {
        "raw_repr": str(identifiers)
    }


def download_station():
    (
        status,
        payload,
        headers,
        ingest_time,
    ) = fetch(STATION_URL)

    if status != 200:
        raise RuntimeError(
            f"Station HTTP status={status}"
        )

    try:
        data = json.loads(
            payload.decode("utf-8")
        )

    except Exception as exc:
        raise RuntimeError(
            "Invalid station JSON"
        ) from exc

    latitude, longitude = (
        validate_station_metadata(data)
    )

    digest = sha256_bytes(
        payload
    )

    return {
        "station_id": STATION_ID,
        "url": STATION_URL,
        "status": status,
        "payload": payload,
        "metadata": data,
        "sha256": digest,
        "ingest_time": ingest_time,
        "headers": headers,
        "latitude": latitude,
        "longitude": longitude,
        "name": normalize_station_name(data),
        "timezone": normalize_station_timezone(data),
        "identifiers": normalize_identifiers(data),
    }


# =============================================================================
# HOURLY ARCHIVE DOWNLOAD
# =============================================================================

def download_year(year: int):
    url = (
        f"{BASE_URL}/hourly/"
        f"{year}/{STATION_ID}.csv.gz"
    )

    (
        status,
        payload,
        headers,
        ingest_time,
    ) = fetch(url)

    if status != 200:
        raise RuntimeError(
            f"{year}: HTTP status={status}"
        )

    if len(payload) < 20:
        raise RuntimeError(
            f"{year}: suspiciously small payload"
        )

    digest = sha256_bytes(
        payload
    )

    expanded = decompress_gzip(
        payload
    )

    try:
        text = expanded.decode(
            "utf-8"
        )

    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"{year}: CSV is not UTF-8"
        ) from exc

    rows = parse_hourly(
        text,
        STATION_ID,
        ingest_time=ingest_time,
        snapshot_sha256=digest,
    )

    if not rows:
        raise RuntimeError(
            f"{year}: parser returned zero rows"
        )

    return {
        "year": year,
        "url": url,
        "status": status,
        "payload": payload,
        "headers": headers,
        "ingest_time": ingest_time,
        "sha256": digest,
        "rows": rows,
    }


# =============================================================================
# TARGET WINDOW
# =============================================================================

def filter_target_window(rows):
    result = []

    for row in rows:
        observation = datetime.fromisoformat(
            row["observation_time"]
        ).astimezone(UTC)

        if (
            TARGET_START_UTC
            <= observation
            < TARGET_END_UTC
        ):
            result.append(row)

    return result


# =============================================================================
# AUX DB PRECHECK
# =============================================================================

def inspect_aux_db():
    if not AUX_DB.exists():
        raise RuntimeError(
            f"Auxiliary DB not found: {AUX_DB}"
        )

    uri = (
        AUX_DB.resolve().as_uri()
        + "?mode=ro"
    )

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    try:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            )
        }

        required = {
            "aux_v1_station_snapshot",
            "aux_v1_raw_snapshot",
            "aux_v1_meteostat_hourly",
            "aux_v1_solar_time",
        }

        missing = required - tables

        if missing:
            raise RuntimeError(
                f"Missing auxiliary tables: {sorted(missing)}"
            )

        solar_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM aux_v1_solar_time
            """
        ).fetchone()[0]

        existing_station = conn.execute(
            """
            SELECT COUNT(*)
            FROM aux_v1_station_snapshot
            """
        ).fetchone()[0]

        existing_raw = conn.execute(
            """
            SELECT COUNT(*)
            FROM aux_v1_raw_snapshot
            """
        ).fetchone()[0]

        existing_hourly = conn.execute(
            """
            SELECT COUNT(*)
            FROM aux_v1_meteostat_hourly
            """
        ).fetchone()[0]

        return {
            "solar": solar_count,
            "station": existing_station,
            "raw": existing_raw,
            "hourly": existing_hourly,
        }

    finally:
        conn.close()


# =============================================================================
# INSERT STATION
# =============================================================================

def insert_station(
    conn: sqlite3.Connection,
    station,
):
    data = station["metadata"]

    location = data["location"]

    name = station["name"]
    timezone_name = station["timezone"]
    identifiers = station["identifiers"]

    elevation = location.get(
        "elevation"
    )

    if isinstance(elevation, bool):
        elevation = None

    elif elevation is not None:
        try:
            elevation = float(elevation)
        except (TypeError, ValueError):
            elevation = None

    metadata_json = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    identifiers_json = json.dumps(
        identifiers,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    # Explicitly guarantee SQLite TEXT binding.
    if not isinstance(name, str):
        raise RuntimeError(
            f"Normalized station name is not TEXT: {type(name)}"
        )

    if not isinstance(timezone_name, str):
        raise RuntimeError(
            "Normalized station timezone is not TEXT"
        )

    cursor = conn.execute(
        """
        INSERT INTO aux_v1_station_snapshot (
            station_id,
            name,
            latitude,
            longitude,
            elevation_m,
            timezone,
            identifiers_json,
            source,
            source_version,
            ingest_time,
            metadata_sha256,
            metadata_json
        )
        VALUES (
            ?,?,?,?,?,?,
            ?,?,?,?,?,?
        )
        """,
        (
            STATION_ID,
            name,
            station["latitude"],
            station["longitude"],
            elevation,
            timezone_name,
            identifiers_json,
            "Meteostat",
            "sha256:" + station["sha256"],
            station["ingest_time"],
            station["sha256"],
            metadata_json,
        ),
    )

    return cursor.lastrowid


# =============================================================================
# INSERT RAW SNAPSHOT
# =============================================================================

def insert_raw_snapshot(
    conn: sqlite3.Connection,
    station_snapshot_id: int,
    item,
):
    headers = item["headers"]

    cursor = conn.execute(
        """
        INSERT INTO aux_v1_raw_snapshot (
            station_snapshot_id,
            request_url,
            requested_year,
            source,
            source_version,
            source_available_time,
            availability_basis,
            ingest_time,
            timezone,
            content_sha256,
            http_etag,
            http_last_modified,
            http_status,
            response_blob,
            compression,
            role
        )
        VALUES (
            ?,?,?,?,?,?,
            ?,?,?,?,?,?,
            ?,?,?,?
        )
        """,
        (
            station_snapshot_id,
            item["url"],
            item["year"],
            "Meteostat",
            "sha256:" + item["sha256"],
            None,
            "UNKNOWN",
            item["ingest_time"],
            "UTC",
            item["sha256"],
            headers.get("ETag")
            or headers.get("etag"),
            headers.get("Last-Modified")
            or headers.get("last-modified"),
            item["status"],
            item["payload"],
            "gzip",
            "AUXILIARY_ONLY",
        ),
    )

    return cursor.lastrowid


# =============================================================================
# INSERT HOURLY PARAMETERS
# =============================================================================

def insert_hourly(
    conn: sqlite3.Connection,
    snapshot_id: int,
    rows,
):
    sql = """
    INSERT INTO aux_v1_meteostat_hourly (
        snapshot_id,
        observation_time,
        target_time,
        parameter,
        value,
        raw_value,
        unit,
        provider,
        value_kind,
        quality,
        source,
        source_version,
        parser_version,
        source_available_time,
        availability_basis,
        evidence_sha256,
        ingest_time,
        timezone,
        resolution_seconds,
        interval_semantics
    )
    VALUES (
        ?,?,?,?,?,?,
        ?,?,?,?,?,?,
        ?,?,?,?,?,?,
        ?,?
    )
    """

    payload = []

    for row in rows:

        values = row.get("values")

        if not isinstance(values, dict):
            raise RuntimeError(
                "Parsed hourly row missing values dictionary"
            )

        for value in values.values():

            payload.append(
                (
                    snapshot_id,
                    row["observation_time"],
                    row["target_time"],
                    value["parameter"],
                    value["value"],
                    value["raw_value"],
                    value["unit"],
                    value["provider"],
                    value["value_kind"],
                    value["quality"],
                    row["source"],
                    row["source_version"],
                    row["parser_version"],
                    row["source_available_time"],
                    row["availability_basis"],
                    row["evidence_sha256"],
                    row["ingest_time"],
                    row["timezone"],
                    row["resolution_seconds"],
                    row["interval_semantics"],
                )
            )

    conn.executemany(
        sql,
        payload,
    )

    return len(payload)


# =============================================================================
# DATABASE AUDIT
# =============================================================================

def audit(
    conn: sqlite3.Connection,
):
    result = {}

    result["integrity"] = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    result["foreign_keys"] = len(
        conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    )

    result["solar"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_solar_time
        """
    ).fetchone()[0]

    result["station"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_station_snapshot
        """
    ).fetchone()[0]

    result["raw"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_raw_snapshot
        """
    ).fetchone()[0]

    result["hourly"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        """
    ).fetchone()[0]

    result["hours"] = conn.execute(
        """
        SELECT COUNT(
            DISTINCT observation_time
        )
        FROM aux_v1_meteostat_hourly
        """
    ).fetchone()[0]

    result["observation_valid"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE value_kind='OBSERVATION'
          AND quality='VALID'
        """
    ).fetchone()[0]

    result["model_valid"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE value_kind='MODEL'
          AND quality='VALID'
        """
    ).fetchone()[0]

    result["unknown_valid"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE value_kind='UNKNOWN'
          AND quality='VALID'
        """
    ).fetchone()[0]

    result["availability_unknown"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE availability_basis='UNKNOWN'
        """
    ).fetchone()[0]

    result["availability_illegal"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE availability_basis!='UNKNOWN'
           OR source_available_time IS NOT NULL
        """
    ).fetchone()[0]

    result["out_of_window"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE observation_time < ?
           OR observation_time >= ?
        """,
        (
            TARGET_START_UTC.isoformat(),
            TARGET_END_UTC.isoformat(),
        ),
    ).fetchone()[0]

    result["bad_source"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE source!='Meteostat'
        """
    ).fetchone()[0]

    result["bad_timezone"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE timezone!='UTC'
        """
    ).fetchone()[0]

    result["bad_resolution"] = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_meteostat_hourly
        WHERE resolution_seconds!=3600
           OR interval_semantics!='SOURCE_HOUR_LABEL'
        """
    ).fetchone()[0]

    result["parameter_counts"] = conn.execute(
        """
        SELECT
            parameter,
            COUNT(*),

            SUM(
                CASE
                WHEN quality='VALID'
                 AND value_kind='OBSERVATION'
                THEN 1
                ELSE 0
                END
            ),

            SUM(
                CASE
                WHEN quality='VALID'
                 AND value_kind='MODEL'
                THEN 1
                ELSE 0
                END
            ),

            SUM(
                CASE
                WHEN quality='VALID'
                 AND value_kind='UNKNOWN'
                THEN 1
                ELSE 0
                END
            ),

            SUM(
                CASE
                WHEN quality='MISSING'
                THEN 1
                ELSE 0
                END
            ),

            SUM(
                CASE
                WHEN quality='UNSUPPORTED'
                THEN 1
                ELSE 0
                END
            ),

            SUM(
                CASE
                WHEN quality='INVALID'
                THEN 1
                ELSE 0
                END
            )

        FROM aux_v1_meteostat_hourly

        GROUP BY parameter

        ORDER BY parameter
        """
    ).fetchall()

    return result


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--dry-run",
        action="store_true",
    )

    group.add_argument(
        "--commit",
        action="store_true",
    )

    args = parser.parse_args()

    # -------------------------------------------------------------------------
    # Production DB protection
    # -------------------------------------------------------------------------

    if not MAIN_DB.exists():
        raise SystemExit(
            f"Production database missing: {MAIN_DB}"
        )

    if not AUX_DB.exists():
        raise SystemExit(
            f"Auxiliary database missing: {AUX_DB}"
        )

    before_main_sha = sha256_file(
        MAIN_DB
    )

    before_aux_sha = sha256_file(
        AUX_DB
    )

    state = inspect_aux_db()

    if state["solar"] != EXPECTED_SOLAR_ROWS:
        raise SystemExit(
            "REFUSING: Solar/Time archive is not "
            f"{EXPECTED_SOLAR_ROWS} rows."
        )

    # Previous failed transaction should have rolled back.
    # Refuse if any Meteostat data already exists.
    if (
        state["station"] != 0
        or state["raw"] != 0
        or state["hourly"] != 0
    ):
        raise SystemExit(
            "\nREFUSING TO CONTINUE.\n\n"
            "Meteostat archive already contains data:\n"
            f"Station snapshots : {state['station']}\n"
            f"Raw snapshots     : {state['raw']}\n"
            f"Hourly rows       : {state['hourly']}\n\n"
            "No overwrite, DELETE or rebuild will be performed."
        )

    print("=" * 88)
    print("PHASE 3 METEOSTAT ARCHIVE BUILDER")
    print("=" * 88)

    print(
        f"Main DB SHA256 Before : "
        f"{before_main_sha}"
    )

    print(
        f"Aux DB SHA256 Before  : "
        f"{before_aux_sha}"
    )

    print(
        f"Solar Rows Protected  : "
        f"{state['solar']}"
    )

    # -------------------------------------------------------------------------
    # Station
    # -------------------------------------------------------------------------

    print()
    print(
        "Downloading station metadata..."
    )

    station = download_station()

    print(
        f"Station              : "
        f"{STATION_ID}"
    )

    print(
        f"Station name         : "
        f"{station['name']}"
    )

    print(
        f"Station coordinates  : "
        f"{station['latitude']}, "
        f"{station['longitude']}"
    )

    print(
        f"Station timezone     : "
        f"{station['timezone']}"
    )

    print(
        f"Metadata SHA256      : "
        f"{station['sha256']}"
    )

    # -------------------------------------------------------------------------
    # Year snapshots
    # -------------------------------------------------------------------------

    downloads = []
    all_target_rows = []

    for year in YEARS:

        print(
            f"Downloading Meteostat {year}..."
        )

        item = download_year(
            year
        )

        target_rows = filter_target_window(
            item["rows"]
        )

        item["target_rows"] = (
            target_rows
        )

        downloads.append(
            item
        )

        all_target_rows.extend(
            target_rows
        )

        print(
            f"  Source rows        : "
            f"{len(item['rows'])}"
        )

        print(
            f"  Target rows        : "
            f"{len(target_rows)}"
        )

        print(
            f"  SHA256             : "
            f"{item['sha256']}"
        )

    # -------------------------------------------------------------------------
    # Source hour validation
    # -------------------------------------------------------------------------

    unique_times = {
        row["observation_time"]
        for row in all_target_rows
    }

    print()
    print(
        f"Target source hours  : "
        f"{len(all_target_rows)}"
    )

    print(
        f"Unique source hours  : "
        f"{len(unique_times)}"
    )

    print(
        f"Expected target hours: "
        f"{EXPECTED_TARGET_HOURS}"
    )

    print(
        f"Absent source hours  : "
        f"{EXPECTED_TARGET_HOURS - len(unique_times)}"
    )

    if len(all_target_rows) != len(unique_times):
        raise SystemExit(
            "Duplicate observation hours across "
            "year snapshots"
        )

    if len(unique_times) > EXPECTED_TARGET_HOURS:
        raise SystemExit(
            "Source contains more unique target "
            "hours than expected"
        )

    # -------------------------------------------------------------------------
    # DRY RUN
    # -------------------------------------------------------------------------

    if args.dry_run:

        after_main_sha = sha256_file(
            MAIN_DB
        )

        after_aux_sha = sha256_file(
            AUX_DB
        )

        print()
        print("=" * 88)
        print("DRY-RUN AUDIT")
        print("=" * 88)

        print(
            f"Main SHA Match       : "
            f"{before_main_sha == after_main_sha}"
        )

        print(
            f"Aux SHA Match        : "
            f"{before_aux_sha == after_aux_sha}"
        )

        print(
            "Database Modification: NONE"
        )

        if before_main_sha != after_main_sha:
            raise SystemExit(
                "MAIN DATABASE CHANGED"
            )

        if before_aux_sha != after_aux_sha:
            raise SystemExit(
                "AUX DATABASE CHANGED"
            )

        print()
        print(
            "RESULT: PHASE 3 METEOSTAT "
            "DRY-RUN PASS"
        )

        print(
            "READY FOR --commit"
        )

        return

    # -------------------------------------------------------------------------
    # COMMIT
    # -------------------------------------------------------------------------

    conn = sqlite3.connect(
        str(AUX_DB)
    )

    conn.execute(
        "PRAGMA foreign_keys=ON"
    )

    conn.execute(
        "PRAGMA recursive_triggers=ON"
    )

    try:

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        # Re-check while holding write transaction.
        existing = conn.execute(
            """
            SELECT
                (
                    SELECT COUNT(*)
                    FROM aux_v1_station_snapshot
                ),
                (
                    SELECT COUNT(*)
                    FROM aux_v1_raw_snapshot
                ),
                (
                    SELECT COUNT(*)
                    FROM aux_v1_meteostat_hourly
                )
            """
        ).fetchone()

        if existing != (0, 0, 0):
            raise RuntimeError(
                "Meteostat tables became non-empty "
                "before commit"
            )

        station_snapshot_id = (
            insert_station(
                conn,
                station,
            )
        )

        inserted_parameter_rows = 0

        for item in downloads:

            snapshot_id = (
                insert_raw_snapshot(
                    conn,
                    station_snapshot_id,
                    item,
                )
            )

            inserted_parameter_rows += (
                insert_hourly(
                    conn,
                    snapshot_id,
                    item["target_rows"],
                )
            )

        # ---------------------------------------------------------------------
        # Pre-commit audit
        # ---------------------------------------------------------------------

        result = audit(
            conn
        )

        errors = []

        if result["integrity"] != "ok":
            errors.append(
                "integrity_check failed"
            )

        if result["foreign_keys"] != 0:
            errors.append(
                "foreign key errors"
            )

        if result["solar"] != EXPECTED_SOLAR_ROWS:
            errors.append(
                "Solar/Time archive changed"
            )

        if result["station"] != 1:
            errors.append(
                "station snapshot count != 1"
            )

        if result["raw"] != 3:
            errors.append(
                "raw snapshot count != 3"
            )

        if result["hours"] != len(
            unique_times
        ):
            errors.append(
                "distinct source hour count mismatch"
            )

        if (
            result["hourly"]
            != inserted_parameter_rows
        ):
            errors.append(
                "parameter row insert count mismatch"
            )

        if result["availability_illegal"] != 0:
            errors.append(
                "historical availability contract violated"
            )

        if result["out_of_window"] != 0:
            errors.append(
                "out-of-window observation imported"
            )

        if result["bad_source"] != 0:
            errors.append(
                "unexpected source value"
            )

        if result["bad_timezone"] != 0:
            errors.append(
                "unexpected timezone value"
            )

        if result["bad_resolution"] != 0:
            errors.append(
                "unexpected hourly resolution semantics"
            )

        if errors:
            raise RuntimeError(
                "PRE-COMMIT AUDIT FAILED: "
                + " | ".join(errors)
            )

        conn.commit()

    except Exception:

        conn.rollback()

        raise

    finally:

        conn.close()

    # -------------------------------------------------------------------------
    # READ-ONLY POST BUILD AUDIT
    # -------------------------------------------------------------------------

    uri = (
        AUX_DB.resolve().as_uri()
        + "?mode=ro"
    )

    ro = sqlite3.connect(
        uri,
        uri=True,
    )

    try:
        result = audit(
            ro
        )

    finally:
        ro.close()

    after_main_sha = sha256_file(
        MAIN_DB
    )

    after_aux_sha = sha256_file(
        AUX_DB
    )

    print()
    print("=" * 88)
    print("POST-BUILD AUDIT")
    print("=" * 88)

    print(
        f"Integrity Check       : "
        f"{result['integrity']}"
    )

    print(
        f"Foreign Key Errors    : "
        f"{result['foreign_keys']}"
    )

    print(
        f"Solar Rows            : "
        f"{result['solar']}"
    )

    print(
        f"Station Snapshots     : "
        f"{result['station']}"
    )

    print(
        f"Raw Snapshots         : "
        f"{result['raw']}"
    )

    print(
        f"Parameter Rows        : "
        f"{result['hourly']}"
    )

    print(
        f"Distinct Source Hours : "
        f"{result['hours']}"
    )

    print(
        f"Missing Source Hours  : "
        f"{EXPECTED_TARGET_HOURS - result['hours']}"
    )

    print(
        f"Valid Observation     : "
        f"{result['observation_valid']}"
    )

    print(
        f"Valid Model           : "
        f"{result['model_valid']}"
    )

    print(
        f"Valid Unknown Source  : "
        f"{result['unknown_valid']}"
    )

    print(
        f"Availability UNKNOWN  : "
        f"{result['availability_unknown']}"
    )

    print(
        f"Illegal Availability  : "
        f"{result['availability_illegal']}"
    )

    print(
        f"Out-of-window Rows    : "
        f"{result['out_of_window']}"
    )

    print(
        f"Bad Source Rows       : "
        f"{result['bad_source']}"
    )

    print(
        f"Bad Timezone Rows     : "
        f"{result['bad_timezone']}"
    )

    print(
        f"Bad Resolution Rows   : "
        f"{result['bad_resolution']}"
    )

    print()
    print("PARAMETER SUMMARY")
    print("-" * 88)

    for row in result["parameter_counts"]:

        (
            parameter,
            total,
            observed,
            model,
            unknown,
            missing,
            unsupported,
            invalid,
        ) = row

        print(
            f"{parameter:5s} "
            f"total={total:6d} "
            f"obs={observed or 0:6d} "
            f"model={model or 0:6d} "
            f"unknown={unknown or 0:6d} "
            f"missing={missing or 0:6d} "
            f"unsupported={unsupported or 0:6d} "
            f"invalid={invalid or 0:6d}"
        )

    print()
    print("=" * 88)
    print("DATABASE PROTECTION")
    print("=" * 88)

    print(
        f"Main DB SHA Before    : "
        f"{before_main_sha}"
    )

    print(
        f"Main DB SHA After     : "
        f"{after_main_sha}"
    )

    print(
        f"Main DB SHA Match     : "
        f"{before_main_sha == after_main_sha}"
    )

    print(
        f"Aux DB SHA Before     : "
        f"{before_aux_sha}"
    )

    print(
        f"Aux DB SHA After      : "
        f"{after_aux_sha}"
    )

    if before_main_sha != after_main_sha:
        raise SystemExit(
            "RESULT: MAIN PRODUCTION DATABASE CHANGED"
        )

    if result["integrity"] != "ok":
        raise SystemExit(
            "RESULT: AUXILIARY DATABASE INTEGRITY FAILED"
        )

    if result["foreign_keys"] != 0:
        raise SystemExit(
            "RESULT: AUXILIARY DATABASE FOREIGN KEY FAILED"
        )

    if result["solar"] != EXPECTED_SOLAR_ROWS:
        raise SystemExit(
            "RESULT: SOLAR/TIME ARCHIVE CHANGED"
        )

    if result["availability_illegal"] != 0:
        raise SystemExit(
            "RESULT: AVAILABILITY CONTRACT FAILED"
        )

    if result["out_of_window"] != 0:
        raise SystemExit(
            "RESULT: TARGET WINDOW CONTRACT FAILED"
        )

    print()
    print(
        "RESULT: PHASE 3 METEOSTAT "
        "ARCHIVE BUILD COMPLETE"
    )

    print(
        "PRODUCTION DATABASE MODIFIED: NO"
    )

    print(
        "METEOSTAT ROLE: AUXILIARY_ONLY"
    )

    print(
        "HISTORICAL TRAINING ADMISSION: BLOCKED"
    )

    print(
        "REASON: HISTORICAL SOURCE "
        "AVAILABILITY UNKNOWN"
    )


if __name__ == "__main__":
    main()