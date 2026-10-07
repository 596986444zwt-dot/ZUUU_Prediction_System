import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings import DATABASE_PATH, ZUUU_BRONZE_DIR
from database.schema import ensure_schema


# ==========================================================
# ZUUU Raw Storage V1.2
# ==========================================================
#
# 正式模式：
#   Pipeline 明确传入本轮 Collector 生成的 Bronze 文件路径。
#
# 手动兼容模式：
#   如果没有传入路径，则自动选择最新 Bronze 文件。
#
# 核心原则：
#   1. Raw 永久保存
#   2. 不覆盖历史 Raw
#   3. 重复报文不重复入库
#   4. Observation / Report / Receipt / Ingest 时间分离
#   5. 明确保存 Bronze -> Raw 的追溯关系
# ==========================================================


def unix_timestamp_to_utc_iso(timestamp):
    """
    Unix timestamp -> UTC ISO 8601
    """
    if timestamp is None:
        return None

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc
    ).isoformat()


def find_latest_bronze_file():
    """
    手动运行 Raw Storage 时的兼容模式。

    正式 Pipeline 不依赖此函数。
    """

    if not ZUUU_BRONZE_DIR.exists():
        raise FileNotFoundError(
            f"Bronze目录不存在：{ZUUU_BRONZE_DIR}"
        )

    files = list(
        ZUUU_BRONZE_DIR.glob(
            "zuuu_metar_*.json"
        )
    )

    if not files:
        raise FileNotFoundError(
            f"Bronze目录没有ZUUU METAR文件："
            f"{ZUUU_BRONZE_DIR}"
        )

    return max(
        files,
        key=lambda path: path.stat().st_mtime_ns
    )


def resolve_bronze_file():
    """
    优先使用命令行明确传入的 Bronze 文件。

    示例：
    python zuuu_raw_storage.py "C:\\...\\zuuu_metar_xxx.json"
    """

    if len(sys.argv) >= 2:

        bronze_file = Path(
            sys.argv[1]
        ).resolve()

        mode = "指定文件模式"

    else:

        bronze_file = (
            find_latest_bronze_file()
        ).resolve()

        mode = "最新文件兼容模式"

    if not bronze_file.exists():
        raise FileNotFoundError(
            f"Bronze文件不存在：{bronze_file}"
        )

    if not bronze_file.is_file():
        raise ValueError(
            f"Bronze路径不是文件：{bronze_file}"
        )

    # 防止误传项目外其他 JSON
    expected_dir = (
        ZUUU_BRONZE_DIR.resolve()
    )

    try:
        bronze_file.relative_to(
            expected_dir
        )
    except ValueError:
        raise ValueError(
            "Bronze文件不在ZUUU Bronze目录中："
            f"{bronze_file}"
        )

    return bronze_file, mode


def load_bronze_file(bronze_file):
    """
    读取并验证 Bronze 原始 JSON。
    """

    with open(
        bronze_file,
        "r",
        encoding="utf-8"
    ) as file:
        raw_json_text = file.read()

    data = json.loads(
        raw_json_text
    )

    if not isinstance(data, list):
        raise ValueError(
            "Bronze JSON顶层结构不是列表"
        )

    if len(data) == 0:
        raise ValueError(
            "Bronze文件中没有METAR数据"
        )

    report = data[0]

    if not isinstance(report, dict):
        raise ValueError(
            "Bronze METAR数据格式异常"
        )

    if report.get("icaoId") != "ZUUU":
        raise ValueError(
            "Bronze数据不是ZUUU："
            f"{report.get('icaoId')}"
        )

    if not report.get("rawOb"):
        raise ValueError(
            "Bronze数据缺少rawOb"
        )

    return raw_json_text, report


def save_to_database(
    raw_json_text,
    report,
    bronze_file
):
    """
    Bronze -> Raw Database
    """

    station_id = report.get(
        "icaoId"
    )

    report_type = report.get(
        "metarType"
    )

    obs_time = report.get(
        "obsTime"
    )

    report_time_utc = report.get(
        "reportTime"
    )

    receipt_time_utc = report.get(
        "receiptTime"
    )

    raw_report = report.get(
        "rawOb"
    )

    observation_time_utc = (
        unix_timestamp_to_utc_iso(
            obs_time
        )
    )

    ingest_time_utc = datetime.now(
        timezone.utc
    ).isoformat()

    if not observation_time_utc:
        raise ValueError("Missing observation timestamp; refusing ambiguous Raw identity")

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    try:

        connection.execute("PRAGMA foreign_keys=ON")
        ensure_schema(connection)

        cursor = connection.cursor()

        try:

            cursor.execute(
                """
                INSERT INTO zuuu_raw_reports (
                    station_id,
                    report_type,
                    observation_time_utc,
                    report_time_utc,
                    receipt_time_utc,
                    ingest_time_utc,
                    source,
                    raw_report,
                    raw_json,
                    bronze_file_path,
                    created_at_utc
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                );
                """,
                (
                    station_id,
                    report_type,
                    observation_time_utc,
                    report_time_utc,
                    receipt_time_utc,
                    ingest_time_utc,
                    "Aviation Weather Center",
                    raw_report,
                    raw_json_text,
                    str(bronze_file),
                    ingest_time_utc,
                ),
            )

            connection.commit()

            return {
                "status": "inserted",
                "row_id": cursor.lastrowid,
            }

        except sqlite3.IntegrityError as error:

            if (
                "UNIQUE constraint failed"
                in str(error)
            ):

                connection.rollback()

                cursor.execute(
                    """
                    SELECT
                        id,
                        bronze_file_path
                    FROM zuuu_raw_reports
                    WHERE station_id = ?
                      AND observation_time_utc = ?
                      AND raw_report = ?
                    LIMIT 1;
                    """,
                    (
                        station_id,
                        observation_time_utc,
                        raw_report,
                    ),
                )

                existing_row = (
                    cursor.fetchone()
                )

                return {
                    "status": "duplicate",
                    "row_id": (
                        existing_row[0]
                        if existing_row
                        else None
                    ),
                    "existing_bronze_file": (
                        existing_row[1]
                        if existing_row
                        else None
                    ),
                }

            raise

    finally:

        connection.close()


def main():

    print("=" * 72)
    print("ZUUU Raw Storage V1.2")
    print("=" * 72)

    bronze_file, mode = (
        resolve_bronze_file()
    )

    print(
        f"运行模式：{mode}"
    )

    print(
        f"本次Bronze文件："
    )

    print(bronze_file)

    print()

    raw_json_text, report = (
        load_bronze_file(
            bronze_file
        )
    )

    observation_time_utc = (
        unix_timestamp_to_utc_iso(
            report.get("obsTime")
        )
    )

    print(
        f"机场：{report.get('icaoId')}"
    )

    print(
        f"报文类型："
        f"{report.get('metarType')}"
    )

    print()

    print("时间链：")

    print(
        "Observation Time UTC："
        f"{observation_time_utc}"
    )

    print(
        "Report Time UTC："
        f"{report.get('reportTime')}"
    )

    print(
        "Receipt Time UTC："
        f"{report.get('receiptTime')}"
    )

    print()

    print("Raw METAR：")
    print(
        report.get("rawOb")
    )

    print()

    result = save_to_database(
        raw_json_text,
        report,
        bronze_file
    )

    if result["status"] == "inserted":

        print("=" * 72)

        print(
            "SQLite Raw写入成功"
        )

        print(
            f"新Raw ID："
            f"{result['row_id']}"
        )

        print(
            f"Bronze追溯："
            f"{bronze_file}"
        )

        print("=" * 72)

    else:

        print("=" * 72)

        print(
            "检测到重复Raw报文"
        )

        print(
            "处理结果："
            "已跳过，不重复写入"
        )

        print(
            f"数据库已有Raw ID："
            f"{result['row_id']}"
        )

        print(
            "原Raw记录对应Bronze："
        )

        print(
            result.get(
                "existing_bronze_file"
            )
        )

        print("=" * 72)


if __name__ == "__main__":
    main()
