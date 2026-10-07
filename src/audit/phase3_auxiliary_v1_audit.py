"""Phase 3 stage-1 read-only acceptance. No collection or production DDL."""
import hashlib
import sqlite3

from src.audit import ecmwf_issue_rule_final_audit as baseline
from src.auxiliary.schema import initialize_test_schema, TABLES
from src.auxiliary.solar.features import features

BASELINE_SHA256 = "2e0149050ed11fe2d750b6f4798f7a51b313bf8f59bb671da5a076daeb124367"


def main():
    before = hashlib.sha256(baseline.DB_PATH.read_bytes()).hexdigest()
    if before != BASELINE_SHA256:
        raise RuntimeError("Production database differs from frozen safety baseline")
    if baseline.main() != 0:
        return 1
    conn = baseline.connect_ro()
    try:
        if conn.execute("SELECT count(*) FROM ecmwf_hourly_forecasts WHERE temperature_2m_c IS NULL").fetchone()[0] != 504:
            raise RuntimeError("Legal temperature NULL count changed")
        ids = [r[0] for r in conn.execute("""SELECT canonical_raw_run_id FROM ecmwf_archive_v1
            WHERE canonical_status='SOURCE_TEMPERATURE_UNAVAILABLE' ORDER BY canonical_raw_run_id""")]
        if ids != [1772,1773,1776,1777,1778,1785,3081]:
            raise RuntimeError("Unavailable run identities changed")
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if set(TABLES) & existing:
            raise RuntimeError("Stage 1 must not install auxiliary tables in production")
    finally:
        conn.close()
    test = sqlite3.connect(":memory:")
    try:
        initialize_test_schema(test)
        row = features("2025-08-07T00:00:00Z", ingest_time="2026-10-01T00:00:00Z")
        test.execute("INSERT INTO aux_v1_solar_time (" + ",".join(row) + ") VALUES (" +
                     ",".join("?" for _ in row) + ")", tuple(row.values()))
        if test.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or test.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Auxiliary in-memory schema integrity failed")
    finally:
        test.close()
    after = hashlib.sha256(baseline.DB_PATH.read_bytes()).hexdigest()
    if before != after:
        raise RuntimeError("Production database changed during audit")
    print(f"PHASE3 STAGE 1 PASS; production SHA256 unchanged: {after}")
    print("Collection admission: archive-only; Meteostat historical training admission: BLOCKED (unknown availability)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
