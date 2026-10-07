"""
ECMWF 7-RUN RAW TEMPERATURE FORENSICS
=====================================

只读取证：
1. 自动找出 temperature_2m_c 全部为 NULL 的 Raw Run
2. 读取对应 ecmwf_raw_runs.raw_json
3. 精确检查 hourly.temperature_2m
4. 输出类型、长度、NULL数、有效值数、前10/后10个值
5. 与 Hourly 表逐 Run 对照
6. 判断：
   A. SOURCE_TEMPERATURE_MISSING
   B. SOURCE_TEMPERATURE_PRESENT_PROCESSOR_LOSS
   C. SOURCE_TEMPERATURE_PARTIAL
   D. RAW_JSON_INVALID
   E. STRUCTURE_UNEXPECTED

绝不修改数据库。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(r"C:\ZUUU_Prediction_System")
DB_PATH = PROJECT_ROOT / "database" / "zuuu_prediction.db"

RAW_TABLE = "ecmwf_raw_runs"
HOURLY_TABLE = "ecmwf_hourly_forecasts"

WIDTH = 120


def hr(char="="):
    print(char * WIDTH)


def connect_read_only():

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    uri = DB_PATH.resolve().as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")

    return conn


def preview(values, n=10):

    if not isinstance(values, list):
        return repr(values)

    if len(values) <= n:
        return repr(values)

    return repr(values[:n])


def classify_temperature(value):

    if value is None:
        return {
            "classification": "SOURCE_TEMPERATURE_MISSING",
            "type": "NoneType",
            "length": None,
            "null_count": None,
            "valid_count": 0,
        }

    if not isinstance(value, list):
        return {
            "classification": "STRUCTURE_UNEXPECTED",
            "type": type(value).__name__,
            "length": None,
            "null_count": None,
            "valid_count": None,
        }

    null_count = sum(
        x is None
        for x in value
    )

    valid_count = len(value) - null_count

    if len(value) == 0:
        classification = "SOURCE_TEMPERATURE_MISSING"

    elif valid_count == 0:
        classification = "SOURCE_TEMPERATURE_MISSING"

    elif null_count > 0:
        classification = "SOURCE_TEMPERATURE_PARTIAL"

    else:
        classification = "SOURCE_TEMPERATURE_PRESENT"

    return {
        "classification": classification,
        "type": "list",
        "length": len(value),
        "null_count": null_count,
        "valid_count": valid_count,
    }


def main():

    hr()
    print("ECMWF 7-RUN RAW TEMPERATURE FORENSICS")
    print(f"Database : {DB_PATH}")
    print("Mode     : STRICT READ ONLY")
    hr()

    conn = connect_read_only()

    try:

        # ==========================================================
        # 自动找出所有“完整 Run 温度全部 NULL”
        # ==========================================================

        runs = conn.execute(
            f"""
            SELECT
                h.raw_run_id,
                h.run_time_utc,

                COUNT(*) AS hourly_rows,

                SUM(
                    CASE
                    WHEN h.temperature_2m_c IS NULL
                    THEN 1 ELSE 0
                    END
                ) AS temp_null_rows,

                SUM(
                    CASE
                    WHEN h.temperature_2m_c IS NOT NULL
                    THEN 1 ELSE 0
                    END
                ) AS temp_valid_rows

            FROM {HOURLY_TABLE} h

            GROUP BY
                h.raw_run_id,
                h.run_time_utc

            HAVING
                COUNT(*) > 0
                AND
                SUM(
                    CASE
                    WHEN h.temperature_2m_c IS NULL
                    THEN 1 ELSE 0
                    END
                ) = COUNT(*)

            ORDER BY
                h.run_time_utc,
                h.raw_run_id
            """
        ).fetchall()

        print()
        print(f"FULL TEMPERATURE-NULL RAW RUNS: {len(runs)}")
        print()

        total_source_missing = 0
        total_processor_loss = 0
        total_partial = 0
        total_invalid_json = 0
        total_unexpected = 0

        final_results = []

        # ==========================================================
        # 每个异常 Raw Run 取证
        # ==========================================================

        for index, run in enumerate(runs, 1):

            raw = conn.execute(
                f"""
                SELECT
                    id,
                    model,
                    api_model,
                    model_cycle,
                    run_time_utc,
                    source_available_time_utc,
                    availability_type,
                    ingest_time_utc,
                    source,
                    archive_type,
                    data_spec_version,
                    collector_version,
                    availability_rule_version,
                    content_sha256,
                    bronze_file_path,
                    raw_json

                FROM {RAW_TABLE}

                WHERE id = ?
                """,
                (run["raw_run_id"],),
            ).fetchone()

            hr("-")
            print(
                f"[{index}/{len(runs)}] "
                f"RUN {run['run_time_utc']}"
            )
            hr("-")

            if raw is None:

                print("ERROR: RAW ROW NOT FOUND")

                final_results.append(
                    (
                        run["run_time_utc"],
                        run["raw_run_id"],
                        "RAW_ROW_NOT_FOUND",
                    )
                )

                total_unexpected += 1
                continue

            print(f"Raw ID      : {raw['id']}")
            print(f"Model       : {raw['model']}")
            print(f"API Model   : {raw['api_model']}")
            print(f"Cycle       : {raw['model_cycle']}")
            print(f"Archive     : {raw['archive_type']}")
            print(f"Spec        : {raw['data_spec_version']}")
            print(f"Collector   : {raw['collector_version']}")
            print(f"Availability: {raw['availability_type']}")
            print(f"Rule        : {raw['availability_rule_version']}")
            print(f"SHA256      : {raw['content_sha256']}")
            print(f"Bronze      : {raw['bronze_file_path']}")

            print()
            print("HOURLY DATABASE")
            print(
                f"    rows        = {run['hourly_rows']}"
            )
            print(
                f"    temp NULL   = {run['temp_null_rows']}"
            )
            print(
                f"    temp valid  = {run['temp_valid_rows']}"
            )

            # ======================================================
            # RAW JSON
            # ======================================================

            try:
                payload = json.loads(
                    raw["raw_json"]
                )

            except Exception as exc:

                print()
                print("RAW JSON")
                print("    VALID       = NO")
                print(f"    ERROR       = {exc}")

                classification = "RAW_JSON_INVALID"

                total_invalid_json += 1

                final_results.append(
                    (
                        run["run_time_utc"],
                        raw["id"],
                        classification,
                    )
                )

                continue

            print()
            print("RAW JSON")
            print("    VALID       = YES")
            print(
                f"    ROOT TYPE   = "
                f"{type(payload).__name__}"
            )

            if isinstance(payload, dict):

                print(
                    f"    ROOT KEYS   = "
                    f"{list(payload.keys())}"
                )

            # ======================================================
            # EXACT PATH: hourly.temperature_2m
            # ======================================================

            hourly = None

            if isinstance(payload, dict):
                hourly = payload.get("hourly")

            print()
            print("PATH: root.hourly")

            if hourly is None:

                print("    EXISTS      = NO")

                temperature = None
                path_exists = False

            elif not isinstance(hourly, dict):

                print("    EXISTS      = YES")
                print(
                    f"    TYPE        = "
                    f"{type(hourly).__name__}"
                )

                temperature = None
                path_exists = False

            else:

                print("    EXISTS      = YES")
                print("    TYPE        = dict")
                print(
                    f"    KEYS        = "
                    f"{list(hourly.keys())}"
                )

                path_exists = (
                    "temperature_2m" in hourly
                )

                temperature = hourly.get(
                    "temperature_2m"
                )

            print()
            print("PATH: root.hourly.temperature_2m")
            print(
                f"    EXISTS      = "
                f"{'YES' if path_exists else 'NO'}"
            )

            info = classify_temperature(
                temperature
            )

            print(
                f"    TYPE        = "
                f"{info['type']}"
            )

            print(
                f"    LENGTH      = "
                f"{info['length']}"
            )

            print(
                f"    NULL COUNT  = "
                f"{info['null_count']}"
            )

            print(
                f"    VALID COUNT = "
                f"{info['valid_count']}"
            )

            if isinstance(temperature, list):

                print(
                    f"    FIRST 10    = "
                    f"{temperature[:10]}"
                )

                print(
                    f"    LAST 10     = "
                    f"{temperature[-10:]}"
                )

            else:

                print(
                    f"    RAW VALUE   = "
                    f"{repr(temperature)}"
                )

            # ======================================================
            # Check time array too
            # ======================================================

            time_values = None

            if isinstance(hourly, dict):
                time_values = hourly.get("time")

            print()
            print("PATH: root.hourly.time")

            if isinstance(time_values, list):

                print("    TYPE        = list")
                print(
                    f"    LENGTH      = "
                    f"{len(time_values)}"
                )
                print(
                    f"    FIRST       = "
                    f"{time_values[:3]}"
                )
                print(
                    f"    LAST        = "
                    f"{time_values[-3:]}"
                )

            else:

                print(
                    f"    VALUE       = "
                    f"{repr(time_values)}"
                )

            # ======================================================
            # Units
            # ======================================================

            units = None

            if isinstance(payload, dict):
                units = payload.get(
                    "hourly_units"
                )

            print()
            print("PATH: root.hourly_units.temperature_2m")

            if isinstance(units, dict):

                print(
                    f"    VALUE       = "
                    f"{repr(units.get('temperature_2m'))}"
                )

            else:

                print(
                    f"    VALUE       = "
                    f"{repr(units)}"
                )

            # ======================================================
            # FINAL CLASSIFICATION
            # ======================================================

            raw_class = info[
                "classification"
            ]

            if raw_class == "SOURCE_TEMPERATURE_PRESENT":

                if run["temp_valid_rows"] == 0:

                    classification = (
                        "SOURCE_TEMPERATURE_PRESENT_PROCESSOR_LOSS"
                    )

                    total_processor_loss += 1

                else:

                    classification = (
                        "SOURCE_AND_DATABASE_TEMPERATURE_PRESENT"
                    )

            elif raw_class == "SOURCE_TEMPERATURE_PARTIAL":

                classification = (
                    "SOURCE_TEMPERATURE_PARTIAL"
                )

                total_partial += 1

            elif raw_class == "SOURCE_TEMPERATURE_MISSING":

                classification = (
                    "SOURCE_TEMPERATURE_MISSING"
                )

                total_source_missing += 1

            else:

                classification = (
                    "STRUCTURE_UNEXPECTED"
                )

                total_unexpected += 1

            print()
            print(
                f"FINAL CLASSIFICATION: "
                f"{classification}"
            )

            # Critical length comparison
            if isinstance(
                temperature,
                list,
            ):

                if len(temperature) != run[
                    "hourly_rows"
                ]:

                    print(
                        "WARNING: RAW temperature length "
                        "does not equal processed hourly rows"
                    )

            final_results.append(
                (
                    run["run_time_utc"],
                    raw["id"],
                    classification,
                )
            )

        # ==========================================================
        # FINAL REPORT
        # ==========================================================

        print()
        hr("=")
        print("FINAL FORENSIC REPORT")
        hr("=")

        print(
            f"Abnormal Raw Runs       : "
            f"{len(runs)}"
        )

        print(
            f"Source Temp Missing     : "
            f"{total_source_missing}"
        )

        print(
            f"Processor Temp Loss     : "
            f"{total_processor_loss}"
        )

        print(
            f"Source Temp Partial     : "
            f"{total_partial}"
        )

        print(
            f"Invalid Raw JSON        : "
            f"{total_invalid_json}"
        )

        print(
            f"Unexpected Structure    : "
            f"{total_unexpected}"
        )

        print()
        print("PER RUN RESULT")

        for (
            run_time,
            raw_id,
            classification,
        ) in final_results:

            print(
                f"    {run_time} "
                f"| raw_id={raw_id} "
                f"| {classification}"
            )

        print()
        hr("=")

        # ==========================================================
        # DECISION
        # ==========================================================

        if total_processor_loss > 0:

            print(
                "RESULT: PROCESSOR DATA LOSS CONFIRMED"
            )

            print(
                "ACTION: PHASE 2 MUST NOT BE FROZEN."
            )

            print(
                "NEXT: Rebuild affected Hourly rows "
                "from immutable Raw JSON."
            )

        elif (
            total_invalid_json > 0
            or total_unexpected > 0
        ):

            print(
                "RESULT: RAW SOURCE STRUCTURE REQUIRES REVIEW"
            )

            print(
                "ACTION: PHASE 2 MUST NOT BE FROZEN YET."
            )

        elif (
            total_source_missing == len(runs)
            and len(runs) > 0
        ):

            print(
                "RESULT: ALL TEMPERATURE NULLS "
                "ORIGINATE IN RAW SOURCE DATA"
            )

            print(
                "ACTION: NO PROCESSOR REPAIR REQUIRED."
            )

            print(
                "STATUS: These runs may be classified as "
                "SOURCE TEMPERATURE UNAVAILABLE."
            )

        elif len(runs) == 0:

            print(
                "RESULT: NO FULL TEMPERATURE-NULL RUNS FOUND"
            )

        else:

            print(
                "RESULT: MIXED ROOT CAUSES"
            )

            print(
                "ACTION: REVIEW PER-RUN CLASSIFICATION."
            )

        hr("=")

        print("Database Modification: NONE")

    finally:

        conn.close()


if __name__ == "__main__":
    main()