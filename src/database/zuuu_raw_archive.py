"""
ZUUU Historical Raw Archive V1.0
================================

Bronze / Raw Layer

核心原则：
1. 保存来源原始报文，不修改 raw_metar
2. IEM / OGIMET 来源必须保留
3. 恢复数据必须保留 recovery_reason
4. Raw 数据只追加，不覆盖
5. 重复写入同一来源、同一观测时间、同一Raw时自动忽略
6. SQLite保存UTC ISO-8601时间
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DB_PATH = (
    PROJECT_ROOT
    / "database"
    / "zuuu_prediction.db"
)


@dataclass(frozen=True)
class RawArchiveRecord:
    station: str
    source: str
    source_query_class: str | None
    message_class: str
    observation_time_utc: datetime
    raw_metar: str
    recovery_reason: str | None = None


def _require_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("NAIVE_DATETIME_NOT_ALLOWED")

    return dt.astimezone(timezone.utc)


def build_raw_identity(
    source: str,
    observation_time_utc: datetime,
    raw_metar: str,
) -> str:
    """
    Raw identity基于：

        source
        observation_time_utc
        raw_metar

    不对raw_metar做strip/upper/replace等标准化。
    """

    utc_dt = _require_aware_utc(
        observation_time_utc
    )

    payload = (
        source.upper()
        + "\x1f"
        + utc_dt.isoformat()
        + "\x1f"
        + raw_metar
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


class ZUUURawArchive:

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
    ):
        self.db_path = Path(db_path)

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def connect(self):
        return sqlite3.connect(
            self.db_path
        )

    def initialize(self):

        with self.connect() as conn:

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS
                zuuu_raw_metar
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    raw_identity TEXT NOT NULL UNIQUE,

                    station TEXT NOT NULL,

                    source TEXT NOT NULL,

                    source_query_class TEXT,

                    message_class TEXT NOT NULL,

                    observation_time_utc TEXT NOT NULL,

                    ingest_time_utc TEXT NOT NULL,

                    raw_metar TEXT NOT NULL,

                    recovery_reason TEXT,

                    created_at_utc TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_zuuu_raw_observation_time
                ON zuuu_raw_metar(
                    observation_time_utc
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_zuuu_raw_source
                ON zuuu_raw_metar(
                    source
                )
                """
            )

            conn.commit()

    def insert(
        self,
        record: RawArchiveRecord,
    ) -> bool:
        """
        返回：
            True  = 新记录成功写入
            False = 已存在，未覆盖
        """

        if record.station.upper() != "ZUUU":
            raise ValueError(
                "INVALID_STATION"
            )

        if record.source.upper() not in {
            "IEM",
            "OGIMET",
        }:
            raise ValueError(
                "UNSUPPORTED_SOURCE"
            )

        observation_time_utc = (
            _require_aware_utc(
                record.observation_time_utc
            )
        )

        # 这里绝对不修改Raw内容
        raw_metar = record.raw_metar

        if not raw_metar:
            raise ValueError(
                "EMPTY_RAW_METAR"
            )

        raw_identity = build_raw_identity(
            source=record.source,
            observation_time_utc=(
                observation_time_utc
            ),
            raw_metar=raw_metar,
        )

        now_utc = datetime.now(
            timezone.utc
        ).isoformat()

        with self.connect() as conn:

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO
                zuuu_raw_metar
                (
                    raw_identity,
                    station,
                    source,
                    source_query_class,
                    message_class,
                    observation_time_utc,
                    ingest_time_utc,
                    raw_metar,
                    recovery_reason,
                    created_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_identity,
                    record.station.upper(),
                    record.source.upper(),
                    record.source_query_class,
                    record.message_class,
                    observation_time_utc.isoformat(),
                    now_utc,
                    raw_metar,
                    record.recovery_reason,
                    now_utc,
                ),
            )

            conn.commit()

            return cursor.rowcount == 1

    def count(self) -> int:

        with self.connect() as conn:

            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM zuuu_raw_metar
                """
            ).fetchone()

        return int(row[0])

    def fetch_all(self):

        with self.connect() as conn:

            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                """
                SELECT *
                FROM zuuu_raw_metar
                ORDER BY
                    observation_time_utc,
                    id
                """
            ).fetchall()

        return rows