import re
from pprint import pprint


# ==========================================================
# ZUUU METAR Parser V2
# ==========================================================
#
# 目标：
#   将原始 METAR / SPECI 报文解析成结构化字段。
#
# V2关键规则：
#   1. 当前观测主体与 TREND / RMK 严格隔离
#   2. TEMPO / BECMG / NOSIG 不参与当前实况解析
#   3. RMK 不参与当前实况解析
#   4. 保留完整 raw_report
#   5. 缺失值使用 None
#   6. 云层全部保留
#   7. 天气现象全部保留
#   8. Parser阶段不做预测、不做特征筛选
# ==========================================================


PARSER_VERSION = "ZUUU_METAR_PARSER_V2"

TREND_START_TOKENS = {
    "TEMPO",
    "BECMG",
    "NOSIG",
}


def parse_temperature(value):
    """
    解析 METAR 温度 / 露点。

    示例：
        24  -> 24
        05  -> 5
        M02 -> -2
        //  -> None
    """

    if not value or value == "//":
        return None

    if value.startswith("M"):
        return -int(value[1:])

    return int(value)


def parse_wind(token):
    """
    解析风组。

    示例：
        04007MPS
        04007G12MPS
        VRB03MPS
        00000MPS
        09010KT
    """

    result = {
        "wind_direction_deg": None,
        "wind_speed_mps": None,
        "wind_gust_mps": None,
        "variable_wind": False,
    }

    match = re.fullmatch(
        r"(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?(MPS|KT)",
        token,
    )

    if not match:
        return None

    direction = match.group(1)
    speed = int(match.group(2))
    gust = match.group(3)
    unit = match.group(4)

    if direction == "VRB":
        result["variable_wind"] = True
    else:
        result["wind_direction_deg"] = int(direction)

    if unit == "KT":
        speed = speed * 0.514444

        if gust is not None:
            gust = int(gust) * 0.514444

    elif gust is not None:
        gust = int(gust)

    result["wind_speed_mps"] = round(speed, 3)

    if gust is not None:
        result["wind_gust_mps"] = round(gust, 3)

    return result


def parse_cloud(token):
    """
    解析云层。

    示例：
        FEW026
        SCT040
        BKN080
        OVC100
        BKN020CB
        SCT030TCU
    """

    match = re.fullmatch(
        r"(FEW|SCT|BKN|OVC)(\d{3}|///)(CB|TCU)?",
        token,
    )

    if not match:
        return None

    cover = match.group(1)
    height = match.group(2)
    cloud_type = match.group(3)

    if height == "///":
        base_ft = None
    else:
        base_ft = int(height) * 100

    return {
        "cover": cover,
        "base_ft": base_ft,
        "cloud_type": cloud_type,
    }


def is_weather_token(token):
    """
    判断一个 token 是否像 METAR 天气现象组。

    Parser只保留原始天气代码。
    """

    pattern = (
        r"^"
        r"(?:\+|-)?"
        r"(?:VC)?"
        r"(?:MI|PR|BC|DR|BL|SH|TS|FZ)?"
        r"(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP){0,3}"
        r"(?:BR|FG|FU|VA|DU|SA|HZ|PY)?"
        r"(?:PO|SQ|FC|SS|DS)?"
        r"$"
    )

    if not re.fullmatch(pattern, token):
        return False

    known_codes = [
        "DZ", "RA", "SN", "SG", "IC", "PL", "GR", "GS", "UP",
        "BR", "FG", "FU", "VA", "DU", "SA", "HZ", "PY",
        "PO", "SQ", "FC", "SS", "DS",
        "SH", "TS", "FZ",
    ]

    return any(
        code in token
        for code in known_codes
    )


def split_metar_sections(raw_report):
    """
    将报文拆成三个区域：

    observation_tokens:
        当前观测主体，只允许这个区域更新当前实况字段。

    trend:
        TEMPO / BECMG / NOSIG 开始的趋势段。
        如果后面存在 RMK，则不把 RMK 包含进去。

    remarks:
        RMK 后面的备注内容。

    注意：
        V2只保存 trend / remarks 原文，
        不把它们解析进当前观测字段。
    """

    cleaned = raw_report.strip().rstrip("=")

    all_tokens = cleaned.split()

    observation_tokens = []
    trend_tokens = []
    remark_tokens = []

    section = "OBSERVATION"

    for token in all_tokens:

        if token == "RMK":
            section = "REMARK"
            continue

        if (
            section == "OBSERVATION"
            and token in TREND_START_TOKENS
        ):
            section = "TREND"
            trend_tokens.append(token)
            continue

        if section == "OBSERVATION":
            observation_tokens.append(token)

        elif section == "TREND":
            trend_tokens.append(token)

        elif section == "REMARK":
            remark_tokens.append(token)

    trend = (
        " ".join(trend_tokens)
        if trend_tokens
        else None
    )

    remarks = (
        " ".join(remark_tokens)
        if remark_tokens
        else None
    )

    return (
        observation_tokens,
        trend,
        remarks,
    )


def parse_metar(raw_report):
    """
    ZUUU METAR / SPECI 主解析函数。

    V2：
        TREND与RMK在进入字段解析之前已经被隔离。
    """

    if not raw_report:
        raise ValueError(
            "raw_report 不能为空"
        )

    (
        tokens,
        trend,
        remarks,
    ) = split_metar_sections(
        raw_report
    )

    if len(tokens) < 3:
        raise ValueError(
            "METAR 报文过短，无法解析"
        )

    result = {
        "station_id": None,
        "report_type": None,
        "raw_report": raw_report,

        "metar_time_group": None,
        "metar_day": None,
        "metar_hour": None,
        "metar_minute": None,

        "wind_direction_deg": None,
        "wind_speed_mps": None,
        "wind_gust_mps": None,

        "variable_wind": False,
        "variable_wind_from": None,
        "variable_wind_to": None,

        "visibility_m": None,
        "cavok": False,

        "weather_phenomena": [],

        "cloud_layers": [],

        "temperature_c": None,
        "dewpoint_c": None,

        "qnh_hpa": None,

        "trend": trend,
        "remarks": remarks,

        "unparsed_tokens": [],

        "is_corrected": False,
        "is_auto": False,
    }

    # ======================================================
    # 报文类型
    # ======================================================

    if tokens[0] in (
        "METAR",
        "SPECI",
    ):
        result["report_type"] = tokens[0]
        index = 1

    else:
        index = 0

    # ======================================================
    # 机场代码
    # ======================================================

    if index < len(tokens):

        station = tokens[index]

        if re.fullmatch(
            r"[A-Z]{4}",
            station,
        ):
            result["station_id"] = station
            index += 1

    if result["station_id"] is None:
        raise ValueError(
            "METAR station identifier is missing"
        )

    # ======================================================
    # 当前观测主体解析
    # ======================================================

    for token in tokens[index:]:

        # --------------------------------------------------
        # COR
        # --------------------------------------------------

        if token == "COR":
            result["is_corrected"] = True
            continue

        # --------------------------------------------------
        # AUTO
        # --------------------------------------------------

        if token == "AUTO":
            result["is_auto"] = True
            continue

        # --------------------------------------------------
        # 时间组
        # --------------------------------------------------

        time_match = re.fullmatch(
            r"(\d{2})(\d{2})(\d{2})Z",
            token,
        )

        if time_match:

            result[
                "metar_time_group"
            ] = token

            result[
                "metar_day"
            ] = int(
                time_match.group(1)
            )

            result[
                "metar_hour"
            ] = int(
                time_match.group(2)
            )

            result[
                "metar_minute"
            ] = int(
                time_match.group(3)
            )

            continue

        # --------------------------------------------------
        # 风
        # --------------------------------------------------

        wind = parse_wind(token)

        if wind is not None:
            result.update(wind)
            continue

        # --------------------------------------------------
        # 可变风向区间
        # --------------------------------------------------

        variable_match = re.fullmatch(
            r"(\d{3})V(\d{3})",
            token,
        )

        if variable_match:

            result[
                "variable_wind"
            ] = True

            result[
                "variable_wind_from"
            ] = int(
                variable_match.group(1)
            )

            result[
                "variable_wind_to"
            ] = int(
                variable_match.group(2)
            )

            continue

        # --------------------------------------------------
        # CAVOK
        # --------------------------------------------------

        if token == "CAVOK":

            result["cavok"] = True

            # 系统内部表示 >=10km
            result["visibility_m"] = 10000

            continue

        # --------------------------------------------------
        # 能见度
        # --------------------------------------------------

        if re.fullmatch(
            r"\d{4}",
            token,
        ):

            visibility = int(token)

            if visibility == 9999:
                result[
                    "visibility_m"
                ] = 10000
            else:
                result[
                    "visibility_m"
                ] = visibility

            continue

        # --------------------------------------------------
        # 云层
        # --------------------------------------------------

        cloud = parse_cloud(token)

        if cloud is not None:

            result[
                "cloud_layers"
            ].append(cloud)

            continue

        # --------------------------------------------------
        # 无重要云
        # --------------------------------------------------

        if token in (
            "NSC",
            "NCD",
            "SKC",
            "CLR",
        ):

            result[
                "cloud_layers"
            ].append(
                {
                    "cover": token,
                    "base_ft": None,
                    "cloud_type": None,
                }
            )

            continue

        # --------------------------------------------------
        # 温度 / 露点
        # --------------------------------------------------

        temp_match = re.fullmatch(
            r"(M?\d{2}|//)/(M?\d{2}|//)",
            token,
        )

        if temp_match:

            result[
                "temperature_c"
            ] = parse_temperature(
                temp_match.group(1)
            )

            result[
                "dewpoint_c"
            ] = parse_temperature(
                temp_match.group(2)
            )

            continue

        # --------------------------------------------------
        # QNH
        # --------------------------------------------------

        qnh_match = re.fullmatch(
            r"Q(\d{4})",
            token,
        )

        if qnh_match:

            result[
                "qnh_hpa"
            ] = int(
                qnh_match.group(1)
            )

            continue

        # --------------------------------------------------
        # 天气现象
        # --------------------------------------------------

        if is_weather_token(token):

            result[
                "weather_phenomena"
            ].append(token)

            continue

        # --------------------------------------------------
        # 未解析字段
        # --------------------------------------------------

        result[
            "unparsed_tokens"
        ].append(token)

    # ======================================================
    # 最低完整性要求
    # ======================================================

    if result["metar_time_group"] is None:
        raise ValueError(
            "METAR observation time group is missing"
        )

    return result


# ==========================================================
# 手工运行测试
# ==========================================================

if __name__ == "__main__":

    TEST_METAR = (
        "METAR ZUUU 280400Z "
        "04007MPS "
        "9999 "
        "FEW026 "
        "24/19 "
        "Q1011 "
        "TEMPO "
        "18015MPS "
        "2000 "
        "TSRA "
        "BKN010CB "
        "RMK TEST"
    )

    print("=" * 70)
    print(
        "ZUUU METAR Parser V2 - "
        "Trend Isolation Test"
    )
    print("=" * 70)

    parsed = parse_metar(
        TEST_METAR
    )

    pprint(
        parsed,
        sort_dicts=False,
    )

    print("=" * 70)