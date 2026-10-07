from datetime import datetime, timezone

from src.database.zuuu_silver_archive import (
    ZUUUSilverArchive,
)


UTC = timezone.utc


def make_archive(tmp_path):

    db_path = (
        tmp_path
        / "test_silver.db"
    )

    archive = ZUUUSilverArchive(
        db_path
    )

    # 测试库需要一个最小 Bronze 表，
    # 用于满足 foreign key 设计语义。
    with archive.connect() as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            zuuu_raw_metar
            (
                id INTEGER PRIMARY KEY
            )
            """
        )

        conn.execute(
            """
            INSERT INTO zuuu_raw_metar(id)
            VALUES (1)
            """
        )

        conn.execute(
            """
            INSERT INTO zuuu_raw_metar(id)
            VALUES (2)
            """
        )

        conn.commit()

    archive.initialize()

    return archive


def insert_sample(
    archive,
    bronze_raw_id=1,
    message_class="PREFIXLESS",
    is_correction=False,
):

    return archive.insert(
        bronze_raw_id=bronze_raw_id,
        station="ZUUU",
        observation_time_utc=(
            "2025-09-24T03:00:00+00:00"
        ),
        observation_time_bjt=(
            "2025-09-24T11:00:00+08:00"
        ),
        business_date_bjt=(
            "2025-09-24"
        ),
        temperature_c=25.0,
        dewpoint_c=18.0,
        source="IEM",
        source_query_class="ROUTINE",
        message_class=message_class,
        is_correction=is_correction,
        supersedes_raw_id=None,
        recovery_reason=None,
        qc_status="PASS",
        qc_flags=None,
        created_at_utc=(
            datetime.now(UTC).isoformat()
        ),
    )


def test_initialize(tmp_path):

    archive = make_archive(
        tmp_path
    )

    assert archive.count() == 0


def test_insert(tmp_path):

    archive = make_archive(
        tmp_path
    )

    inserted = insert_sample(
        archive
    )

    assert inserted is True

    assert archive.count() == 1


def test_duplicate_bronze_raw_id_is_ignored(
    tmp_path,
):

    archive = make_archive(
        tmp_path
    )

    first = insert_sample(
        archive
    )

    second = insert_sample(
        archive
    )

    assert first is True
    assert second is False

    assert archive.count() == 1


def test_fetch_by_bronze_raw_id(
    tmp_path,
):

    archive = make_archive(
        tmp_path
    )

    insert_sample(
        archive
    )

    row = (
        archive.fetch_by_bronze_raw_id(
            1
        )
    )

    assert row is not None

    assert row["station"] == "ZUUU"

    assert (
        row["temperature_c"]
        == 25.0
    )

    assert (
        row["business_date_bjt"]
        == "2025-09-24"
    )


def test_correction_flag(
    tmp_path,
):

    archive = make_archive(
        tmp_path
    )

    inserted = insert_sample(
        archive,
        message_class="COR",
        is_correction=True,
    )

    assert inserted is True

    row = (
        archive.fetch_by_bronze_raw_id(
            1
        )
    )

    assert row["message_class"] == "COR"

    assert row["is_correction"] == 1

    assert (
        row["supersedes_raw_id"]
        is None
    )


def test_recovery_provenance(
    tmp_path,
):

    archive = make_archive(
        tmp_path
    )

    inserted = archive.insert(
        bronze_raw_id=2,
        station="ZUUU",
        observation_time_utc=(
            "2025-09-24T04:00:00+00:00"
        ),
        observation_time_bjt=(
            "2025-09-24T12:00:00+08:00"
        ),
        business_date_bjt=(
            "2025-09-24"
        ),
        temperature_c=26.0,
        dewpoint_c=17.0,
        source="OGIMET",
        source_query_class=None,
        message_class="METAR",
        is_correction=False,
        supersedes_raw_id=None,
        recovery_reason="IEM_MISSING",
        qc_status="PASS",
        qc_flags="RECOVERY_SOURCE",
        created_at_utc=(
            datetime.now(UTC).isoformat()
        ),
    )

    assert inserted is True

    row = (
        archive.fetch_by_bronze_raw_id(
            2
        )
    )

    assert row["source"] == "OGIMET"

    assert (
        row["recovery_reason"]
        == "IEM_MISSING"
    )

    assert (
        row["qc_flags"]
        == "RECOVERY_SOURCE"
    )


def test_fetch_all_order(
    tmp_path,
):

    archive = make_archive(
        tmp_path
    )

    insert_sample(
        archive,
        bronze_raw_id=1,
    )

    archive.insert(
        bronze_raw_id=2,
        station="ZUUU",
        observation_time_utc=(
            "2025-09-24T04:00:00+00:00"
        ),
        observation_time_bjt=(
            "2025-09-24T12:00:00+08:00"
        ),
        business_date_bjt=(
            "2025-09-24"
        ),
        temperature_c=26.0,
        dewpoint_c=17.0,
        source="OGIMET",
        source_query_class=None,
        message_class="METAR",
        is_correction=False,
        supersedes_raw_id=None,
        recovery_reason="IEM_MISSING",
        qc_status="PASS",
        qc_flags=None,
        created_at_utc=(
            datetime.now(UTC).isoformat()
        ),
    )

    rows = archive.fetch_all()

    assert len(rows) == 2

    assert (
        rows[0]["bronze_raw_id"]
        == 1
    )

    assert (
        rows[1]["bronze_raw_id"]
        == 2
    )