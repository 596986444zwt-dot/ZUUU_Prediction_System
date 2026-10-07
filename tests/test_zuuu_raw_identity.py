import sqlite3
import unittest


class TestZuuuRawIdentityV2(unittest.TestCase):

    def setUp(self):
        """
        每个测试使用独立的内存 SQLite 数据库。
        不接触正式 zuuu_prediction.db。
        """

        self.connection = sqlite3.connect(":memory:")

        self.connection.execute(
            """
            CREATE TABLE zuuu_raw_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id TEXT NOT NULL,
                observation_time_utc TEXT NOT NULL,
                raw_report TEXT NOT NULL
            );
            """
        )

        self.connection.execute(
            """
            CREATE UNIQUE INDEX
            idx_zuuu_raw_observation_report
            ON zuuu_raw_reports (
                station_id,
                observation_time_utc,
                raw_report
            );
            """
        )

    def tearDown(self):
        self.connection.close()

    def insert_report(
        self,
        station_id,
        observation_time_utc,
        raw_report,
    ):
        self.connection.execute(
            """
            INSERT INTO zuuu_raw_reports (
                station_id,
                observation_time_utc,
                raw_report
            )
            VALUES (?, ?, ?);
            """,
            (
                station_id,
                observation_time_utc,
                raw_report,
            ),
        )

        self.connection.commit()

    def test_same_raw_report_different_month_allowed(self):
        """
        核心回归测试：

        METAR正文完全相同，
        但真实 observation datetime 不同。

        两条都必须允许入库。
        """

        raw_report = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011 NOSIG"
        )

        self.insert_report(
            "ZUUU",
            "2026-08-28T04:00:00+00:00",
            raw_report,
        )

        self.insert_report(
            "ZUUU",
            "2026-09-28T04:00:00+00:00",
            raw_report,
        )

        count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM zuuu_raw_reports;
            """
        ).fetchone()[0]

        self.assertEqual(
            count,
            2,
            "跨月份相同 METAR 被错误去重",
        )

    def test_same_identity_blocked(self):
        """
        同机场 + 同 observation time + 同 raw_report
        才是真正重复。

        第二次 INSERT 必须被 SQLite 阻止。
        """

        raw_report = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011 NOSIG"
        )

        observation_time = (
            "2026-09-28T04:00:00+00:00"
        )

        self.insert_report(
            "ZUUU",
            observation_time,
            raw_report,
        )

        with self.assertRaises(
            sqlite3.IntegrityError
        ):
            self.insert_report(
                "ZUUU",
                observation_time,
                raw_report,
            )

    def test_same_time_different_raw_report_allowed(self):
        """
        同一个 observation time 出现不同原始报文，
        例如后续 COR 修正版。

        Raw 层必须全部保留，
        不能因为 observation time 相同就删除其中一版。
        """

        original = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011"
        )

        corrected = (
            "METAR ZUUU 280400Z COR "
            "04007MPS 9999 "
            "FEW026 25/19 Q1011"
        )

        observation_time = (
            "2026-09-28T04:00:00+00:00"
        )

        self.insert_report(
            "ZUUU",
            observation_time,
            original,
        )

        self.insert_report(
            "ZUUU",
            observation_time,
            corrected,
        )

        count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM zuuu_raw_reports;
            """
        ).fetchone()[0]

        self.assertEqual(
            count,
            2,
            "Raw层错误覆盖/去重了COR版本",
        )

    def test_different_year_allowed(self):
        """
        防止不仅跨月，而且跨年误去重。
        """

        raw_report = (
            "METAR ZUUU 280400Z "
            "04007MPS 9999 "
            "FEW026 24/19 Q1011 NOSIG"
        )

        self.insert_report(
            "ZUUU",
            "2025-09-28T04:00:00+00:00",
            raw_report,
        )

        self.insert_report(
            "ZUUU",
            "2026-09-28T04:00:00+00:00",
            raw_report,
        )

        count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM zuuu_raw_reports;
            """
        ).fetchone()[0]

        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)