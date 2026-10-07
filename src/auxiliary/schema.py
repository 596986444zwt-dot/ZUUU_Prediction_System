"""Isolated PHASE3_AUXILIARY_V1 DDL. No production database initializer.

Future storage target: database/phase3_auxiliary_v1.db (not created in stage 1).
This module can materialize the design ONLY in an empty in-memory test database.
"""
DDL = """
CREATE TABLE aux_v1_station_snapshot (
 id INTEGER PRIMARY KEY,
 station_id TEXT NOT NULL,
 name TEXT NOT NULL,
 latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
 longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
 elevation_m REAL,
 timezone TEXT NOT NULL,
 identifiers_json TEXT NOT NULL CHECK(json_valid(identifiers_json)),
 source TEXT NOT NULL CHECK(source='Meteostat'),
 source_version TEXT NOT NULL,
 ingest_time TEXT NOT NULL,
 metadata_sha256 TEXT NOT NULL CHECK(length(metadata_sha256)=64),
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 UNIQUE(station_id, metadata_sha256, ingest_time)
);
CREATE TABLE aux_v1_raw_snapshot (
 id INTEGER PRIMARY KEY,
 station_snapshot_id INTEGER NOT NULL REFERENCES aux_v1_station_snapshot(id),
 request_url TEXT NOT NULL,
 requested_year INTEGER NOT NULL,
 source TEXT NOT NULL CHECK(source='Meteostat'),
 source_version TEXT NOT NULL,
 source_available_time TEXT,
 availability_basis TEXT NOT NULL CHECK(availability_basis IN ('UNKNOWN','CAPTURED_SNAPSHOT')),
 ingest_time TEXT NOT NULL,
 timezone TEXT NOT NULL CHECK(timezone='UTC'),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 http_etag TEXT,
 http_last_modified TEXT,
 http_status INTEGER NOT NULL CHECK(http_status=200),
 response_blob BLOB NOT NULL,
 compression TEXT NOT NULL CHECK(compression='gzip'),
 role TEXT NOT NULL DEFAULT 'AUXILIARY_ONLY' CHECK(role='AUXILIARY_ONLY'),
 CHECK((availability_basis='UNKNOWN' AND source_available_time IS NULL) OR
       (availability_basis='CAPTURED_SNAPSHOT' AND source_available_time IS NOT NULL AND source_available_time=ingest_time)),
 UNIQUE(request_url, content_sha256, ingest_time)
);
CREATE TABLE aux_v1_meteostat_hourly (
 snapshot_id INTEGER NOT NULL REFERENCES aux_v1_raw_snapshot(id),
 observation_time TEXT NOT NULL,
 target_time TEXT CHECK(target_time IS NULL),
 parameter TEXT NOT NULL CHECK(parameter IN
   ('temp','dwpt','rhum','prcp','snow','snwd','wdir','wspd','wpgt','pres','tsun','cldc','coco')),
 value REAL,
 raw_value TEXT NOT NULL,
 unit TEXT NOT NULL,
 provider TEXT,
 value_kind TEXT NOT NULL CHECK(value_kind IN ('OBSERVATION','MODEL','UNKNOWN')),
 quality TEXT NOT NULL CHECK(quality IN ('VALID','MISSING','UNSUPPORTED','INVALID')),
 source TEXT NOT NULL CHECK(source='Meteostat'),
 source_version TEXT NOT NULL,
 parser_version TEXT NOT NULL,
 source_available_time TEXT,
 availability_basis TEXT NOT NULL CHECK(availability_basis IN ('UNKNOWN','CAPTURED_SNAPSHOT')),
 evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
 ingest_time TEXT NOT NULL,
 timezone TEXT NOT NULL CHECK(timezone='UTC'),
 resolution_seconds INTEGER NOT NULL CHECK(resolution_seconds=3600),
 interval_semantics TEXT NOT NULL CHECK(interval_semantics='SOURCE_HOUR_LABEL'),
 CHECK(quality!='VALID' OR value IS NOT NULL),
 CHECK(quality!='VALID' OR value BETWEEN -1e308 AND 1e308),
 CHECK(value_kind!='OBSERVATION' OR (provider IS NOT NULL AND provider IN ('metar','isd_lite'))),
 CHECK(quality NOT IN ('MISSING','UNSUPPORTED') OR value IS NULL),
 CHECK((availability_basis='UNKNOWN' AND source_available_time IS NULL) OR
       (availability_basis='CAPTURED_SNAPSHOT' AND source_available_time IS NOT NULL AND source_available_time=ingest_time)),
 PRIMARY KEY(snapshot_id, observation_time, parameter)
);
CREATE INDEX aux_v1_hourly_time ON aux_v1_meteostat_hourly(observation_time,parameter);
CREATE TABLE aux_v1_solar_time (
 target_time TEXT NOT NULL,
 observation_time TEXT CHECK(observation_time IS NULL),
 source_available_time TEXT CHECK(source_available_time IS NULL),
 availability_basis TEXT NOT NULL CHECK(availability_basis='DETERMINISTIC_NOT_APPLICABLE'),
 ingest_time TEXT NOT NULL,
 timezone TEXT NOT NULL CHECK(timezone='Asia/Shanghai'),
 source TEXT NOT NULL CHECK(source='NOAA_SOLAR_EQUATIONS'),
 source_version TEXT NOT NULL CHECK(source_version='NOAA_FRACTIONAL_YEAR_ZUUU_V1'),
 latitude REAL NOT NULL CHECK(latitude=30.576),
 longitude REAL NOT NULL CHECK(longitude=103.950),
 coordinate_version TEXT NOT NULL CHECK(coordinate_version='ZUUU_CONFIG_COORDINATES_V1'),
 business_date_bjt TEXT NOT NULL,
 hour_bjt INTEGER NOT NULL CHECK(hour_bjt BETWEEN 0 AND 23),
 day_of_year INTEGER NOT NULL CHECK(day_of_year BETWEEN 1 AND 366),
 month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
 season TEXT NOT NULL CHECK(season IN ('spring','summer','autumn','winter')),
 sin_hour REAL NOT NULL CHECK(sin_hour BETWEEN -1 AND 1),
 cos_hour REAL NOT NULL CHECK(cos_hour BETWEEN -1 AND 1),
 sin_doy REAL NOT NULL CHECK(sin_doy BETWEEN -1 AND 1),
 cos_doy REAL NOT NULL CHECK(cos_doy BETWEEN -1 AND 1),
 sunrise TEXT NOT NULL,
 sunset TEXT NOT NULL,
 solar_elevation REAL NOT NULL CHECK(solar_elevation BETWEEN -90 AND 90),
 solar_azimuth REAL NOT NULL CHECK(solar_azimuth>=0 AND solar_azimuth<360),
 daylight_duration REAL NOT NULL CHECK(daylight_duration BETWEEN 0 AND 1440),
 minutes_since_sunrise REAL NOT NULL,
 minutes_to_sunset REAL NOT NULL,
 PRIMARY KEY(target_time, source_version, coordinate_version)
);
"""

TABLES = ("aux_v1_station_snapshot", "aux_v1_raw_snapshot", "aux_v1_meteostat_hourly", "aux_v1_solar_time")


def initialize_test_schema(conn):
    databases = conn.execute("PRAGMA database_list").fetchall()
    if any(row[2] or row[1] not in ("main", "temp") for row in databases):
        raise RuntimeError("Stage 1 schema materialization is restricted to an in-memory database")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone():
        raise RuntimeError("An empty independent database is required")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA recursive_triggers=ON")
    conn.executescript("BEGIN;\n" + DDL + "\nCOMMIT;")
    conn.execute("""CREATE TRIGGER aux_v1_hourly_provenance BEFORE INSERT ON aux_v1_meteostat_hourly
        WHEN NOT EXISTS (SELECT 1 FROM aux_v1_raw_snapshot s WHERE s.id=NEW.snapshot_id
            AND s.content_sha256=NEW.evidence_sha256 AND s.source_version=NEW.source_version
            AND s.ingest_time=NEW.ingest_time AND s.availability_basis=NEW.availability_basis
            AND s.source_available_time IS NEW.source_available_time)
        BEGIN SELECT RAISE(ABORT, 'Snapshot provenance mismatch'); END""")
    for table in TABLES:
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {table}_block_{action.lower()} BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT, 'Auxiliary snapshots are append-only'); END""")
