"""
ZUUU Time Normalizer V1.0
=========================

职责：
1. UTC -> Asia/Shanghai
2. 生成北京时间业务日
3. 生成北京时间自然日对应的UTC边界

原则：
- 全部datetime必须timezone-aware
- 禁止手工 UTC + 8小时
- UTC与北京时间同时保留
- Business Day = Asia/Shanghai自然日
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


UTC = timezone.utc
BJT = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class NormalizedTime:
    observation_time_utc: datetime
    observation_time_bjt: datetime
    business_date_bjt: date


def ensure_utc_aware(dt: datetime) -> datetime:
    """
    确保输入是timezone-aware datetime，
    并统一转换为UTC。
    """

    if dt.tzinfo is None:
        raise ValueError(
            "NAIVE_DATETIME_NOT_ALLOWED"
        )

    return dt.astimezone(UTC)


def utc_to_bjt(dt: datetime) -> datetime:
    """
    UTC/其他aware时间 -> Asia/Shanghai
    """

    utc_dt = ensure_utc_aware(dt)

    return utc_dt.astimezone(BJT)


def normalize_observation_time(
    dt: datetime,
) -> NormalizedTime:
    """
    标准化一个观测时间。
    """

    utc_dt = ensure_utc_aware(dt)

    bjt_dt = utc_dt.astimezone(BJT)

    return NormalizedTime(
        observation_time_utc=utc_dt,
        observation_time_bjt=bjt_dt,
        business_date_bjt=bjt_dt.date(),
    )


def bjt_day_utc_bounds(
    business_date: date,
) -> tuple[datetime, datetime]:
    """
    返回一个北京时间自然日对应的UTC半开区间：

        [start_utc, end_utc)

    例如：

        BJT 2025-09-24 00:00
        ->
        UTC 2025-09-23 16:00

        BJT 2025-09-25 00:00
        ->
        UTC 2025-09-24 16:00

    使用半开区间可以避免：
        23:59:59.999999
        精度边界问题。
    """

    start_bjt = datetime.combine(
        business_date,
        time.min,
        tzinfo=BJT,
    )

    # 下一自然日00:00
    next_date = (
        business_date.fromordinal(
            business_date.toordinal() + 1
        )
    )

    end_bjt = datetime.combine(
        next_date,
        time.min,
        tzinfo=BJT,
    )

    return (
        start_bjt.astimezone(UTC),
        end_bjt.astimezone(UTC),
    )


def belongs_to_bjt_day(
    observation_time: datetime,
    business_date: date,
) -> bool:
    """
    判断某个观测是否属于指定北京时间自然日。
    """

    utc_dt = ensure_utc_aware(
        observation_time
    )

    start_utc, end_utc = (
        bjt_day_utc_bounds(
            business_date
        )
    )

    return (
        start_utc
        <= utc_dt
        < end_utc
    )