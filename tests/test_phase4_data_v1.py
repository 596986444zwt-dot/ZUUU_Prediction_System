"""Actual frozen-source integration tests plus negative leakage/transaction cases.

Sources are opened only through byte snapshots. No formal Gold commit in tests.
"""
import copy
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src.builders import phase4_data_v1_builder as builder
from src.audit.phase4_data_v1_audit import validate_data, audit_connection
from src.audit.phase4_leakage_audit import audit_leakage
from src.data_v1.contracts import *
from src.data_v1.hashing import dataset_hash
from src.data_v1.schema import PREFIX, create_schema, read_rows
from src.data_v1.solar_join import load_solar
from src.data_v1.source_io import frozen_fingerprints, open_snapshot


def changed(data, table, column, value, index=0):
    result = dict(data)
    result[table] = list(data[table])
    result[table][index] = dict(data[table][index], **{column:value})
    return result


class Phase4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial = frozen_fingerprints()
        with redirect_stdout(io.StringIO()):
            cls.data, cls.report, cls.created, cls.before = builder.prepare()
        cls.conn = sqlite3.connect(":memory:")
        builder.populate(cls.conn, cls.data, cls.report, cls.created)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        if frozen_fingerprints() != cls.initial:
            raise AssertionError("Frozen source bytes/sidecars changed")

    def test_ground_truth_729_and_full_provenance(self):
        self.assertEqual(len(self.data["target"]), 729)
        self.assertEqual(tuple(r["business_date_bjt"] for r in self.data["target"]), DATES)
        import json
        for target in self.data["target"]:
            original = json.loads(target["source_provenance_json"])
            for key in ("tmax_silver_ids","tmax_bronze_raw_ids","all_silver_ids","all_bronze_raw_ids",
                        "first_tmax_time_bjt","last_tmax_time_bjt","has_correction","has_recovery"):
                self.assertIn(key, original)
            self.assertEqual(target["daily_tmax_c"], original["daily_tmax_c"])

    def test_2187_samples_and_expected_counts(self):
        self.assertEqual(len(self.data["sample"]), 2187)
        self.assertEqual(self.report["eligible"], {"T0":729,"T1":729,"T2":728})
        self.assertEqual(self.report["eligible_total"], 2186)
        self.assertEqual(self.report["excluded_total"], 1)

    def test_gap_exact_identity_14_of_24_no_padding(self):
        gap = [r for r in self.data["sample"] if r["sample_status"] != "ELIGIBLE"]
        self.assertEqual(len(gap),1)
        row = gap[0]
        self.assertEqual((row["business_date_bjt"],row["horizon"],row["sample_status"],row["trajectory_valid_hours"]),
                         ("2025-08-07","T2","EXCLUDED_INCOMPLETE_ECMWF",14))
        self.assertEqual(row["selected_ecmwf_run_time_utc"], "2025-08-04T06:00:00+00:00")
        rows = [r for r in self.data["ecmwf_hourly"] if (r["business_date_bjt"],r["horizon"]) == ("2025-08-07","T2")]
        self.assertEqual(len(rows),14)
        self.assertEqual([int(r["target_time_bjt"][11:13]) for r in rows],list(range(14)))
        self.assertTrue(all(r["temperature_2m_c"] is not None for r in rows))

    def test_eligible_24_hours(self):
        for sample in self.data["sample"]:
            if sample["sample_status"] == "ELIGIBLE":
                self.assertEqual((sample["trajectory_present_hours"],sample["trajectory_valid_hours"]),(24,24))

    def test_zero_leakage_and_cutoff(self):
        self.assertEqual(audit_leakage(self.data),0)
        for row in self.data["sample"]:
            self.assertLessEqual(utc(row["selected_ecmwf_source_available_time_utc"]),utc(row["issue_time_utc"]))

    def test_future_vintage_hard_fails(self):
        invalid = changed(self.data,"sample","selected_ecmwf_source_available_time_utc","2099-01-01T00:00:00Z")
        with self.assertRaisesRegex(RuntimeError,"LEAKAGE"): validate_data(invalid)

    def test_issue_time_change_fails(self):
        invalid = changed(self.data,"sample","issue_time_utc","2024-09-03T14:00:00Z")
        with self.assertRaisesRegex(RuntimeError,"cutoff"): audit_leakage(invalid)

    def test_cross_vintage_fails(self):
        invalid = changed(self.data,"ecmwf_hourly","source_raw_run_id",999999)
        with self.assertRaisesRegex(RuntimeError,"Cross-vintage"): audit_leakage(invalid)

    def test_independent_horizon_vintages(self):
        first = [r for r in self.data["sample"] if r["business_date_bjt"] == DATES[0]]
        self.assertEqual(len({r["selected_ecmwf_run_time_utc"] for r in first}),3)

    def test_solar_17496_and_exact_join(self):
        self.assertEqual(len(self.data["solar"]),17496)
        self.assertEqual(self.report["solar_join_count"],52478)
        self.assertEqual(self.report["eligible_solar_join_count"],52464)
        self.assertTrue(all(r["target_time_utc"] == r["solar_target_time"] for r in self.data["ecmwf_hourly"]))
        self.assertTrue(all(r["availability_basis"] == "DETERMINISTIC_NOT_APPLICABLE" for r in self.data["solar"]))

    def test_solar_shift_fails(self):
        invalid = changed(self.data,"ecmwf_hourly","solar_target_time",self.data["ecmwf_hourly"][1]["target_time_utc"])
        with self.assertRaisesRegex(RuntimeError,"Solar time misalignment"): validate_data(invalid)

    def test_no_meteostat_or_labels_in_feature_view(self):
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({PREFIX}training_hourly)")}
        self.assertFalse(any("meteostat" in c for c in cols))
        for forbidden in ("target_tmax_c","first_tmax_time_bjt","error_c","absolute_error_c","has_recovery"):
            self.assertNotIn(forbidden,cols)
        self.assertEqual(self.conn.execute(f"SELECT count(*) FROM {PREFIX}training_hourly").fetchone()[0],52464)
        self.assertEqual(self.conn.execute(f"SELECT count(*) FROM {PREFIX}training_labels").fetchone()[0],2186)
        invalid = changed(self.data,"sample","meteostat_temp",25.)
        with self.assertRaisesRegex(RuntimeError,"Unexpected feature"): validate_data(invalid)

    def test_excluded_daily_statistics_remain_null(self):
        gap = next(r for r in self.data["daily"] if (r["business_date_bjt"],r["horizon"]) == ("2025-08-07","T2"))
        self.assertEqual(gap["trajectory_complete"],0)
        for f in ("ecmwf_daily_max_c","error_c","absolute_error_c"):
            self.assertIsNone(gap[f])

    def test_semantic_hash_stable_order_ids_and_clock(self):
        expected = self.report["dataset_semantic_sha256"]
        reverse = {t:list(reversed(rows)) for t,rows in self.data.items()}
        self.assertEqual(dataset_hash(reverse), expected)
        for table, key, value in (("target","source_target_id",999), ("target","source_provenance_json",'{}'),
                                  ("sample","created_at_utc","2099-01-01T00:00:00Z"),
                                  ("sample","source_archive_id",999), ("ecmwf_hourly","source_hourly_id",999),
                                  ("solar","ingest_time","2099-01-01T00:00:00Z")):
            with self.subTest(table=table,key=key):
                self.assertEqual(dataset_hash(changed(self.data,table,key,value)), expected)

    def test_semantic_hash_detects_real_feature_change(self):
        invalid = changed(self.data,"ecmwf_hourly","temperature_2m_c",-99.)
        self.assertNotEqual(dataset_hash(invalid),self.report["dataset_semantic_sha256"])

    def test_production_auxiliary_hash_unchanged(self):
        fingerprints=frozen_fingerprints()
        self.assertEqual(fingerprints,self.initial)
        self.assertEqual(fingerprints["production"]["sha256"],PRODUCTION_SHA)
        self.assertEqual(fingerprints["auxiliary"]["sha256"],AUXILIARY_SHA)

    def test_dry_run_no_file_or_source_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"gold.db"
            with patch.object(builder,"prepare",return_value=(self.data,self.report,self.created,self.before)):
                result=builder.build(output=path)
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(folder).iterdir()),[])
            self.assertEqual(result["dataset_semantic_sha256"],self.report["dataset_semantic_sha256"])
            self.assertEqual(frozen_fingerprints(),self.initial)

    def test_existing_output_and_source_paths_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"exists.db";path.write_bytes(b"do not overwrite")
            with patch.object(builder,"prepare") as prepare:
                with self.assertRaisesRegex(RuntimeError,"already exists"): builder.build(commit=True,output=path)
                for protected in (PRODUCTION,AUXILIARY):
                    with self.assertRaisesRegex(RuntimeError,"protected"): builder.build(commit=True,output=protected)
                prepare.assert_not_called()
            self.assertEqual(path.read_bytes(),b"do not overwrite")

    def test_transaction_rolls_back_schema_and_rows(self):
        conn=sqlite3.connect(":memory:")
        try:
            with patch.object(builder,"audit_connection",side_effect=RuntimeError("injected final failure")):
                with self.assertRaisesRegex(RuntimeError,"injected"):
                    builder.populate(conn,self.data,self.report,self.created)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master").fetchall(),[])
        finally: conn.close()

    def test_sql_constraints_foreign_keys_and_immutability(self):
        for statement in (f"DELETE FROM {PREFIX}sample",f"UPDATE {PREFIX}solar SET hour_bjt=1"):
            with self.assertRaises(sqlite3.IntegrityError): self.conn.execute(statement)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_source_reconstruction_and_stored_hash(self):
        with redirect_stdout(io.StringIO()):
            result=audit_connection(self.conn,reconstruct=True)
        self.assertEqual(result["source_reconstruction"],"PASS")
        self.assertEqual(result["dataset_semantic_sha256"],self.report["dataset_semantic_sha256"])


if __name__ == "__main__":
    unittest.main()
