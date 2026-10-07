"""
ECMWF ISSUE MAPPING RULE SCANNER
================================

目的：
    自动扫描北京时间 00:00-23:00 的固定发行时点，
    寻找适合 T0 / T+1 / T+2 的 ECMWF canonical run mapping rule。

严格规则：
    1. READ ONLY
    2. 不修改 ZUUU_TARGET_V1
    3. 不修改 ECMWF_ARCHIVE_V1
    4. 不修改 ECMWF hourly/raw
    5. ECMWF source_available_time_utc <= issue_time_utc
    6. 目标北京时间自然日必须完整覆盖 24/24 小时
    7. 24 个 temperature_2m_c 必须全部非 NULL
    8. SOURCE_TEMPERATURE_UNAVAILABLE 不允许作为候选
    9. historical estimated availability 继续保留其估计语义
    10. 不创建训练数据

注意：
    这是 RULE SCANNER。
    不冻结任何规则。
"""

from __future__ import annotations

import sqlite3
import math
from collections import Counter
from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DB_PATH = (
    PROJECT_ROOT
    / "database"
    / "zuuu_prediction.db"
)

TARGET_TABLE = "zuuu_target_v1"
ARCHIVE_TABLE = "ecmwf_archive_v1"
HOURLY_TABLE = "ecmwf_hourly_forecasts"

UTC = timezone.utc
BJT = ZoneInfo("Asia/Shanghai")

EXPECTED_TARGET_DAYS = 729

TOP_N = 10

WIDTH = 118


# ============================================================
# BASIC
# ============================================================

def hr(char="="):
    print(char * WIDTH)


def parse_dt(value):
    if value is None:
        return None

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    return dt.astimezone(UTC)


def connect_read_only():

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    uri = DB_PATH.resolve().as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA query_only = ON"
    )

    return conn


# ============================================================
# LOAD TARGET
# ============================================================

def load_targets(conn):

    rows = conn.execute(
        f"""
        SELECT
            business_date_bjt,
            daily_tmax_c
        FROM {TARGET_TABLE}
        WHERE target_status = 'FROZEN'
        ORDER BY business_date_bjt
        """
    ).fetchall()

    result = []

    for row in rows:

        d = date.fromisoformat(
            row["business_date_bjt"]
        )

        result.append(
            {
                "date": d,
                "daily_tmax_c":
                    row["daily_tmax_c"],
            }
        )

    return result


# ============================================================
# LOAD ARCHIVE
# ============================================================

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

        item = dict(row)

        item["_run_dt"] = parse_dt(
            row["run_time_utc"]
        )

        item["_available_dt"] = parse_dt(
            row["source_available_time_utc"]
        )

        result.append(item)

    return result


# ============================================================
# LOAD HOURLY ONCE
# ============================================================

def load_hourly_index(conn):

    print(
        "Loading ECMWF hourly forecast index..."
    )

    rows = conn.execute(
        f"""
        SELECT
            raw_run_id,
            target_time_utc,
            temperature_2m_c
        FROM {HOURLY_TABLE}
        ORDER BY
            raw_run_id,
            target_time_utc
        """
    ).fetchall()

    index = {}

    for row in rows:

        raw_id = row["raw_run_id"]

        if raw_id not in index:
            index[raw_id] = {}

        dt = parse_dt(
            row["target_time_utc"]
        )

        index[raw_id][dt] = (
            row["temperature_2m_c"]
        )

    print(
        f"Hourly rows loaded : {len(rows)}"
    )

    print(
        f"Raw runs indexed   : {len(index)}"
    )

    return index


# ============================================================
# TARGET DAY WINDOW
# ============================================================

def target_day_hours(
    target_date,
):

    start_bjt = datetime.combine(
        target_date,
        time(0, 0),
        tzinfo=BJT,
    )

    start_utc = (
        start_bjt.astimezone(UTC)
    )

    return [
        start_utc
        + timedelta(hours=i)
        for i in range(24)
    ]


# ============================================================
# ISSUE TIME
# ============================================================

def build_issue_time(
    target_date,
    horizon,
    issue_hour_bjt,
):
    """
    定义：

    T0:
        target_date 当天的 issue_hour_bjt

    T+1:
        target_date - 1 day 的 issue_hour_bjt

    T+2:
        target_date - 2 days 的 issue_hour_bjt

    例如：

        target = 2025-07-10

        issue hour = 08:00 BJT

        T0   issue = 2025-07-10 08:00 BJT
        T+1 issue = 2025-07-09 08:00 BJT
        T+2 issue = 2025-07-08 08:00 BJT
    """

    issue_date = (
        target_date
        - timedelta(days=horizon)
    )

    issue_bjt = datetime.combine(
        issue_date,
        time(
            issue_hour_bjt,
            0,
        ),
        tzinfo=BJT,
    )

    return issue_bjt.astimezone(UTC)


# ============================================================
# COVERAGE
# ============================================================

def check_coverage(
    hourly_index,
    raw_id,
    target_date,
):

    forecast = hourly_index.get(
        raw_id
    )

    if not forecast:
        return False, 0, 0

    hours = target_day_hours(
        target_date
    )

    present = 0
    valid_temp = 0

    for dt in hours:

        if dt not in forecast:
            continue

        present += 1

        if (isinstance(forecast[dt], (int, float))
                and not isinstance(forecast[dt], bool) and math.isfinite(forecast[dt])):
            valid_temp += 1

    complete = (
        present == 24
        and valid_temp == 24
    )

    return (
        complete,
        present,
        valid_temp,
    )


# ============================================================
# CANDIDATE SELECTION
# ============================================================

def get_available_candidates(
    archive,
    issue_time_utc,
):

    result = []

    for run in archive:

        if (
            run.get("canonical_status")
            != "AVAILABLE"
        ):
            continue

        available = run[
            "_available_dt"
        ]

        if available is None:
            continue

        # Critical no-lookahead rule
        if available > issue_time_utc:
            continue

        result.append(run)

    result.sort(
        key=lambda x: (
            x["_run_dt"],
        ),
        reverse=True,
    )

    return result


def choose_latest_complete_run(
    archive,
    hourly_index,
    target_date,
    issue_time_utc,
):
    """
    从 issue time 当时真正允许使用的 Run 中，
    从最新向旧扫描。

    找到第一个：
        24/24 complete
        temperature valid

    的 run。
    """

    candidates = (
        get_available_candidates(
            archive,
            issue_time_utc,
        )
    )

    best_partial = None

    for run in candidates:

        raw_id = run[
            "canonical_raw_run_id"
        ]

        (
            complete,
            present,
            valid_temp,
        ) = check_coverage(
            hourly_index,
            raw_id,
            target_date,
        )

        if complete:

            return {
                "run": run,
                "complete": True,
                "present": present,
                "valid_temp": valid_temp,
            }

        if best_partial is None:

            best_partial = {
                "run": run,
                "complete": False,
                "present": present,
                "valid_temp": valid_temp,
            }

    return best_partial


# ============================================================
# SCAN ONE RULE
# ============================================================

def scan_rule(
    targets,
    archive,
    hourly_index,
    horizon,
    issue_hour_bjt,
):

    complete = 0
    partial = 0
    no_candidate = 0

    estimated = 0
    observed = 0

    leakage_errors = 0

    run_cycles = Counter()

    failures = []

    lead_hours = []

    for target in targets:

        target_date = target["date"]

        issue_time = build_issue_time(
            target_date,
            horizon,
            issue_hour_bjt,
        )

        selected = (
            choose_latest_complete_run(
                archive,
                hourly_index,
                target_date,
                issue_time,
            )
        )

        if selected is None:

            no_candidate += 1

            failures.append(
                {
                    "date":
                        target_date,
                    "status":
                        "NO_CANDIDATE",
                    "present":
                        0,
                    "valid":
                        0,
                    "run":
                        None,
                }
            )

            continue

        run = selected["run"]

        # Independent leakage check
        if (
            run["_available_dt"]
            > issue_time
        ):
            leakage_errors += 1

        if selected["complete"]:

            complete += 1

        else:

            partial += 1

            failures.append(
                {
                    "date":
                        target_date,
                    "status":
                        "PARTIAL",
                    "present":
                        selected["present"],
                    "valid":
                        selected[
                            "valid_temp"
                        ],
                    "run":
                        run,
                }
            )

        semantics = run.get(
            "availability_semantics"
        )

        if (
            semantics
            == "ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED"
        ):
            estimated += 1

        elif (
            semantics
            == "OBSERVED_INGEST_TIME"
        ):
            observed += 1

        run_cycle = (
            run["_run_dt"].hour
        )

        run_cycles[
            f"{run_cycle:02d}Z"
        ] += 1

        target_start = (
            datetime.combine(
                target_date,
                time(0, 0),
                tzinfo=BJT,
            )
            .astimezone(UTC)
        )

        lead = (
            target_start
            - run["_run_dt"]
        ).total_seconds() / 3600

        lead_hours.append(
            lead
        )

    total = len(targets)

    return {
        "horizon":
            horizon,

        "issue_hour_bjt":
            issue_hour_bjt,

        "total":
            total,

        "complete":
            complete,

        "partial":
            partial,

        "no_candidate":
            no_candidate,

        "rate":
            complete / total,

        "estimated":
            estimated,

        "observed":
            observed,

        "leakage_errors":
            leakage_errors,

        "run_cycles":
            run_cycles,

        "failures":
            failures,

        "min_lead":
            min(lead_hours)
            if lead_hours
            else None,

        "max_lead":
            max(lead_hours)
            if lead_hours
            else None,

        "mean_lead":
            (
                sum(lead_hours)
                / len(lead_hours)
            )
            if lead_hours
            else None,
    }


# ============================================================
# PRINT
# ============================================================

def label_horizon(h):

    if h == 0:
        return "T0"

    return f"T+{h}"


def print_top_results(
    horizon,
    results,
):

    label = label_horizon(
        horizon
    )

    hr()
    print(
        f"{label} TOP ISSUE-TIME RULES"
    )
    hr()

    ranked = sorted(
        results,
        key=lambda x: (
            x["complete"],
            -x["leakage_errors"],
            x["issue_hour_bjt"],
        ),
        reverse=True,
    )

    print(
        f"{'Rank':<6}"
        f"{'Issue BJT':<12}"
        f"{'Complete':<14}"
        f"{'Rate':<12}"
        f"{'Partial':<10}"
        f"{'NoCand':<10}"
        f"{'Leak':<8}"
        f"{'MeanLead':<12}"
        f"Run Cycles"
    )

    print("-" * WIDTH)

    for rank, r in enumerate(
        ranked[:TOP_N],
        start=1,
    ):

        cycles = ", ".join(
            f"{k}:{v}"
            for k, v
            in sorted(
                r["run_cycles"].items()
            )
        )

        mean_lead = (
            f"{r['mean_lead']:.2f}h"
            if r["mean_lead"]
            is not None
            else "-"
        )

        print(
            f"{rank:<6}"
            f"{r['issue_hour_bjt']:02d}:00"
            f"{'':<7}"
            f"{r['complete']}/{r['total']:<8}"
            f"{r['rate']*100:>6.2f}%"
            f"{'':<5}"
            f"{r['partial']:<10}"
            f"{r['no_candidate']:<10}"
            f"{r['leakage_errors']:<8}"
            f"{mean_lead:<12}"
            f"{cycles}"
        )

    return ranked


def print_best_detail(
    horizon,
    best,
):

    label = label_horizon(
        horizon
    )

    print()
    print(
        f"{label} BEST RULE DETAIL"
    )

    print(
        f"    Issue Time BJT : "
        f"{best['issue_hour_bjt']:02d}:00"
    )

    print(
        f"    Complete       : "
        f"{best['complete']}/"
        f"{best['total']}"
    )

    print(
        f"    Complete Rate  : "
        f"{best['rate']*100:.2f}%"
    )

    print(
        f"    Partial        : "
        f"{best['partial']}"
    )

    print(
        f"    No Candidate   : "
        f"{best['no_candidate']}"
    )

    print(
        f"    Leakage Errors : "
        f"{best['leakage_errors']}"
    )

    print(
        f"    Availability   : "
        f"Estimated={best['estimated']} "
        f"Observed={best['observed']}"
    )

    if best["min_lead"] is not None:

        print(
            f"    Lead Range     : "
            f"{best['min_lead']:.2f}h "
            f"to "
            f"{best['max_lead']:.2f}h"
        )

        print(
            f"    Mean Lead      : "
            f"{best['mean_lead']:.2f}h"
        )

    cycles = ", ".join(
        f"{k}={v}"
        for k, v
        in sorted(
            best["run_cycles"].items()
        )
    )

    print(
        f"    Selected Runs  : "
        f"{cycles}"
    )

    if best["failures"]:

        print(
            "    Failure Dates:"
        )

        # 最多只打印20条
        for item in (
            best["failures"][:20]
        ):

            run = item["run"]

            run_text = (
                run["run_time_utc"]
                if run
                else "NONE"
            )

            print(
                f"        "
                f"{item['date']} | "
                f"{item['status']} | "
                f"{item['present']}/24 | "
                f"temp "
                f"{item['valid']}/24 | "
                f"run={run_text}"
            )

        if (
            len(best["failures"])
            > 20
        ):

            print(
                f"        ... "
                f"{len(best['failures']) - 20} "
                f"more"
            )

    else:

        print(
            "    Failure Dates  : NONE"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    hr()
    print(
        "ECMWF ISSUE MAPPING RULE SCANNER"
    )
    print(
        f"Database : {DB_PATH}"
    )
    print(
        "Mode     : STRICT READ ONLY"
    )
    print(
        "Scan     : BJT 00:00 - 23:00"
    )
    hr()

    conn = connect_read_only()

    try:

        targets = load_targets(
            conn
        )

        archive = load_archive(
            conn
        )

        print()
        print(
            f"Frozen Target Rows  : "
            f"{len(targets)}"
        )

        print(
            f"Frozen Archive Runs : "
            f"{len(archive)}"
        )

        if (
            len(targets)
            != EXPECTED_TARGET_DAYS
        ):
            raise RuntimeError(
                "Unexpected target row count."
            )

        if not archive:
            raise RuntimeError(
                "ECMWF_ARCHIVE_V1 empty."
            )

        hourly_index = (
            load_hourly_index(
                conn
            )
        )

        print()
        hr()
        print(
            "STARTING 72-RULE SCAN"
        )
        print(
            "24 issue hours × "
            "3 horizons"
        )
        hr()

        all_results = {}

        for horizon in (
            0,
            1,
            2,
        ):

            horizon_results = []

            for issue_hour in range(24):

                print(
                    f"\rScanning "
                    f"{label_horizon(horizon)} "
                    f"{issue_hour:02d}:00 BJT...",
                    end="",
                    flush=True,
                )

                result = scan_rule(
                    targets,
                    archive,
                    hourly_index,
                    horizon,
                    issue_hour,
                )

                horizon_results.append(
                    result
                )

            print()

            all_results[
                horizon
            ] = horizon_results

        print()

        best_rules = {}

        for horizon in (
            0,
            1,
            2,
        ):

            ranked = (
                print_top_results(
                    horizon,
                    all_results[
                        horizon
                    ],
                )
            )

            best_rules[
                horizon
            ] = ranked[0]

            print_best_detail(
                horizon,
                ranked[0],
            )

            print()

        # ====================================================
        # FINAL
        # ====================================================

        hr()
        print(
            "FINAL RULE SCAN SUMMARY"
        )
        hr()

        all_perfect = True

        for horizon in (
            0,
            1,
            2,
        ):

            best = best_rules[
                horizon
            ]

            label = label_horizon(
                horizon
            )

            perfect = (
                best["complete"]
                == best["total"]
                and
                best[
                    "leakage_errors"
                ]
                == 0
            )

            if not perfect:
                all_perfect = False

            print(
                f"{label:<5} "
                f"Best Issue="
                f"{best['issue_hour_bjt']:02d}:00 BJT | "
                f"Complete="
                f"{best['complete']}/"
                f"{best['total']} | "
                f"Rate="
                f"{best['rate']*100:.2f}% | "
                f"Leak="
                f"{best['leakage_errors']}"
            )

        print()
        print(
            "Database Modification : NONE"
        )

        print(
            "Issue Mapping Frozen  : NO"
        )

        print(
            "Historical availability semantics "
            "remain unchanged."
        )

        print()

        if all_perfect:

            print(
                "RESULT: PERFECT ISSUE-TIME "
                "CANDIDATES FOUND FOR "
                "T0 / T+1 / T+2"
            )

            print(
                "NEXT: REVIEW BUSINESS ISSUE TIME "
                "THEN FREEZE ISSUE_MAPPING_RULE_V1"
            )

        else:

            print(
                "RESULT: AT LEAST ONE HORIZON "
                "HAS NO 100% FIXED-HOUR RULE"
            )

            print(
                "NEXT: DO NOT FREEZE. "
                "REVIEW FORECAST HORIZON / "
                "ISSUE POLICY / ARCHIVE COVERAGE."
            )

        hr()

    finally:

        conn.close()


if __name__ == "__main__":
    main()
