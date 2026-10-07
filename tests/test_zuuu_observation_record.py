"""
Tests for ZUUU Observation Record V1.0
"""

from datetime import date, datetime, timezone

from src.parsers.zuuu_observation_record import (
    build_observation_record,
    classify_message,
    extract_station,
)


# ============================================================
# 1. IEM正常报文
# ============================================================

def test_iem_normal_record():

    raw = (
        "ZUUU 240200Z "
        "VRB01MPS CAVOK "
        "24/18 Q1016 NOSIG"
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            24,
            2,
            0,
            tzinfo=timezone.utc,
        ),
        source="IEM",
    )

    assert result.success is True

    assert result.error is None

    assert result.record is not None

    record = result.record

    assert record.station == "ZUUU"

    assert record.temperature_c == 24

    assert record.dewpoint_c == 18

    assert record.message_class == "PREFIXLESS"

    assert record.source == "IEM"

    assert record.is_recovered is False

    assert record.recovery_reason is None

    assert (
        record.business_date_bjt
        == date(2025, 9, 24)
    )

    assert (
        record.observation_time_bjt.hour
        == 10
    )

    assert record.raw_metar == raw


# ============================================================
# 2. 恢复的03:00
# ============================================================

def test_recovered_0300():

    raw = (
        "METAR ZUUU 240300Z "
        "18002MPS 120V240 "
        "CAVOK 25/18 "
        "Q1016 NOSIG="
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            24,
            3,
            0,
            tzinfo=timezone.utc,
        ),
        source="OGIMET",
        recovery_reason="IEM_MISSING",
    )

    assert result.success is True

    record = result.record

    assert record is not None

    assert record.temperature_c == 25

    assert record.dewpoint_c == 18

    assert record.temperature_token == "25/18"

    assert record.message_class == "METAR"

    assert record.source == "OGIMET"

    assert record.is_recovered is True

    assert (
        record.recovery_reason
        == "IEM_MISSING"
    )

    assert (
        record.observation_time_bjt.hour
        == 11
    )

    assert (
        record.business_date_bjt
        == date(2025, 9, 24)
    )

    # Raw必须完全保留
    assert record.raw_metar == raw


# ============================================================
# 3. 恢复的04:00
# ============================================================

def test_recovered_0400():

    raw = (
        "METAR ZUUU 240400Z "
        "VRB02MPS 9999 "
        "FEW026 26/17 "
        "Q1015 NOSIG="
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            24,
            4,
            0,
            tzinfo=timezone.utc,
        ),
        source="OGIMET",
        recovery_reason="IEM_MISSING",
    )

    assert result.success is True

    record = result.record

    assert record is not None

    assert record.temperature_c == 26

    assert record.dewpoint_c == 17

    assert record.source == "OGIMET"

    assert record.is_recovered is True

    assert (
        record.observation_time_bjt.hour
        == 12
    )


# ============================================================
# 4. UTC跨北京时间日期边界
# ============================================================

def test_bjt_business_date_boundary():

    raw = (
        "ZUUU 231600Z "
        "00000MPS CAVOK "
        "20/15 Q1015 NOSIG"
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            23,
            16,
            0,
            tzinfo=timezone.utc,
        ),
        source="IEM",
    )

    assert result.success is True

    record = result.record

    assert record is not None

    assert (
        record.business_date_bjt
        == date(2025, 9, 24)
    )

    assert (
        record.observation_time_bjt.hour
        == 0
    )


# ============================================================
# 5. COR
# ============================================================

def test_cor_record():

    raw = (
        "COR ZUUU 051430Z "
        "35002MPS CAVOK "
        "07/03 Q1023 NOSIG"
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            1,
            5,
            14,
            30,
            tzinfo=timezone.utc,
        ),
        source="IEM",
    )

    assert result.success is True

    record = result.record

    assert record is not None

    assert record.message_class == "COR"

    assert record.temperature_c == 7

    assert record.dewpoint_c == 3


# ============================================================
# 6. 负温
# ============================================================

def test_negative_temperature():

    raw = (
        "METAR ZUUU 010000Z "
        "00000MPS CAVOK "
        "M03/M08 Q1025 NOSIG="
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            1,
            1,
            0,
            0,
            tzinfo=timezone.utc,
        ),
        source="OGIMET",
    )

    assert result.success is True

    record = result.record

    assert record is not None

    assert record.temperature_c == -3

    assert record.dewpoint_c == -8


# ============================================================
# 7. 错误站点
# ============================================================

def test_wrong_station_rejected():

    raw = (
        "METAR ZBAA 240300Z "
        "00000MPS CAVOK "
        "25/18 Q1016 NOSIG="
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            24,
            3,
            0,
            tzinfo=timezone.utc,
        ),
        source="OGIMET",
    )

    assert result.success is False

    assert result.record is None

    assert (
        result.error
        == "INVALID_STATION:ZBAA"
    )


# ============================================================
# 8. 未知Source
# ============================================================

def test_unknown_source_rejected():

    raw = (
        "ZUUU 240300Z "
        "00000MPS CAVOK "
        "25/18 Q1016 NOSIG"
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            24,
            3,
            0,
            tzinfo=timezone.utc,
        ),
        source="UNKNOWN",
    )

    assert result.success is False

    assert (
        result.error
        == "UNSUPPORTED_SOURCE:UNKNOWN"
    )


# ============================================================
# 9. Naive datetime禁止
# ============================================================

def test_naive_datetime_rejected():

    raw = (
        "ZUUU 240300Z "
        "00000MPS CAVOK "
        "25/18 Q1016 NOSIG"
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            24,
            3,
            0,
        ),
        source="IEM",
    )

    assert result.success is False

    assert result.error is not None

    assert result.error.startswith(
        "TIME_NORMALIZATION_ERROR:"
    )


# ============================================================
# 10. 无温度
# ============================================================

def test_missing_temperature_rejected():

    raw = (
        "ZUUU 240300Z "
        "18002MPS CAVOK "
        "Q1016 NOSIG"
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            24,
            3,
            0,
            tzinfo=timezone.utc,
        ),
        source="IEM",
    )

    assert result.success is False

    assert result.error is not None

    assert result.error.startswith(
        "TEMPERATURE_ERROR:"
    )


# ============================================================
# 11. Raw不能被修改
# ============================================================

def test_raw_preserved_exactly():

    raw = (
        "METAR ZUUU 240300Z  "
        "18002MPS 120V240 "
        "CAVOK 25/18 "
        "Q1016 NOSIG="
    )

    result = build_observation_record(
        raw_metar=raw,
        observation_time=datetime(
            2025,
            9,
            24,
            3,
            0,
            tzinfo=timezone.utc,
        ),
        source="OGIMET",
        recovery_reason="IEM_MISSING",
    )

    assert result.success is True

    assert result.record is not None

    assert (
        result.record.raw_metar
        == raw
    )


# ============================================================
# 12. Message Classification
# ============================================================

def test_message_classification():

    assert (
        classify_message(
            "ZUUU 240300Z CAVOK 25/18 Q1016"
        )
        == "PREFIXLESS"
    )

    assert (
        classify_message(
            "METAR ZUUU 240300Z CAVOK 25/18 Q1016"
        )
        == "METAR"
    )

    assert (
        classify_message(
            "SPECI ZUUU 240330Z CAVOK 25/18 Q1016"
        )
        == "SPECI"
    )

    assert (
        classify_message(
            "COR ZUUU 240300Z CAVOK 25/18 Q1016"
        )
        == "COR"
    )

    assert (
        classify_message(
            "AMD ZUUU 240300Z CAVOK 25/18 Q1016"
        )
        == "AMD"
    )


# ============================================================
# 13. Station Extraction
# ============================================================

def test_station_extraction():

    assert (
        extract_station(
            "ZUUU 240300Z CAVOK 25/18 Q1016"
        )
        == "ZUUU"
    )

    assert (
        extract_station(
            "METAR ZUUU 240300Z CAVOK 25/18 Q1016"
        )
        == "ZUUU"
    )

    assert (
        extract_station(
            "COR ZUUU 240300Z CAVOK 25/18 Q1016"
        )
        == "ZUUU"
    )