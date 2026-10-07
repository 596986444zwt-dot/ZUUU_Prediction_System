"""
ECMWF ISSUE MAPPING V1 AUDIT
============================

目的：
    为 T0 / T+1 / T+2 建立正式 Issue Mapping Rule 之前，
    对冻结后的 ZUUU_TARGET_V1 与 ECMWF_ARCHIVE_V1 做一次完整映射审计。

原则：
    1. READ ONLY
    2. 不修改 ZUUU_TARGET_V1
    3. 不修改 ECMWF_ARCHIVE_V1
    4. 不创建训练集
    5. 不使用未来 Run
    6. historical official_schedule_estimate 只作为估计发布时间
    7. 检查每个目标日的候选 Run
    8. 检查北京时间自然日 00:00-23:00 是否完整被预报覆盖
    9. 分别审计 T0 / T+1 / T+2
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "database" / "zuuu_prediction.db"

BJT = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc

TARGET_TABLE = "zuuu_target_v1"
ARCHIVE_TABLE = "ecmwf_archive_v1"
HOURLY_TABLE = "ecmwf_hourly_forecasts"

WIDTH = 118


def hr(char="="):
    print(char * WIDTH)


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    return dt.astimezone(UTC)


def connect_read_only():
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")

    return conn


def load_targets(conn):
    return conn.execute(
        f"""
        SELECT
            business_date_bjt,
            daily_tmax_c
        FROM {TARGET_TABLE}
        WHERE archive_status IS NULL
           OR 1 = 1
        ORDER BY business_date_bjt
        """
    ).fetchall()


def load_archive(conn):
    rows = conn.execute(
        f"""
        SELECT *
        FROM {ARCHIVE_TABLE}
        WHERE archive_status = 'FROZEN'
        ORDER BY run_time_utc
        """
    ).fetchall()

    result = []

    for row in rows:
        d = dict(row)
        d["_run_dt"] = parse_dt(
            row["run_time_utc"]
        )

        if row["source_available_time_utc"]:
            d["_available_dt"] = parse_dt(
                row["source_available_time_utc"]
            )
        else:
            d["_available_dt"] = None

        result.append(d)

    return result


def hourly_times_for_raw(conn, raw_id):
    rows = conn.execute(
        f"""
        SELECT
            target_time_utc,
            temperature_2m_c
        FROM {HOURLY_TABLE}
        WHERE raw_run_id = ?
        ORDER BY target_time_utc
        """,
        (raw_id,),
    ).fetchall()

    result = {}

    for row in rows:
        dt = parse_dt(
            row["target_time_utc"]
        )

        result[dt] = row["temperature_2m_c"]

    return result


def target_day_window(target_date):
    """
    北京时间自然日：
        00:00 BJT -> next day 00:00 BJT

    转成 UTC：
        previous day 16:00 UTC -> target day 16:00 UTC
    """

    start_bjt = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        0,
        0,
        tzinfo=BJT,
    )

    end_bjt = start_bjt + timedelta(days=1)

    return (
        start_bjt.astimezone(UTC),
        end_bjt.astimezone(UTC),
    )


def issue_cutoff(target_date, horizon):
    """
    诊断版 Issue Cutoff。

    这里故意采用非常清楚的业务定义：

    T0:
        目标日北京时间 00:00 前已经可用的数据

    T+1:
        目标日前一天北京时间 00:00 前已经可用的数据

    T+2:
        目标日前两天北京时间 00:00 前已经可用的数据

    注意：
        此处只是 AUDIT CANDIDATE RULE，
        尚未冻结为最终规则。
    """

    target_midnight = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        0,
        0,
        tzinfo=BJT,
    )

    cutoff_bjt = (
        target_midnight
        - timedelta(days=horizon)
    )

    return cutoff_bjt.astimezone(UTC)


def select_latest_candidate(
    archive,
    cutoff_utc,
):
    """
    只允许：
        available_time <= cutoff

    然后选择 available_time 最新的 canonical run。

    SOURCE_TEMPERATURE_UNAVAILABLE 不作为有效候选。
    """

    candidates = []

    for row in archive:

        if row["canonical_status"] != "AVAILABLE":
            continue

        available = row["_available_dt"]

        if available is None:
            continue

        if available <= cutoff_utc:
            candidates.append(row)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["_available_dt"],
            x["_run_dt"],
        )
    )

    return candidates[-1]


def coverage_for_target_day(
    conn,
    raw_id,
    target_date,
):
    hourly = hourly_times_for_raw(
        conn,
        raw_id,
    )

    start_utc, end_utc = target_day_window(
        target_date
    )

    expected = [
        start_utc + timedelta(hours=i)
        for i in range(24)
    ]

    present = 0
    valid_temp = 0
    missing_times = []
    null_temp_times = []

    for dt in expected:

        if dt not in hourly:
            missing_times.append(dt)
            continue

        present += 1

        if hourly[dt] is None:
            null_temp_times.append(dt)
        else:
            valid_temp += 1

    return {
        "present": present,
        "valid_temp": valid_temp,
        "missing_times": missing_times,
        "null_temp_times": null_temp_times,
        "complete": (
            present == 24
            and valid_temp == 24
        ),
    }


def audit_horizon(
    conn,
    targets,
    archive,
    horizon,
):
    label = (
        "T0"
        if horizon == 0
        else f"T+{horizon}"
    )

    results = []

    for target in targets:

        target_date = datetime.strptime(
            target["business_date_bjt"],
            "%Y-%m-%d",
        ).date()

        cutoff = issue_cutoff(
            target_date,
            horizon,
        )

        candidate = select_latest_candidate(
            archive,
            cutoff,
        )

        if candidate is None:

            results.append(
                {
                    "target_date": target_date,
                    "cutoff": cutoff,
                    "candidate": None,
                    "coverage": None,
                    "status": "NO_CANDIDATE",
                }
            )

            continue

        coverage = coverage_for_target_day(
            conn,
            candidate[
                "canonical_raw_run_id"
            ],
            target_date,
        )

        if coverage["complete"]:
            status = "COMPLETE"
        elif coverage["present"] == 0:
            status = "NO_TARGET_DAY_COVERAGE"
        elif coverage["valid_temp"] == 0:
            status = "NO_VALID_TEMPERATURE"
        else:
            status = "PARTIAL_COVERAGE"

        results.append(
            {
                "target_date": target_date,
                "cutoff": cutoff,
                "candidate": candidate,
                "coverage": coverage,
                "status": status,
            }
        )

    print()
    hr()
    print(
        f"{label} ISSUE MAPPING AUDIT"
    )
    hr()

    counts = Counter(
        x["status"]
        for x in results
    )

    print(
        f"Target Days             : "
        f"{len(results)}"
    )

    for status in sorted(counts):
        print(
            f"{status:<24}: "
            f"{counts[status]}"
        )

    complete = counts.get(
        "COMPLETE",
        0,
    )

    print(
        f"Complete Rate           : "
        f"{complete}/{len(results)} "
        f"({complete / len(results) * 100:.2f}%)"
    )

    print()
    print("NON-COMPLETE DAYS")

    bad = [
        x
        for x in results
        if x["status"] != "COMPLETE"
    ]

    if not bad:
        print("    NONE")

    for item in bad:

        print()
        print(
            f"    Target Date : "
            f"{item['target_date']}"
        )

        print(
            f"    Status      : "
            f"{item['status']}"
        )

        print(
            f"    Cutoff UTC  : "
            f"{item['cutoff'].isoformat()}"
        )

        candidate = item["candidate"]

        if candidate:

            print(
                f"    Run UTC     : "
                f"{candidate['run_time_utc']}"
            )

            print(
                f"    Available   : "
                f"{candidate['source_available_time_utc']}"
            )

            print(
                f"    Avail Type  : "
                f"{candidate['availability_type']}"
            )

            print(
                f"    Semantics   : "
                f"{candidate['availability_semantics']}"
            )

            print(
                f"    Raw ID      : "
                f"{candidate['canonical_raw_run_id']}"
            )

            coverage = item["coverage"]

            print(
                f"    Hours       : "
                f"{coverage['present']}/24"
            )

            print(
                f"    Valid Temp  : "
                f"{coverage['valid_temp']}/24"
            )

    # ---------------------------------------------------------
    # Lead-time statistics
    # ---------------------------------------------------------

    lead_hours = []

    availability_lag = []

    estimated_availability_count = 0
    observed_availability_count = 0

    for item in results:

        candidate = item["candidate"]

        if candidate is None:
            continue

        target_start, _ = target_day_window(
            item["target_date"]
        )

        lead = (
            target_start
            - candidate["_run_dt"]
        ).total_seconds() / 3600

        lead_hours.append(lead)

        if candidate["_available_dt"]:
            lag = (
                candidate["_available_dt"]
                - candidate["_run_dt"]
            ).total_seconds() / 3600

            availability_lag.append(lag)

        if (
            candidate["availability_semantics"]
            == "ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED"
        ):
            estimated_availability_count += 1

        if (
            candidate["availability_semantics"]
            == "OBSERVED_INGEST_TIME"
        ):
            observed_availability_count += 1

    print()
    print("MAPPING STATISTICS")

    if lead_hours:
        print(
            f"    Min Lead Hours       : "
            f"{min(lead_hours):.2f}"
        )
        print(
            f"    Max Lead Hours       : "
            f"{max(lead_hours):.2f}"
        )
        print(
            f"    Mean Lead Hours      : "
            f"{sum(lead_hours)/len(lead_hours):.2f}"
        )

    if availability_lag:
        print(
            f"    Min Availability Lag : "
            f"{min(availability_lag):.2f}"
        )
        print(
            f"    Max Availability Lag : "
            f"{max(availability_lag):.2f}"
        )

    print(
        f"    Estimated Availability: "
        f"{estimated_availability_count}"
    )

    print(
        f"    Observed Availability : "
        f"{observed_availability_count}"
    )

    return results


def main():

    hr()
    print("ECMWF ISSUE MAPPING V1 AUDIT")
    print(f"Database : {DB_PATH}")
    print("Mode     : STRICT READ ONLY")
    hr()

    if not DB_PATH.exists():
        raise FileNotFoundError(
            DB_PATH
        )

    conn = connect_read_only()

    try:

        # ------------------------------------------------------
        # Verify frozen inputs
        # ------------------------------------------------------

        target_count = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {TARGET_TABLE}
            WHERE target_status = 'FROZEN'
            """
        ).fetchone()[0]

        archive_count = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {ARCHIVE_TABLE}
            WHERE archive_status = 'FROZEN'
            """
        ).fetchone()[0]

        print()
        print("FROZEN INPUTS")
        print(
            f"    ZUUU_TARGET_V1       : "
            f"{target_count}"
        )
        print(
            f"    ECMWF_ARCHIVE_V1     : "
            f"{archive_count}"
        )

        if target_count != 729:
            raise RuntimeError(
                "ZUUU_TARGET_V1 expected 729 frozen rows."
            )

        if archive_count == 0:
            raise RuntimeError(
                "ECMWF_ARCHIVE_V1 is empty."
            )

        targets = conn.execute(
            f"""
            SELECT
                business_date_bjt,
                daily_tmax_c
            FROM {TARGET_TABLE}
            WHERE target_status = 'FROZEN'
            ORDER BY business_date_bjt
            """
        ).fetchall()

        archive = load_archive(
            conn
        )

        # ------------------------------------------------------
        # Audit T0 / T+1 / T+2
        # ------------------------------------------------------

        all_results = {}

        for horizon in (0, 1, 2):

            all_results[horizon] = (
                audit_horizon(
                    conn,
                    targets,
                    archive,
                    horizon,
                )
            )

        # ------------------------------------------------------
        # Cross-window integrity
        # ------------------------------------------------------

        print()
        hr()
        print("CROSS-WINDOW INTEGRITY")
        hr()

        leakage_errors = 0
        chronology_errors = 0

        for horizon, results in all_results.items():

            for item in results:

                candidate = item[
                    "candidate"
                ]

                if candidate is None:
                    continue

                available = candidate[
                    "_available_dt"
                ]

                cutoff = item["cutoff"]

                if (
                    available is not None
                    and available > cutoff
                ):
                    leakage_errors += 1

        # T+2 should never use a later availability
        # than T+1; T+1 should never use later than T0.

        for i in range(len(targets)):

            selected = []

            for horizon in (0, 1, 2):

                candidate = all_results[
                    horizon
                ][i]["candidate"]

                if candidate:
                    selected.append(
                        (
                            horizon,
                            candidate[
                                "_available_dt"
                            ],
                        )
                    )

            by_horizon = {
                h: dt
                for h, dt in selected
            }

            if (
                0 in by_horizon
                and 1 in by_horizon
                and by_horizon[1]
                > by_horizon[0]
            ):
                chronology_errors += 1

            if (
                1 in by_horizon
                and 2 in by_horizon
                and by_horizon[2]
                > by_horizon[1]
            ):
                chronology_errors += 1

        print(
            f"Availability Leakage Errors : "
            f"{leakage_errors}"
        )

        print(
            f"Horizon Chronology Errors    : "
            f"{chronology_errors}"
        )

        # ------------------------------------------------------
        # Final report
        # ------------------------------------------------------

        print()
        hr()
        print("FINAL REPORT")
        hr()

        for horizon in (0, 1, 2):

            label = (
                "T0"
                if horizon == 0
                else f"T+{horizon}"
            )

            results = all_results[
                horizon
            ]

            complete = sum(
                x["status"]
                == "COMPLETE"
                for x in results
            )

            print(
                f"{label:<5} Complete Mapping : "
                f"{complete}/{len(results)}"
            )

        print(
            f"Leakage Errors       : "
            f"{leakage_errors}"
        )

        print(
            f"Chronology Errors    : "
            f"{chronology_errors}"
        )

        print(
            "Database Modification: NONE"
        )

        print()
        print(
            "IMPORTANT:"
        )

        print(
            "This audit does NOT freeze the Issue Mapping Rule."
        )

        print(
            "Historical official_schedule_estimate remains "
            "ESTIMATED availability, not observed availability."
        )

        hr()

        if (
            leakage_errors == 0
            and chronology_errors == 0
        ):
            print(
                "RESULT: ISSUE MAPPING CANDIDATE AUDIT COMPLETE"
            )
        else:
            print(
                "RESULT: ISSUE MAPPING RULE REQUIRES CORRECTION"
            )

        hr()

    finally:
        conn.close()


if __name__ == "__main__":
    main()