"""
ZUUU TARGET Evidence Review V1.0
================================

Purpose:
    Produce human-readable evidence for TARGET_V1 review.

READ ONLY:
    - No Bronze modification
    - No Silver modification
    - No Candidate modification
    - No TARGET freeze

Review scope:
    1. Minimum Daily Tmax day
    2. Maximum Daily Tmax day
    3. Days with >24 observations
    4. Days where Tmax has COR support
    5. Days containing recovery observations
    6. Consecutive-day Tmax jumps >= 8 C

For every review day:
    - Candidate summary
    - Full BJT observation trajectory
    - UTC / BJT
    - Temperature
    - Source
    - Message class
    - COR / Recovery
    - Silver ID
    - Bronze ID
    - Raw METAR
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH


JUMP_THRESHOLD_C = 8.0


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:

    print("=" * 120)
    print("ZUUU TARGET Evidence Review V1.0")
    print("=" * 120)

    print(f"Database : {DEFAULT_DB_PATH}")

    print("=" * 120)

    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row

    try:

        candidates = conn.execute(
            """
            SELECT
                id,
                business_date_bjt,
                daily_tmax_c,
                first_tmax_time_bjt,
                last_tmax_time_bjt,
                tmax_occurrence_count,
                observation_count,
                hourly_coverage_count,
                has_correction,
                tmax_has_correction,
                has_recovery,
                tmax_has_recovery,
                target_status,
                target_version
            FROM zuuu_ground_truth_candidate
            ORDER BY business_date_bjt
            """
        ).fetchall()

        evidence_rows = conn.execute(
            """
            SELECT
                s.id AS silver_id,
                s.bronze_raw_id,
                s.business_date_bjt,
                s.observation_time_utc,
                s.observation_time_bjt,
                s.temperature_c,
                s.dewpoint_c,
                s.source,
                s.source_query_class,
                s.message_class,
                s.is_correction,
                s.supersedes_raw_id,
                s.recovery_reason,
                s.qc_status,
                s.qc_flags,

                b.raw_metar

            FROM zuuu_silver_observation AS s

            INNER JOIN zuuu_raw_metar AS b
                ON b.id = s.bronze_raw_id

            ORDER BY
                s.business_date_bjt,
                s.observation_time_bjt,
                s.id
            """
        ).fetchall()

    finally:
        conn.close()

    print(f"Candidate Rows : {len(candidates)}")
    print(f"Evidence Rows  : {len(evidence_rows)}")

    if not candidates:
        print("RESULT: NO CANDIDATES")
        return 2

    # ========================================================
    # Indexes
    # ========================================================

    candidate_by_date = {
        parse_date(row["business_date_bjt"]): row
        for row in candidates
    }

    evidence_by_date = defaultdict(list)

    for row in evidence_rows:

        d = parse_date(
            row["business_date_bjt"]
        )

        evidence_by_date[d].append(row)

    # ========================================================
    # Determine minimum / maximum
    # ========================================================

    minimum_candidate = min(
        candidates,
        key=lambda row: float(row["daily_tmax_c"]),
    )

    maximum_candidate = max(
        candidates,
        key=lambda row: float(row["daily_tmax_c"]),
    )

    minimum_date = parse_date(
        minimum_candidate["business_date_bjt"]
    )

    maximum_date = parse_date(
        maximum_candidate["business_date_bjt"]
    )

    # ========================================================
    # Build review reason map
    # ========================================================

    review_reasons = defaultdict(list)

    review_reasons[minimum_date].append(
        "MINIMUM_DAILY_TMAX"
    )

    review_reasons[maximum_date].append(
        "MAXIMUM_DAILY_TMAX"
    )

    # --------------------------------------------------------
    # Candidate special flags
    # --------------------------------------------------------

    for row in candidates:

        d = parse_date(
            row["business_date_bjt"]
        )

        if int(row["observation_count"]) > 24:
            review_reasons[d].append(
                "GT24_OBSERVATIONS"
            )

        if int(row["tmax_has_correction"]) == 1:
            review_reasons[d].append(
                "TMAX_HAS_CORRECTION"
            )

        if int(row["has_recovery"]) == 1:
            review_reasons[d].append(
                "HAS_RECOVERY"
            )

        if int(row["tmax_has_recovery"]) == 1:
            review_reasons[d].append(
                "TMAX_HAS_RECOVERY"
            )

    # ========================================================
    # Consecutive-day jump review
    # ========================================================

    jump_pairs = []

    ordered_candidates = list(candidates)

    for previous, current in zip(
        ordered_candidates,
        ordered_candidates[1:],
    ):

        previous_date = parse_date(
            previous["business_date_bjt"]
        )

        current_date = parse_date(
            current["business_date_bjt"]
        )

        # Only compare true consecutive days.
        if (
            current_date
            != previous_date + timedelta(days=1)
        ):
            continue

        previous_tmax = float(
            previous["daily_tmax_c"]
        )

        current_tmax = float(
            current["daily_tmax_c"]
        )

        jump = (
            current_tmax
            - previous_tmax
        )

        if abs(jump) >= JUMP_THRESHOLD_C:

            jump_pairs.append(
                {
                    "previous_date":
                        previous_date,

                    "previous_tmax":
                        previous_tmax,

                    "current_date":
                        current_date,

                    "current_tmax":
                        current_tmax,

                    "jump":
                        jump,
                }
            )

            review_reasons[
                previous_date
            ].append(
                "LARGE_JUMP_PAIR"
            )

            review_reasons[
                current_date
            ].append(
                "LARGE_JUMP_PAIR"
            )

    # ========================================================
    # Deduplicate reasons
    # ========================================================

    for d in list(review_reasons):

        review_reasons[d] = list(
            dict.fromkeys(
                review_reasons[d]
            )
        )

    review_dates = sorted(
        review_reasons
    )

    # ========================================================
    # Summary
    # ========================================================

    print()
    print("=" * 120)
    print("REVIEW SCOPE")
    print("=" * 120)

    print(
        f"Minimum Tmax Day : "
        f"{minimum_date} | "
        f"{float(minimum_candidate['daily_tmax_c']):g} C"
    )

    print(
        f"Maximum Tmax Day : "
        f"{maximum_date} | "
        f"{float(maximum_candidate['daily_tmax_c']):g} C"
    )

    print(
        f"Large Jump Pairs : "
        f"{len(jump_pairs)}"
    )

    print(
        f"Unique Review Dates : "
        f"{len(review_dates)}"
    )

    # ========================================================
    # Jump pairs
    # ========================================================

    print()
    print("=" * 120)
    print(
        f"LARGE DAILY TMAX JUMPS >= "
        f"{JUMP_THRESHOLD_C:g} C"
    )
    print("=" * 120)

    for item in jump_pairs:

        print(
            f"{item['previous_date']} "
            f"{item['previous_tmax']:g}C"
            f" -> "
            f"{item['current_date']} "
            f"{item['current_tmax']:g}C "
            f"({item['jump']:+g}C)"
        )

    # ========================================================
    # Evidence output
    # ========================================================

    hard_errors = []

    for review_date in review_dates:

        candidate = candidate_by_date.get(
            review_date
        )

        rows = evidence_by_date.get(
            review_date,
            [],
        )

        print()
        print("=" * 120)
        print(
            f"REVIEW DATE : {review_date}"
        )
        print("=" * 120)

        print(
            "Reasons     : "
            + ", ".join(
                review_reasons[review_date]
            )
        )

        if candidate is None:

            print(
                "ERROR       : Candidate missing"
            )

            hard_errors.append(
                f"CANDIDATE_MISSING:{review_date}"
            )

            continue

        print(
            f"Daily Tmax  : "
            f"{float(candidate['daily_tmax_c']):g} C"
        )

        print(
            f"First Tmax  : "
            f"{candidate['first_tmax_time_bjt']}"
        )

        print(
            f"Last Tmax   : "
            f"{candidate['last_tmax_time_bjt']}"
        )

        print(
            f"Occurrences : "
            f"{candidate['tmax_occurrence_count']}"
        )

        print(
            f"Observations: "
            f"{candidate['observation_count']}"
        )

        print(
            f"Hour Coverage: "
            f"{candidate['hourly_coverage_count']}/24"
        )

        print(
            f"Has COR     : "
            f"{candidate['has_correction']}"
        )

        print(
            f"Tmax COR    : "
            f"{candidate['tmax_has_correction']}"
        )

        print(
            f"Recovery    : "
            f"{candidate['has_recovery']}"
        )

        print(
            f"TmaxRecovery: "
            f"{candidate['tmax_has_recovery']}"
        )

        if not rows:

            print(
                "ERROR       : Evidence missing"
            )

            hard_errors.append(
                f"EVIDENCE_MISSING:{review_date}"
            )

            continue

        print()
        print(
            "BJT TIME                  "
            "TEMP   DEW    "
            "CLASS        "
            "SOURCE    "
            "COR REC "
            "SILVER   BRONZE"
        )

        print("-" * 120)

        candidate_tmax = float(
            candidate["daily_tmax_c"]
        )

        recomputed_tmax = max(
            float(row["temperature_c"])
            for row in rows
        )

        if recomputed_tmax != candidate_tmax:

            hard_errors.append(
                f"TMAX_MISMATCH:"
                f"{review_date}:"
                f"{candidate_tmax}:"
                f"{recomputed_tmax}"
            )

        for row in rows:

            temp = float(
                row["temperature_c"]
            )

            dewpoint = (
                "-"
                if row["dewpoint_c"] is None
                else f"{float(row['dewpoint_c']):g}"
            )

            marker = (
                " <== TMAX"
                if temp == candidate_tmax
                else ""
            )

            cor_marker = (
                "Y"
                if int(row["is_correction"]) == 1
                else "-"
            )

            recovery_marker = (
                "Y"
                if row["recovery_reason"]
                else "-"
            )

            print(
                f"{row['observation_time_bjt']:<25} "
                f"{temp:>4g}C "
                f"{dewpoint:>5} "
                f"{row['message_class']:<12} "
                f"{row['source']:<9} "
                f"{cor_marker:^3} "
                f"{recovery_marker:^3} "
                f"{row['silver_id']:>7} "
                f"{row['bronze_raw_id']:>8}"
                f"{marker}"
            )

        # ====================================================
        # Raw METAR evidence
        # ====================================================

        print()
        print("RAW METAR EVIDENCE")
        print("-" * 120)

        for row in rows:

            temp = float(
                row["temperature_c"]
            )

            marker = (
                " [TMAX]"
                if temp == candidate_tmax
                else ""
            )

            print(
                f"Silver={row['silver_id']} | "
                f"Bronze={row['bronze_raw_id']} | "
                f"BJT={row['observation_time_bjt']} | "
                f"UTC={row['observation_time_utc']} | "
                f"T={temp:g}C | "
                f"{row['message_class']} | "
                f"{row['source']}"
                f"{marker}"
            )

            print(
                f"    {row['raw_metar']}"
            )

    # ========================================================
    # Final report
    # ========================================================

    hard_errors = list(
        dict.fromkeys(
            hard_errors
        )
    )

    print()
    print("=" * 120)
    print("FINAL REPORT")
    print("=" * 120)

    print(
        f"Candidate Rows      : "
        f"{len(candidates)}"
    )

    print(
        f"Evidence Rows       : "
        f"{len(evidence_rows)}"
    )

    print(
        f"Review Dates        : "
        f"{len(review_dates)}"
    )

    print(
        f"Large Jump Pairs    : "
        f"{len(jump_pairs)}"
    )

    print(
        f"Minimum Tmax        : "
        f"{float(minimum_candidate['daily_tmax_c']):g} C "
        f"({minimum_date})"
    )

    print(
        f"Maximum Tmax        : "
        f"{float(maximum_candidate['daily_tmax_c']):g} C "
        f"({maximum_date})"
    )

    print(
        f"Hard Errors         : "
        f"{len(hard_errors)}"
    )

    if hard_errors:

        print()
        print("Hard Errors:")

        for item in hard_errors:
            print(
                f"    {item}"
            )

    print("=" * 120)

    if hard_errors:

        print(
            "RESULT: TARGET EVIDENCE REVIEW REQUIRED"
        )

        return 2

    print(
        "RESULT: TARGET EVIDENCE EXTRACTION PASS"
    )

    print(
        "NOTE: PASS means evidence extraction is internally consistent."
    )

    print(
        "NOTE: Human evidence review is still required before ZUUU_TARGET_V1 freeze."
    )

    return 0


if __name__ == "__main__":

    raise SystemExit(
        main()
    )