from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.auxiliary.schema import DDL
from src.auxiliary.solar.features import features


PROJECT_ROOT = Path(r"C:\ZUUU_Prediction_System")

MAIN_DB = PROJECT_ROOT / "database" / "zuuu_prediction.db"
AUX_DB = PROJECT_ROOT / "database" / "phase3_auxiliary_v1.db"

START_DATE = date(2024, 9, 3)
END_DATE = date(2026, 9, 1)

EXPECTED_DAYS = 729
EXPECTED_ROWS = EXPECTED_DAYS * 24

BJT = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def build_expected_rows(ingest_time: datetime):
    rows = []

    current_date = START_DATE

    while current_date <= END_DATE:
        for hour in range(24):
            local_time = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                hour,
                0,
                0,
                tzinfo=BJT,
            )

            target_utc = local_time.astimezone(UTC)

            row = features(
                target_utc,
                ingest_time=ingest_time,
            )

            rows.append(row)

        current_date += timedelta(days=1)

    return rows


def validate_generated_rows(rows):
    errors = []

    if len(rows) != EXPECTED_ROWS:
        errors.append(
            f"ROW_COUNT expected={EXPECTED_ROWS} actual={len(rows)}"
        )

    dates = {}
    target_times = set()

    for row in rows:
        business_date = row["business_date_bjt"]

        dates.setdefault(
            business_date,
            [],
        ).append(row)

        target_time = row["target_time"]

        if target_time in target_times:
            errors.append(
                f"DUPLICATE_TARGET_TIME:{target_time}"
            )

        target_times.add(target_time)

        if row["timezone"] != "Asia/Shanghai":
            errors.append(
                f"BAD_TIMEZONE:{target_time}"
            )

        if row["source"] != "NOAA_SOLAR_EQUATIONS":
            errors.append(
                f"BAD_SOURCE:{target_time}"
            )

        if row["source_version"] != "NOAA_FRACTIONAL_YEAR_ZUUU_V1":
            errors.append(
                f"BAD_SOURCE_VERSION:{target_time}"
            )

        if row["coordinate_version"] != "ZUUU_CONFIG_COORDINATES_V1":
            errors.append(
                f"BAD_COORDINATE_VERSION:{target_time}"
            )

        if row["source_available_time"] is not None:
            errors.append(
                f"UNEXPECTED_AVAILABLE_TIME:{target_time}"
            )

        if row["observation_time"] is not None:
            errors.append(
                f"UNEXPECTED_OBSERVATION_TIME:{target_time}"
            )

        if row["availability_basis"] != "DETERMINISTIC_NOT_APPLICABLE":
            errors.append(
                f"BAD_AVAILABILITY_BASIS:{target_time}"
            )

    if len(dates) != EXPECTED_DAYS:
        errors.append(
            f"DAY_COUNT expected={EXPECTED_DAYS} actual={len(dates)}"
        )

    expected_date = START_DATE

    while expected_date <= END_DATE:
        key = expected_date.isoformat()

        day_rows = dates.get(
            key,
            [],
        )

        if len(day_rows) != 24:
            errors.append(
                f"DAY_NOT_24_HOURS:{key}:{len(day_rows)}"
            )

        hours = sorted(
            row["hour_bjt"]
            for row in day_rows
        )

        if hours != list(range(24)):
            errors.append(
                f"BAD_HOURS:{key}:{hours}"
            )

        expected_date += timedelta(days=1)

    return errors


def create_aux_database(
    conn: sqlite3.Connection,
):
    existing = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
          AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()

    if existing:
        raise RuntimeError(
            "Auxiliary database is not empty; refusing initialization"
        )

    conn.execute(
        "PRAGMA foreign_keys=ON"
    )

    conn.execute(
        "PRAGMA recursive_triggers=ON"
    )

    conn.executescript(DDL)

    conn.execute(
        """
        CREATE TRIGGER aux_v1_hourly_provenance
        BEFORE INSERT ON aux_v1_meteostat_hourly
        WHEN NOT EXISTS (
            SELECT 1
            FROM aux_v1_raw_snapshot s
            WHERE s.id = NEW.snapshot_id
              AND s.content_sha256 = NEW.evidence_sha256
              AND s.source_version = NEW.source_version
              AND s.ingest_time = NEW.ingest_time
              AND s.availability_basis = NEW.availability_basis
              AND s.source_available_time IS NEW.source_available_time
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'Snapshot provenance mismatch'
            );
        END
        """
    )

    tables = (
        "aux_v1_station_snapshot",
        "aux_v1_raw_snapshot",
        "aux_v1_meteostat_hourly",
        "aux_v1_solar_time",
    )

    for table in tables:
        conn.execute(
            f"""
            CREATE TRIGGER {table}_block_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'Auxiliary snapshots are append-only'
                );
            END
            """
        )

        conn.execute(
            f"""
            CREATE TRIGGER {table}_block_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'Auxiliary snapshots are append-only'
                );
            END
            """
        )


def insert_solar_rows(
    conn: sqlite3.Connection,
    rows,
):
    sql = """
    INSERT INTO aux_v1_solar_time (
        target_time,
        observation_time,
        source_available_time,
        availability_basis,
        ingest_time,
        timezone,
        source,
        source_version,
        latitude,
        longitude,
        coordinate_version,
        business_date_bjt,
        hour_bjt,
        day_of_year,
        month,
        season,
        sin_hour,
        cos_hour,
        sin_doy,
        cos_doy,
        sunrise,
        sunset,
        solar_elevation,
        solar_azimuth,
        daylight_duration,
        minutes_since_sunrise,
        minutes_to_sunset
    )
    VALUES (
        ?,?,?,?,?,?,?,?,?,?,
        ?,?,?,?,?,?,?,?,?,?,
        ?,?,?,?,?,?,?
    )
    """

    payload = []

    for r in rows:
        payload.append(
            (
                r["target_time"],
                r["observation_time"],
                r["source_available_time"],
                r["availability_basis"],
                r["ingest_time"],
                r["timezone"],
                r["source"],
                r["source_version"],
                r["latitude"],
                r["longitude"],
                r["coordinate_version"],
                r["business_date_bjt"],
                r["hour_bjt"],
                r["day_of_year"],
                r["month"],
                r["season"],
                r["sin_hour"],
                r["cos_hour"],
                r["sin_doy"],
                r["cos_doy"],
                r["sunrise"],
                r["sunset"],
                r["solar_elevation"],
                r["solar_azimuth"],
                r["daylight_duration"],
                r["minutes_since_sunrise"],
                r["minutes_to_sunset"],
            )
        )

    conn.executemany(
        sql,
        payload,
    )


def audit_database(
    conn: sqlite3.Connection,
):
    errors = []

    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if integrity != "ok":
        errors.append(
            f"INTEGRITY_CHECK:{integrity}"
        )

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if fk_errors:
        errors.append(
            f"FOREIGN_KEY_ERRORS:{len(fk_errors)}"
        )

    count = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_solar_time
        """
    ).fetchone()[0]

    if count != EXPECTED_ROWS:
        errors.append(
            f"ROW_COUNT:{count}"
        )

    day_count = conn.execute(
        """
        SELECT COUNT(DISTINCT business_date_bjt)
        FROM aux_v1_solar_time
        """
    ).fetchone()[0]

    if day_count != EXPECTED_DAYS:
        errors.append(
            f"DAY_COUNT:{day_count}"
        )

    bad_days = conn.execute(
        """
        SELECT
            business_date_bjt,
            COUNT(*) AS n,
            COUNT(DISTINCT hour_bjt) AS h
        FROM aux_v1_solar_time
        GROUP BY business_date_bjt
        HAVING n != 24 OR h != 24
        """
    ).fetchall()

    if bad_days:
        errors.append(
            f"BAD_DAY_COVERAGE:{len(bad_days)}"
        )

    first_row = conn.execute(
        """
        SELECT
            business_date_bjt,
            hour_bjt,
            target_time
        FROM aux_v1_solar_time
        ORDER BY business_date_bjt, hour_bjt
        LIMIT 1
        """
    ).fetchone()

    last_row = conn.execute(
        """
        SELECT
            business_date_bjt,
            hour_bjt,
            target_time
        FROM aux_v1_solar_time
        ORDER BY business_date_bjt DESC, hour_bjt DESC
        LIMIT 1
        """
    ).fetchone()

    null_required = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_solar_time
        WHERE
            target_time IS NULL
            OR ingest_time IS NULL
            OR business_date_bjt IS NULL
            OR solar_elevation IS NULL
            OR solar_azimuth IS NULL
            OR sunrise IS NULL
            OR sunset IS NULL
        """
    ).fetchone()[0]

    if null_required:
        errors.append(
            f"NULL_REQUIRED_FIELDS:{null_required}"
        )

    wrong_contract = conn.execute(
        """
        SELECT COUNT(*)
        FROM aux_v1_solar_time
        WHERE
            source != 'NOAA_SOLAR_EQUATIONS'
            OR source_version != 'NOAA_FRACTIONAL_YEAR_ZUUU_V1'
            OR coordinate_version != 'ZUUU_CONFIG_COORDINATES_V1'
            OR availability_basis != 'DETERMINISTIC_NOT_APPLICABLE'
            OR observation_time IS NOT NULL
            OR source_available_time IS NOT NULL
        """
    ).fetchone()[0]

    if wrong_contract:
        errors.append(
            f"CONTRACT_ERRORS:{wrong_contract}"
        )

    return {
        "errors": errors,
        "integrity": integrity,
        "fk_errors": len(fk_errors),
        "rows": count,
        "days": day_count,
        "first": first_row,
        "last": last_row,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--commit",
        action="store_true",
    )

    args = parser.parse_args()

    if args.dry_run == args.commit:
        raise SystemExit(
            "Use exactly one: --dry-run OR --commit"
        )

    if not MAIN_DB.exists():
        raise SystemExit(
            f"Production database not found: {MAIN_DB}"
        )

    production_sha_before = sha256_file(
        MAIN_DB
    )

    ingest_time = datetime.now(UTC)

    rows = build_expected_rows(
        ingest_time
    )

    generation_errors = validate_generated_rows(
        rows
    )

    print("=" * 90)
    print("PHASE 3 SOLAR / TIME BUILDER")
    print("=" * 90)

    print(
        f"Production DB       : {MAIN_DB}"
    )
    print(
        f"Auxiliary DB        : {AUX_DB}"
    )
    print(
        f"Start Date BJT      : {START_DATE}"
    )
    print(
        f"End Date BJT        : {END_DATE}"
    )
    print(
        f"Expected Days       : {EXPECTED_DAYS}"
    )
    print(
        f"Expected Rows       : {EXPECTED_ROWS}"
    )
    print(
        f"Generated Rows      : {len(rows)}"
    )
    print(
        "Algorithm           : "
        "NOAA_FRACTIONAL_YEAR_ZUUU_V1"
    )
    print(
        "Coordinates         : "
        "30.576 N, 103.950 E"
    )
    print(
        "Availability        : "
        "DETERMINISTIC_NOT_APPLICABLE"
    )
    print(
        f"Generation Errors   : "
        f"{len(generation_errors)}"
    )

    if generation_errors:
        for error in generation_errors[:20]:
            print(
                "  ",
                error,
            )

        raise SystemExit(
            "RESULT: GENERATION AUDIT FAILED"
        )

    if args.dry_run:
        production_sha_after = sha256_file(
            MAIN_DB
        )

        print()
        print(
            "Database Modification : NONE"
        )
        print(
            "Production SHA Before : "
            + production_sha_before
        )
        print(
            "Production SHA After  : "
            + production_sha_after
        )
        print(
            "Production SHA Match  : "
            + str(
                production_sha_before
                == production_sha_after
            )
        )

        if production_sha_before != production_sha_after:
            raise SystemExit(
                "PRODUCTION DATABASE CHANGED"
            )

        print()
        print(
            "RESULT: PHASE 3 SOLAR/TIME "
            "DRY-RUN PASS"
        )
        print(
            "READY FOR --commit"
        )

        return

    # COMMIT MODE
    if AUX_DB.exists():
        raise SystemExit(
            f"""
REFUSING TO CONTINUE.

Auxiliary database already exists:

{AUX_DB}

No overwrite, delete or rebuild is allowed.
"""
        )

    AUX_DB.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = None

    try:
        conn = sqlite3.connect(
            str(AUX_DB)
        )

        conn.execute(
            "PRAGMA foreign_keys=ON"
        )

        create_aux_database(
            conn
        )

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        insert_solar_rows(
            conn,
            rows,
        )

        result = audit_database(
            conn
        )

        if result["errors"]:
            raise RuntimeError(
                " | ".join(
                    result["errors"]
                )
            )

        conn.commit()

    except Exception:
        if conn is not None:
            conn.rollback()
            conn.close()
            conn = None

        # AUX_DB is a new Phase 3 database only.
        # Remove incomplete construction on failure.
        if AUX_DB.exists():
            AUX_DB.unlink()

        raise

    finally:
        if conn is not None:
            conn.close()

    # Read-only post-build audit.
    uri = AUX_DB.resolve().as_uri() + "?mode=ro"

    ro = sqlite3.connect(
        uri,
        uri=True,
    )

    try:
        result = audit_database(
            ro
        )
    finally:
        ro.close()

    production_sha_after = sha256_file(
        MAIN_DB
    )

    aux_sha = sha256_file(
        AUX_DB
    )

    print()
    print("=" * 90)
    print("POST-BUILD AUDIT")
    print("=" * 90)

    print(
        f"Rows                  : "
        f"{result['rows']}"
    )
    print(
        f"Days                  : "
        f"{result['days']}"
    )
    print(
        f"Integrity Check       : "
        f"{result['integrity']}"
    )
    print(
        f"Foreign Key Errors    : "
        f"{result['fk_errors']}"
    )
    print(
        f"First Row             : "
        f"{result['first']}"
    )
    print(
        f"Last Row              : "
        f"{result['last']}"
    )
    print(
        f"Post-Build Errors     : "
        f"{len(result['errors'])}"
    )

    print()
    print(
        "Production SHA Before : "
        + production_sha_before
    )
    print(
        "Production SHA After  : "
        + production_sha_after
    )
    print(
        "Production SHA Match  : "
        + str(
            production_sha_before
            == production_sha_after
        )
    )

    print(
        "Auxiliary DB SHA256   : "
        + aux_sha
    )

    if result["errors"]:
        raise SystemExit(
            "RESULT: POST-BUILD AUDIT FAILED"
        )

    if production_sha_before != production_sha_after:
        raise SystemExit(
            "RESULT: PRODUCTION DATABASE CHANGED"
        )

    print()
    print(
        "RESULT: PHASE 3 SOLAR/TIME BUILD COMPLETE"
    )
    print(
        "PRODUCTION DATABASE MODIFIED: NO"
    )


if __name__ == "__main__":
    main()