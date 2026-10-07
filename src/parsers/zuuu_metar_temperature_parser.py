"""
ZUUU METAR Temperature Parser V1.0
==================================

职责：
    从 ZUUU 原始 METAR/SPECI/COR/AMD 报文中解析：

    1. 气温 Temperature
    2. 露点 Dew Point

原则：
    - 不修改 Raw
    - 不负责 UTC/BJT 转换
    - 不负责 Daily Tmax
    - 不负责 Ground Truth 聚合
    - 只做气温/露点字段解析

典型字段：

    25/18       -> +25°C / +18°C
    M03/M08     -> -3°C / -8°C
    00/M01      ->  0°C / -1°C
    M00/M02     ->  0°C / -2°C
    25/         -> +25°C / Dew Point missing
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ============================================================
# Temperature / Dew Point Token
# ============================================================

TEMP_DEW_RE = re.compile(
    r"^(M?\d{2})/(M?\d{2})?$"
)


# ============================================================
# Result
# ============================================================

@dataclass(frozen=True)
class TemperatureParseResult:

    temperature_c: int | None

    dewpoint_c: int | None

    token: str | None

    success: bool

    error: str | None = None


# ============================================================
# Signed Temperature
# ============================================================

def parse_signed_temperature(
    value: str | None,
) -> int | None:
    """
    METAR温度编码：

        25  -> +25
        03  -> +3
        00  -> 0
        M03 -> -3
        M00 -> 0

    METAR中的M表示Minus。

    Python整数不存在“-0”，
    因此M00最终标准化为0。
    """

    if value is None:
        return None

    value = value.strip().upper()

    if not value:
        return None

    if value.startswith("M"):

        number = int(
            value[1:]
        )

        return -number

    return int(value)


# ============================================================
# Main Parser
# ============================================================

def parse_temperature(
    raw_metar: str,
) -> TemperatureParseResult:
    """
    从完整Raw METAR中寻找温度/露点字段。

    支持例如：

        ZUUU 240300Z ... 25/18 Q1016 NOSIG

        METAR ZUUU 240300Z ... 25/18 Q1016 NOSIG=

        COR ZUUU 051430Z ... 07/03 Q1023 NOSIG

        SPECI ZUUU ...

        AMD ZUUU ...

    注意：
        不依赖报文前缀。

    返回：
        TemperatureParseResult
    """

    if not raw_metar:

        return TemperatureParseResult(
            temperature_c=None,
            dewpoint_c=None,
            token=None,
            success=False,
            error="EMPTY_RAW_METAR",
        )

    text = (
        raw_metar
        .strip()
        .upper()
    )

    if not text:

        return TemperatureParseResult(
            temperature_c=None,
            dewpoint_c=None,
            token=None,
            success=False,
            error="EMPTY_RAW_METAR",
        )


    # --------------------------------------------------------
    # OGIMET等来源可能保留结尾 =
    #
    # 不修改原始数据。
    # 这里只建立临时解析文本。
    # --------------------------------------------------------

    parse_text = (
        text
        .replace("=", " ")
    )


    tokens = (
        parse_text.split()
    )


    # --------------------------------------------------------
    # 扫描token
    # --------------------------------------------------------

    candidates = []

    for token in tokens:

        match = TEMP_DEW_RE.fullmatch(
            token
        )

        if match:

            candidates.append(
                (
                    token,
                    match,
                )
            )


    # --------------------------------------------------------
    # 没找到
    # --------------------------------------------------------

    if not candidates:

        return TemperatureParseResult(
            temperature_c=None,
            dewpoint_c=None,
            token=None,
            success=False,
            error="TEMPERATURE_TOKEN_NOT_FOUND",
        )


    # --------------------------------------------------------
    # 正常METAR理论上应只有一个温度/露点token。
    #
    # 如果出现多个，不自行猜测。
    # --------------------------------------------------------

    if len(candidates) > 1:

        return TemperatureParseResult(
            temperature_c=None,
            dewpoint_c=None,
            token=None,
            success=False,
            error=(
                "MULTIPLE_TEMPERATURE_TOKENS:"
                + ",".join(
                    item[0]
                    for item in candidates
                )
            ),
        )


    token, match = (
        candidates[0]
    )


    temperature_raw = (
        match.group(1)
    )

    dewpoint_raw = (
        match.group(2)
    )


    try:

        temperature_c = (
            parse_signed_temperature(
                temperature_raw
            )
        )

        dewpoint_c = (
            parse_signed_temperature(
                dewpoint_raw
            )
        )

    except (
        ValueError,
        TypeError,
    ) as exc:

        return TemperatureParseResult(
            temperature_c=None,
            dewpoint_c=None,
            token=token,
            success=False,
            error=(
                "TEMPERATURE_PARSE_ERROR:"
                + str(exc)
            ),
        )


    return TemperatureParseResult(
        temperature_c=temperature_c,
        dewpoint_c=dewpoint_c,
        token=token,
        success=True,
        error=None,
    )


# ============================================================
# Convenience Function
# ============================================================

def extract_temperature_c(
    raw_metar: str,
) -> int | None:
    """
    只需要Temperature时使用。

    如果解析失败：
        返回None。

    正式Ground Truth流程中，
    建议使用parse_temperature()，
    因为它保留success/error信息。
    """

    result = parse_temperature(
        raw_metar
    )

    if not result.success:
        return None

    return result.temperature_c