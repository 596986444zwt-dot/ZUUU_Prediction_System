"""Phase 3 safety regressions. All database mutations use :memory: only."""
import copy
import csv
import io
import hashlib
import json
import sqlite3
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from database.schema import ensure_schema
from src.ecmwf_contract import EXPECTED_UNITS, HOURLY_VARIABLES
from src.parsers import ecmwf_silver_processor as old
from src.parsers import ecmwf_silver_processor_v3 as current
from src.audit import ecmwf_issue_rule_final_audit as audit
from src.audit import ecmwf_issue_mapping_rule_scanner as scanner
from src.collectors.zuuu_historical_raw_importer import parse_iem_response
from src.database.zuuu_silver_archive import ZUUUSilverArchive


def raw_fixture():
    run = datetime(2025, 8, 1, tzinfo=timezone.utc)
    data = {
        "utc_offset_seconds": 0, "hourly_units": dict(EXPECTED_UNITS),
        "hourly": {f: [10.0] * 72 for f in HOURLY_VARIABLES},
    }
    data["hourly"]["time"] = [(run + timedelta(hours=h)).isoformat() for h in range(72)]
    return dict(id=1, model="IFS_HRES", model_cycle="49r1_hindcast",
                run_time_utc=run.isoformat(), source_available_time_utc=(run + timedelta(hours=6)).isoformat(),
                availability_type="official_schedule_estimate", ingest_time_utc="2026-09-30T00:00:00+00:00",
                raw_json=json.dumps(data))


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    raw = raw_fixture()
    stored = dict(raw, source="test", bronze_file_path="memory-only", content_sha256="test",
                  created_at_utc=raw["ingest_time_utc"])
    conn.execute("INSERT INTO ecmwf_raw_runs (" + ",".join(stored) + ") VALUES (" +
                 ",".join("?" for _ in stored) + ")", tuple(stored.values()))
    conn.commit()
    return conn, raw


class ProcessorSafety(unittest.TestCase):
    def test_frozen_run_blocked_even_with_no_hourly_rows(self):
        for processor in (old, current):
            with self.subTest(processor=processor.__name__):
                conn, raw = memory_db()
                self.addCleanup(conn.close)
                conn.execute("CREATE TABLE ecmwf_archive_v1 (canonical_raw_run_id INTEGER)")
                conn.execute("INSERT INTO ecmwf_archive_v1 VALUES (1)")
                conn.commit()
                before = conn.serialize()
                with self.assertRaisesRegex(RuntimeError, "Frozen"):
                    processor.process_raw_run(conn, raw)
                self.assertEqual(conn.serialize(), before)
                self.assertEqual(list(old.iter_pending_raw_runs(conn)), [])

    def test_existing_legacy_or_partial_rows_never_rebuilt(self):
        for processor in (old, current):
            for count in (1, 72):
                with self.subTest(processor=processor.__name__, count=count):
                    conn, raw = memory_db()
                    self.addCleanup(conn.close)
                    old.process_raw_run(conn, raw)
                    conn.execute("UPDATE ecmwf_hourly_forecasts SET processor_version='V2'")
                    conn.execute("DELETE FROM ecmwf_hourly_forecasts WHERE lead_hours>=?", (count,))
                    conn.commit()
                    before = conn.serialize()
                    with self.assertRaisesRegex(RuntimeError, "Existing"):
                        processor.process_raw_run(conn, raw)
                    self.assertEqual(conn.serialize(), before)
                    self.assertEqual(list(old.iter_pending_raw_runs(conn)), [])

    def test_shared_contract_rejects_bad_new_data_without_writes(self):
        for processor in (old, current):
            for defect in ("unit", "nan", "inf", "bool", "string", "length", "time", "offset", "missing", "object"):
                with self.subTest(processor=processor.__name__, defect=defect):
                    conn, raw = memory_db()
                    self.addCleanup(conn.close)
                    payload = json.loads(raw["raw_json"])
                    if defect == "unit": payload["hourly_units"]["wind_speed_10m"] = "m/s"
                    elif defect in ("nan", "inf", "bool", "string"):
                        payload["hourly"]["temperature_2m"][0] = {
                            "nan": float("nan"), "inf": float("inf"), "bool": True, "string": "10"}[defect]
                    elif defect == "length": payload["hourly"]["rain"].pop()
                    elif defect == "time": payload["hourly"]["time"][1] = payload["hourly"]["time"][0]
                    elif defect == "offset": payload["utc_offset_seconds"] = 28800
                    elif defect == "missing": del payload["hourly"]["cape"]
                    elif defect == "object": payload = []
                    raw["raw_json"] = json.dumps(payload)
                    before = conn.serialize()
                    with self.assertRaises(ValueError): processor.process_raw_run(conn, raw)
                    self.assertEqual(conn.serialize(), before)

    def test_new_nulls_retained_and_qc_not_ok(self):
        for processor in (old, current):
            for all_null in (False, True):
                with self.subTest(processor=processor.__name__, all_null=all_null):
                    conn, raw = memory_db()
                    self.addCleanup(conn.close)
                    payload = json.loads(raw["raw_json"])
                    for field in (HOURLY_VARIABLES if all_null else ["temperature_2m"]):
                        payload["hourly"][field] = [None] * 72
                    raw["raw_json"] = json.dumps(payload)
                    processor.process_raw_run(conn, raw)
                    rows = conn.execute("SELECT * FROM ecmwf_hourly_forecasts ORDER BY lead_hours").fetchall()
                    self.assertEqual(len(rows), 72)
                    self.assertTrue(all(r["temperature_2m_c"] is None for r in rows))
                    self.assertEqual({r["qc_status"] for r in rows}, {"ERROR" if all_null else "WARNING"})
                    self.assertEqual({r["availability_type"] for r in rows}, {"official_schedule_estimate"})
                    self.assertEqual({r["ingest_time_utc"] for r in rows}, {raw["ingest_time_utc"]})

    def test_insert_failure_rolls_back_whole_run(self):
        for processor in (old, current):
            conn, raw = memory_db()
            self.addCleanup(conn.close)
            conn.execute("""CREATE TRIGGER fail_mid_run BEFORE INSERT ON ecmwf_hourly_forecasts
                WHEN NEW.lead_hours=35 BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
            conn.commit()
            before = conn.serialize()
            with self.assertRaises(sqlite3.IntegrityError): processor.process_raw_run(conn, raw)
            self.assertEqual(conn.serialize(), before)


class SchemaSafety(unittest.TestCase):
    def test_existing_schema_does_not_reconcile_or_write(self):
        conn, raw = memory_db()
        self.addCleanup(conn.close)
        old.process_raw_run(conn, raw)
        conn.execute("UPDATE ecmwf_raw_runs SET model_cycle='legacy', api_model=NULL")
        conn.commit()
        before = conn.serialize()
        conn.execute("PRAGMA query_only=ON")
        ensure_schema(conn)
        self.assertEqual(before, conn.serialize())

    def test_incomplete_schema_fails_without_migration(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE ecmwf_raw_runs (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO ecmwf_raw_runs VALUES (1)")
        conn.commit()
        before = conn.serialize()
        with self.assertRaisesRegex(RuntimeError, "migration"):
            ensure_schema(conn)
        self.assertEqual(before, conn.serialize())


class IssueSafety(unittest.TestCase):
    def test_realtime_boundaries(self):
        audit.audit_realtime_policy()

    def test_scanner_orders_run_not_availability(self):
        cutoff = datetime(2026, 10, 1, 13, tzinfo=timezone.utc)
        runs = [dict(canonical_status="AVAILABLE", _run_dt=cutoff-timedelta(hours=age),
                     _available_dt=cutoff+timedelta(seconds=delay))
                for age, delay in ((12, 0), (6, -1), (0, 1))]
        self.assertEqual(scanner.get_available_candidates(runs, cutoff), [runs[1], runs[0]])

    def test_exact_gap_signature(self):
        good = {h: dict(complete=729 if h < 2 else 728, partial=int(h == 2), no_run=0, leakage=0,
                        failures=[] if h < 2 else [(date(2025, 8, 7), "PARTIAL", "2025-08-04T06:00:00+00:00", 14, 14)])
                for h in (0, 1, 2)}
        audit.validate_backtest_results(good)
        for index, value in ((0, date(2025, 8, 8)), (1, "NO_RUN"), (2, "2025-08-04T00:00:00+00:00"),
                             (3, 13), (3, 24), (4, 13), (4, 24)):
            bad = copy.deepcopy(good)
            gap = list(bad[2]["failures"][0]); gap[index] = value; bad[2]["failures"] = [tuple(gap)]
            with self.assertRaises(RuntimeError): audit.validate_backtest_results(bad)
        bad = copy.deepcopy(good); bad[0]["leakage"] = 1
        with self.assertRaises(RuntimeError): audit.validate_backtest_results(bad)

    def test_nonfinite_temperature_is_not_complete(self):
        day = date(2026, 10, 1)
        data = {1: dict.fromkeys(audit.target_hours(day), 20.)}
        for invalid in (None, float("nan"), float("inf"), True, "20"):
            data[1][audit.target_hours(day)[0]] = invalid
            self.assertEqual(audit.coverage(data, 1, day), (24, 23))
            self.assertEqual(scanner.check_coverage(data, 1, day), (False, 24, 23))

    def test_scanner_falls_back_to_complete_legal_run(self):
        day = date(2026, 10, 1)
        cutoff = datetime(2026, 10, 1, 13, tzinfo=timezone.utc)
        runs = [dict(canonical_raw_run_id=key, canonical_status="AVAILABLE",
                     _run_dt=cutoff-timedelta(hours=age), _available_dt=cutoff+timedelta(seconds=delay))
                for key, age, delay in ((1, 12, 0), (2, 6, -1), (3, 0, 1))]
        hourly = {key: dict.fromkeys(audit.target_hours(day), 20.) for key in (1, 2, 3)}
        hourly[2][audit.target_hours(day)[0]] = None
        selected = scanner.choose_latest_complete_run(runs, hourly, day, cutoff)
        self.assertEqual(selected["run"]["canonical_raw_run_id"], 1)
        self.assertTrue(selected["complete"])
        partial = scanner.choose_latest_complete_run(runs[1:], hourly, day, cutoff)
        self.assertFalse(partial["complete"])
        self.assertEqual((partial["present"], partial["valid_temp"]), (24, 23))

    def test_audit_failure_returns_nonzero(self):
        with patch.object(audit, "connect_ro", side_effect=RuntimeError("test failure")):
            with redirect_stdout(io.StringIO()): self.assertEqual(audit.main(), 1)

    def test_frozen_tampering_fails(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE zuuu_target_v1 (id INTEGER, payload TEXT)")
        conn.execute("INSERT INTO zuuu_target_v1 VALUES (1,'changed')")
        with self.assertRaisesRegex(RuntimeError, "Frozen content changed"):
            audit.validate_frozen_objects(conn)

    def test_rule_payload_rehashed_tampering_still_fails(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE ecmwf_issue_rule_v1 (id INTEGER, rule_payload_json TEXT, semantic_sha256 TEXT)")
        payload = '{"rule":"tampered"}'
        digest = hashlib.sha256(payload.encode()).hexdigest()
        conn.execute("INSERT INTO ecmwf_issue_rule_v1 VALUES (1,?,?)", (payload, digest))
        # Bypass the outer full-row fingerprint to independently exercise the
        # pinned payload digest, including a maliciously recomputed stored SHA.
        with patch.object(audit, "EXPECTED_FROZEN_SHA", {}):
            with self.assertRaisesRegex(RuntimeError, "payload / SHA"):
                audit.validate_frozen_objects(conn)


class SilverSafety(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("CREATE TABLE zuuu_raw_metar(id INTEGER PRIMARY KEY)")
        self.conn.executemany("INSERT INTO zuuu_raw_metar VALUES (?)", [(1,), (2,)])
        self.conn.commit()
        self.archive = ZUUUSilverArchive("unused-test-path")
        self.addCleanup(patch.stopall)
        patch.object(self.archive, "connect", return_value=self.conn).start()
        with patch.object(Path, "mkdir"):
            self.archive.initialize()
        self.record = dict(bronze_raw_id=1, station="ZUUU", observation_time_utc="2025-01-01T00:00:00+00:00",
            observation_time_bjt="2025-01-01T08:00:00+08:00", business_date_bjt="2025-01-01",
            temperature_c=20., dewpoint_c=10., source="IEM", source_query_class="ROUTINE", message_class="METAR",
            is_correction=False, supersedes_raw_id=None, recovery_reason=None, qc_status="PASS", qc_flags=None,
            created_at_utc="2025-01-01T00:01:00+00:00")

    def test_write_connection_enables_fk(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        with patch("src.database.zuuu_silver_archive.sqlite3.connect", return_value=conn):
            archive = ZUUUSilverArchive("unused")
            self.assertEqual(archive.connect().execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_only_unique_bronze_duplicate_ignored(self):
        self.assertTrue(self.archive.insert(**self.record))
        before = [tuple(r) for r in self.archive.fetch_all()]
        self.assertFalse(self.archive.insert(**dict(self.record, temperature_c=99.)))
        self.assertEqual([tuple(r) for r in self.archive.fetch_all()], before)

    def test_other_constraints_raise(self):
        for change in (dict(bronze_raw_id=999), dict(temperature_c=None), dict(station=None),
                       dict(is_correction=2), dict(supersedes_raw_id=999)):
            with self.subTest(change=change):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.archive.insert(**dict(self.record, **change))
                self.assertEqual(self.archive.count(), 0)


class ImportSafety(unittest.TestCase):
    def test_header_only_is_legal_empty(self):
        self.assertEqual(parse_iem_response("station,valid,metar\n", "ROUTINE"), [])

    def test_abnormal_responses_fail(self):
        for text in ("", "<html>error</html>", "error,message\nfailed,timeout", "station,valid\n",
                     "station,valid,metar,metar\n", "station,valid,metar\nZUUU,2025-01-01 00:00\n",
                     "station,valid,metar\nZUUU,bad,report\n", "station,valid,metar\nZUUU,2025-01-01 00:00,\n",
                     "station,valid,metar\nZBAA,2025-01-01 00:00,report\n",
                     'station,valid,metar\nZUUU,2025-01-01 00:00,"unterminated'):
            with self.subTest(text=text):
                with self.assertRaises((ValueError, csv.Error)): parse_iem_response(text, "ROUTINE")

    def test_valid_record_preserves_source_field(self):
        report = " METAR ZUUU 010000Z 00000MPS 9999 20/10 Q1010 "
        rows = parse_iem_response("station,valid,metar\nZUUU,2025-01-01 00:00," + report + "\n", "ROUTINE")
        self.assertEqual(rows[0].raw_metar, report)
        self.assertEqual(rows[0].observation_time_utc, datetime(2025, 1, 1, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
