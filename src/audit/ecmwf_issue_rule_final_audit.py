from __future__ import annotations

import sqlite3
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "zuuu_prediction.db"

TARGET_TABLE = "zuuu_target_v1"
ARCHIVE_TABLE = "ecmwf_archive_v1"
HOURLY_TABLE = "ecmwf_hourly_forecasts"

UTC = timezone.utc
BJT = ZoneInfo("Asia/Shanghai")

BACKTEST_ISSUE_HOUR_BJT = 21

EXPECTED_DAYS = 729


def parse_dt(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def connect_ro():
    # Read bytes rather than opening the production WAL database with SQLite:
    # even mode=ro can create/update a shared-memory sidecar. Fail closed if live.
    path = DB_PATH.resolve()
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    if any(p.exists() for p in sidecars):
        raise RuntimeError("Database has active sidecars; require a quiescent read-only snapshot")
    original = path.read_bytes()
    if original != path.read_bytes() or any(p.exists() for p in sidecars):
        raise RuntimeError("Database changed while taking the read-only snapshot")
    snapshot = bytearray(original)
    # Normalize WAL header ONLY in memory so SQLite deserialize can read it.
    snapshot[18:20] = b"\x01\x01"
    conn = sqlite3.connect(":memory:")
    conn.deserialize(bytes(snapshot))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def load_targets(conn):
    rows = conn.execute(
        f"""
        SELECT business_date_bjt
        FROM {TARGET_TABLE}
        WHERE target_status='FROZEN'
        ORDER BY business_date_bjt
        """
    ).fetchall()

    return [
        date.fromisoformat(r["business_date_bjt"])
        for r in rows
    ]


def load_archive(conn):
    rows = conn.execute(
        f"""
        SELECT *
        FROM {ARCHIVE_TABLE}
        WHERE archive_status='FROZEN'
        ORDER BY run_time_utc
        """
    ).fetchall()

    result = []

    for r in rows:
        x = dict(r)
        x["_run"] = parse_dt(r["run_time_utc"])
        x["_available"] = parse_dt(
            r["source_available_time_utc"]
        )
        result.append(x)

    return result


def load_hourly(conn):
    rows = conn.execute(
        f"""
        SELECT
            raw_run_id,
            target_time_utc,
            temperature_2m_c
        FROM {HOURLY_TABLE}
        """
    ).fetchall()

    result = {}

    for r in rows:
        raw_id = r["raw_run_id"]

        target = parse_dt(r["target_time_utc"])
        if target in result.setdefault(raw_id, {}):
            raise RuntimeError(f"Duplicate hourly target: {raw_id}, {target}")
        result[raw_id][target] = r["temperature_2m_c"]

    return result


def target_hours(target_date):
    start = datetime.combine(
        target_date,
        time(0, 0),
        tzinfo=BJT
    ).astimezone(UTC)

    return [
        start + timedelta(hours=i)
        for i in range(24)
    ]


def coverage(hourly, raw_id, target_date):
    data = hourly.get(raw_id, {})

    hours = target_hours(target_date)

    present = sum(
        h in data
        for h in hours
    )

    valid = sum(
        h in data and isinstance(data[h], (int, float))
        and not isinstance(data[h], bool) and math.isfinite(data[h])
        for h in hours
    )

    return present, valid


def issue_time_backtest(
    target_date,
    horizon
):
    issue_date = (
        target_date
        - timedelta(days=horizon)
    )

    return datetime.combine(
        issue_date,
        time(
            BACKTEST_ISSUE_HOUR_BJT,
            0
        ),
        tzinfo=BJT
    ).astimezone(UTC)


def choose_latest_complete(
    archive,
    hourly,
    target_date,
    cutoff
):
    candidates = []

    for run in archive:

        if run["canonical_status"] != "AVAILABLE":
            continue

        if run["_available"] > cutoff:
            continue

        candidates.append(run)

    candidates.sort(
        key=lambda x: (
            x["_run"]
        ),
        reverse=True
    )

    best_partial = None

    for run in candidates:

        raw_id = run["canonical_raw_run_id"]

        present, valid = coverage(
            hourly,
            raw_id,
            target_date
        )

        if present == 24 and valid == 24:
            return run, present, valid

        if best_partial is None:
            best_partial = (
                run,
                present,
                valid
            )

    return best_partial


def audit_backtest(
    targets,
    archive,
    hourly
):
    print()
    print("=" * 90)
    print("BACKTEST_ISSUE_RULE_V1")
    print("Fixed Snapshot: 21:00 BJT")
    print("=" * 90)

    results = {}

    for horizon in (0, 1, 2):

        complete = 0
        partial = 0
        no_run = 0
        leakage = 0

        cycles = Counter()
        failures = []

        for d in targets:

            cutoff = issue_time_backtest(
                d,
                horizon
            )

            selected = choose_latest_complete(
                archive,
                hourly,
                d,
                cutoff
            )

            if selected is None:
                no_run += 1
                failures.append(
                    (d, "NO_RUN", None, 0, 0)
                )
                continue

            run, present, valid = selected

            if run["_available"] > cutoff:
                leakage += 1

            cycles[
                f"{run['_run'].hour:02d}Z"
            ] += 1

            if present == 24 and valid == 24:
                complete += 1
            else:
                partial += 1

                failures.append(
                    (
                        d,
                        "PARTIAL",
                        run["run_time_utc"],
                        present,
                        valid
                    )
                )

        label = (
            "T0"
            if horizon == 0
            else f"T+{horizon}"
        )

        results[horizon] = {
            "complete": complete,
            "partial": partial,
            "no_run": no_run,
            "leakage": leakage,
            "failures": failures,
            "cycles": cycles
        }

        print()
        print(label)
        print(
            f"  Complete : "
            f"{complete}/{len(targets)}"
        )
        print(
            f"  Partial  : {partial}"
        )
        print(
            f"  No Run   : {no_run}"
        )
        print(
            f"  Leakage  : {leakage}"
        )
        print(
            f"  Cycles   : {dict(cycles)}"
        )

        if failures:
            print("  Failures:")

            for f in failures[:20]:
                print(
                    f"    {f[0]} | "
                    f"{f[1]} | "
                    f"run={f[2]} | "
                    f"{f[3]}/24 | "
                    f"temp={f[4]}/24"
                )

    return results


EXPECTED_RULE_SHA = "7fdbf14f0f105870f06e9e4d29753718d8cfebfe98d7608c9f916ea441455504"
# Full frozen rows, including provenance and frozen_at, pinned at safety repair.
EXPECTED_FROZEN_SHA = {
    "zuuu_target_v1": "0fa17d685ebd77687bbf193727775d0ae530263963eeac05c6ac7e6a886e5744",
    "ecmwf_archive_v1": "b14a60c2cb6bbb399d4ee5f46b4565c4843c9fd7e9321166c38807935802298a",
    "ecmwf_issue_rule_v1": "f9a984cf4ebe733803148c6beaafee6ec0d2dfc5331aa3bf6f84a8f3af7c9ff7",
}


def validate_frozen_objects(conn):
    for table, expected in EXPECTED_FROZEN_SHA.items():
        rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        actual = hashlib.sha256(json.dumps(
            rows, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Frozen content changed: {table}, SHA256={actual}")
    row = conn.execute("SELECT * FROM ecmwf_issue_rule_v1").fetchone()
    payload = row["rule_payload_json"]
    canonical = json.dumps(json.loads(payload), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    actual = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if payload != canonical or actual != EXPECTED_RULE_SHA or row["semantic_sha256"] != actual:
        raise RuntimeError("Frozen Issue payload / SHA mismatch")


def validate_backtest_results(results):
    for h, expected in ((0, 729), (1, 729), (2, 728)):
        r = results[h]
        if (r["complete"], r["partial"], r["no_run"], r["leakage"]) != (
                expected, int(h == 2), 0, 0):
            raise RuntimeError(f"Unexpected T+{h} coverage or leakage")
        if h < 2 and r["failures"]:
            raise RuntimeError(f"Unexpected T+{h} failures")
    failures = results[2]["failures"]
    if len(failures) != 1:
        raise RuntimeError("Expected exactly one T+2 gap")
    d, status, run_time, present, valid = failures[0]
    if ((d, status, present, valid) != (date(2025, 8, 7), "PARTIAL", 14, 14)
            or parse_dt(run_time) != datetime(2025, 8, 4, 6, tzinfo=UTC)):
        raise RuntimeError("T+2 gap signature changed; never fill or train on this gap")


def audit_realtime_policy():
    """Executable synthetic NOW boundary checks; no data/network writes."""
    day = date(2026, 10, 1)
    cutoff = datetime(2026, 10, 1, 13, tzinfo=UTC)
    def run(key, age, delay):
        rt = cutoff - timedelta(hours=age)
        return {"canonical_status": "AVAILABLE", "canonical_raw_run_id": key,
                "run_time_utc": rt.isoformat(), "_run": rt,
                "_available": cutoff + timedelta(seconds=delay)}
    old, new = run(1, 12, 0), run(2, 6, -1)
    hourly = {key: dict.fromkeys(target_hours(day), 20.0) for key in (1, 2)}
    def require(condition, message):
        if not condition:
            raise RuntimeError("Realtime boundary failed: " + message)
    selected = choose_latest_complete([old, new], hourly, day, cutoff)
    require(selected[0] is new, "run_time priority over arrival order")
    new["_available"] = cutoff + timedelta(microseconds=1)
    require(choose_latest_complete([old, new], hourly, day, cutoff)[0] is old,
            "future availability excluded / equality accepted")
    require(choose_latest_complete([new], hourly, day, cutoff) is None, "no legal run")
    new["_available"] = cutoff
    require(choose_latest_complete([old, new], hourly, day, cutoff)[0] is new, "arrival at NOW")
    hourly[2][target_hours(day)[0]] = None
    require(choose_latest_complete([old, new], hourly, day, cutoff)[0] is old, "fallback on null")
    partial = choose_latest_complete([new], hourly, day, cutoff)
    require(partial[1:] == (24, 23), "partial remains partial")
    new["canonical_status"] = "SOURCE_TEMPERATURE_UNAVAILABLE"
    require(choose_latest_complete([new], hourly, day, cutoff) is None, "unavailable run")
    new["canonical_status"] = "AVAILABLE"
    hourly[2].update(dict.fromkeys(target_hours(day + timedelta(days=1)), 21.0))
    require(choose_latest_complete([old, new], hourly, day + timedelta(days=1), cutoff)[0] is new,
            "horizons selected independently")
    print("REALTIME BOUNDARY AUDIT: PASS")


def main():
    conn = None
    try:
        conn = connect_ro()
        validate_frozen_objects(conn)
        if [r[0] for r in conn.execute("PRAGMA integrity_check")] != ["ok"]:
            raise RuntimeError("SQLite integrity_check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("SQLite foreign_key_check failed")
        targets, archive, hourly = load_targets(conn), load_archive(conn), load_hourly(conn)
        if len(targets) != 729 or len(archive) != 3411:
            raise RuntimeError("Frozen Target / Archive count mismatch")
        results = audit_backtest(targets, archive, hourly)
        validate_backtest_results(results)
        audit_realtime_policy()
        print("RESULT: ISSUE RULE FINAL AUDIT PASS; frozen payload/SHA verified; no writes")
        return 0
    except Exception as exc:
        print(f"RESULT: ISSUE RULE FINAL AUDIT FAILED: {exc}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
