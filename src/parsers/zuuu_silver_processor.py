import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config.settings import DATABASE_PATH
from src.parsers.zuuu_metar_parser import parse_metar
from database.schema import ensure_schema, backup_database


PARSER_VERSION = "ZUUU_METAR_PARSER_V2"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


# ==========================================================
# 时间工具
# ==========================================================

def parse_iso_utc(value):
    """
    将数据库中的 UTC ISO 时间解析为带时区 datetime。
    """

    if not value:
        return None

    value = value.replace("Z", "+00:00")

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def utc_to_bjt_iso(value):
    """
    UTC ISO -> 北京时间 ISO
    """

    dt = parse_iso_utc(value)

    if dt is None:
        return None

    return dt.astimezone(LOCAL_TZ).isoformat()


# ==========================================================
# METAR 时间组 QC
# ==========================================================

def check_metar_time_group(parsed, observation_time_utc):
    """
    检查 METAR 内部 DDHHMMZ 是否与 obsTime 对应的
    observation_time_utc 一致。

    注意：
    METAR 时间组不负责推断年月。
    这里只做 day/hour/minute 的交叉验证。
    """

    if not observation_time_utc:
        return None, ["缺少 observation_time_utc"]

    if not parsed.get("metar_time_group"):
        return None, ["缺少 METAR 时间组"]

    observation_dt = parse_iso_utc(
        observation_time_utc
    )

    expected = (
        observation_dt.day,
        observation_dt.hour,
        observation_dt.minute,
    )

    actual = (
        parsed.get("metar_day"),
        parsed.get("metar_hour"),
        parsed.get("metar_minute"),
    )

    if actual == expected:
        return True, []

    warning = (
        "METAR时间组与observation_time_utc不一致："
        f"METAR={parsed.get('metar_time_group')}，"
        f"Observation={observation_dt.isoformat()}"
    )

    return False, [warning]


# ==========================================================
# 获取尚未处理的 Raw 数据
# ==========================================================

def get_unprocessed_raw_reports(connection):
    """
    只读取尚未进入 Silver 的 Raw 数据。

    使用 LEFT JOIN，保证重复运行不会重复处理。
    """

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            r.id,
            r.station_id,
            r.report_type,
            r.observation_time_utc,
            r.report_time_utc,
            r.receipt_time_utc,
            r.ingest_time_utc,
            r.raw_report

        FROM zuuu_raw_reports AS r

        LEFT JOIN zuuu_observations AS s
            ON s.raw_report_id = r.id

        WHERE s.id IS NULL OR s.parser_version != ?

        ORDER BY
            r.observation_time_utc ASC,
            r.id ASC;
        """, (PARSER_VERSION,)
    )

    return cursor.fetchall()


# ==========================================================
# 单条 Raw -> Silver
# ==========================================================

def process_raw_report(connection, raw_row):

    (
        raw_report_id,
        station_id,
        raw_report_type,
        observation_time_utc,
        report_time_utc,
        receipt_time_utc,
        ingest_time_utc,
        raw_report,
    ) = raw_row

    qc_warnings = []

    # ------------------------------------------------------
    # Parser
    # ------------------------------------------------------

    try:
        parsed = parse_metar(raw_report)
        parse_success = 1

    except Exception as error:
        parsed = {}
        parse_success = 0

        qc_warnings.append(
            f"Parser异常：{type(error).__name__}: {error}"
        )

    # ------------------------------------------------------
    # 时间 QC
    # ------------------------------------------------------

    if parse_success:

        time_match, time_warnings = (
            check_metar_time_group(
                parsed,
                observation_time_utc
            )
        )

        qc_warnings.extend(time_warnings)

    else:
        time_match = None

    # ------------------------------------------------------
    # Station QC
    # ------------------------------------------------------

    parsed_station = parsed.get("station_id")

    if (
        parse_success
        and parsed_station
        and parsed_station != station_id
    ):
        qc_warnings.append(
            "Parser机场代码与Raw机场代码不一致："
            f"Raw={station_id}，"
            f"Parser={parsed_station}"
        )

    # ------------------------------------------------------
    # Report Type QC
    # ------------------------------------------------------

    parsed_report_type = parsed.get(
        "report_type"
    )

    if (
        parse_success
        and raw_report_type
        and parsed_report_type
        and parsed_report_type != raw_report_type
    ):
        qc_warnings.append(
            "Parser报文类型与Raw报文类型不一致："
            f"Raw={raw_report_type}，"
            f"Parser={parsed_report_type}"
        )

    # ------------------------------------------------------
    # QC Status
    # ------------------------------------------------------

    if parse_success == 0:
        qc_status = "ERROR"

    elif qc_warnings:
        qc_status = "WARNING"

    else:
        qc_status = "OK"

    if parse_success:
        unknown = parsed.get("unparsed_tokens", [])
        if unknown:
            qc_warnings.append("Unsupported observed tokens: " + " ".join(unknown))
        for field in ("temperature_c", "dewpoint_c", "wind_speed_mps", "visibility_m", "qnh_hpa"):
            if parsed.get(field) is None:
                qc_warnings.append(f"Missing observation field: {field}")
        if qc_warnings:
            qc_status = "WARNING"

    # ------------------------------------------------------
    # 北京时间
    # ------------------------------------------------------

    observation_time_bjt = utc_to_bjt_iso(
        observation_time_utc
    )

    # ------------------------------------------------------
    # 9999 语义
    # ------------------------------------------------------

    visibility_is_10km_or_more = 0

    if parse_success:

        tokens = raw_report.split()

        if (
            "9999" in tokens
            or parsed.get("cavok") is True
        ):
            visibility_is_10km_or_more = 1

    # ------------------------------------------------------
    # JSON 字段
    # ------------------------------------------------------

    weather_json = json.dumps(
        parsed.get(
            "weather_phenomena",
            []
        ),
        ensure_ascii=False
    )

    cloud_json = json.dumps(
        parsed.get(
            "cloud_layers",
            []
        ),
        ensure_ascii=False
    )

    warnings_json = json.dumps(
        qc_warnings,
        ensure_ascii=False
    )

    # ------------------------------------------------------
    # Silver 创建时间
    # ------------------------------------------------------

    created_at_utc = datetime.now(
        timezone.utc
    ).isoformat()

    # ------------------------------------------------------
    # 写入 Silver
    # ------------------------------------------------------

    cursor = connection.cursor()
    # Derived rows are replaceable; the caller commits or rolls back the batch.
    cursor.execute("DELETE FROM zuuu_observations WHERE raw_report_id=?", (raw_report_id,))

    cursor.execute(
        """
        INSERT INTO zuuu_observations (

            raw_report_id,

            station_id,
            report_type,
            raw_report,

            observation_time_utc,
            observation_time_bjt,

            report_time_utc,
            receipt_time_utc,
            ingest_time_utc,

            metar_time_group,
            metar_day,
            metar_hour,
            metar_minute,

            wind_direction_deg,
            wind_speed_mps,
            wind_gust_mps,

            variable_wind,
            variable_wind_from,
            variable_wind_to,

            visibility_m,
            visibility_is_10km_or_more,
            cavok,

            temperature_c,
            dewpoint_c,

            qnh_hpa,

            weather_phenomena_json,
            cloud_layers_json,

            trend,

            is_corrected,
            is_auto,

            parse_success,
            time_group_matches_observation,

            qc_status,
            qc_warnings_json,

            parser_version,
            created_at_utc

        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        );
        """,

        (
            raw_report_id,

            station_id,
            raw_report_type,
            raw_report,

            observation_time_utc,
            observation_time_bjt,

            report_time_utc,
            receipt_time_utc,
            ingest_time_utc,

            parsed.get("metar_time_group"),
            parsed.get("metar_day"),
            parsed.get("metar_hour"),
            parsed.get("metar_minute"),

            parsed.get("wind_direction_deg"),
            parsed.get("wind_speed_mps"),
            parsed.get("wind_gust_mps"),

            int(
                bool(
                    parsed.get(
                        "variable_wind",
                        False
                    )
                )
            ),

            parsed.get("variable_wind_from"),
            parsed.get("variable_wind_to"),

            parsed.get("visibility_m"),
            visibility_is_10km_or_more,

            int(
                bool(
                    parsed.get(
                        "cavok",
                        False
                    )
                )
            ),

            parsed.get("temperature_c"),
            parsed.get("dewpoint_c"),

            parsed.get("qnh_hpa"),

            weather_json,
            cloud_json,

            parsed.get("trend"),

            int(
                bool(
                    parsed.get(
                        "is_corrected",
                        False
                    )
                )
            ),

            int(
                bool(
                    parsed.get(
                        "is_auto",
                        False
                    )
                )
            ),

            parse_success,

            (
                None
                if time_match is None
                else int(time_match)
            ),

            qc_status,
            warnings_json,

            PARSER_VERSION,
            created_at_utc,
        ),
    )

    return {
        "raw_report_id": raw_report_id,
        "silver_id": cursor.lastrowid,
        "qc_status": qc_status,
        "warnings": qc_warnings,
        "temperature_c": parsed.get(
            "temperature_c"
        ),
        "dewpoint_c": parsed.get(
            "dewpoint_c"
        ),
        "observation_time_utc":
            observation_time_utc,
        "observation_time_bjt":
            observation_time_bjt,
    }


# ==========================================================
# 主程序
# ==========================================================

def main():

    print(f"Database backup: {backup_database(DATABASE_PATH)}")

    print("=" * 72)
    print("ZUUU Raw -> Silver Processor V1")
    print("=" * 72)

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    try:

        connection.execute(
            "PRAGMA foreign_keys=ON;"
        )

        ensure_schema(connection)

        raw_rows = get_unprocessed_raw_reports(
            connection
        )

        print(
            f"待处理 Raw 记录：{len(raw_rows)}"
        )
        print()

        if not raw_rows:

            print("没有新的 Raw 数据需要处理。")
            print("Silver 数据保持不变。")

            return

        success_count = 0

        for raw_row in raw_rows:

            try:

                result = process_raw_report(
                    connection,
                    raw_row
                )

                success_count += 1

                print("-" * 72)

                print(
                    f"Raw ID："
                    f"{result['raw_report_id']}"
                )

                print(
                    f"Silver ID："
                    f"{result['silver_id']}"
                )

                print(
                    f"Observation UTC："
                    f"{result['observation_time_utc']}"
                )

                print(
                    f"Observation BJT："
                    f"{result['observation_time_bjt']}"
                )

                print(
                    f"Temperature："
                    f"{result['temperature_c']} °C"
                )

                print(
                    f"Dew Point："
                    f"{result['dewpoint_c']} °C"
                )

                print(
                    f"QC："
                    f"{result['qc_status']}"
                )

                if result["warnings"]:

                    print("QC Warnings：")

                    for warning in result[
                        "warnings"
                    ]:
                        print(
                            f"  - {warning}"
                        )

            except Exception as error:

                connection.rollback()

                print("-" * 72)

                print(
                    f"Raw ID {raw_row[0]} "
                    f"处理失败"
                )

                print(
                    f"{type(error).__name__}: "
                    f"{error}"
                )

                raise

        connection.commit()

        print()
        print("=" * 72)

        print(
            f"本次成功写入 Silver："
            f"{success_count} 条"
        )

        print(
            f"数据库：{DATABASE_PATH}"
        )

        print(
            f"Parser版本：{PARSER_VERSION}"
        )

        print("=" * 72)

    finally:

        connection.close()


if __name__ == "__main__":
    main()
