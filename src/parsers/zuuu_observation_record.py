"""
ZUUU Observation Record V1.0
============================

职责：
    将一条ZUUU Raw METAR + Observation Time
    转换为标准化观测记录。

组合：
    1. Raw METAR
    2. UTC Observation Time
    3. Asia/Shanghai Observation Time
    4. BJT Business Date
    5. Temperature
    6. Dew Point
    7. Source
    8. Message Class
    9. Recovery Metadata

原则：
    - Raw原文保留，不修改
    - UTC/BJT同时保留
    - 不负责数据库写入
    - 不负责Daily Tmax
    - 不负责COR覆盖规则
    - 不负责最终Ground Truth决策
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from src.parsers.zuuu_metar_temperature_parser import (
    parse_temperature,
)

from src.parsers.zuuu_time_normalizer import (
    normalize_observation_time,
)


# ============================================================
# 1. 支持的数据来源
# ============================================================

ALLOWED_SOURCES = {
    "IEM",
    "OGIMET",
}


# ============================================================
# 2. Message Class
# ============================================================

def classify_message(
    raw_metar: str,
) -> str:
    """
    根据Raw报文本身分类。

    注意：
        这里只描述Raw Message Class。

        不等同于IEM的：
            report_type=3
            report_type=4
    """

    text = raw_metar.strip().upper()

    if text.startswith("METAR "):
        return "METAR"

    if text.startswith("SPECI "):
        return "SPECI"

    if text.startswith("COR "):
        return "COR"

    if text.startswith("AMD "):
        return "AMD"

    if text.startswith("ZUUU "):
        return "PREFIXLESS"

    return "OTHER"


# ============================================================
# 3. Station
# ============================================================

def extract_station(
    raw_metar: str,
) -> str | None:
    """
    从Raw报文中提取ICAO站号。
    """

    text = raw_metar.strip().upper()

    if not text:
        return None

    tokens = text.split()

    if not tokens:
        return None

    # Prefixless:
    #
    # ZUUU 240300Z ...

    if len(tokens) >= 1:

        if tokens[0] == "ZUUU":
            return "ZUUU"

    # Explicit prefix:
    #
    # METAR ZUUU ...
    # SPECI ZUUU ...
    # COR ZUUU ...
    # AMD ZUUU ...

    if (
        len(tokens) >= 2
        and tokens[0]
        in {
            "METAR",
            "SPECI",
            "COR",
            "AMD",
        }
    ):

        return tokens[1]

    return None


# ============================================================
# 4. Standard Observation Record
# ============================================================

@dataclass(frozen=True)
class ZUUUObservationRecord:

    # --------------------------------------------------------
    # Identity
    # --------------------------------------------------------

    station: str

    # --------------------------------------------------------
    # Time
    # --------------------------------------------------------

    observation_time_utc: datetime

    observation_time_bjt: datetime

    business_date_bjt: date

    # --------------------------------------------------------
    # Meteorological fields
    # --------------------------------------------------------

    temperature_c: int

    dewpoint_c: int | None

    temperature_token: str

    # --------------------------------------------------------
    # Message
    # --------------------------------------------------------

    message_class: str

    raw_metar: str

    # --------------------------------------------------------
    # Lineage
    # --------------------------------------------------------

    source: str

    is_recovered: bool

    recovery_reason: str | None


# ============================================================
# 5. Build Result
# ============================================================

@dataclass(frozen=True)
class ObservationBuildResult:

    success: bool

    record: ZUUUObservationRecord | None

    error: str | None


# ============================================================
# 6. Builder
# ============================================================

def build_observation_record(
    *,
    raw_metar: str,
    observation_time: datetime,
    source: str,
    recovery_reason: str | None = None,
) -> ObservationBuildResult:
    """
    建立一条标准ZUUU观测记录。

    注意：
        observation_time必须是timezone-aware。

    source示例：
        IEM
        OGIMET

    recovery_reason示例：
        IEM_MISSING
    """

    # ========================================================
    # Raw检查
    # ========================================================

    if not raw_metar:

        return ObservationBuildResult(
            success=False,
            record=None,
            error="EMPTY_RAW_METAR",
        )

    if not raw_metar.strip():

        return ObservationBuildResult(
            success=False,
            record=None,
            error="EMPTY_RAW_METAR",
        )


    # ========================================================
    # Source
    # ========================================================

    source_normalized = (
        source.strip().upper()
        if source
        else ""
    )

    if (
        source_normalized
        not in ALLOWED_SOURCES
    ):

        return ObservationBuildResult(
            success=False,
            record=None,
            error=(
                "UNSUPPORTED_SOURCE:"
                f"{source_normalized}"
            ),
        )


    # ========================================================
    # Station
    # ========================================================

    station = extract_station(
        raw_metar
    )

    if station != "ZUUU":

        return ObservationBuildResult(
            success=False,
            record=None,
            error=(
                "INVALID_STATION:"
                f"{station}"
            ),
        )


    # ========================================================
    # Message Class
    # ========================================================

    message_class = (
        classify_message(
            raw_metar
        )
    )

    if message_class == "OTHER":

        return ObservationBuildResult(
            success=False,
            record=None,
            error="UNKNOWN_MESSAGE_CLASS",
        )


    # ========================================================
    # Temperature
    # ========================================================

    temperature_result = (
        parse_temperature(
            raw_metar
        )
    )

    if not temperature_result.success:

        return ObservationBuildResult(
            success=False,
            record=None,
            error=(
                "TEMPERATURE_ERROR:"
                f"{temperature_result.error}"
            ),
        )

    if (
        temperature_result.temperature_c
        is None
    ):

        return ObservationBuildResult(
            success=False,
            record=None,
            error="TEMPERATURE_IS_NONE",
        )

    if (
        temperature_result.token
        is None
    ):

        return ObservationBuildResult(
            success=False,
            record=None,
            error="TEMPERATURE_TOKEN_IS_NONE",
        )


    # ========================================================
    # Time
    # ========================================================

    try:

        normalized_time = (
            normalize_observation_time(
                observation_time
            )
        )

    except Exception as exc:

        return ObservationBuildResult(
            success=False,
            record=None,
            error=(
                "TIME_NORMALIZATION_ERROR:"
                f"{exc}"
            ),
        )


    # ========================================================
    # Recovery metadata
    # ========================================================

    is_recovered = (
        recovery_reason is not None
    )

    if recovery_reason is not None:

        recovery_reason = (
            recovery_reason
            .strip()
            .upper()
        )

        if not recovery_reason:

            recovery_reason = None

            is_recovered = False


    # ========================================================
    # Build
    # ========================================================

    record = ZUUUObservationRecord(

        station="ZUUU",

        observation_time_utc=(
            normalized_time
            .observation_time_utc
        ),

        observation_time_bjt=(
            normalized_time
            .observation_time_bjt
        ),

        business_date_bjt=(
            normalized_time
            .business_date_bjt
        ),

        temperature_c=(
            temperature_result
            .temperature_c
        ),

        dewpoint_c=(
            temperature_result
            .dewpoint_c
        ),

        temperature_token=(
            temperature_result
            .token
        ),

        message_class=(
            message_class
        ),

        # 关键：
        # Raw原样保存。
        raw_metar=raw_metar,

        source=(
            source_normalized
        ),

        is_recovered=(
            is_recovered
        ),

        recovery_reason=(
            recovery_reason
        ),
    )


    return ObservationBuildResult(
        success=True,
        record=record,
        error=None,
    )