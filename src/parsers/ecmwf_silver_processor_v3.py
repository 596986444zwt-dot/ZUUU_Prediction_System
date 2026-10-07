from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# 基础配置
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if __package__ in (None, ""):
    # Preserve the existing direct-file CLI after adding shared validation.
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ecmwf_contract import validate_payload
from src.ecmwf_write_safety import assert_new_silver_run

DATABASE_PATH = PROJECT_ROOT / "database" / "zuuu_prediction.db"

PROCESSOR_VERSION = "ECMWF_SILVER_PROCESSOR_V3"

UTC_TZ = ZoneInfo("UTC")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

EXPECTED_HOURS = 72


# ============================================================
# Raw 18变量 -> Silver字段
# ============================================================

VARIABLE_MAP = {
    "temperature_2m": "temperature_2m_c",
    "dew_point_2m": "dew_point_2m_c",
    "relative_humidity_2m": "relative_humidity_2m_pct",
    "surface_pressure": "surface_pressure_hpa",
    "pressure_msl": "pressure_msl_hpa",
    "cloud_cover": "cloud_cover_pct",
    "cloud_cover_low": "cloud_cover_low_pct",
    "cloud_cover_mid": "cloud_cover_mid_pct",
    "cloud_cover_high": "cloud_cover_high_pct",
    "wind_speed_10m": "wind_speed_10m_kmh",
    "wind_direction_10m": "wind_direction_10m_deg",
    "wind_gusts_10m": "wind_gusts_10m_kmh",
    "shortwave_radiation": "shortwave_radiation_wm2",
    "direct_radiation": "direct_radiation_wm2",
    "diffuse_radiation": "diffuse_radiation_wm2",
    "precipitation": "precipitation_mm",
    "rain": "rain_mm",
    "cape": "cape_jkg",
}


EXPECTED_UNITS = {
    "temperature_2m": "°C",
    "dew_point_2m": "°C",
    "relative_humidity_2m": "%",
    "surface_pressure": "hPa",
    "pressure_msl": "hPa",
    "cloud_cover": "%",
    "cloud_cover_low": "%",
    "cloud_cover_mid": "%",
    "cloud_cover_high": "%",
    "wind_speed_10m": "km/h",
    "wind_direction_10m": "°",
    "wind_gusts_10m": "km/h",
    "shortwave_radiation": "W/m²",
    "direct_radiation": "W/m²",
    "diffuse_radiation": "W/m²",
    "precipitation": "mm",
    "rain": "mm",
    "cape": "J/kg",
}


# ============================================================
# 时间
# ============================================================

def parse_datetime(value):
    if value is None:
        return None

    text = str(value).strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dt = datetime.fromisoformat(text)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)

    return dt.astimezone(UTC_TZ)


def iso_utc(dt):
    return dt.astimezone(UTC_TZ).isoformat()


def kmh_to_ms(value):
    if value is None:
        return None

    return float(value) / 3.6


# ============================================================
# 数据库
# ============================================================

def connect_database(readonly=False):
    if readonly:
        uri = DATABASE_PATH.resolve().as_uri() + "?mode=ro"

        connection = sqlite3.connect(
            uri,
            uri=True,
        )
    else:
        connection = sqlite3.connect(
            DATABASE_PATH
        )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys=ON;"
    )

    return connection


def table_exists(connection, table_name):
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table'
          AND name=?;
        """,
        (table_name,),
    ).fetchone()

    return row is not None


# ============================================================
# Raw结构验证
# ============================================================

def validate_raw_payload(raw_row):
    data = json.loads(raw_row["raw_json"])
    hourly = validate_payload(data, raw_row["run_time_utc"], require_usable=False)
    return data, hourly, []


# ============================================================
# 72小时时间轴严格验证
# ============================================================

def validate_time_axis(raw_row, hourly):
    run_time = parse_datetime(
        raw_row["run_time_utc"]
    )

    if run_time is None:
        raise ValueError(
            "Raw缺少run_time_utc"
        )

    parsed_times = []

    for index, time_text in enumerate(
        hourly["time"]
    ):
        target = parse_datetime(
            time_text
        )

        expected = (
            run_time
            + timedelta(hours=index)
        )

        if target != expected:
            raise ValueError(
                f"时间轴异常 index={index}: "
                f"target={target.isoformat()} "
                f"expected={expected.isoformat()}"
            )

        parsed_times.append(target)

    if len(parsed_times) != EXPECTED_HOURS:
        raise ValueError(
            "时间轴不是72小时"
        )

    if parsed_times[0] != run_time:
        raise ValueError(
            "Lead 0目标时间不等于Run时间"
        )

    if (
        parsed_times[-1]
        != run_time
        + timedelta(hours=71)
    ):
        raise ValueError(
            "Lead 71目标时间异常"
        )

    return run_time, parsed_times


# ============================================================
# QC
# ============================================================

def build_qc_warnings(values, structure_warnings):
    warnings = list(
        structure_warnings
    )

    rh = values[
        "relative_humidity_2m"
    ]

    if (
        rh is not None
        and not 0 <= rh <= 100
    ):
        warnings.append(
            "relative_humidity_2m超出0-100%"
        )

    for field in (
        "cloud_cover",
        "cloud_cover_low",
        "cloud_cover_mid",
        "cloud_cover_high",
    ):
        value = values[field]

        if (
            value is not None
            and not 0 <= value <= 100
        ):
            warnings.append(
                f"{field}超出0-100%"
            )

    wind_direction = values[
        "wind_direction_10m"
    ]

    if (
        wind_direction is not None
        and not 0 <= wind_direction <= 360
    ):
        warnings.append(
            "wind_direction_10m超出0-360°"
        )

    cape = values["cape"]

    if (
        cape is not None
        and cape < 0
    ):
        # Raw/Silver保留原始负值。
        # 这里只做QC标记，不擅自改成0。
        warnings.append(
            "cape为负值"
        )

    precipitation = values[
        "precipitation"
    ]

    if (
        precipitation is not None
        and precipitation < 0
    ):
        warnings.append(
            "precipitation为负值"
        )

    rain = values["rain"]

    if (
        rain is not None
        and rain < 0
    ):
        warnings.append(
            "rain为负值"
        )

    return warnings


# ============================================================
# 先在内存中构造完整72行
# ============================================================

def build_silver_rows(raw_row):
    (
        data,
        hourly,
        structure_warnings,
    ) = validate_raw_payload(
        raw_row
    )

    (
        run_time,
        parsed_times,
    ) = validate_time_axis(
        raw_row,
        hourly,
    )

    raw_run_id = raw_row["id"]

    model = raw_row["model"]
    model_cycle = raw_row["model_cycle"]

    source_available_time = (
        raw_row[
            "source_available_time_utc"
        ]
    )

    availability_type = (
        raw_row[
            "availability_type"
        ]
    )

    ingest_time = raw_row[
        "ingest_time_utc"
    ]

    grid_latitude = data.get(
        "latitude"
    )

    grid_longitude = data.get(
        "longitude"
    )

    grid_elevation = data.get(
        "elevation"
    )

    rows = []

    for index in range(
        EXPECTED_HOURS
    ):
        target_time = (
            parsed_times[index]
        )

        target_time_bjt = (
            target_time.astimezone(
                LOCAL_TZ
            )
        )

        values = {
            field:
                hourly[field][index]
            for field in VARIABLE_MAP
        }

        row_warnings = (
            build_qc_warnings(
                values,
                structure_warnings,
            )
        )

        qc_status = (
            "WARNING"
            if row_warnings
            else "OK"
        )

        # New rows retain source NULLs, but missing core data cannot be QC OK.
        missing = [name for name in ("temperature_2m", "dew_point_2m", "wind_speed_10m")
                   if values[name] is None]
        row_warnings.extend(f"Missing core variable: {name}" for name in missing)
        if all(value is None for value in values.values()):
            qc_status = "ERROR"
            row_warnings.append("All 18 weather variables are null")
        elif missing:
            qc_status = "WARNING"

        wind_speed_kmh = values[
            "wind_speed_10m"
        ]

        wind_gust_kmh = values[
            "wind_gusts_10m"
        ]

        created_at = (
            datetime.now(
                UTC_TZ
            ).isoformat()
        )

        row = {
            "raw_run_id":
                raw_run_id,

            "model":
                model,

            "model_cycle":
                model_cycle,

            "run_time_utc":
                iso_utc(
                    run_time
                ),

            "source_available_time_utc":
                source_available_time,

            "availability_type":
                availability_type,

            "ingest_time_utc":
                ingest_time,

            "target_time_utc":
                iso_utc(
                    target_time
                ),

            "target_time_bjt":
                target_time_bjt.isoformat(),

            "lead_hours":
                index,

            "grid_latitude":
                grid_latitude,

            "grid_longitude":
                grid_longitude,

            "grid_elevation_m":
                grid_elevation,

            "temperature_2m_c":
                values[
                    "temperature_2m"
                ],

            "dew_point_2m_c":
                values[
                    "dew_point_2m"
                ],

            "relative_humidity_2m_pct":
                values[
                    "relative_humidity_2m"
                ],

            "surface_pressure_hpa":
                values[
                    "surface_pressure"
                ],

            "pressure_msl_hpa":
                values[
                    "pressure_msl"
                ],

            "cloud_cover_pct":
                values[
                    "cloud_cover"
                ],

            "wind_speed_10m_kmh":
                wind_speed_kmh,

            "wind_speed_10m_ms":
                kmh_to_ms(
                    wind_speed_kmh
                ),

            "wind_direction_10m_deg":
                values[
                    "wind_direction_10m"
                ],

            "parse_success":
                1,

            "qc_status":
                qc_status,

            "qc_warnings_json":
                json.dumps(
                    row_warnings,
                    ensure_ascii=False,
                ),

            "processor_version":
                PROCESSOR_VERSION,

            "created_at_utc":
                created_at,

            "cloud_cover_low_pct":
                values[
                    "cloud_cover_low"
                ],

            "cloud_cover_mid_pct":
                values[
                    "cloud_cover_mid"
                ],

            "cloud_cover_high_pct":
                values[
                    "cloud_cover_high"
                ],

            "wind_gusts_10m_kmh":
                wind_gust_kmh,

            "wind_gusts_10m_ms":
                kmh_to_ms(
                    wind_gust_kmh
                ),

            "shortwave_radiation_wm2":
                values[
                    "shortwave_radiation"
                ],

            "direct_radiation_wm2":
                values[
                    "direct_radiation"
                ],

            "diffuse_radiation_wm2":
                values[
                    "diffuse_radiation"
                ],

            "precipitation_mm":
                values[
                    "precipitation"
                ],

            "rain_mm":
                values[
                    "rain"
                ],

            "cape_jkg":
                values[
                    "cape"
                ],
        }

        rows.append(row)

    # ========================================================
    # 写数据库之前，完整验证内存结果
    # ========================================================

    if len(rows) != 72:
        raise RuntimeError(
            "内存Silver结果不是72行"
        )

    leads = [
        row["lead_hours"]
        for row in rows
    ]

    if leads != list(range(72)):
        raise RuntimeError(
            "内存Silver Lead不是严格0-71"
        )

    targets = [
        parse_datetime(
            row["target_time_utc"]
        )
        for row in rows
    ]

    for index in range(72):
        expected = (
            run_time
            + timedelta(hours=index)
        )

        if targets[index] != expected:
            raise RuntimeError(
                "内存Silver时间轴验证失败"
            )

    return rows


# ============================================================
# 检查一个Raw是否已经拥有完整Silver
# ============================================================

def inspect_existing_silver(
    connection,
    raw_run_id,
):
    rows = connection.execute(
        """
        SELECT
            lead_hours,
            target_time_utc
        FROM ecmwf_hourly_forecasts
        WHERE raw_run_id=?
        ORDER BY lead_hours ASC;
        """,
        (raw_run_id,),
    ).fetchall()

    if not rows:
        return "MISSING"

    if len(rows) != 72:
        return "INCOMPLETE"

    leads = [
        row["lead_hours"]
        for row in rows
    ]

    if leads != list(range(72)):
        return "INCOMPLETE"

    raw = connection.execute(
        """
        SELECT run_time_utc
        FROM ecmwf_raw_runs
        WHERE id=?;
        """,
        (raw_run_id,),
    ).fetchone()

    if raw is None:
        return "INCOMPLETE"

    run_time = parse_datetime(
        raw["run_time_utc"]
    )

    for index, row in enumerate(rows):
        target = parse_datetime(
            row["target_time_utc"]
        )

        expected = (
            run_time
            + timedelta(hours=index)
        )

        if target != expected:
            return "INCOMPLETE"

    return "COMPLETE"


# ============================================================
# 获取目标Raw
# ============================================================

def get_candidate_raw_runs(
    connection,
):
    rows = connection.execute(
        """
        SELECT r.*
        FROM ecmwf_raw_runs r
        WHERE
            r.api_model='ecmwf_ifs'
            AND r.archive_type='historical'
            AND r.data_spec_version=
                'ECMWF_18_VARIABLE_V2'
        ORDER BY r.run_time_utc ASC, r.id ASC;
        """
    ).fetchall()

    return rows


# ============================================================
# Silver INSERT
# ============================================================

INSERT_SQL = """
INSERT INTO ecmwf_hourly_forecasts
(
    raw_run_id,
    model,
    model_cycle,
    run_time_utc,
    source_available_time_utc,
    availability_type,
    ingest_time_utc,
    target_time_utc,
    target_time_bjt,
    lead_hours,
    grid_latitude,
    grid_longitude,
    grid_elevation_m,
    temperature_2m_c,
    dew_point_2m_c,
    relative_humidity_2m_pct,
    surface_pressure_hpa,
    pressure_msl_hpa,
    cloud_cover_pct,
    wind_speed_10m_kmh,
    wind_speed_10m_ms,
    wind_direction_10m_deg,
    parse_success,
    qc_status,
    qc_warnings_json,
    processor_version,
    created_at_utc,
    cloud_cover_low_pct,
    cloud_cover_mid_pct,
    cloud_cover_high_pct,
    wind_gusts_10m_kmh,
    wind_gusts_10m_ms,
    shortwave_radiation_wm2,
    direct_radiation_wm2,
    diffuse_radiation_wm2,
    precipitation_mm,
    rain_mm,
    cape_jkg
)
VALUES
(
    :raw_run_id,
    :model,
    :model_cycle,
    :run_time_utc,
    :source_available_time_utc,
    :availability_type,
    :ingest_time_utc,
    :target_time_utc,
    :target_time_bjt,
    :lead_hours,
    :grid_latitude,
    :grid_longitude,
    :grid_elevation_m,
    :temperature_2m_c,
    :dew_point_2m_c,
    :relative_humidity_2m_pct,
    :surface_pressure_hpa,
    :pressure_msl_hpa,
    :cloud_cover_pct,
    :wind_speed_10m_kmh,
    :wind_speed_10m_ms,
    :wind_direction_10m_deg,
    :parse_success,
    :qc_status,
    :qc_warnings_json,
    :processor_version,
    :created_at_utc,
    :cloud_cover_low_pct,
    :cloud_cover_mid_pct,
    :cloud_cover_high_pct,
    :wind_gusts_10m_kmh,
    :wind_gusts_10m_ms,
    :shortwave_radiation_wm2,
    :direct_radiation_wm2,
    :diffuse_radiation_wm2,
    :precipitation_mm,
    :rain_mm,
    :cape_jkg
);
"""


# ============================================================
# 单Run事务处理
# ============================================================

def process_raw_run(
    connection,
    raw_row,
):
    raw_run_id = raw_row["id"]

    # --------------------------------------------------------
    # 第一步：
    # 在事务外解析并严格验证。
    # 此阶段不修改Silver。
    # --------------------------------------------------------

    silver_rows = build_silver_rows(
        raw_row
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE;"
        )

        # ----------------------------------------------------
        # 再次确认，防止运行过程中状态变化
        # ----------------------------------------------------

        assert_new_silver_run(connection, raw_run_id)
        existing = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM ecmwf_hourly_forecasts
            WHERE raw_run_id=?;
            """,
            (raw_run_id,),
        ).fetchone()["total"]

        if existing != 0:
            raise RuntimeError(
                f"Raw {raw_run_id}在事务开始后"
                f"发现已有{existing}条Silver，"
                "拒绝覆盖"
            )

        connection.executemany(
            INSERT_SQL,
            silver_rows,
        )

        # ----------------------------------------------------
        # COMMIT前数据库级最终验证
        # ----------------------------------------------------

        db_rows = connection.execute(
            """
            SELECT
                lead_hours,
                target_time_utc
            FROM ecmwf_hourly_forecasts
            WHERE raw_run_id=?
            ORDER BY lead_hours ASC;
            """,
            (raw_run_id,),
        ).fetchall()

        if len(db_rows) != 72:
            raise RuntimeError(
                f"数据库Silver行数="
                f"{len(db_rows)}，预期72"
            )

        run_time = parse_datetime(
            raw_row["run_time_utc"]
        )

        for index, row in enumerate(
            db_rows
        ):
            if row["lead_hours"] != index:
                raise RuntimeError(
                    "数据库Lead不是严格0-71"
                )

            target = parse_datetime(
                row["target_time_utc"]
            )

            expected = (
                run_time
                + timedelta(hours=index)
            )

            if target != expected:
                raise RuntimeError(
                    "数据库Target时间轴异常"
                )

        # ----------------------------------------------------
        # 所有验证通过，最后才提交
        # ----------------------------------------------------

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    warning_count = sum(
        1
        for row in silver_rows
        if row["qc_status"] == "WARNING"
    )

    return {
        "inserted": 72,
        "warning_rows": warning_count,
    }


# ============================================================
# PLAN
# ============================================================

def build_plan(
    connection,
):
    raw_runs = get_candidate_raw_runs(
        connection
    )

    complete = []
    missing = []
    incomplete = []

    for raw_row in raw_runs:
        status = inspect_existing_silver(
            connection,
            raw_row["id"],
        )

        if status == "COMPLETE":
            complete.append(
                raw_row
            )

        elif status == "MISSING":
            missing.append(
                raw_row
            )

        else:
            incomplete.append(
                raw_row
            )

    return {
        "raw_runs": raw_runs,
        "complete": complete,
        "missing": missing,
        "incomplete": incomplete,
    }


# ============================================================
# CLI
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "ECMWF Historical Raw -> "
            "Silver Processor V3"
        )
    )

    mode = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    mode.add_argument(
        "--plan",
        action="store_true",
    )

    mode.add_argument(
        "--run",
        action="store_true",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    args = parser.parse_args()

    if (
        args.limit is not None
        and args.limit < 1
    ):
        parser.error(
            "--limit必须大于0"
        )

    return args


# ============================================================
# MAIN
# ============================================================

def main():
    args = parse_args()

    if not DATABASE_PATH.exists():
        print(
            f"[FATAL] 数据库不存在："
            f"{DATABASE_PATH}"
        )
        return 1

    connection = connect_database(
        readonly=args.plan
    )

    try:
        if not table_exists(
            connection,
            "ecmwf_raw_runs",
        ):
            print(
                "[FATAL] ecmwf_raw_runs不存在"
            )
            return 1

        if not table_exists(
            connection,
            "ecmwf_hourly_forecasts",
        ):
            print(
                "[FATAL] "
                "ecmwf_hourly_forecasts不存在"
            )
            return 1

        print(
            "=" * 80
        )

        print(
            "ECMWF Historical Raw "
            "-> Silver Processor V3"
        )

        print(
            "=" * 80
        )

        plan = build_plan(
            connection
        )

        print(
            f"Historical Raw总数      : "
            f"{len(plan['raw_runs'])}"
        )

        print(
            f"完整Silver Run          : "
            f"{len(plan['complete'])}"
        )

        print(
            f"尚未Silver化            : "
            f"{len(plan['missing'])}"
        )

        print(
            f"不完整/异常Silver Run   : "
            f"{len(plan['incomplete'])}"
        )

        # ====================================================
        # 不自动删除或修复旧Silver
        # ====================================================

        if plan["incomplete"]:
            print()
            print(
                "[STOP] 发现不完整或异常Silver。"
            )
            print(
                "为保护现有数据，V3不会自动删除。"
            )

            print(
                "异常Raw ID："
            )

            for row in plan[
                "incomplete"
            ][:30]:
                print(
                    f"  Raw ID "
                    f"{row['id']} | "
                    f"{row['run_time_utc']}"
                )

            return 2

        if args.plan:
            print()
            print(
                "[PLAN ONLY] "
                "没有修改任何数据。"
            )
            return 0

        selected = (
            plan["missing"][
                :args.limit
            ]
            if args.limit
            else plan["missing"]
        )

        print()
        print(
            f"本次准备处理          : "
            f"{len(selected)} Run"
        )

        if not selected:
            print(
                "没有需要处理的Raw Run。"
            )
            return 0

        success = 0
        failed = 0
        inserted_total = 0
        warning_total = 0

        for index, raw_row in enumerate(
            selected,
            1,
        ):
            print(
                f"[{index}/{len(selected)}] "
                f"Raw ID={raw_row['id']} "
                f"Run={raw_row['run_time_utc']}"
            )

            try:
                result = process_raw_run(
                    connection,
                    raw_row,
                )

                success += 1

                inserted_total += (
                    result["inserted"]
                )

                warning_total += (
                    result[
                        "warning_rows"
                    ]
                )

                print(
                    f"  PASS | "
                    f"72 rows | "
                    f"QC WARNING="
                    f"{result['warning_rows']}"
                )

            except Exception as exc:
                failed += 1

                print(
                    f"  FAIL | "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                # V3遇到真正失败立即停止，
                # 避免批量制造问题。
                break

        print()
        print(
            "=" * 80
        )

        print(
            "SILVER V3 RESULT"
        )

        print(
            "=" * 80
        )

        print(
            f"Success Run             : "
            f"{success}"
        )

        print(
            f"Failed Run              : "
            f"{failed}"
        )

        print(
            f"Inserted Silver Rows    : "
            f"{inserted_total}"
        )

        print(
            f"QC Warning Rows         : "
            f"{warning_total}"
        )

        print(
            f"Processor               : "
            f"{PROCESSOR_VERSION}"
        )

        return (
            1
            if failed
            else 0
        )

    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(
        main()
    )
