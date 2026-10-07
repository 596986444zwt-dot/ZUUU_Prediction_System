import io
import math
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.auxiliary.contracts import eligible_observation, utc
from src.auxiliary.meteostat.parser import parse_hourly
from src.auxiliary.meteostat.probe import summarize, fetch, MAX_BYTES
from src.auxiliary.schema import initialize_test_schema, TABLES
from src.auxiliary.solar.features import features

NOW = "2026-10-01T07:00:00+00:00"
SHA = "a" * 64
CSV = ("year,month,day,hour,temp,temp_source,rhum,rhum_source,prcp,prcp_source\n"
       "2025,1,1,0,10,metar,80,isd_lite,0,metno_forecast\n")


def parse(text=CSV):
    return parse_hourly(text, "56294", ingest_time=NOW, snapshot_sha256=SHA)


class MeteostatContractTests(unittest.TestCase):
    def test_sources_and_missing_not_fabricated(self):
        row = parse()[0]
        self.assertEqual(row["observation_time"], "2025-01-01T00:00:00+00:00")
        self.assertIsNone(row["source_available_time"])
        self.assertIsNone(row["target_time"])
        self.assertEqual(row["values"]["temp"]["value_kind"], "OBSERVATION")
        self.assertEqual(row["values"]["prcp"]["value_kind"], "MODEL")
        self.assertEqual(row["values"]["dwpt"]["quality"], "UNSUPPORTED")
        self.assertIsNone(row["values"]["snow"]["value"])

    def test_only_valid_header_can_be_empty(self):
        self.assertEqual(parse(CSV.splitlines()[0] + "\n"), [])
        for text in ("", "<html>error</html>", "year,month,day,hour\n", "year,month,day,hour,temp\n",
                     CSV + CSV.splitlines()[1] + "\n", CSV + "2025,1,1,2\n"):
            with self.subTest(text=text), self.assertRaises(ValueError): parse(text)

    def test_bad_numeric_and_unknown_source_not_eligible(self):
        for value in ("nan", "inf", "-150"):
            row = parse(CSV.replace(",10,metar,", f",{value},metar,"))[0]
            self.assertEqual(row["values"]["temp"]["quality"], "INVALID")
        row = parse(CSV.replace("10,metar", "10,unverified"))[0]
        self.assertEqual(row["values"]["temp"]["value_kind"], "UNKNOWN")

    def test_missing_rate_includes_absent_hours_and_excludes_model(self):
        result = summarize(parse(), "2025-01-01T00:00:00Z", "2025-01-01T02:00:00Z")
        self.assertEqual(result["absent_hours"], 1)
        self.assertEqual(result["parameters"]["temp"]["observation_missing_pct"], 50.)
        self.assertEqual(result["parameters"]["prcp"]["observation_missing_pct"], 100.)

    def test_probe_network_cap(self):
        class Response(io.BytesIO):
            headers = {}
        with patch("src.auxiliary.meteostat.probe.urlopen", return_value=Response(b"x"*(MAX_BYTES+1))):
            with self.assertRaisesRegex(ValueError, "cap"): fetch("https://data.meteostat.net/test")


class LookaheadTests(unittest.TestCase):
    def setUp(self):
        hourly = parse()[0]
        self.row = dict(hourly, **hourly["values"]["temp"])

    def test_download_now_cannot_be_used_in_historical_backtest(self):
        self.assertFalse(eligible_observation(self.row, "2025-01-01T13:00:00Z"))
        self.assertFalse(eligible_observation(self.row, "2027-01-01T13:00:00Z"))

    def test_observed_snapshot_exact_cutoff_and_future(self):
        row = dict(self.row, availability_basis="CAPTURED_SNAPSHOT", source_available_time=NOW)
        self.assertTrue(eligible_observation(row, NOW))
        self.assertFalse(eligible_observation(row, "2026-10-01T06:59:59.999999Z"))
        self.assertFalse(eligible_observation(dict(row, observation_time="2026-10-02T00:00:00Z"), NOW))
        self.assertFalse(eligible_observation(dict(row, source_available_time="2025-01-01T01:00:00Z"), NOW))
        self.assertFalse(eligible_observation(dict(row, value_kind="MODEL"), NOW))
        self.assertFalse(eligible_observation(dict(row, evidence_sha256=""), NOW))

    def test_estimated_availability_not_accepted(self):
        for basis in ("official_schedule_estimate", "OBSERVATION_PLUS_24H", "HTTP_LAST_MODIFIED"):
            self.assertFalse(eligible_observation(dict(self.row, availability_basis=basis, source_available_time=NOW), NOW))

    def test_naive_times_rejected(self):
        with self.assertRaises(ValueError): utc("2025-01-01T00:00:00")
        with self.assertRaises(ValueError): eligible_observation(self.row, datetime(2025, 1, 1))


class SolarTests(unittest.TestCase):
    def test_timezone_equivalence(self):
        a = features("2025-01-01T00:00:00Z", ingest_time=NOW)
        b = features("2025-01-01T08:00:00+08:00", ingest_time=NOW)
        self.assertEqual(a, b)
        self.assertEqual((a["latitude"], a["longitude"], a["hour_bjt"]), (30.576, 103.950, 8))
        self.assertIsNone(a["observation_time"])
        self.assertIsNone(a["source_available_time"])

    def test_leap_year_and_bjt_midnight(self):
        r = features("2024-02-28T16:00:00Z", ingest_time=NOW)
        self.assertEqual((r["day_of_year"], r["hour_bjt"], r["month"]), (60, 0, 2))
        self.assertAlmostEqual(r["sin_doy"], math.sin(2*math.pi*59/366))
        r = features("2024-12-31T16:00:00Z", ingest_time=NOW)
        self.assertEqual(r["day_of_year"], 1)

    def test_sunrise_sunset_and_seasons(self):
        summer = features("2025-06-21T05:00:00Z", ingest_time=NOW)
        winter = features("2025-12-21T05:00:00Z", ingest_time=NOW)
        self.assertGreater(summer["daylight_duration"], winter["daylight_duration"])
        self.assertTrue(820 < summer["daylight_duration"] < 860)
        self.assertTrue(600 < winter["daylight_duration"] < 640)
        self.assertGreater(summer["solar_elevation"], 75)
        night = features("2025-06-20T16:00:00Z", ingest_time=NOW)
        self.assertLess(night["solar_elevation"], 0)
        self.assertLess(night["minutes_since_sunrise"], 0)
        self.assertEqual(night["sunrise"], summer["sunrise"])
        self.assertAlmostEqual(summer["minutes_since_sunrise"] + summer["minutes_to_sunset"], summer["daylight_duration"])

    def test_future_target_uses_no_weather(self):
        a = features("2030-06-21T05:00:00Z", ingest_time=NOW)
        b = features("2030-06-21T05:00:00Z", ingest_time="2020-01-01T00:00:00Z")
        for key in a:
            if key != "ingest_time": self.assertEqual(a[key], b[key])

    def test_azimuth_orientation(self):
        morning = features("2025-03-21T01:00:00Z", ingest_time=NOW)
        afternoon = features("2025-03-21T09:00:00Z", ingest_time=NOW)
        self.assertTrue(0 < morning["solar_azimuth"] < 180)
        self.assertTrue(180 < afternoon["solar_azimuth"] < 360)

    def test_all_729_days_hourly_deterministic_coverage(self):
        start = utc("2024-09-02T16:00:00Z")
        for day in range(729):
            duration = None
            for hour in range(24):
                row = features(start + timedelta(days=day, hours=hour), ingest_time=NOW)
                self.assertEqual(row["hour_bjt"], hour)
                self.assertTrue(-90 <= row["solar_elevation"] <= 90)
                self.assertTrue(0 <= row["solar_azimuth"] < 360)
                self.assertAlmostEqual(row["sin_hour"]**2 + row["cos_hour"]**2, 1)
                if duration is not None: self.assertEqual(row["daylight_duration"], duration)
                duration = row["daylight_duration"]


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        initialize_test_schema(self.conn)

    def insert_solar(self):
        row = features("2025-01-01T00:00:00Z", ingest_time=NOW)
        sql = "INSERT INTO aux_v1_solar_time (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")"
        self.conn.execute(sql, tuple(row.values()))
        self.conn.commit()
        return sql, row

    def test_only_independent_tables_and_foreign_keys(self):
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, set(TABLES))
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.insert_solar()
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_updates_deletes_replacements_blocked(self):
        sql, row = self.insert_solar()
        for query, params in (("DELETE FROM aux_v1_solar_time", ()),
                              ("UPDATE aux_v1_solar_time SET hour_bjt=9", ()),
                              (sql.replace("INSERT INTO", "INSERT OR REPLACE INTO"), tuple(row.values()))):
            with self.assertRaises(sqlite3.IntegrityError): self.conn.execute(query, params)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM aux_v1_solar_time").fetchone()[0], 1)

    def test_existing_or_attached_database_refused(self):
        with self.assertRaises(RuntimeError): initialize_test_schema(self.conn)
        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        c.execute("ATTACH ':memory:' AS old_data")
        with self.assertRaises(RuntimeError): initialize_test_schema(c)

    def test_wrong_coordinates_rejected(self):
        sql, row = self.insert_solar()
        row["target_time"] = "2025-01-02T00:00:00+00:00"
        row["latitude"] = 30.6667  # Meteostat station coordinate is NOT airport coordinate.
        with self.assertRaises(sqlite3.IntegrityError): self.conn.execute(sql, tuple(row.values()))

    def test_hourly_snapshot_lineage_and_model_classification(self):
        self.conn.execute("""INSERT INTO aux_v1_station_snapshot VALUES
            (1,'56294','test',30.6667,104.0167,508,'Asia/Chongqing','{}','Meteostat',?,?,?,'{}')""",
            ("sha256:"+SHA, NOW, SHA))
        self.conn.execute("""INSERT INTO aux_v1_raw_snapshot
            (id,station_snapshot_id,request_url,requested_year,source,source_version,
             source_available_time,availability_basis,ingest_time,timezone,content_sha256,
             http_status,response_blob,compression) VALUES
            (1,1,'https://data.meteostat.net/hourly/2025/56294.csv.gz',2025,'Meteostat',?,NULL,
             'UNKNOWN',?,'UTC',?,200,?,'gzip')""", ("sha256:"+SHA, NOW, SHA, b"test fixture"))
        hourly = parse()[0]
        row = {k:v for k,v in hourly.items() if k not in ("station_id", "values")}
        row.update({k:v for k,v in hourly["values"]["temp"].items() if k != "name"})
        row["snapshot_id"] = 1
        sql = "INSERT INTO aux_v1_meteostat_hourly (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")"
        self.conn.execute(sql, tuple(row.values()))
        self.conn.commit()
        for changes in (dict(observation_time="2025-01-01T01:00:00+00:00", evidence_sha256="b"*64),
                        dict(observation_time="2025-01-01T01:00:00+00:00", provider="metno_forecast"),
                        dict(observation_time="2025-01-01T01:00:00+00:00", source_available_time=NOW,
                             availability_basis="CAPTURED_SNAPSHOT")):
            modified = dict(row, **changes)
            with self.assertRaises(sqlite3.IntegrityError): self.conn.execute(sql, tuple(modified.values()))
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
