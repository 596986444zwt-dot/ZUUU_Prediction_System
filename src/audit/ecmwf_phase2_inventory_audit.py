"""
ECMWF Phase 2 Inventory Audit
=============================

Purpose:
    Read-only inventory audit of all existing ECMWF-related
    SQLite tables before continuing Phase 2.

Safety:
    - READ ONLY
    - No CREATE
    - No INSERT
    - No UPDATE
    - No DELETE
    - No schema modification

This script discovers:
    1. ECMWF-related tables
    2. Table schemas
    3. Row counts
    4. Time-related columns
    5. Min/max timestamps
    6. Distinct counts
    7. NULL counts
    8. Possible duplicate structures
    9. Status distributions
   10. Run-time distributions when detectable
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from src.database.zuuu_raw_archive import DEFAULT_DB_PATH


KEYWORDS = (
    "ecmwf",
    "forecast",
    "ifs",
    "run",
    "archive",
)

TIME_KEYWORDS = (
    "time",
    "date",
    "issue",
    "valid",
    "available",
    "ingest",
    "created",
    "updated",
)

STATUS_KEYWORDS = (
    "status",
    "state",
    "result",
    "availability",
)

RUN_KEYWORDS = (
    "run_time",
    "runtime",
    "run_datetime",
    "run_date",
    "issue_time",
    "issue_datetime",
    "initialization_time",
    "init_time",
    "forecast_reference_time",
)


def connect_read_only() -> sqlite3.Connection:

    db_path = Path(DEFAULT_DB_PATH).resolve()

    uri = db_path.as_uri() + "?mode=ro"

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    conn.row_factory = sqlite3.Row

    return conn


def quote_identifier(name: str) -> str:

    return '"' + name.replace('"', '""') + '"'


def get_tables(conn):

    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        ORDER BY name
        """
    ).fetchall()

    return [
        row["name"]
        for row in rows
        if not row["name"].startswith("sqlite_")
    ]


def is_ecmwf_related(table_name: str) -> bool:

    name = table_name.lower()

    return any(
        keyword in name
        for keyword in KEYWORDS
    )


def get_columns(conn, table_name):

    q = quote_identifier(table_name)

    rows = conn.execute(
        f"PRAGMA table_info({q})"
    ).fetchall()

    return [
        {
            "cid": row["cid"],
            "name": row["name"],
            "type": row["type"],
            "notnull": row["notnull"],
            "default": row["dflt_value"],
            "pk": row["pk"],
        }
        for row in rows
    ]


def get_indexes(conn, table_name):

    q = quote_identifier(table_name)

    rows = conn.execute(
        f"PRAGMA index_list({q})"
    ).fetchall()

    result = []

    for row in rows:

        index_name = row["name"]

        qi = quote_identifier(index_name)

        index_columns = conn.execute(
            f"PRAGMA index_info({qi})"
        ).fetchall()

        result.append(
            {
                "name": index_name,
                "unique": row["unique"],
                "columns": [
                    x["name"]
                    for x in index_columns
                ],
            }
        )

    return result


def row_count(conn, table_name):

    q = quote_identifier(table_name)

    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM {q}"
        ).fetchone()[0]
    )


def null_count(
    conn,
    table_name,
    column_name,
):

    qt = quote_identifier(table_name)
    qc = quote_identifier(column_name)

    return int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {qt}
            WHERE {qc} IS NULL
            """
        ).fetchone()[0]
    )


def distinct_count(
    conn,
    table_name,
    column_name,
):

    qt = quote_identifier(table_name)
    qc = quote_identifier(column_name)

    return int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT {qc})
            FROM {qt}
            WHERE {qc} IS NOT NULL
            """
        ).fetchone()[0]
    )


def min_max(
    conn,
    table_name,
    column_name,
):

    qt = quote_identifier(table_name)
    qc = quote_identifier(column_name)

    row = conn.execute(
        f"""
        SELECT
            MIN({qc}) AS minimum,
            MAX({qc}) AS maximum
        FROM {qt}
        WHERE {qc} IS NOT NULL
        """
    ).fetchone()

    return (
        row["minimum"],
        row["maximum"],
    )


def looks_like_time_column(name):

    n = name.lower()

    return any(
        keyword in n
        for keyword in TIME_KEYWORDS
    )


def looks_like_status_column(name):

    n = name.lower()

    return any(
        keyword in n
        for keyword in STATUS_KEYWORDS
    )


def looks_like_run_column(name):

    n = name.lower()

    if n in RUN_KEYWORDS:
        return True

    return (
        "run" in n
        and (
            "time" in n
            or "date" in n
            or "utc" in n
        )
    )


def print_status_distribution(
    conn,
    table_name,
    column_name,
):

    qt = quote_identifier(table_name)
    qc = quote_identifier(column_name)

    rows = conn.execute(
        f"""
        SELECT
            {qc} AS value,
            COUNT(*) AS n
        FROM {qt}
        GROUP BY {qc}
        ORDER BY n DESC
        LIMIT 30
        """
    ).fetchall()

    print(
        f"\n    Distribution: {column_name}"
    )

    for row in rows:

        print(
            f"        {str(row['value']):<35}"
            f" {row['n']:>10}"
        )


def print_run_hour_distribution(
    conn,
    table_name,
    column_name,
):

    qt = quote_identifier(table_name)
    qc = quote_identifier(column_name)

    try:

        rows = conn.execute(
            f"""
            SELECT {qc}
            FROM {qt}
            WHERE {qc} IS NOT NULL
            """
        ).fetchall()

        hours = Counter()

        for row in rows:

            value = str(
                row[column_name]
            )

            # Handles common ISO forms:
            #
            # YYYY-MM-DDTHH...
            # YYYY-MM-DD HH...
            #
            if len(value) >= 13:

                try:

                    if "T" in value:
                        hour = int(
                            value.split("T")[1][0:2]
                        )

                    elif " " in value:
                        hour = int(
                            value.split(" ")[1][0:2]
                        )

                    else:
                        continue

                    if 0 <= hour <= 23:
                        hours[hour] += 1

                except Exception:
                    pass

        if hours:

            print(
                f"\n    Run-hour distribution "
                f"from {column_name}:"
            )

            for hour in sorted(hours):

                print(
                    f"        {hour:02d}Z : "
                    f"{hours[hour]}"
                )

    except sqlite3.Error as exc:

        print(
            f"    Run-hour analysis failed: "
            f"{exc}"
        )


def inspect_table(
    conn,
    table_name,
):

    print()
    print("=" * 110)
    print(
        f"TABLE: {table_name}"
    )
    print("=" * 110)

    count = row_count(
        conn,
        table_name,
    )

    print(
        f"Rows: {count}"
    )

    columns = get_columns(
        conn,
        table_name,
    )

    print()
    print("Columns:")

    for column in columns:

        flags = []

        if column["pk"]:
            flags.append("PK")

        if column["notnull"]:
            flags.append("NOT NULL")

        flag_text = (
            ", ".join(flags)
            if flags
            else "-"
        )

        print(
            f"    "
            f"{column['name']:<35}"
            f"{column['type']:<18}"
            f"{flag_text}"
        )

    indexes = get_indexes(
        conn,
        table_name,
    )

    print()
    print("Indexes:")

    if not indexes:

        print("    NONE")

    else:

        for index in indexes:

            print(
                f"    "
                f"{index['name']}"
                f" | unique={bool(index['unique'])}"
                f" | columns={index['columns']}"
            )

    if count == 0:

        print()
        print(
            "Table is empty."
        )

        return

    # ------------------------------------------------------
    # Column statistics
    # ------------------------------------------------------

    print()
    print("Important Column Statistics:")

    for column in columns:

        name = column["name"]

        if (
            looks_like_time_column(name)
            or looks_like_status_column(name)
            or looks_like_run_column(name)
        ):

            try:

                nulls = null_count(
                    conn,
                    table_name,
                    name,
                )

                distinct = distinct_count(
                    conn,
                    table_name,
                    name,
                )

                minimum, maximum = min_max(
                    conn,
                    table_name,
                    name,
                )

                print()
                print(
                    f"    Column   : {name}"
                )

                print(
                    f"    NULL     : {nulls}"
                )

                print(
                    f"    Distinct : {distinct}"
                )

                print(
                    f"    Min      : {minimum}"
                )

                print(
                    f"    Max      : {maximum}"
                )

            except sqlite3.Error as exc:

                print(
                    f"    {name}: "
                    f"analysis failed: {exc}"
                )

    # ------------------------------------------------------
    # Status distributions
    # ------------------------------------------------------

    for column in columns:

        name = column["name"]

        if looks_like_status_column(
            name
        ):

            try:

                print_status_distribution(
                    conn,
                    table_name,
                    name,
                )

            except sqlite3.Error as exc:

                print(
                    f"    Status analysis failed "
                    f"for {name}: {exc}"
                )

    # ------------------------------------------------------
    # Run distributions
    # ------------------------------------------------------

    for column in columns:

        name = column["name"]

        if looks_like_run_column(
            name
        ):

            print_run_hour_distribution(
                conn,
                table_name,
                name,
            )


def main():

    print("=" * 110)
    print(
        "ECMWF PHASE 2 INVENTORY AUDIT"
    )
    print("=" * 110)

    print(
        f"Database : {DEFAULT_DB_PATH}"
    )

    print(
        "Mode     : READ ONLY"
    )

    print("=" * 110)

    conn = connect_read_only()

    try:

        tables = get_tables(
            conn
        )

        print()
        print(
            f"Database Tables : {len(tables)}"
        )

        print()
        print("All Tables:")

        for table in tables:

            print(
                f"    {table}"
            )

        ecmwf_tables = [
            table
            for table in tables
            if is_ecmwf_related(
                table
            )
        ]

        print()
        print("=" * 110)
        print(
            "ECMWF-RELATED TABLE DISCOVERY"
        )
        print("=" * 110)

        print(
            f"Detected Tables : "
            f"{len(ecmwf_tables)}"
        )

        if not ecmwf_tables:

            print()
            print(
                "No ECMWF-related tables "
                "were detected by table name."
            )

            print()
            print(
                "RESULT: INVENTORY COMPLETE — "
                "NO ECMWF TABLES DETECTED"
            )

            return 0

        for table in ecmwf_tables:

            print(
                f"    {table}"
            )

        total_rows = 0

        for table in ecmwf_tables:

            total_rows += row_count(
                conn,
                table,
            )

            inspect_table(
                conn,
                table,
            )

        print()
        print("=" * 110)
        print("FINAL REPORT")
        print("=" * 110)

        print(
            f"Database Tables       : "
            f"{len(tables)}"
        )

        print(
            f"ECMWF Related Tables  : "
            f"{len(ecmwf_tables)}"
        )

        print(
            f"Combined ECMWF Rows   : "
            f"{total_rows}"
        )

        print(
            "Database Modification : NONE"
        )

        print("=" * 110)

        print(
            "RESULT: ECMWF PHASE 2 "
            "INVENTORY AUDIT COMPLETE"
        )

        return 0

    finally:

        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )