"""
ZUUU Ogimet Recovery Commit V1.0
================================

用途：
    将已经独立验证过的 IEM 缺失记录，
    以 OGIMET 来源正式写入 Bronze Raw Archive。

当前恢复：
    2025-09-24 03:00 UTC
    2025-09-24 04:00 UTC

原则：
    - 不伪装成 IEM
    - Raw 保留 Ogimet 原始形式
    - recovery_reason = IEM_MISSING
    - INSERT OR IGNORE，重复运行不会重复写入
"""

from datetime import datetime, timezone

from src.database.zuuu_raw_archive import (
    RawArchiveRecord,
    ZUUURawArchive,
)


UTC = timezone.utc


RECOVERY_RECORDS = [

    RawArchiveRecord(
        station="ZUUU",
        source="OGIMET",
        source_query_class=None,
        message_class="METAR",
        observation_time_utc=datetime(
            2025, 9, 24, 3, 0,
            tzinfo=UTC,
        ),
        raw_metar=(
            "METAR ZUUU 240300Z "
            "18002MPS 120V240 "
            "CAVOK 25/18 "
            "Q1016 NOSIG="
        ),
        recovery_reason="IEM_MISSING",
    ),

    RawArchiveRecord(
        station="ZUUU",
        source="OGIMET",
        source_query_class=None,
        message_class="METAR",
        observation_time_utc=datetime(
            2025, 9, 24, 4, 0,
            tzinfo=UTC,
        ),
        raw_metar=(
            "METAR ZUUU 240400Z "
            "VRB02MPS 9999 "
            "FEW026 26/17 "
            "Q1015 NOSIG="
        ),
        recovery_reason="IEM_MISSING",
    ),
]


def main():

    archive = ZUUURawArchive()

    archive.initialize()

    print("=" * 70)
    print("ZUUU Ogimet Recovery Commit V1.0")
    print("=" * 70)

    print(
        f"数据库：{archive.db_path}"
    )

    print(
        f"恢复记录：{len(RECOVERY_RECORDS)}"
    )

    print("=" * 70)

    inserted_count = 0
    existing_count = 0

    for record in RECOVERY_RECORDS:

        print()
        print(
            "UTC：",
            record.observation_time_utc.isoformat(),
        )

        print(
            "Source：",
            record.source,
        )

        print(
            "Reason：",
            record.recovery_reason,
        )

        print(
            "Raw：",
            record.raw_metar,
        )

        inserted = archive.insert(
            record
        )

        if inserted:

            inserted_count += 1

            print(
                "RESULT：INSERTED"
            )

        else:

            existing_count += 1

            print(
                "RESULT：ALREADY EXISTS"
            )

    print()
    print("=" * 70)
    print("FINAL REPORT")
    print("=" * 70)

    print(
        f"Recovery Records : {len(RECOVERY_RECORDS)}"
    )

    print(
        f"Inserted         : {inserted_count}"
    )

    print(
        f"Already Existing : {existing_count}"
    )

    print(
        f"Database Total   : {archive.count()}"
    )

    print("=" * 70)

    if inserted_count + existing_count != 2:

        raise RuntimeError(
            "RECOVERY_COUNT_MISMATCH"
        )

    print(
        "RESULT: OGIMET RECOVERY COMMIT COMPLETE"
    )


if __name__ == "__main__":
    main()