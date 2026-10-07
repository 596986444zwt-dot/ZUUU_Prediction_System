from datetime import datetime, timezone

from src.database.zuuu_raw_archive import (
    RawArchiveRecord,
    ZUUURawArchive,
)


def make_archive(tmp_path):

    archive = ZUUURawArchive(
        tmp_path / "test.db"
    )

    archive.initialize()

    return archive


def test_insert_iem(tmp_path):

    archive = make_archive(
        tmp_path
    )

    raw = (
        "ZUUU 240200Z "
        "VRB01MPS CAVOK "
        "24/18 Q1016 NOSIG"
    )

    record = RawArchiveRecord(
        station="ZUUU",
        source="IEM",
        source_query_class="ROUTINE",
        message_class="PREFIXLESS",
        observation_time_utc=datetime(
            2025, 9, 24, 2, 0,
            tzinfo=timezone.utc,
        ),
        raw_metar=raw,
    )

    inserted = archive.insert(
        record
    )

    assert inserted is True
    assert archive.count() == 1

    rows = archive.fetch_all()

    assert rows[0]["source"] == "IEM"
    assert rows[0]["raw_metar"] == raw


def test_duplicate_does_not_overwrite(tmp_path):

    archive = make_archive(
        tmp_path
    )

    raw = (
        "ZUUU 240200Z "
        "VRB01MPS CAVOK "
        "24/18 Q1016 NOSIG"
    )

    record = RawArchiveRecord(
        station="ZUUU",
        source="IEM",
        source_query_class="ROUTINE",
        message_class="PREFIXLESS",
        observation_time_utc=datetime(
            2025, 9, 24, 2, 0,
            tzinfo=timezone.utc,
        ),
        raw_metar=raw,
    )

    first = archive.insert(
        record
    )

    second = archive.insert(
        record
    )

    assert first is True
    assert second is False

    assert archive.count() == 1


def test_ogimet_recovery_0300(tmp_path):

    archive = make_archive(
        tmp_path
    )

    raw = (
        "METAR ZUUU 240300Z "
        "18002MPS 120V240 "
        "CAVOK 25/18 "
        "Q1016 NOSIG="
    )

    record = RawArchiveRecord(
        station="ZUUU",
        source="OGIMET",
        source_query_class=None,
        message_class="METAR",
        observation_time_utc=datetime(
            2025, 9, 24, 3, 0,
            tzinfo=timezone.utc,
        ),
        raw_metar=raw,
        recovery_reason="IEM_MISSING",
    )

    assert archive.insert(
        record
    ) is True

    rows = archive.fetch_all()

    assert len(rows) == 1

    row = rows[0]

    assert row["source"] == "OGIMET"

    assert (
        row["recovery_reason"]
        == "IEM_MISSING"
    )

    assert row["raw_metar"] == raw


def test_ogimet_recovery_0400(tmp_path):

    archive = make_archive(
        tmp_path
    )

    raw = (
        "METAR ZUUU 240400Z "
        "VRB02MPS 9999 "
        "FEW026 26/17 "
        "Q1015 NOSIG="
    )

    record = RawArchiveRecord(
        station="ZUUU",
        source="OGIMET",
        source_query_class=None,
        message_class="METAR",
        observation_time_utc=datetime(
            2025, 9, 24, 4, 0,
            tzinfo=timezone.utc,
        ),
        raw_metar=raw,
        recovery_reason="IEM_MISSING",
    )

    assert archive.insert(
        record
    ) is True

    rows = archive.fetch_all()

    assert (
        rows[0]["raw_metar"]
        == raw
    )


def test_same_time_different_raw_preserved(
    tmp_path,
):

    archive = make_archive(
        tmp_path
    )

    observation_time = datetime(
        2025, 1, 5, 14, 30,
        tzinfo=timezone.utc,
    )

    raw1 = (
        "ZUUU 051430Z "
        "35002MPS CAVOK "
        "08/03 Q1023 NOSIG"
    )

    raw2 = (
        "COR ZUUU 051430Z "
        "35002MPS CAVOK "
        "07/03 Q1023 NOSIG"
    )

    archive.insert(
        RawArchiveRecord(
            station="ZUUU",
            source="IEM",
            source_query_class="SPECIAL",
            message_class="PREFIXLESS",
            observation_time_utc=(
                observation_time
            ),
            raw_metar=raw1,
        )
    )

    archive.insert(
        RawArchiveRecord(
            station="ZUUU",
            source="IEM",
            source_query_class="SPECIAL",
            message_class="COR",
            observation_time_utc=(
                observation_time
            ),
            raw_metar=raw2,
        )
    )

    # Bronze不能因为时间相同覆盖旧Raw
    assert archive.count() == 2


def test_same_report_different_source_preserved(
    tmp_path,
):

    archive = make_archive(
        tmp_path
    )

    observation_time = datetime(
        2025, 9, 24, 3, 0,
        tzinfo=timezone.utc,
    )

    iem_raw = (
        "ZUUU 240300Z "
        "18002MPS 120V240 "
        "CAVOK 25/18 Q1016 NOSIG"
    )

    ogimet_raw = (
        "METAR ZUUU 240300Z "
        "18002MPS 120V240 "
        "CAVOK 25/18 Q1016 NOSIG="
    )

    archive.insert(
        RawArchiveRecord(
            station="ZUUU",
            source="IEM",
            source_query_class="ROUTINE",
            message_class="PREFIXLESS",
            observation_time_utc=(
                observation_time
            ),
            raw_metar=iem_raw,
        )
    )

    archive.insert(
        RawArchiveRecord(
            station="ZUUU",
            source="OGIMET",
            source_query_class=None,
            message_class="METAR",
            observation_time_utc=(
                observation_time
            ),
            raw_metar=ogimet_raw,
        )
    )

    assert archive.count() == 2


def test_naive_time_rejected(
    tmp_path,
):

    archive = make_archive(
        tmp_path
    )

    record = RawArchiveRecord(
        station="ZUUU",
        source="IEM",
        source_query_class="ROUTINE",
        message_class="PREFIXLESS",
        observation_time_utc=datetime(
            2025, 9, 24, 3, 0
        ),
        raw_metar=(
            "ZUUU 240300Z "
            "CAVOK 25/18 Q1016"
        ),
    )

    try:

        archive.insert(
            record
        )

        assert False

    except ValueError as exc:

        assert (
            str(exc)
            == "NAIVE_DATETIME_NOT_ALLOWED"
        )