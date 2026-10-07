"""
ZUUU TARGET V1 Freezer
======================

Purpose:
    Freeze the audited Ground Truth Candidate dataset into ZUUU_TARGET_V1.

IMPORTANT:
    - Candidate is a rebuildable intermediate dataset.
    - ZUUU_TARGET_V1 is a frozen data asset.
    - Existing TARGET_V1 rows must never be silently overwritten.
    - --dry-run performs validation only.
    - --commit performs a one-time transactional freeze.

Frozen target period:
    2024-09-03 -> 2026-09-01
    729 complete BJT business days.

Target rule:
    DAILY_TMAX_RULE_V1

Target version:
    ZUUU_TARGET_V1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH


UTC = timezone.utc

EXPECTED_START_DATE = date(2024, 9, 3)
EXPECTED_END_DATE = date(2026, 9, 1)
EXPECTED_DAYS = 729

EXPECTED_CANDIDATE_STATUS = "CANDIDATE"
EXPECTED_CANDIDATE_VERSION = "DAILY_TMAX_RULE_V1"

RULE_VERSION = "DAILY_TMAX_RULE_V1"
TARGET_VERSION = "ZUUU_TARGET_V1"
TARGET_STATUS = "FROZEN"

TARGET_TABLE = "zuuu_target_v1"


def utc_now_iso() -> str:
    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
    )


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DEFAULT_DB_PATH
    )

    conn.row_factory = sqlite3.Row

    # Enforce FK constraints for this connection.
    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    return conn


def table_exists(
    conn: sqlite3.Connection,
    table_name: str,
) -> bool:

    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE
            type = 'table'
            AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def get_existing_target_count(
    conn: sqlite3.Connection,
) -> int:

    if not table_exists(
        conn,
        TARGET_TABLE,
    ):
        return 0

    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {TARGET_TABLE}
        """
    ).fetchone()

    return int(row["n"])


def create_target_table(
    conn: sqlite3.Connection,
) -> None:

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            business_date_bjt TEXT
                NOT NULL
                UNIQUE,

            daily_tmax_c REAL
                NOT NULL,

            first_tmax_time_bjt TEXT
                NOT NULL,

            last_tmax_time_bjt TEXT
                NOT NULL,

            tmax_occurrence_count INTEGER
                NOT NULL
                CHECK (tmax_occurrence_count >= 1),

            observation_count INTEGER
                NOT NULL
                CHECK (observation_count >= 24),

            hourly_coverage_count INTEGER
                NOT NULL
                CHECK (hourly_coverage_count = 24),

            has_correction INTEGER
                NOT NULL
                CHECK (has_correction IN (0, 1)),

            tmax_has_correction INTEGER
                NOT NULL
                CHECK (tmax_has_correction IN (0, 1)),

            has_recovery INTEGER
                NOT NULL
                CHECK (has_recovery IN (0, 1)),

            tmax_has_recovery INTEGER
                NOT NULL
                CHECK (tmax_has_recovery IN (0, 1)),

            tmax_silver_ids TEXT
                NOT NULL,

            tmax_bronze_raw_ids TEXT
                NOT NULL,

            all_silver_ids TEXT
                NOT NULL,

            all_bronze_raw_ids TEXT
                NOT NULL,

            rule_version TEXT
                NOT NULL,

            target_version TEXT
                NOT NULL,

            target_status TEXT
                NOT NULL,

            source_candidate_id INTEGER
                NOT NULL
                UNIQUE,

            source_candidate_created_at_utc TEXT
                NOT NULL,

            frozen_at_utc TEXT
                NOT NULL,

            FOREIGN KEY (
                source_candidate_id
            )
            REFERENCES zuuu_ground_truth_candidate(id)
        )
        """
    )

    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS
        idx_{TARGET_TABLE}_date
        ON {TARGET_TABLE}(
            business_date_bjt
        )
        """
    )

    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS
        idx_{TARGET_TABLE}_tmax
        ON {TARGET_TABLE}(
            daily_tmax_c
        )
        """
    )


def read_candidates(
    conn: sqlite3.Connection,
):

    return conn.execute(
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
            tmax_silver_ids,
            tmax_bronze_raw_ids,
            all_silver_ids,
            all_bronze_raw_ids,
            target_status,
            target_version,
            created_at_utc

        FROM zuuu_ground_truth_candidate

        ORDER BY business_date_bjt
        """
    ).fetchall()


def expected_dates():
    current = EXPECTED_START_DATE

    while current <= EXPECTED_END_DATE:
        yield current
        current += timedelta(days=1)


def parse_json_ids(
    value: str,
) -> list[int]:

    parsed = json.loads(value)

    if not isinstance(parsed, list):
        raise ValueError(
            "LINEAGE_NOT_LIST"
        )

    return [
        int(item)
        for item in parsed
    ]


def canonical_candidate_payload(
    row: sqlite3.Row,
) -> dict:

    """
    Build deterministic immutable payload.

    This excludes database-local Candidate ID and creation
    timestamp because those are provenance metadata rather
    than target semantics.
    """

    return {
        "business_date_bjt":
            row["business_date_bjt"],

        "daily_tmax_c":
            float(row["daily_tmax_c"]),

        "first_tmax_time_bjt":
            row["first_tmax_time_bjt"],

        "last_tmax_time_bjt":
            row["last_tmax_time_bjt"],

        "tmax_occurrence_count":
            int(row["tmax_occurrence_count"]),

        "observation_count":
            int(row["observation_count"]),

        "hourly_coverage_count":
            int(row["hourly_coverage_count"]),

        "has_correction":
            int(row["has_correction"]),

        "tmax_has_correction":
            int(row["tmax_has_correction"]),

        "has_recovery":
            int(row["has_recovery"]),

        "tmax_has_recovery":
            int(row["tmax_has_recovery"]),

        "tmax_silver_ids":
            parse_json_ids(
                row["tmax_silver_ids"]
            ),

        "tmax_bronze_raw_ids":
            parse_json_ids(
                row["tmax_bronze_raw_ids"]
            ),

        "all_silver_ids":
            parse_json_ids(
                row["all_silver_ids"]
            ),

        "all_bronze_raw_ids":
            parse_json_ids(
                row["all_bronze_raw_ids"]
            ),

        "rule_version":
            RULE_VERSION,

        "target_version":
            TARGET_VERSION,
    }


def compute_dataset_sha256(
    candidates,
) -> str:

    payload = [
        canonical_candidate_payload(row)
        for row in candidates
    ]

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(
        encoded
    ).hexdigest()


def validate_candidates(
    conn: sqlite3.Connection,
    candidates,
):
    hard_errors = []
    review_items = []

    expected_date_list = list(
        expected_dates()
    )

    expected_date_strings = [
        d.isoformat()
        for d in expected_date_list
    ]

    actual_date_strings = [
        row["business_date_bjt"]
        for row in candidates
    ]

    # --------------------------------------------------------
    # Dataset size
    # --------------------------------------------------------

    if len(candidates) != EXPECTED_DAYS:
        hard_errors.append(
            f"CANDIDATE_COUNT:"
            f"{len(candidates)}:"
            f"EXPECTED={EXPECTED_DAYS}"
        )

    # --------------------------------------------------------
    # Exact date sequence
    # --------------------------------------------------------

    if (
        actual_date_strings
        != expected_date_strings
    ):
        hard_errors.append(
            "CANDIDATE_DATE_SEQUENCE_MISMATCH"
        )

    # --------------------------------------------------------
    # Per-row validation
    # --------------------------------------------------------

    seen_dates = set()
    seen_candidate_ids = set()

    days_gt24 = 0
    days_with_cor = 0
    tmax_cor_days = 0
    recovery_days = 0
    tmax_recovery_days = 0

    tmax_values = []

    for row in candidates:

        candidate_id = int(
            row["id"]
        )

        business_date = (
            row["business_date_bjt"]
        )

        if business_date in seen_dates:
            hard_errors.append(
                f"DUPLICATE_DATE:"
                f"{business_date}"
            )

        seen_dates.add(
            business_date
        )

        if candidate_id in seen_candidate_ids:
            hard_errors.append(
                f"DUPLICATE_CANDIDATE_ID:"
                f"{candidate_id}"
            )

        seen_candidate_ids.add(
            candidate_id
        )

        if (
            row["target_status"]
            != EXPECTED_CANDIDATE_STATUS
        ):
            hard_errors.append(
                f"CANDIDATE_STATUS_MISMATCH:"
                f"{business_date}:"
                f"{row['target_status']}"
            )

        if (
            row["target_version"]
            != EXPECTED_CANDIDATE_VERSION
        ):
            hard_errors.append(
                f"CANDIDATE_RULE_VERSION_MISMATCH:"
                f"{business_date}:"
                f"{row['target_version']}"
            )

        observation_count = int(
            row["observation_count"]
        )

        hourly_coverage = int(
            row["hourly_coverage_count"]
        )

        if observation_count < 24:
            hard_errors.append(
                f"OBS_LT24:"
                f"{business_date}:"
                f"{observation_count}"
            )

        if hourly_coverage != 24:
            hard_errors.append(
                f"HOURLY_COVERAGE:"
                f"{business_date}:"
                f"{hourly_coverage}"
            )

        if observation_count > 24:
            days_gt24 += 1

            review_items.append(
                f"GT24_OBSERVATIONS:"
                f"{business_date}:"
                f"{observation_count}"
            )

        has_correction = int(
            row["has_correction"]
        )

        tmax_has_correction = int(
            row["tmax_has_correction"]
        )

        has_recovery = int(
            row["has_recovery"]
        )

        tmax_has_recovery = int(
            row["tmax_has_recovery"]
        )

        if has_correction:
            days_with_cor += 1

        if tmax_has_correction:
            tmax_cor_days += 1

            review_items.append(
                f"TMAX_HAS_CORRECTION:"
                f"{business_date}"
            )

        if has_recovery:
            recovery_days += 1

        if tmax_has_recovery:
            tmax_recovery_days += 1

            review_items.append(
                f"TMAX_HAS_RECOVERY:"
                f"{business_date}"
            )

        # ----------------------------------------------------
        # Lineage JSON validation
        # ----------------------------------------------------

        try:
            tmax_silver_ids = (
                parse_json_ids(
                    row[
                        "tmax_silver_ids"
                    ]
                )
            )

            tmax_bronze_ids = (
                parse_json_ids(
                    row[
                        "tmax_bronze_raw_ids"
                    ]
                )
            )

            all_silver_ids = (
                parse_json_ids(
                    row[
                        "all_silver_ids"
                    ]
                )
            )

            all_bronze_ids = (
                parse_json_ids(
                    row[
                        "all_bronze_raw_ids"
                    ]
                )
            )

        except Exception as exc:
            hard_errors.append(
                f"LINEAGE_PARSE_ERROR:"
                f"{business_date}:"
                f"{exc}"
            )

            continue

        if (
            len(all_silver_ids)
            != observation_count
        ):
            hard_errors.append(
                f"ALL_SILVER_COUNT_MISMATCH:"
                f"{business_date}"
            )

        if (
            len(all_bronze_ids)
            != observation_count
        ):
            hard_errors.append(
                f"ALL_BRONZE_COUNT_MISMATCH:"
                f"{business_date}"
            )

        occurrence_count = int(
            row["tmax_occurrence_count"]
        )

        if (
            len(tmax_silver_ids)
            != occurrence_count
        ):
            hard_errors.append(
                f"TMAX_SILVER_COUNT_MISMATCH:"
                f"{business_date}"
            )

        if (
            len(tmax_bronze_ids)
            != occurrence_count
        ):
            hard_errors.append(
                f"TMAX_BRONZE_COUNT_MISMATCH:"
                f"{business_date}"
            )

        # ----------------------------------------------------
        # Verify Silver lineage exists
        # ----------------------------------------------------

        for silver_id in all_silver_ids:

            silver = conn.execute(
                """
                SELECT
                    id,
                    bronze_raw_id,
                    business_date_bjt
                FROM zuuu_silver_observation
                WHERE id = ?
                """,
                (silver_id,),
            ).fetchone()

            if silver is None:
                hard_errors.append(
                    f"MISSING_SILVER:"
                    f"{business_date}:"
                    f"{silver_id}"
                )

                continue

            if (
                silver["business_date_bjt"]
                != business_date
            ):
                hard_errors.append(
                    f"CROSS_DAY_SILVER:"
                    f"{business_date}:"
                    f"{silver_id}"
                )

        # ----------------------------------------------------
        # Verify Bronze lineage exists
        # ----------------------------------------------------

        for bronze_id in all_bronze_ids:

            bronze = conn.execute(
                """
                SELECT id
                FROM zuuu_raw_metar
                WHERE id = ?
                """,
                (bronze_id,),
            ).fetchone()

            if bronze is None:
                hard_errors.append(
                    f"MISSING_BRONZE:"
                    f"{business_date}:"
                    f"{bronze_id}"
                )

        # ----------------------------------------------------
        # Tmax range
        # ----------------------------------------------------

        tmax_values.append(
            float(
                row["daily_tmax_c"]
            )
        )

    stats = {
        "days_gt24":
            days_gt24,

        "days_with_cor":
            days_with_cor,

        "tmax_cor_days":
            tmax_cor_days,

        "recovery_days":
            recovery_days,

        "tmax_recovery_days":
            tmax_recovery_days,

        "minimum_tmax":
            min(tmax_values)
            if tmax_values
            else None,

        "maximum_tmax":
            max(tmax_values)
            if tmax_values
            else None,
    }

    return (
        list(dict.fromkeys(hard_errors)),
        list(dict.fromkeys(review_items)),
        stats,
    )


def insert_target_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    frozen_at_utc: str,
) -> None:

    # IMPORTANT:
    # Plain INSERT.
    #
    # No OR IGNORE.
    # No OR REPLACE.
    # No UPSERT.
    #
    # Any collision must fail loudly.

    conn.execute(
        f"""
        INSERT INTO {TARGET_TABLE} (

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

            tmax_silver_ids,
            tmax_bronze_raw_ids,

            all_silver_ids,
            all_bronze_raw_ids,

            rule_version,
            target_version,
            target_status,

            source_candidate_id,
            source_candidate_created_at_utc,

            frozen_at_utc

        ) VALUES (

            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?

        )
        """,
        (
            row[
                "business_date_bjt"
            ],

            float(
                row["daily_tmax_c"]
            ),

            row[
                "first_tmax_time_bjt"
            ],

            row[
                "last_tmax_time_bjt"
            ],

            int(
                row[
                    "tmax_occurrence_count"
                ]
            ),

            int(
                row[
                    "observation_count"
                ]
            ),

            int(
                row[
                    "hourly_coverage_count"
                ]
            ),

            int(
                row[
                    "has_correction"
                ]
            ),

            int(
                row[
                    "tmax_has_correction"
                ]
            ),

            int(
                row[
                    "has_recovery"
                ]
            ),

            int(
                row[
                    "tmax_has_recovery"
                ]
            ),

            row[
                "tmax_silver_ids"
            ],

            row[
                "tmax_bronze_raw_ids"
            ],

            row[
                "all_silver_ids"
            ],

            row[
                "all_bronze_raw_ids"
            ],

            RULE_VERSION,
            TARGET_VERSION,
            TARGET_STATUS,

            int(row["id"]),

            row[
                "created_at_utc"
            ],

            frozen_at_utc,
        ),
    )


def verify_frozen_target(
    conn: sqlite3.Connection,
    candidates,
) -> list[str]:

    errors = []

    target_rows = conn.execute(
        f"""
        SELECT *
        FROM {TARGET_TABLE}
        ORDER BY business_date_bjt
        """
    ).fetchall()

    if len(target_rows) != EXPECTED_DAYS:
        errors.append(
            f"TARGET_COUNT:"
            f"{len(target_rows)}:"
            f"EXPECTED={EXPECTED_DAYS}"
        )

        return errors

    if len(target_rows) != len(candidates):
        errors.append(
            "TARGET_CANDIDATE_COUNT_MISMATCH"
        )

        return errors

    for candidate, target in zip(
        candidates,
        target_rows,
    ):

        business_date = (
            candidate[
                "business_date_bjt"
            ]
        )

        comparisons = {
            "business_date_bjt":
                (
                    candidate[
                        "business_date_bjt"
                    ],
                    target[
                        "business_date_bjt"
                    ],
                ),

            "daily_tmax_c":
                (
                    float(
                        candidate[
                            "daily_tmax_c"
                        ]
                    ),
                    float(
                        target[
                            "daily_tmax_c"
                        ]
                    ),
                ),

            "first_tmax_time_bjt":
                (
                    candidate[
                        "first_tmax_time_bjt"
                    ],
                    target[
                        "first_tmax_time_bjt"
                    ],
                ),

            "last_tmax_time_bjt":
                (
                    candidate[
                        "last_tmax_time_bjt"
                    ],
                    target[
                        "last_tmax_time_bjt"
                    ],
                ),

            "tmax_occurrence_count":
                (
                    int(
                        candidate[
                            "tmax_occurrence_count"
                        ]
                    ),
                    int(
                        target[
                            "tmax_occurrence_count"
                        ]
                    ),
                ),

            "observation_count":
                (
                    int(
                        candidate[
                            "observation_count"
                        ]
                    ),
                    int(
                        target[
                            "observation_count"
                        ]
                    ),
                ),

            "hourly_coverage_count":
                (
                    int(
                        candidate[
                            "hourly_coverage_count"
                        ]
                    ),
                    int(
                        target[
                            "hourly_coverage_count"
                        ]
                    ),
                ),

            "has_correction":
                (
                    int(
                        candidate[
                            "has_correction"
                        ]
                    ),
                    int(
                        target[
                            "has_correction"
                        ]
                    ),
                ),

            "tmax_has_correction":
                (
                    int(
                        candidate[
                            "tmax_has_correction"
                        ]
                    ),
                    int(
                        target[
                            "tmax_has_correction"
                        ]
                    ),
                ),

            "has_recovery":
                (
                    int(
                        candidate[
                            "has_recovery"
                        ]
                    ),
                    int(
                        target[
                            "has_recovery"
                        ]
                    ),
                ),

            "tmax_has_recovery":
                (
                    int(
                        candidate[
                            "tmax_has_recovery"
                        ]
                    ),
                    int(
                        target[
                            "tmax_has_recovery"
                        ]
                    ),
                ),

            "tmax_silver_ids":
                (
                    candidate[
                        "tmax_silver_ids"
                    ],
                    target[
                        "tmax_silver_ids"
                    ],
                ),

            "tmax_bronze_raw_ids":
                (
                    candidate[
                        "tmax_bronze_raw_ids"
                    ],
                    target[
                        "tmax_bronze_raw_ids"
                    ],
                ),

            "all_silver_ids":
                (
                    candidate[
                        "all_silver_ids"
                    ],
                    target[
                        "all_silver_ids"
                    ],
                ),

            "all_bronze_raw_ids":
                (
                    candidate[
                        "all_bronze_raw_ids"
                    ],
                    target[
                        "all_bronze_raw_ids"
                    ],
                ),
        }

        for field, (
            candidate_value,
            target_value,
        ) in comparisons.items():

            if (
                candidate_value
                != target_value
            ):

                errors.append(
                    f"POST_FREEZE_MISMATCH:"
                    f"{business_date}:"
                    f"{field}"
                )

        if (
            target["rule_version"]
            != RULE_VERSION
        ):
            errors.append(
                f"RULE_VERSION_MISMATCH:"
                f"{business_date}"
            )

        if (
            target["target_version"]
            != TARGET_VERSION
        ):
            errors.append(
                f"TARGET_VERSION_MISMATCH:"
                f"{business_date}"
            )

        if (
            target["target_status"]
            != TARGET_STATUS
        ):
            errors.append(
                f"TARGET_STATUS_MISMATCH:"
                f"{business_date}"
            )

        if (
            int(
                target[
                    "source_candidate_id"
                ]
            )
            != int(
                candidate["id"]
            )
        ):
            errors.append(
                f"SOURCE_CANDIDATE_ID_MISMATCH:"
                f"{business_date}"
            )

    return list(
        dict.fromkeys(errors)
    )


def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Freeze audited ZUUU Ground Truth "
            "Candidate into ZUUU_TARGET_V1."
        )
    )

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only; no database writes.",
    )

    mode.add_argument(
        "--commit",
        action="store_true",
        help="Perform one-time TARGET_V1 freeze.",
    )

    args = parser.parse_args()

    run_mode = (
        "COMMIT"
        if args.commit
        else "DRY-RUN"
    )

    print("=" * 100)
    print(
        "ZUUU TARGET V1 Freezer"
    )
    print("=" * 100)

    print(
        f"Mode           : {run_mode}"
    )

    print(
        f"Database       : {DEFAULT_DB_PATH}"
    )

    print(
        f"Rule Version   : {RULE_VERSION}"
    )

    print(
        f"Target Version : {TARGET_VERSION}"
    )

    print("=" * 100)

    conn = connect()

    try:

        # ====================================================
        # Existing frozen target protection
        # ====================================================

        existing_target_count = (
            get_existing_target_count(
                conn
            )
        )

        print(
            f"Existing TARGET Rows : "
            f"{existing_target_count}"
        )

        if existing_target_count != 0:

            print()
            print("=" * 100)
            print(
                "RESULT: FREEZE REFUSED"
            )

            print(
                "Reason: ZUUU_TARGET_V1 already contains data."
            )

            print(
                "Frozen target data must not be silently overwritten."
            )

            print("=" * 100)

            return 3

        # ====================================================
        # Candidate read
        # ====================================================

        candidates = read_candidates(
            conn
        )

        print(
            f"Candidate Rows       : "
            f"{len(candidates)}"
        )

        # ====================================================
        # Validation
        # ====================================================

        (
            hard_errors,
            review_items,
            stats,
        ) = validate_candidates(
            conn,
            candidates,
        )

        dataset_sha256 = (
            compute_dataset_sha256(
                candidates
            )
            if candidates
            else None
        )

        print()
        print("=" * 100)
        print("CANDIDATE VALIDATION")
        print("=" * 100)

        print(
            f"Expected Days       : "
            f"{EXPECTED_DAYS}"
        )

        print(
            f"Actual Days         : "
            f"{len(candidates)}"
        )

        print(
            f"Start Date          : "
            f"{EXPECTED_START_DATE}"
        )

        print(
            f"End Date            : "
            f"{EXPECTED_END_DATE}"
        )

        print(
            f"Days >24 Obs        : "
            f"{stats['days_gt24']}"
        )

        print(
            f"Days With COR       : "
            f"{stats['days_with_cor']}"
        )

        print(
            f"Tmax COR Days       : "
            f"{stats['tmax_cor_days']}"
        )

        print(
            f"Recovery Days       : "
            f"{stats['recovery_days']}"
        )

        print(
            f"Tmax Recovery Days  : "
            f"{stats['tmax_recovery_days']}"
        )

        print(
            f"Minimum Tmax        : "
            f"{stats['minimum_tmax']:g} C"
            if stats["minimum_tmax"] is not None
            else "Minimum Tmax        : N/A"
        )

        print(
            f"Maximum Tmax        : "
            f"{stats['maximum_tmax']:g} C"
            if stats["maximum_tmax"] is not None
            else "Maximum Tmax        : N/A"
        )

        print(
            f"Dataset SHA256      : "
            f"{dataset_sha256}"
        )

        print(
            f"Review Items        : "
            f"{len(review_items)}"
        )

        print(
            f"Hard Errors         : "
            f"{len(hard_errors)}"
        )

        if review_items:

            print()
            print("Review Items:")

            for item in review_items:
                print(
                    f"    {item}"
                )

        if hard_errors:

            print()
            print("Hard Errors:")

            for item in hard_errors:
                print(
                    f"    {item}"
                )

            print("=" * 100)

            print(
                "RESULT: TARGET V1 FREEZE BLOCKED"
            )

            return 2

        # ====================================================
        # Dry run stops here
        # ====================================================

        if args.dry_run:

            print()
            print("=" * 100)
            print("FINAL REPORT")
            print("=" * 100)

            print(
                "Mode                 : DRY-RUN"
            )

            print(
                f"Candidate Rows       : "
                f"{len(candidates)}"
            )

            print(
                f"Expected Rows        : "
                f"{EXPECTED_DAYS}"
            )

            print(
                f"Existing TARGET Rows : "
                f"{existing_target_count}"
            )

            print(
                f"Review Items         : "
                f"{len(review_items)}"
            )

            print(
                f"Hard Errors          : "
                f"{len(hard_errors)}"
            )

            print(
                f"Dataset SHA256       : "
                f"{dataset_sha256}"
            )

            print("=" * 100)

            print(
                "RESULT: TARGET V1 FREEZE DRY-RUN PASS"
            )

            print(
                "NOTE: No database changes were made."
            )

            return 0

        # ====================================================
        # COMMIT
        # ====================================================

        frozen_at_utc = utc_now_iso()

        try:

            # One atomic transaction.
            conn.execute(
                "BEGIN IMMEDIATE"
            )

            # Re-check inside write transaction.
            if (
                get_existing_target_count(
                    conn
                )
                != 0
            ):
                raise RuntimeError(
                    "TARGET_BECAME_NONEMPTY"
                )

            create_target_table(
                conn
            )

            inserted = 0

            for row in candidates:

                insert_target_row(
                    conn,
                    row,
                    frozen_at_utc,
                )

                inserted += 1

            target_count = (
                get_existing_target_count(
                    conn
                )
            )

            if inserted != EXPECTED_DAYS:
                raise RuntimeError(
                    f"INSERTED_COUNT:"
                    f"{inserted}"
                )

            if target_count != EXPECTED_DAYS:
                raise RuntimeError(
                    f"TARGET_COUNT:"
                    f"{target_count}"
                )

            # -----------------------------------------------
            # Post-freeze exact verification
            # -----------------------------------------------

            post_freeze_errors = (
                verify_frozen_target(
                    conn,
                    candidates,
                )
            )

            if post_freeze_errors:

                raise RuntimeError(
                    "POST_FREEZE_VERIFY_FAILED:"
                    + "|".join(
                        post_freeze_errors
                    )
                )

            conn.commit()

        except Exception:

            conn.rollback()
            raise

        # ====================================================
        # Final committed report
        # ====================================================

        print()
        print("=" * 100)
        print("FINAL REPORT")
        print("=" * 100)

        print(
            "Mode                 : COMMIT"
        )

        print(
            f"Candidate Rows       : "
            f"{len(candidates)}"
        )

        print(
            f"Inserted TARGET Rows : "
            f"{inserted}"
        )

        print(
            f"TARGET Database Total: "
            f"{target_count}"
        )

        print(
            f"Rule Version         : "
            f"{RULE_VERSION}"
        )

        print(
            f"Target Version       : "
            f"{TARGET_VERSION}"
        )

        print(
            f"Target Status        : "
            f"{TARGET_STATUS}"
        )

        print(
            f"Frozen At UTC        : "
            f"{frozen_at_utc}"
        )

        print(
            f"Dataset SHA256       : "
            f"{dataset_sha256}"
        )

        print(
            f"Review Items         : "
            f"{len(review_items)}"
        )

        print(
            f"Hard Errors          : 0"
        )

        print(
            f"Post-Freeze Errors   : 0"
        )

        print("=" * 100)

        print(
            "RESULT: ZUUU_TARGET_V1 FREEZE COMPLETE"
        )

        print(
            "IMPORTANT: ZUUU_TARGET_V1 is now a frozen data asset."
        )

        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )