"""
ZUUU Historical Raw Importer V1.0
=================================

用途：
    将 IEM 历史 ZUUU METAR 原始报文导入 Bronze SQLite。

模式：
    --dry-run
        下载、解析、审计，但绝不写数据库。

    --commit
        正式写入 Bronze Raw Archive。

原则：
    1. Raw source-native text 不修改
    2. Routine / Special 分开请求
    3. 网络失败 != 无报文
    4. Raw 只追加，不覆盖
    5. 每条记录保存来源与 query class
    6. 使用 timezone-aware UTC
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.database.zuuu_raw_archive import (
    RawArchiveRecord,
    ZUUURawArchive,
)


STATION = "ZUUU"
NETWORK = "CN_ASOS"

IEM_URL = (
    "https://mesonet.agron.iastate.edu/"
    "cgi-bin/request/asos.py"
)

UTC = timezone.utc

DEFAULT_START_DATE = "2024-09-02"
DEFAULT_END_DATE = "2026-09-01"

MAX_RETRIES = 5
BASE_RETRY_SECONDS = 2.0
REQUEST_PAUSE_SECONDS = 0.35


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class SourceRecord:
    observation_time_utc: datetime
    raw_metar: str
    source_query_class: str
    message_class: str


@dataclass
class ImportStats:
    days_total: int = 0
    days_success: int = 0
    days_failed: int = 0

    http_requests: int = 0

    routine_records: int = 0
    special_records: int = 0

    inserted: int = 0
    duplicates: int = 0

    parse_skipped: int = 0


# ============================================================
# Message Class
# ============================================================

def classify_message(raw: str) -> str:

    text = raw.strip().upper()

    if text.startswith("METAR "):
        return "METAR"

    if text.startswith("SPECI "):
        return "SPECI"

    if text.startswith("COR "):
        return "COR"

    if text.startswith("AMD "):
        return "AMD"

    if text.startswith("ZUUU "):
        return "PREFIXLESS"

    return "OTHER"


# ============================================================
# 日期
# ============================================================

def parse_date(value: str) -> date:

    return datetime.strptime(
        value,
        "%Y-%m-%d",
    ).date()


def iter_dates(
    start_date: date,
    end_date: date,
):

    current = start_date

    while current <= end_date:

        yield current

        current += timedelta(days=1)


# ============================================================
# IEM 请求参数
# ============================================================

def build_iem_url(
    target_date: date,
    report_type: int,
) -> str:

    next_date = (
        target_date
        + timedelta(days=1)
    )

    params = [
        ("station", STATION),
        ("data", "metar"),
        ("year1", str(target_date.year)),
        ("month1", str(target_date.month)),
        ("day1", str(target_date.day)),
        ("year2", str(next_date.year)),
        ("month2", str(next_date.month)),
        ("day2", str(next_date.day)),
        ("tz", "Etc/UTC"),
        ("format", "onlycomma"),
        ("latlon", "no"),
        ("elev", "no"),
        ("missing", "null"),
        ("trace", "null"),
        ("direct", "no"),
        ("report_type", str(report_type)),
    ]

    return (
        IEM_URL
        + "?"
        + urlencode(params)
    )


# ============================================================
# 网络请求
# ============================================================

def download_text(
    url: str,
    stats: ImportStats,
) -> str:

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        stats.http_requests += 1

        try:

            request = Request(
                url,
                headers={
                    "User-Agent":
                        "ZUUU-Prediction-System/1.0"
                },
            )

            with urlopen(
                request,
                timeout=30,
            ) as response:

                raw_bytes = response.read()

            return raw_bytes.decode(
                "utf-8",
                errors="replace",
            )

        except Exception as exc:

            last_error = exc

            if attempt >= MAX_RETRIES:
                break

            wait_seconds = (
                BASE_RETRY_SECONDS
                * attempt
            )

            print(
                f"      请求失败，"
                f"{wait_seconds:.1f}s 后重试 "
                f"({attempt}/{MAX_RETRIES})"
            )

            print(
                f"      {type(exc).__name__}: "
                f"{exc}"
            )

            time.sleep(
                wait_seconds
            )

    raise RuntimeError(
        f"IEM_REQUEST_FAILED: {last_error}"
    )


# ============================================================
# CSV 解析
# ============================================================

def parse_iem_response(
    text: str,
    source_query_class: str,
) -> list[SourceRecord]:

    records: list[SourceRecord] = []

    reader = csv.DictReader(io.StringIO(text), strict=True)
    fields = reader.fieldnames
    if (not fields or len(fields) != len(set(fields))
            or not {"valid", "metar"}.issubset(fields)
            or not ({"station", "station_id"} & set(fields))):
        raise ValueError("IEM_INVALID_CSV_SCHEMA: expected station/station_id, valid, metar")

    # Only a valid header with zero records represents a legitimate empty day.
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"IEM_INVALID_CSV_ROW: line {reader.line_num}")
        station = (row.get("station") or row.get("station_id") or "").strip().upper()
        if station != STATION:
            raise ValueError(f"IEM_UNEXPECTED_STATION: {station!r}")
        valid_text = row["valid"].strip()
        raw_metar = row["metar"]  # Preserve the source field verbatim.
        if not raw_metar.strip() or raw_metar.strip().upper() in {"M", "NULL", "NONE"}:
            raise ValueError(f"IEM_MISSING_METAR: line {reader.line_num}")
        observation_time = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                observation_time = datetime.strptime(valid_text, fmt).replace(tzinfo=UTC)
                break
            except ValueError:
                pass
        if observation_time is None:
            raise ValueError(f"IEM_INVALID_TIME: {valid_text!r}")

        records.append(
            SourceRecord(
                observation_time_utc=(
                    observation_time
                ),
                raw_metar=raw_metar,
                source_query_class=(
                    source_query_class
                ),
                message_class=(
                    classify_message(
                        raw_metar
                    )
                ),
            )
        )

    return records


# ============================================================
# 单日请求
# ============================================================

def fetch_one_day(
    target_date: date,
    stats: ImportStats,
) -> list[SourceRecord]:

    all_records: list[SourceRecord] = []

    # Routine
    routine_url = build_iem_url(
        target_date,
        report_type=3,
    )

    routine_text = download_text(
        routine_url,
        stats,
    )

    routine = parse_iem_response(
        routine_text,
        "ROUTINE",
    )

    stats.routine_records += len(
        routine
    )

    all_records.extend(
        routine
    )

    time.sleep(
        REQUEST_PAUSE_SECONDS
    )

    # Special
    special_url = build_iem_url(
        target_date,
        report_type=4,
    )

    special_text = download_text(
        special_url,
        stats,
    )

    special = parse_iem_response(
        special_text,
        "SPECIAL",
    )

    stats.special_records += len(
        special
    )

    all_records.extend(
        special
    )

    return all_records


# ============================================================
# CLI
# ============================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description=(
            "ZUUU Historical Raw Importer V1.0"
        )
    )

    parser.add_argument(
        "--start",
        default=DEFAULT_START_DATE,
    )

    parser.add_argument(
        "--end",
        default=DEFAULT_END_DATE,
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--dry-run",
        action="store_true",
    )

    mode.add_argument(
        "--commit",
        action="store_true",
    )

    return parser.parse_args()


# ============================================================
# Main
# ============================================================

def main():

    args = parse_args()

    start_date = parse_date(
        args.start
    )

    end_date = parse_date(
        args.end
    )

    if end_date < start_date:
        raise ValueError(
            "END_DATE_BEFORE_START_DATE"
        )

    mode = (
        "COMMIT"
        if args.commit
        else "DRY-RUN"
    )

    archive = ZUUURawArchive()

    # initialize只建表。
    # dry-run模式不调用。
    if args.commit:
        archive.initialize()

    stats = ImportStats()

    failed_dates: list[
        tuple[date, str]
    ] = []

    message_counter = Counter()

    print("=" * 70)
    print(
        "ZUUU Historical Raw Importer V1.0"
    )
    print("=" * 70)

    print(
        f"模式：{mode}"
    )

    print(
        f"日期：{start_date} -> {end_date}"
    )

    print(
        f"站点：{STATION}"
    )

    if args.commit:

        print(
            f"数据库：{archive.db_path}"
        )

    else:

        print(
            "数据库写入：禁止"
        )

    print("=" * 70)

    dates = list(
        iter_dates(
            start_date,
            end_date,
        )
    )

    stats.days_total = len(
        dates
    )

    for index, target_date in enumerate(
        dates,
        start=1,
    ):

        print(
            f"[{index}/{len(dates)}] "
            f"{target_date}"
        )

        try:

            records = fetch_one_day(
                target_date,
                stats,
            )

        except Exception as exc:

            stats.days_failed += 1

            failed_dates.append(
                (
                    target_date,
                    str(exc),
                )
            )

            print(
                "    RESULT: UNKNOWN / REQUEST FAILED"
            )

            print(
                f"    {exc}"
            )

            continue

        stats.days_success += 1

        routine_count = sum(
            1
            for record in records
            if (
                record.source_query_class
                == "ROUTINE"
            )
        )

        special_count = sum(
            1
            for record in records
            if (
                record.source_query_class
                == "SPECIAL"
            )
        )

        print(
            f"    Routine: {routine_count}"
        )

        print(
            f"    Special: {special_count}"
        )

        for record in records:

            message_counter[
                record.message_class
            ] += 1

            if not args.commit:
                continue

            archive_record = (
                RawArchiveRecord(
                    station=STATION,
                    source="IEM",
                    source_query_class=(
                        record
                        .source_query_class
                    ),
                    message_class=(
                        record
                        .message_class
                    ),
                    observation_time_utc=(
                        record
                        .observation_time_utc
                    ),
                    raw_metar=(
                        record
                        .raw_metar
                    ),
                    recovery_reason=None,
                )
            )

            inserted = archive.insert(
                archive_record
            )

            if inserted:
                stats.inserted += 1
            else:
                stats.duplicates += 1

        time.sleep(
            REQUEST_PAUSE_SECONDS
        )

    print()
    print("=" * 70)
    print("FINAL REPORT")
    print("=" * 70)

    print(
        f"Mode                  : {mode}"
    )

    print(
        f"Days Total            : {stats.days_total}"
    )

    print(
        f"Days Success          : {stats.days_success}"
    )

    print(
        f"Days Failed / UNKNOWN : {stats.days_failed}"
    )

    print(
        f"HTTP Requests         : {stats.http_requests}"
    )

    print(
        f"Routine Records       : {stats.routine_records}"
    )

    print(
        f"Special Records       : {stats.special_records}"
    )

    print(
        f"Total Source Records  : "
        f"{stats.routine_records + stats.special_records}"
    )

    print()

    print("Message Classes:")

    for key in sorted(
        message_counter
    ):

        print(
            f"    {key:<12}: "
            f"{message_counter[key]}"
        )

    if args.commit:

        print()

        print(
            f"Inserted              : {stats.inserted}"
        )

        print(
            f"Already Existing      : {stats.duplicates}"
        )

        print(
            f"Database Total        : {archive.count()}"
        )

    if failed_dates:

        print()
        print("FAILED / UNKNOWN DATES:")

        for failed_date, error in (
            failed_dates
        ):

            print(
                f"    {failed_date}: "
                f"{error}"
            )

    print("=" * 70)

    if stats.days_failed > 0:

        print(
            "RESULT: INCOMPLETE - "
            "FAILED DATES MUST BE RETRIED"
        )

        sys.exit(2)

    print(
        "RESULT: SOURCE DOWNLOAD COMPLETE"
    )


if __name__ == "__main__":
    main()