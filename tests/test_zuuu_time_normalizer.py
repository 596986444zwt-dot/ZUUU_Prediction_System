"""
Tests for ZUUU Time Normalizer V1.0
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.parsers.zuuu_time_normalizer import (
    BJT,
    UTC,
    belongs_to_bjt_day,
    bjt_day_utc_bounds,
    ensure_utc_aware,
    normalize_observation_time,
    utc_to_bjt,
)


def test_utc_to_bjt():

    utc_time = datetime(
        2025, 9, 24, 3, 0,
        tzinfo=timezone.utc,
    )

    result = utc_to_bjt(
        utc_time
    )

    assert result.year == 2025
    assert result.month == 9
    assert result.day == 24
    assert result.hour == 11
    assert result.minute == 0

    assert result.tzinfo == BJT


def test_recovered_0300():

    utc_time = datetime(
        2025, 9, 24, 3, 0,
        tzinfo=UTC,
    )

    result = normalize_observation_time(
        utc_time
    )

    assert (
        result.observation_time_bjt.hour
        == 11
    )

    assert (
        result.business_date_bjt
        == date(2025, 9, 24)
    )


def test_recovered_0400():

    utc_time = datetime(
        2025, 9, 24, 4, 0,
        tzinfo=UTC,
    )

    result = normalize_observation_time(
        utc_time
    )

    assert (
        result.observation_time_bjt.hour
        == 12
    )

    assert (
        result.business_date_bjt
        == date(2025, 9, 24)
    )


def test_bjt_midnight_boundary():

    utc_time = datetime(
        2025, 9, 23, 16, 0,
        tzinfo=UTC,
    )

    result = normalize_observation_time(
        utc_time
    )

    assert (
        result.observation_time_bjt
        == datetime(
            2025,
            9,
            24,
            0,
            0,
            tzinfo=BJT,
        )
    )

    assert (
        result.business_date_bjt
        == date(2025, 9, 24)
    )


def test_one_minute_before_bjt_midnight():

    utc_time = datetime(
        2025, 9, 23, 15, 59,
        tzinfo=UTC,
    )

    result = normalize_observation_time(
        utc_time
    )

    assert (
        result.observation_time_bjt
        == datetime(
            2025,
            9,
            23,
            23,
            59,
            tzinfo=BJT,
        )
    )

    assert (
        result.business_date_bjt
        == date(2025, 9, 23)
    )


def test_next_bjt_day_boundary():

    utc_time = datetime(
        2025, 9, 24, 16, 0,
        tzinfo=UTC,
    )

    result = normalize_observation_time(
        utc_time
    )

    assert (
        result.business_date_bjt
        == date(2025, 9, 25)
    )

    assert (
        result.observation_time_bjt.hour
        == 0
    )


def test_bjt_day_utc_bounds():

    start, end = (
        bjt_day_utc_bounds(
            date(2025, 9, 24)
        )
    )

    assert start == datetime(
        2025,
        9,
        23,
        16,
        0,
        tzinfo=UTC,
    )

    assert end == datetime(
        2025,
        9,
        24,
        16,
        0,
        tzinfo=UTC,
    )


def test_belongs_start_boundary():

    observation = datetime(
        2025,
        9,
        23,
        16,
        0,
        tzinfo=UTC,
    )

    assert belongs_to_bjt_day(
        observation,
        date(2025, 9, 24),
    )


def test_belongs_last_hour():

    observation = datetime(
        2025,
        9,
        24,
        15,
        0,
        tzinfo=UTC,
    )

    assert belongs_to_bjt_day(
        observation,
        date(2025, 9, 24),
    )


def test_next_day_not_in_previous():

    observation = datetime(
        2025,
        9,
        24,
        16,
        0,
        tzinfo=UTC,
    )

    assert not belongs_to_bjt_day(
        observation,
        date(2025, 9, 24),
    )

    assert belongs_to_bjt_day(
        observation,
        date(2025, 9, 25),
    )


def test_naive_datetime_rejected():

    naive = datetime(
        2025,
        9,
        24,
        3,
        0,
    )

    with pytest.raises(
        ValueError,
        match="NAIVE_DATETIME_NOT_ALLOWED",
    ):

        ensure_utc_aware(
            naive
        )


def test_non_utc_aware_input():

    shanghai_time = datetime(
        2025,
        9,
        24,
        11,
        0,
        tzinfo=ZoneInfo(
            "Asia/Shanghai"
        ),
    )

    result = ensure_utc_aware(
        shanghai_time
    )

    assert result == datetime(
        2025,
        9,
        24,
        3,
        0,
        tzinfo=UTC,
    )