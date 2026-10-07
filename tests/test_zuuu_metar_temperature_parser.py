"""
Tests for ZUUU METAR Temperature Parser V1.0
"""

from src.parsers.zuuu_metar_temperature_parser import (
    extract_temperature_c,
    parse_signed_temperature,
    parse_temperature,
)


def test_positive_temperature():

    raw = (
        "ZUUU 240300Z "
        "18002MPS 120V240 "
        "CAVOK 25/18 "
        "Q1016 NOSIG"
    )

    result = parse_temperature(
        raw
    )

    assert result.success is True

    assert result.temperature_c == 25

    assert result.dewpoint_c == 18

    assert result.token == "25/18"


def test_ogimet_metar():

    raw = (
        "METAR ZUUU 240400Z "
        "VRB02MPS 9999 "
        "FEW026 26/17 "
        "Q1015 NOSIG="
    )

    result = parse_temperature(
        raw
    )

    assert result.success is True

    assert result.temperature_c == 26

    assert result.dewpoint_c == 17


def test_cor_metar():

    raw = (
        "COR ZUUU 051430Z "
        "35002MPS CAVOK "
        "07/03 Q1023 NOSIG"
    )

    result = parse_temperature(
        raw
    )

    assert result.success is True

    assert result.temperature_c == 7

    assert result.dewpoint_c == 3


def test_negative_temperature():

    raw = (
        "ZUUU 010000Z "
        "00000MPS CAVOK "
        "M03/M08 "
        "Q1025 NOSIG"
    )

    result = parse_temperature(
        raw
    )

    assert result.success is True

    assert result.temperature_c == -3

    assert result.dewpoint_c == -8


def test_zero_temperature():

    raw = (
        "ZUUU 010000Z "
        "00000MPS CAVOK "
        "00/M01 "
        "Q1025 NOSIG"
    )

    result = parse_temperature(
        raw
    )

    assert result.success is True

    assert result.temperature_c == 0

    assert result.dewpoint_c == -1


def test_negative_zero():

    result = parse_signed_temperature(
        "M00"
    )

    assert result == 0


def test_missing_dewpoint():

    raw = (
        "ZUUU 010000Z "
        "00000MPS CAVOK "
        "25/ "
        "Q1015 NOSIG"
    )

    result = parse_temperature(
        raw
    )

    assert result.success is True

    assert result.temperature_c == 25

    assert result.dewpoint_c is None


def test_empty_metar():

    result = parse_temperature(
        ""
    )

    assert result.success is False

    assert result.temperature_c is None

    assert result.error == "EMPTY_RAW_METAR"


def test_no_temperature():

    raw = (
        "ZUUU 240300Z "
        "18002MPS CAVOK "
        "Q1016 NOSIG"
    )

    result = parse_temperature(
        raw
    )

    assert result.success is False

    assert result.temperature_c is None

    assert (
        result.error
        == "TEMPERATURE_TOKEN_NOT_FOUND"
    )


def test_extract_temperature():

    raw = (
        "METAR ZUUU 240300Z "
        "18002MPS CAVOK "
        "25/18 Q1016 NOSIG="
    )

    temperature = (
        extract_temperature_c(
            raw
        )
    )

    assert temperature == 25


def test_recovered_0300():

    raw = (
        "METAR ZUUU 240300Z "
        "18002MPS 120V240 "
        "CAVOK 25/18 "
        "Q1016 NOSIG="
    )

    result = parse_temperature(
        raw
    )

    assert result.success is True

    assert result.temperature_c == 25

    assert result.dewpoint_c == 18


def test_recovered_0400():

    raw = (
        "METAR ZUUU 240400Z "
        "VRB02MPS 9999 "
        "FEW026 26/17 "
        "Q1015 NOSIG="
    )

    result = parse_temperature(
        raw
    )

    assert result.success is True

    assert result.temperature_c == 26

    assert result.dewpoint_c == 17