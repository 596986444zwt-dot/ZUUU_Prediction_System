"""
ZUUU Message Classification Probe V1.3
=======================================

项目：
    ZUUU 成都双流机场温度预测系统

阶段：
    Phase 1 - ZUUU Ground Truth

目的：
    在 IEM Historical Archive 中进一步调查 ZUUU 的：

    1. Routine
    2. Special
    3. SPECI
    4. COR
    5. AMD
    6. PREFIXLESS
    7. 同时刻多版本报文
    8. 修订报与原始报关系

重要背景：
    V1.2.1 已确认：

        2025-01-05
        report_type=4

    返回：

        COR ZUUU 051430Z 35002MPS CAVOK 07/03 Q1023 NOSIG

    因此已经证明：

        IEM report_type=4 != SPECI

    report_type=4 是查询分类；
    报文本身的 message prefix / message type
    必须独立识别。

本探针目标：
    A. 寻找显式 SPECI ZUUU 报文
    B. 寻找 COR ZUUU 报文
    C. 寻找 AMD ZUUU 报文
    D. 寻找同 valid 多 Raw
    E. 检查 COR 前后上下文
    F. 不把网络失败当作“无记录”

安全规则：
    正式数据库写入：NO
    Bronze写入：NO
    Raw写入：NO
    Silver写入：NO

本程序只读 IEM，不修改任何正式数据。
"""

from __future__ import annotations

import csv
import io
import re
import time

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ============================================================
# 1. 基础配置
# ============================================================

STATION = "ZUUU"
NETWORK = "CN_ASOS"

IEM_URL = (
    "https://mesonet.agron.iastate.edu/"
    "cgi-bin/request/asos.py"
)

# ------------------------------------------------------------
# 搜索范围
#
# 从已经验证过的日期向历史回扫。
# 先给两年窗口。
# ------------------------------------------------------------

SEARCH_END_DATE = date(2026, 9, 1)

SEARCH_BACK_DAYS = 730


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HTTP_TIMEOUT = 30

MAX_RETRIES = 5

RETRY_BASE_SECONDS = 2

# 每次成功请求以后暂停。
# V1.2扫描出现过429，因此这里故意降低请求速度。
REQUEST_SLEEP_SECONDS = 0.75


USER_AGENT = (
    "ZUUU-Prediction-System/"
    "Message-Classification-Probe-V1.3 "
    "(research; historical METAR audit)"
)


# ============================================================
# 2. 搜索目标
# ============================================================

# 找到这些类型以后继续扫描，
# 直到目标全部完成或搜索窗口结束。

TARGET_EXPLICIT_SPECI = True

TARGET_COR = True

TARGET_AMD = True

TARGET_MULTI_RAW_SAME_VALID = True


# ============================================================
# 3. 数据结构
# ============================================================

@dataclass(frozen=True)
class MetarRecord:

    valid: str

    raw: str

    query_class: str

    message_class: str

    station: str

    @property
    def identity(self) -> tuple[str, str]:

        return (
            self.valid,
            self.raw,
        )


@dataclass
class SearchResult:

    explicit_speci: list[tuple[date, MetarRecord]]

    cor: list[tuple[date, MetarRecord]]

    amd: list[tuple[date, MetarRecord]]

    multi_raw: list[
        tuple[
            date,
            str,
            list[MetarRecord],
        ]
    ]

    successful_days: int = 0

    failed_days: int = 0

    total_requests: int = 0

    network_failures: int = 0


# ============================================================
# 4. 打印
# ============================================================

def banner(title: str) -> None:

    print()

    print("=" * 78)

    print(title)

    print("=" * 78)


def normalize_space(
    text: str,
) -> str:

    return " ".join(
        text.strip().split()
    )


# ============================================================
# 5. Message Classification
# ============================================================

def classify_message(
    raw: str,
) -> str:
    """
    只根据SOURCE RAW本身分类。

    注意：

        query_class
        与
        message_class

    完全分开。

    例如：

        query_class = SPECIAL
        message_class = COR

    是合法组合。
    """

    text = normalize_space(
        raw
    ).upper()

    # --------------------------------------------------------
    # 显式METAR
    # --------------------------------------------------------

    if text.startswith(
        "METAR "
    ):

        return "METAR"


    # --------------------------------------------------------
    # 显式SPECI
    # --------------------------------------------------------

    if text.startswith(
        "SPECI "
    ):

        return "SPECI"


    # --------------------------------------------------------
    # COR
    #
    # 我们已经真实观察到：
    #
    # COR ZUUU ...
    # --------------------------------------------------------

    if text.startswith(
        "COR "
    ):

        return "COR"


    # --------------------------------------------------------
    # AMD
    # --------------------------------------------------------

    if text.startswith(
        "AMD "
    ):

        return "AMD"


    # --------------------------------------------------------
    # Prefixless ZUUU
    # --------------------------------------------------------

    if text.startswith(
        STATION + " "
    ):

        return "PREFIXLESS"


    return "OTHER"


# ============================================================
# 6. 从Raw提取站点
# ============================================================

def extract_station(
    raw: str,
) -> str:
    """
    支持：

        ZUUU ...
        METAR ZUUU ...
        SPECI ZUUU ...
        COR ZUUU ...
        AMD ZUUU ...

    这里只用于Probe。
    正式Parser以后单独建设。
    """

    text = normalize_space(
        raw
    ).upper()

    tokens = text.split()

    if not tokens:

        return ""

    if tokens[0] == STATION:

        return STATION

    if (
        tokens[0]
        in {
            "METAR",
            "SPECI",
            "COR",
            "AMD",
        }
        and len(tokens) >= 2
    ):

        return tokens[1]

    return ""


# ============================================================
# 7. IEM URL
# ============================================================

def build_url(
    target_date: date,
    report_type: str,
) -> str:

    next_day = (
        target_date
        + timedelta(days=1)
    )

    params = [

        (
            "station",
            STATION,
        ),

        (
            "data",
            "metar",
        ),

        (
            "year1",
            str(target_date.year),
        ),

        (
            "month1",
            str(target_date.month),
        ),

        (
            "day1",
            str(target_date.day),
        ),

        (
            "year2",
            str(next_day.year),
        ),

        (
            "month2",
            str(next_day.month),
        ),

        (
            "day2",
            str(next_day.day),
        ),

        (
            "tz",
            "UTC",
        ),

        (
            "format",
            "onlycomma",
        ),

        (
            "latlon",
            "no",
        ),

        (
            "elev",
            "no",
        ),

        (
            "missing",
            "null",
        ),

        (
            "trace",
            "null",
        ),

        (
            "direct",
            "no",
        ),
    ]

    for item in report_type.split(","):

        params.append(
            (
                "report_type",
                item.strip(),
            )
        )

    return (
        IEM_URL
        + "?"
        + urlencode(params)
    )


# ============================================================
# 8. HTTP请求
# ============================================================

def download_text(
    target_date: date,
    report_type: str,
) -> str:

    url = build_url(
        target_date,
        report_type,
    )

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Connection": "close",
                    "Accept": (
                        "text/csv,"
                        "text/plain,"
                        "*/*"
                    ),
                },
            )

            with urlopen(
                request,
                timeout=HTTP_TIMEOUT,
            ) as response:

                raw_bytes = (
                    response.read()
                )

            if attempt > 1:

                print(
                    f"[RECOVERED] "
                    f"{target_date} | "
                    f"type={report_type} | "
                    f"attempt={attempt}"
                )

            return raw_bytes.decode(
                "utf-8",
                errors="replace",
            )

        except (
            HTTPError,
            URLError,
            TimeoutError,
            ConnectionError,
        ) as exc:

            last_error = exc

            print(
                f"[RETRY] "
                f"{target_date} | "
                f"type={report_type} | "
                f"{attempt}/{MAX_RETRIES} | "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            if attempt >= MAX_RETRIES:

                break

            wait_seconds = min(
                RETRY_BASE_SECONDS
                * (
                    2
                    ** (
                        attempt - 1
                    )
                ),
                30,
            )

            print(
                f"        "
                f"{wait_seconds}s 后重试"
            )

            time.sleep(
                wait_seconds
            )

        except Exception as exc:

            last_error = exc

            print(
                f"[UNEXPECTED] "
                f"{target_date} | "
                f"type={report_type} | "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            if attempt >= MAX_RETRIES:

                break

            time.sleep(
                RETRY_BASE_SECONDS
            )

    raise RuntimeError(
        f"IEM request failed: "
        f"date={target_date}, "
        f"type={report_type}, "
        f"error={last_error}"
    )


# ============================================================
# 9. CSV解析
# ============================================================

def parse_response(
    text: str,
    query_class: str,
) -> list[MetarRecord]:

    records: list[
        MetarRecord
    ] = []

    if not text.strip():

        return records

    reader = csv.DictReader(
        io.StringIO(
            text
        )
    )

    if reader.fieldnames is None:

        return records

    field_map = {

        name.lower().strip(): name

        for name
        in reader.fieldnames

        if name is not None
    }

    valid_field = (
        field_map.get(
            "valid"
        )
    )

    metar_field = (
        field_map.get(
            "metar"
        )
    )

    if (
        not valid_field
        or not metar_field
    ):

        return records

    for row in reader:

        valid = (
            row.get(
                valid_field
            )
            or ""
        ).strip()

        raw = (
            row.get(
                metar_field
            )
            or ""
        ).strip()

        if not valid:

            continue

        if not raw:

            continue

        if raw.lower() in {
            "null",
            "none",
            "nan",
        }:

            continue

        raw = normalize_space(
            raw
        )

        message_class = (
            classify_message(
                raw
            )
        )

        station = (
            extract_station(
                raw
            )
        )

        records.append(

            MetarRecord(

                valid=valid,

                raw=raw,

                query_class=(
                    query_class
                ),

                message_class=(
                    message_class
                ),

                station=station,
            )
        )

    return records


# ============================================================
# 10. 单日Special查询
# ============================================================

def query_special_day(
    target_date: date,
) -> list[MetarRecord]:

    text = download_text(
        target_date,
        "4",
    )

    records = parse_response(
        text,
        "SPECIAL",
    )

    return records


# ============================================================
# 11. 单日Combined查询
# ============================================================

def query_combined_day(
    target_date: date,
) -> list[MetarRecord]:

    text = download_text(
        target_date,
        "3,4",
    )

    records = parse_response(
        text,
        "COMBINED",
    )

    return records


# ============================================================
# 12. 查找同valid多Raw
# ============================================================

def find_multi_raw(
    records: list[MetarRecord],
) -> list[
    tuple[
        str,
        list[MetarRecord],
    ]
]:

    by_valid: dict[
        str,
        list[MetarRecord],
    ] = defaultdict(
        list
    )

    for record in records:

        by_valid[
            record.valid
        ].append(
            record
        )

    results = []

    for valid, items in (
        by_valid.items()
    ):

        unique_raws = {
            item.raw
            for item in items
        }

        if len(
            unique_raws
        ) > 1:

            results.append(
                (
                    valid,
                    items,
                )
            )

    return results


# ============================================================
# 13. 判断搜索目标
# ============================================================

def goals_complete(
    result: SearchResult,
) -> bool:

    speci_ok = (
        not TARGET_EXPLICIT_SPECI
        or bool(
            result.explicit_speci
        )
    )

    cor_ok = (
        not TARGET_COR
        or bool(
            result.cor
        )
    )

    amd_ok = (
        not TARGET_AMD
        or bool(
            result.amd
        )
    )

    multi_ok = (
        not TARGET_MULTI_RAW_SAME_VALID
        or bool(
            result.multi_raw
        )
    )

    return (
        speci_ok
        and cor_ok
        and amd_ok
        and multi_ok
    )


# ============================================================
# 14. 历史分类扫描
# ============================================================

def scan_history() -> SearchResult:

    banner(
        "Stage 1："
        "ZUUU Message Classification Scan"
    )

    print(
        f"搜索终点："
        f"{SEARCH_END_DATE}"
    )

    print(
        f"最大回扫："
        f"{SEARCH_BACK_DAYS} 天"
    )

    print()

    print(
        "主要目标："
    )

    print(
        "  显式SPECI"
    )

    print(
        "  COR"
    )

    print(
        "  AMD"
    )

    print(
        "  同valid多Raw"
    )

    print()

    print(
        "网络失败将记录为UNKNOWN，"
        "不会当成0记录。"
    )

    result = SearchResult(
        explicit_speci=[],
        cor=[],
        amd=[],
        multi_raw=[],
    )

    # --------------------------------------------------------
    # 已知COR
    #
    # 我们仍然会扫描，
    # 但这条作为已知审计事实记录。
    # --------------------------------------------------------

    known_cor_date = date(
        2025,
        1,
        5,
    )

    known_cor_raw = (
        "COR ZUUU 051430Z "
        "35002MPS CAVOK "
        "07/03 Q1023 NOSIG"
    )

    result.cor.append(
        (
            known_cor_date,
            MetarRecord(
                valid=(
                    "2025-01-05 14:30"
                ),
                raw=known_cor_raw,
                query_class=(
                    "SPECIAL"
                ),
                message_class=(
                    "COR"
                ),
                station=STATION,
            ),
        )
    )

    seen_speci = set()

    seen_cor = {
        (
            "2025-01-05 14:30",
            known_cor_raw,
        )
    }

    seen_amd = set()

    seen_multi = set()


    for offset in range(
        SEARCH_BACK_DAYS + 1
    ):

        target_date = (
            SEARCH_END_DATE
            - timedelta(
                days=offset
            )
        )

        # ----------------------------------------------------
        # 第一步：
        # 先查Special。
        #
        # 这是寻找SPECI/COR/AMD最经济的入口。
        # ----------------------------------------------------

        try:

            result.total_requests += 1

            special_records = (
                query_special_day(
                    target_date
                )
            )

            result.successful_days += 1

        except Exception as exc:

            result.failed_days += 1

            result.network_failures += 1

            print(
                f"[UNKNOWN] "
                f"{target_date} | "
                f"Special查询失败 | "
                f"{exc}"
            )

            continue


        # ----------------------------------------------------
        # 分析Special
        # ----------------------------------------------------

        interesting_today = False

        for record in (
            special_records
        ):

            identity = (
                record.identity
            )

            if (
                record.message_class
                == "SPECI"
            ):

                if identity not in (
                    seen_speci
                ):

                    seen_speci.add(
                        identity
                    )

                    result.explicit_speci.append(
                        (
                            target_date,
                            record,
                        )
                    )

                    interesting_today = True

                    print()

                    print(
                        ">>> FOUND EXPLICIT SPECI <<<"
                    )

                    print(
                        f"日期：{target_date}"
                    )

                    print(
                        f"VALID："
                        f"{record.valid}"
                    )

                    print(
                        f"RAW："
                        f"{record.raw}"
                    )


            elif (
                record.message_class
                == "COR"
            ):

                if identity not in (
                    seen_cor
                ):

                    seen_cor.add(
                        identity
                    )

                    result.cor.append(
                        (
                            target_date,
                            record,
                        )
                    )

                    interesting_today = True

                    print()

                    print(
                        ">>> FOUND COR <<<"
                    )

                    print(
                        f"日期：{target_date}"
                    )

                    print(
                        f"VALID："
                        f"{record.valid}"
                    )

                    print(
                        f"RAW："
                        f"{record.raw}"
                    )


            elif (
                record.message_class
                == "AMD"
            ):

                if identity not in (
                    seen_amd
                ):

                    seen_amd.add(
                        identity
                    )

                    result.amd.append(
                        (
                            target_date,
                            record,
                        )
                    )

                    interesting_today = True

                    print()

                    print(
                        ">>> FOUND AMD <<<"
                    )

                    print(
                        f"日期：{target_date}"
                    )

                    print(
                        f"VALID："
                        f"{record.valid}"
                    )

                    print(
                        f"RAW："
                        f"{record.raw}"
                    )


        # ----------------------------------------------------
        # 如果当天有Special，
        # 再查询Combined。
        #
        # 这样不用每天请求两次，
        # 可以降低429风险。
        # ----------------------------------------------------

        if special_records:

            time.sleep(
                REQUEST_SLEEP_SECONDS
            )

            try:

                result.total_requests += 1

                combined_records = (
                    query_combined_day(
                        target_date
                    )
                )

                multi_groups = (
                    find_multi_raw(
                        combined_records
                    )
                )

                for (
                    valid,
                    items,
                ) in multi_groups:

                    key = (
                        target_date,
                        valid,
                        tuple(
                            sorted(
                                item.raw
                                for item
                                in items
                            )
                        ),
                    )

                    if key in seen_multi:

                        continue

                    seen_multi.add(
                        key
                    )

                    result.multi_raw.append(
                        (
                            target_date,
                            valid,
                            items,
                        )
                    )

                    interesting_today = True

                    print()

                    print(
                        ">>> FOUND SAME VALID "
                        "MULTIPLE RAW <<<"
                    )

                    print(
                        f"日期：{target_date}"
                    )

                    print(
                        f"VALID：{valid}"
                    )

                    for item in items:

                        print(
                            f"  "
                            f"[{item.message_class}] "
                            f"{item.raw}"
                        )

            except Exception as exc:

                result.network_failures += 1

                print(
                    f"[UNKNOWN] "
                    f"{target_date} | "
                    f"Combined查询失败 | "
                    f"{exc}"
                )


        # ----------------------------------------------------
        # 进度
        # ----------------------------------------------------

        if (
            offset == 0
            or (
                offset + 1
            ) % 30 == 0
            or interesting_today
        ):

            print()

            print(
                f"[SCAN] "
                f"{offset + 1}/"
                f"{SEARCH_BACK_DAYS + 1} | "
                f"{target_date}"
            )

            print(
                f"       "
                f"SPECI="
                f"{len(result.explicit_speci)} | "
                f"COR="
                f"{len(result.cor)} | "
                f"AMD="
                f"{len(result.amd)} | "
                f"MULTI="
                f"{len(result.multi_raw)}"
            )


        # ----------------------------------------------------
        # 如果所有目标都已经找到，
        # 可以提前结束。
        # ----------------------------------------------------

        if goals_complete(
            result
        ):

            print()

            print(
                ">>> ALL SEARCH GOALS COMPLETED <<<"
            )

            break


        time.sleep(
            REQUEST_SLEEP_SECONDS
        )

    return result


# ============================================================
# 15. 输出发现详情
# ============================================================

def print_findings(
    result: SearchResult,
) -> None:

    # --------------------------------------------------------
    # SPECI
    # --------------------------------------------------------

    banner(
        "Explicit SPECI Findings"
    )

    if not result.explicit_speci:

        print(
            "没有找到显式 "
            "SPECI ZUUU 报文。"
        )

    else:

        for index, (
            target_date,
            record,
        ) in enumerate(
            result.explicit_speci,
            start=1,
        ):

            print()

            print(
                f"[{index:02d}]"
            )

            print(
                f"日期："
                f"{target_date}"
            )

            print(
                f"VALID："
                f"{record.valid}"
            )

            print(
                f"RAW："
                f"{record.raw}"
            )


    # --------------------------------------------------------
    # COR
    # --------------------------------------------------------

    banner(
        "COR Findings"
    )

    if not result.cor:

        print(
            "没有找到COR。"
        )

    else:

        for index, (
            target_date,
            record,
        ) in enumerate(
            result.cor,
            start=1,
        ):

            print()

            print(
                f"[{index:02d}]"
            )

            print(
                f"日期："
                f"{target_date}"
            )

            print(
                f"VALID："
                f"{record.valid}"
            )

            print(
                f"RAW："
                f"{record.raw}"
            )


    # --------------------------------------------------------
    # AMD
    # --------------------------------------------------------

    banner(
        "AMD Findings"
    )

    if not result.amd:

        print(
            "没有找到AMD。"
        )

    else:

        for index, (
            target_date,
            record,
        ) in enumerate(
            result.amd,
            start=1,
        ):

            print()

            print(
                f"[{index:02d}]"
            )

            print(
                f"日期："
                f"{target_date}"
            )

            print(
                f"VALID："
                f"{record.valid}"
            )

            print(
                f"RAW："
                f"{record.raw}"
            )


    # --------------------------------------------------------
    # Multi Raw
    # --------------------------------------------------------

    banner(
        "Same Valid Multiple Raw Findings"
    )

    if not result.multi_raw:

        print(
            "没有找到同一valid多个不同Raw。"
        )

    else:

        for index, (
            target_date,
            valid,
            items,
        ) in enumerate(
            result.multi_raw,
            start=1,
        ):

            print()

            print(
                f"[{index:02d}]"
            )

            print(
                f"日期："
                f"{target_date}"
            )

            print(
                f"VALID："
                f"{valid}"
            )

            for item in items:

                print(
                    f"  "
                    f"[{item.message_class}] "
                    f"{item.raw}"
                )


# ============================================================
# 16. 已知COR上下文专项审计
# ============================================================

def audit_known_cor_context() -> None:

    banner(
        "Stage 2：Known COR Context Audit"
    )

    target_date = date(
        2025,
        1,
        5,
    )

    target_valid = (
        "2025-01-05 14:30"
    )

    print(
        f"日期：{target_date}"
    )

    print(
        f"目标VALID："
        f"{target_valid}"
    )

    print()

    try:

        combined = (
            query_combined_day(
                target_date
            )
        )

    except Exception as exc:

        print(
            "COR上下文查询失败："
        )

        print(
            exc
        )

        print()

        print(
            "RESULT：UNKNOWN"
        )

        return


    combined_sorted = sorted(
        combined,
        key=lambda record: (
            record.valid,
            record.raw,
        ),
    )


    # --------------------------------------------------------
    # 同时刻
    # --------------------------------------------------------

    same_valid = [

        record

        for record
        in combined_sorted

        if record.valid
        == target_valid
    ]


    print(
        "同VALID记录数量："
        f"{len(same_valid)}"
    )

    for record in same_valid:

        print()

        print(
            f"[{record.message_class}]"
        )

        print(
            record.raw
        )


    # --------------------------------------------------------
    # 前后上下文
    # --------------------------------------------------------

    print()

    print(
        "前后时间上下文："
    )

    indexes = [

        index

        for index, record
        in enumerate(
            combined_sorted
        )

        if record.valid
        == target_valid
    ]

    if not indexes:

        print(
            "Combined中没有找到"
            "目标VALID。"
        )

        return

    target_index = (
        indexes[0]
    )

    start = max(
        0,
        target_index - 4,
    )

    end = min(
        len(combined_sorted),
        target_index + 5,
    )

    for index in range(
        start,
        end,
    ):

        record = (
            combined_sorted[
                index
            ]
        )

        marker = (
            ">>>"
            if record.valid
            == target_valid
            else "   "
        )

        print(
            f"{marker} "
            f"{record.valid} | "
            f"[{record.message_class}] | "
            f"{record.raw}"
        )


    # --------------------------------------------------------
    # 初步结论
    # --------------------------------------------------------

    print()

    if len(
        same_valid
    ) == 1:

        print(
            "OBSERVATION："
        )

        print(
            "IEM Combined当前只返回"
            "1条14:30记录。"
        )

        print(
            "因此仅凭当前IEM结果，"
            "无法证明它是否保存了"
            "COR之前的原始版本。"
        )

    else:

        print(
            "OBSERVATION："
        )

        print(
            "同一VALID存在多个版本，"
            "需要进一步研究"
            "修订优先级。"
        )


# ============================================================
# 17. 最终报告
# ============================================================

def final_report(
    result: SearchResult,
) -> None:

    banner(
        "V1.3 FINAL REPORT"
    )

    print(
        "扫描统计："
    )

    print(
        f"  成功Special日期查询："
        f"{result.successful_days}"
    )

    print(
        f"  失败日期查询："
        f"{result.failed_days}"
    )

    print(
        f"  总HTTP请求："
        f"{result.total_requests}"
    )

    print(
        f"  网络/请求失败事件："
        f"{result.network_failures}"
    )

    print()

    print(
        "分类发现："
    )

    print(
        f"  显式SPECI："
        f"{len(result.explicit_speci)}"
    )

    print(
        f"  COR："
        f"{len(result.cor)}"
    )

    print(
        f"  AMD："
        f"{len(result.amd)}"
    )

    print(
        f"  同valid多Raw："
        f"{len(result.multi_raw)}"
    )

    print()

    # --------------------------------------------------------
    # 判断
    # --------------------------------------------------------

    if result.explicit_speci:

        print(
            "SPECI状态：FOUND"
        )

    else:

        print(
            "SPECI状态：NOT FOUND "
            "IN TEST WINDOW"
        )


    if result.cor:

        print(
            "COR状态：FOUND"
        )

    else:

        print(
            "COR状态：NOT FOUND"
        )


    if result.amd:

        print(
            "AMD状态：FOUND"
        )

    else:

        print(
            "AMD状态：NOT FOUND "
            "IN TEST WINDOW"
        )


    if result.multi_raw:

        print(
            "同valid多Raw状态：FOUND"
        )

    else:

        print(
            "同valid多Raw状态："
            "NOT FOUND IN TEST WINDOW"
        )


    print()

    print(
        "重要："
    )

    print(
        "NOT FOUND IN TEST WINDOW"
        " 不等于不存在。"
    )

    print(
        "任何请求失败日期都属于UNKNOWN，"
        "不能解释为0记录。"
    )

    print()

    print(
        "本探针仍未写入任何正式数据。"
    )


# ============================================================
# 18. Main
# ============================================================

def main() -> None:

    banner(
        "ZUUU Message Classification Probe V1.3"
    )

    print(
        f"Station："
        f"{STATION}"
    )

    print(
        f"Network："
        f"{NETWORK}"
    )

    print(
        f"搜索终点："
        f"{SEARCH_END_DATE}"
    )

    print(
        f"最大回扫："
        f"{SEARCH_BACK_DAYS} 天"
    )

    print()

    print(
        "安全模式："
    )

    print(
        "  正式数据库写入：NO"
    )

    print(
        "  Bronze写入：NO"
    )

    print(
        "  Raw写入：NO"
    )

    print(
        "  Silver写入：NO"
    )

    print()

    print(
        "V1.3核心原则："
    )

    print(
        "  query_class "
        "与 message_class 分离"
    )

    print(
        "  SPECIAL 不自动等于 SPECI"
    )

    print(
        "  网络失败不等于无报文"
    )

    print(
        "  SOURCE RAW不修改"
    )


    # ========================================================
    # Stage 1
    # ========================================================

    result = (
        scan_history()
    )


    # ========================================================
    # Findings
    # ========================================================

    print_findings(
        result
    )


    # ========================================================
    # Stage 2
    # ========================================================

    audit_known_cor_context()


    # ========================================================
    # Final
    # ========================================================

    final_report(
        result
    )


    # ========================================================
    # Safety
    # ========================================================

    banner(
        "Safety Confirmation"
    )

    print(
        "正式数据库：未修改"
    )

    print(
        "Bronze：未修改"
    )

    print(
        "Raw：未修改"
    )

    print(
        "Silver：未修改"
    )

    print()

    print(
        "V1.3只进行了IEM历史数据读取"
        "和消息分类审计。"
    )


# ============================================================
# 19. Entry
# ============================================================

if __name__ == "__main__":

    main()