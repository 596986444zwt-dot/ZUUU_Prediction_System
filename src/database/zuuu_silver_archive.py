"""
ZUUU Silver Observation Archive V1.0

职责：
    保存由 Bronze Raw 转换得到的结构化 ZUUU 观测。

原则：
    1. Bronze 永久不修改
    2. Silver 可以根据 Bronze 重建
    3. 每条 Silver 必须能追溯到 Bronze raw_id
    4. COR 不自动删除
    5. Recovery 来源不丢失
    6. UTC / BJT / Business Date 同时保存
    7. Silver V1 先保存温度、露点及核心 provenance/QC 字段
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DB_PATH = (
    PROJECT_ROOT
    / "database"
    / "zuuu_prediction.db"
)


class ZUUUSilverArchive:

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
    ):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:

        conn = sqlite3.connect(
            self.db_path
        )

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        return conn

    def initialize(self) -> None:

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with self.connect() as conn:

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS
                zuuu_silver_observation
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    bronze_raw_id INTEGER NOT NULL UNIQUE,

                    station TEXT NOT NULL,

                    observation_time_utc TEXT NOT NULL,
                    observation_time_bjt TEXT NOT NULL,
                    business_date_bjt TEXT NOT NULL,

                    temperature_c REAL NOT NULL,
                    dewpoint_c REAL,

                    source TEXT NOT NULL,
                    source_query_class TEXT,

                    message_class TEXT NOT NULL,

                    is_correction INTEGER NOT NULL
                        CHECK (
                            is_correction IN (0, 1)
                        ),

                    supersedes_raw_id INTEGER,

                    recovery_reason TEXT,

                    qc_status TEXT NOT NULL,
                    qc_flags TEXT,

                    created_at_utc TEXT NOT NULL,

                    FOREIGN KEY (
                        bronze_raw_id
                    )
                    REFERENCES zuuu_raw_metar(id),

                    FOREIGN KEY (
                        supersedes_raw_id
                    )
                    REFERENCES zuuu_raw_metar(id)
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_zuuu_silver_obs_time
                ON zuuu_silver_observation(
                    observation_time_utc
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_zuuu_silver_business_date
                ON zuuu_silver_observation(
                    business_date_bjt
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_zuuu_silver_message_class
                ON zuuu_silver_observation(
                    message_class
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_zuuu_silver_qc_status
                ON zuuu_silver_observation(
                    qc_status
                )
                """
            )

            conn.commit()

    def insert(
        self,
        *,
        bronze_raw_id: int,
        station: str,
        observation_time_utc: str,
        observation_time_bjt: str,
        business_date_bjt: str,
        temperature_c: float,
        dewpoint_c: Optional[float],
        source: str,
        source_query_class: Optional[str],
        message_class: str,
        is_correction: bool,
        supersedes_raw_id: Optional[int],
        recovery_reason: Optional[str],
        qc_status: str,
        qc_flags: Optional[str],
        created_at_utc: str,
    ) -> bool:
        """
        返回：
            True  = 新增
            False = bronze_raw_id 已存在

        Silver 使用 bronze_raw_id UNIQUE，
        保证同一 Bronze Raw 不会重复生成 Silver。
        """

        with self.connect() as conn:

            cursor = conn.execute(
                """
                INSERT INTO
                zuuu_silver_observation
                (
                    bronze_raw_id,
                    station,
                    observation_time_utc,
                    observation_time_bjt,
                    business_date_bjt,
                    temperature_c,
                    dewpoint_c,
                    source,
                    source_query_class,
                    message_class,
                    is_correction,
                    supersedes_raw_id,
                    recovery_reason,
                    qc_status,
                    qc_flags,
                    created_at_utc
                )
                VALUES
                (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?
                )
                ON CONFLICT(bronze_raw_id) DO NOTHING
                """,
                (
                    bronze_raw_id,
                    station,
                    observation_time_utc,
                    observation_time_bjt,
                    business_date_bjt,
                    temperature_c,
                    dewpoint_c,
                    source,
                    source_query_class,
                    message_class,
                    int(is_correction),
                    supersedes_raw_id,
                    recovery_reason,
                    qc_status,
                    qc_flags,
                    created_at_utc,
                ),
            )

            conn.commit()

            return cursor.rowcount == 1

    def count(self) -> int:

        with self.connect() as conn:

            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM zuuu_silver_observation
                """
            ).fetchone()

        return int(
            row["count"]
        )

    def fetch_by_bronze_raw_id(
        self,
        bronze_raw_id: int,
    ):

        with self.connect() as conn:

            return conn.execute(
                """
                SELECT *
                FROM zuuu_silver_observation
                WHERE bronze_raw_id = ?
                """,
                (
                    bronze_raw_id,
                ),
            ).fetchone()

    def fetch_all(self):

        with self.connect() as conn:

            return conn.execute(
                """
                SELECT *
                FROM zuuu_silver_observation
                ORDER BY
                    observation_time_utc,
                    id
                """
            ).fetchall()