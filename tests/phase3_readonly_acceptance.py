"""Run with python -B -m tests.phase3_readonly_acceptance from project root.

Reads the quiescent production file into memory, never opens it with SQLite.
The baseline was recorded before the Phase 3 source-only safety repair.
"""
import hashlib
import json
from pathlib import Path

from src.audit import ecmwf_issue_rule_final_audit as audit


BASELINE_DATABASE_SHA256 = "2e0149050ed11fe2d750b6f4798f7a51b313bf8f59bb671da5a076daeb124367"


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    path = audit.DB_PATH
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    check(before == BASELINE_DATABASE_SHA256, "Production database differs from pre-repair baseline")
    conn = audit.connect_ro()
    try:
        check([r[0] for r in conn.execute("PRAGMA integrity_check")] == ["ok"], "integrity_check failed")
        check(not conn.execute("PRAGMA foreign_key_check").fetchall(), "foreign_key_check failed")
        audit.validate_frozen_objects(conn)
        targets, archive, hourly = audit.load_targets(conn), audit.load_archive(conn), audit.load_hourly(conn)
        check(len(targets) == 729 and len(archive) == 3411, "Frozen counts changed")
        results = audit.audit_backtest(targets, archive, hourly)
        audit.validate_backtest_results(results)
        audit.audit_realtime_policy()
        unavailable = conn.execute("""SELECT canonical_raw_run_id FROM ecmwf_archive_v1
            WHERE canonical_status='SOURCE_TEMPERATURE_UNAVAILABLE'
            ORDER BY canonical_raw_run_id""").fetchall()
        check([r[0] for r in unavailable] == [1772, 1773, 1776, 1777, 1778, 1785, 3081],
              "Unavailable run identities changed")
        null_count = conn.execute("SELECT count(*) FROM ecmwf_hourly_forecasts WHERE temperature_2m_c IS NULL").fetchone()[0]
        check(null_count == 504, "Legal temperature NULL count changed")
        for row in unavailable:
            n, nulls = conn.execute("""SELECT count(*), sum(temperature_2m_c IS NULL)
                FROM ecmwf_hourly_forecasts WHERE raw_run_id=?""", (row[0],)).fetchone()
            check((n, nulls) == (72, 72), "Unavailable run source coverage changed")
        # Initialization on the real schema's read-only memory copy must be a no-op.
        from database.schema import ensure_schema
        ensure_schema(conn)
    finally:
        conn.close()
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    check(after == before == BASELINE_DATABASE_SHA256, "Production database changed during acceptance")
    check(not any(Path(str(path) + s).exists() for s in ("-wal", "-shm", "-journal")), "Unexpected DB sidecar")
    print(json.dumps({"integrity_check": "ok", "foreign_key_check": 0,
        "target": 729, "archive": 3411, "T0": "729/729", "T+1": "729/729", "T+2": "728/729",
        "gap_2025_08_07_T2": "14/24", "leakage": 0, "unavailable_runs": 7, "temperature_null": 504,
        "frozen_full_content_sha256": audit.EXPECTED_FROZEN_SHA,
        "database_sha256_before": before, "database_sha256_after": after,
        "result": "PASS"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
