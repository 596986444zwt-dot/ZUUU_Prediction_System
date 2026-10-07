"""
ZUUU Historical Source Probe V1.2.1
===================================

项目：
    ZUUU 成都双流机场温度预测系统

当前阶段：
    Phase 1 - ZUUU Ground Truth

用途：
    对已经由 V1.2 找到的真实 Special 日期
    2025-01-05
    执行完整的 IEM Historical METAR 三路审计。

背景：
    V1.2 已经确认：

        Station = ZUUU
        Network = CN_ASOS
        Date = 2025-01-05
        report_type=4
        Special数量 = 1

    但随后执行完整审计时发生：

        SSL: UNEXPECTED_EOF_WHILE_READING

    因此 V1.2.1：

        1. 不再扫描730天
        2. 直接锁定 2025-01-05
        3. 对 report_type=3 / 4 / 3,4 分别查询
        4. 增加HTTP重试和指数退避
        5. 审计Routine / Special / Combined集合关系
        6. 检查重复报文
        7. 检查同一valid多个不同Raw
        8. 检查COR / AMD文本迹象
        9. 打印Special完整SOURCE RAW
       10. 打印Special附近报文

安全规则：
    1. 不写正式数据库
    2. 不写 Bronze
    3. 不写 Raw
    4. 不写 Silver
    5. 不修改任何正式数据

重要原则：
    SOURCE RAW 始终保持 IEM 返回的原始文本。

    如果 IEM 省略 METAR / SPECI 前缀，
    只允许在 PARSER FORM 中补充。

    SOURCE RAW 永远不修改。
"""

from __future__ import annotations

import csv
import io
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
# V1.2 已经找到真实 Special 的日期
# ------------------------------------------------------------

AUDIT_DATE = date(2025, 1, 5)


# ------------------------------------------------------------
# HTTP配置
# ------------------------------------------------------------

HTTP_TIMEOUT = 30

MAX_RETRIES = 6

RETRY_BASE_SECONDS = 2

REQUEST_SLEEP_SECONDS = 1.0


USER_AGENT = (
    "ZUUU-Prediction-System-Historical-Probe/1.2.1 "
    "(research; historical METAR validation)"
)


# ============================================================
# 2. 数据结构
# ============================================================

@dataclass(frozen=True)
class MetarRecord:

    valid: str

    raw: str

    parser_form: str

    source_type: str

    query_type: str

    @property
    def identity(self) -> tuple[str, str]:
        """
        历史报文Identity。

        当前Probe使用：

            valid + raw

        用于判断：

            完全重复
            Routine / Special集合关系
            Combined完整性
        """

        return (
            self.valid,
            self.raw,
        )


# ============================================================
# 3. 通用打印工具
# ============================================================

def banner(title: str) -> None:

    print()

    print("=" * 78)

    print(title)

    print("=" * 78)


def normalize_space(text: str) -> str:
    """
    只用于解析和比较。

    不代表修改SOURCE RAW数据库。

    当前Probe从CSV字段读取后，
    将多余空白压缩为单空格，
    用于审计展示。
    """

    return " ".join(
        text.strip().split()
    )


# ============================================================
# 4. 报文类型识别
# ============================================================

def detect_source_type(
    raw: str,
) -> str:
    """
    判断IEM返回的文本是否显式包含：

        METAR
        SPECI

    如果直接以：

        ZUUU ...

    开头，则标记：

        PREFIXLESS

    注意：

        PREFIXLESS 并不能单独证明
        它一定是Routine或Special。

        报文身份还需要结合
        report_type查询来源。
    """

    text = normalize_space(
        raw
    ).upper()

    if text.startswith(
        "METAR "
    ):
        return "METAR"

    if text.startswith(
        "SPECI "
    ):
        return "SPECI"

    if text.startswith(
        STATION + " "
    ):
        return "PREFIXLESS"

    return "OTHER"


# ============================================================
# 5. Parser Form
# ============================================================

def build_parser_form(
    raw: str,
    query_type: str,
) -> str:
    """
    生成后续Parser可以读取的标准形式。

    非常重要：

        SOURCE RAW 不修改。

    这里只生成：

        PARSER FORM

    ----------------------------------------------------------

    如果原始文本已经有：

        METAR
        SPECI

    则保持不变。

    ----------------------------------------------------------

    如果IEM返回：

        ZUUU 050100Z ...

    并且来源是：

        ROUTINE

    则Parser Form：

        METAR ZUUU 050100Z ...

    ----------------------------------------------------------

    如果来源是：

        SPECIAL

    则Parser Form：

        SPECI ZUUU 050100Z ...

    ----------------------------------------------------------

    COMBINED查询：

    如果报文PREFIXLESS，仅凭Combined本身
    无法100%知道属于Routine还是Special。

    因此这里暂时补METAR，仅供Probe展示。

    正式Historical Adapter以后应该通过：

        Routine集合
        Special集合

    给Combined中的报文确定真实查询分类，
    而不是猜测。
    """

    text = normalize_space(
        raw
    )

    upper = text.upper()

    if upper.startswith(
        "METAR "
    ):
        return text

    if upper.startswith(
        "SPECI "
    ):
        return text

    if not upper.startswith(
        STATION + " "
    ):
        return text

    if query_type == "SPECIAL":

        return (
            "SPECI "
            + text
        )

    return (
        "METAR "
        + text
    )


# ============================================================
# 6. 构建IEM URL
# ============================================================

def build_url(
    target_date: date,
    report_type: str,
) -> str:
    """
    构建IEM ASOS历史查询URL。

    target_date：

        UTC自然日

    查询范围：

        target_date 00:00 UTC
        至
        next_day 00:00 UTC

    当前Probe只验证Source行为。

    不在这里处理北京时间业务日。
    """

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

    # report_type:
    #
    # 3   = Routine
    # 4   = Specials
    # 3,4 = Routine + Specials

    for item in report_type.split(","):

        params.append(
            (
                "report_type",
                item.strip(),
            )
        )

    query_string = urlencode(
        params
    )

    return (
        IEM_URL
        + "?"
        + query_string
    )


# ============================================================
# 7. HTTP下载 + 重试机制
# ============================================================

def download_text(
    target_date: date,
    report_type: str,
) -> str:
    """
    从IEM读取历史数据。

    V1.2.1新增：

        - SSL EOF重试
        - HTTP 429重试
        - HTTP 503重试
        - Timeout重试
        - URLError重试
        - 指数退避

    最多：

        MAX_RETRIES

    次。

    不写任何文件。
    不写任何数据库。
    """

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
                    "User-Agent": (
                        USER_AGENT
                    ),
                    "Connection": (
                        "close"
                    ),
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

            text = raw_bytes.decode(
                "utf-8",
                errors="replace",
            )

            # 如果前面失败过，
            # 成功以后打印恢复信息。
            if attempt > 1:

                print(
                    f"[RECOVERED] "
                    f"{target_date} | "
                    f"report_type="
                    f"{report_type} | "
                    f"第 {attempt} 次"
                    f"请求成功"
                )

            return text

        except (
            HTTPError,
            URLError,
            TimeoutError,
            ConnectionError,
        ) as exc:

            last_error = exc

            print()

            print(
                f"[RETRY] "
                f"{target_date} | "
                f"report_type="
                f"{report_type}"
            )

            print(
                f"        "
                f"第 "
                f"{attempt}/"
                f"{MAX_RETRIES} "
                f"次失败"
            )

            print(
                f"        "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            if attempt >= MAX_RETRIES:

                break

            wait_seconds = (
                RETRY_BASE_SECONDS
                * (
                    2
                    ** (
                        attempt - 1
                    )
                )
            )

            # 最大等待30秒
            wait_seconds = min(
                wait_seconds,
                30,
            )

            print(
                f"        "
                f"等待 "
                f"{wait_seconds} "
                f"秒后重试..."
            )

            time.sleep(
                wait_seconds
            )

        except Exception as exc:

            last_error = exc

            print()

            print(
                f"[UNEXPECTED ERROR] "
                f"{target_date} | "
                f"report_type="
                f"{report_type}"
            )

            print(
                f"        "
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
                f"等待 "
                f"{wait_seconds} "
                f"秒后重试..."
            )

            time.sleep(
                wait_seconds
            )

    raise RuntimeError(
        "\n"
        "IEM请求连续失败。\n"
        f"Date：{target_date}\n"
        f"Report Type：{report_type}\n"
        f"最大重试：{MAX_RETRIES}\n"
        f"最后错误：{last_error}"
    )


# ============================================================
# 8. IEM CSV解析
# ============================================================

def parse_iem_response(
    text: str,
    query_type: str,
) -> list[MetarRecord]:
    """
    将IEM CSV返回解析为MetarRecord。

    预期字段通常包括：

        station
        valid
        metar

    本函数：

        不写数据库
        不写Raw
        不写Silver
    """

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

    station_field = (
        field_map.get(
            "station"
        )
    )

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

    if not valid_field:

        print(
            "[WARN] "
            "IEM返回中没有valid字段"
        )

        return records

    if not metar_field:

        print(
            "[WARN] "
            "IEM返回中没有metar字段"
        )

        return records

    for row in reader:

        station = ""

        if station_field:

            station = (
                row.get(
                    station_field
                )
                or ""
            ).strip()

        valid = (
            row.get(
                valid_field
            )
            or ""
        ).strip()

        raw_original = (
            row.get(
                metar_field
            )
            or ""
        ).strip()

        if not valid:

            continue

        if not raw_original:

            continue

        if raw_original.lower() in {
            "null",
            "none",
            "nan",
        }:

            continue

        # ----------------------------------------------------
        # SOURCE RAW
        #
        # 这里不添加METAR/SPECI前缀。
        # ----------------------------------------------------

        raw = normalize_space(
            raw_original
        )

        source_type = (
            detect_source_type(
                raw
            )
        )

        parser_form = (
            build_parser_form(
                raw,
                query_type,
            )
        )

        records.append(

            MetarRecord(

                valid=valid,

                raw=raw,

                parser_form=(
                    normalize_space(
                        parser_form
                    )
                ),

                source_type=(
                    source_type
                ),

                query_type=(
                    query_type
                ),
            )
        )

    return records


# ============================================================
# 9. 单次Probe
# ============================================================

def run_probe(
    target_date: date,
    report_type: str,
    query_type: str,
    show_output: bool = True,
) -> tuple[
    str,
    list[MetarRecord],
]:
    """
    请求 + 解析 + 基本统计。
    """

    text = download_text(
        target_date,
        report_type,
    )

    records = (
        parse_iem_response(
            text,
            query_type,
        )
    )

    if show_output:

        banner(
            f"Probe："
            f"{query_type}"
        )

        print(
            f"日期："
            f"{target_date}"
        )

        print(
            f"Report Type参数："
            f"{report_type}"
        )

        print(
            f"服务器返回字符数："
            f"{len(text)}"
        )

        print(
            f"有效报文数量："
            f"{len(records)}"
        )

        counts = Counter(
            record.source_type
            for record in records
        )

        print(
            f"显式METAR："
            f"{counts.get('METAR', 0)}"
        )

        print(
            f"显式SPECI："
            f"{counts.get('SPECI', 0)}"
        )

        print(
            f"省略前缀："
            f"{counts.get('PREFIXLESS', 0)}"
        )

        print(
            f"OTHER："
            f"{counts.get('OTHER', 0)}"
        )

    return (
        text,
        records,
    )


# ============================================================
# 10. 基础记录审计
# ============================================================

def audit_records(
    name: str,
    records: list[MetarRecord],
) -> None:
    """
    检查：

        总记录
        非ZUUU
        OTHER
        完全重复Identity
        同一valid多个Raw
    """

    banner(
        f"{name} Audit"
    )

    print(
        f"总记录："
        f"{len(records)}"
    )

    non_zuuu = 0

    other = 0

    identities = Counter(
        record.identity
        for record in records
    )

    exact_duplicates = sum(

        count - 1

        for count
        in identities.values()

        if count > 1
    )

    by_valid: dict[
        str,
        set[str],
    ] = defaultdict(
        set
    )

    for record in records:

        upper = (
            record.raw.upper()
        )

        is_zuuu = (

            upper.startswith(
                STATION + " "
            )

            or

            upper.startswith(
                "METAR "
                + STATION
                + " "
            )

            or

            upper.startswith(
                "SPECI "
                + STATION
                + " "
            )
        )

        if not is_zuuu:

            non_zuuu += 1

        if (
            record.source_type
            == "OTHER"
        ):

            other += 1

        by_valid[
            record.valid
        ].add(
            record.raw
        )

    multi_raw_same_valid = {

        valid: raws

        for valid, raws
        in by_valid.items()

        if len(raws) > 1
    }

    print(
        f"非ZUUU："
        f"{non_zuuu}"
    )

    print(
        f"OTHER："
        f"{other}"
    )

    print(
        f"完全重复Identity："
        f"{exact_duplicates}"
    )

    print(
        f"同一valid多个不同Raw："
        f"{len(multi_raw_same_valid)}"
    )

    if multi_raw_same_valid:

        print()

        print(
            "同一valid多个Raw明细："
        )

        for valid, raws in sorted(
            multi_raw_same_valid.items()
        ):

            print()

            print(
                f"  VALID：{valid}"
            )

            for raw in sorted(
                raws
            ):

                print(
                    f"    {raw}"
                )


# ============================================================
# 11. Routine / Special / Combined集合审计
# ============================================================

def audit_set_relationship(
    routine: list[MetarRecord],
    special: list[MetarRecord],
    combined: list[MetarRecord],
) -> None:

    banner(
        "Routine / Specials 集合关系"
    )

    routine_ids = {
        record.identity
        for record in routine
    }

    special_ids = {
        record.identity
        for record in special
    }

    combined_ids = {
        record.identity
        for record in combined
    }

    union_ids = (
        routine_ids
        | special_ids
    )

    intersection = (
        routine_ids
        & special_ids
    )

    missing_from_combined = (
        union_ids
        - combined_ids
    )

    extra_in_combined = (
        combined_ids
        - union_ids
    )

    print(
        f"Routine Identity："
        f"{len(routine_ids)}"
    )

    print(
        f"Special Identity："
        f"{len(special_ids)}"
    )

    print(
        f"Routine∩Special："
        f"{len(intersection)}"
    )

    print(
        f"Routine∪Special："
        f"{len(union_ids)}"
    )

    print(
        f"Combined Identity："
        f"{len(combined_ids)}"
    )

    print(
        "Union中存在但Combined缺失："
        f"{len(missing_from_combined)}"
    )

    print(
        "Combined额外记录："
        f"{len(extra_in_combined)}"
    )

    if intersection:

        print()

        print(
            "[INFO] "
            "Routine与Special存在交集："
        )

        for valid, raw in sorted(
            intersection
        ):

            print()

            print(
                f"VALID：{valid}"
            )

            print(
                raw
            )

    if missing_from_combined:

        print()

        print(
            "[WARNING] "
            "Routine/Special Union中"
            "存在Combined没有返回的记录："
        )

        for valid, raw in sorted(
            missing_from_combined
        ):

            print()

            print(
                f"VALID：{valid}"
            )

            print(
                raw
            )

    if extra_in_combined:

        print()

        print(
            "[WARNING] "
            "Combined中存在Routine/Special"
            "单独查询没有返回的记录："
        )

        for valid, raw in sorted(
            extra_in_combined
        ):

            print()

            print(
                f"VALID：{valid}"
            )

            print(
                raw
            )


# ============================================================
# 12. COR / AMD迹象审计
# ============================================================

def audit_correction_signals(
    records: list[MetarRecord],
) -> None:

    banner(
        "COR / Amendment Signal Audit"
    )

    correction_records: list[
        MetarRecord
    ] = []

    for record in records:

        upper = (
            " "
            + record.raw.upper()
            + " "
        )

        # 这里只检查文本迹象。
        #
        # 不能因为没找到COR文本
        # 就认定不存在修订报。

        if (
            " COR " in upper
            or " AMD " in upper
        ):

            correction_records.append(
                record
            )

    print(
        "包含COR/AMD文本迹象："
        f"{len(correction_records)}"
    )

    if not correction_records:

        print()

        print(
            "本测试日没有发现"
            "显式COR/AMD文本。"
        )

        print(
            "注意："
            "这不能证明IEM不存在"
            "修订/更正报机制。"
        )

        return

    for index, record in enumerate(
        correction_records,
        start=1,
    ):

        print()

        print(
            f"[{index:02d}] "
            f"{record.valid}"
        )

        print(
            record.raw
        )


# ============================================================
# 13. 样本打印
# ============================================================

def print_samples(
    title: str,
    records: list[MetarRecord],
    limit: int = 100,
) -> None:

    banner(
        title
    )

    if not records:

        print(
            "无记录"
        )

        return

    for index, record in enumerate(
        records[:limit],
        start=1,
    ):

        print()

        print(
            f"[{index:02d}] "
            f"{record.valid} | "
            f"source="
            f"{record.source_type} | "
            f"query="
            f"{record.query_type}"
        )

        print(
            "SOURCE RAW:"
        )

        print(
            record.raw
        )

        print(
            "PARSER FORM:"
        )

        print(
            record.parser_form
        )


# ============================================================
# 14. Special附近报文
# ============================================================

def print_special_neighborhood(
    routine: list[MetarRecord],
    special: list[MetarRecord],
    combined: list[MetarRecord],
) -> None:
    """
    打印Special前后附近的报文。

    用于观察：

        Special发生时间
        前一个Routine
        后一个Routine
        同时刻是否存在Routine
    """

    banner(
        "Special时间附近报文"
    )

    if not special:

        print(
            "没有Special记录。"
        )

        return

    combined_sorted = sorted(
        combined,
        key=lambda record: (
            record.valid,
            record.raw,
        ),
    )

    for special_record in special:

        print()

        print(
            "-" * 78
        )

        print(
            "SPECIAL EVENT"
        )

        print(
            f"VALID："
            f"{special_record.valid}"
        )

        print(
            f"SOURCE RAW："
            f"{special_record.raw}"
        )

        print(
            "-" * 78
        )

        # 找到Combined里同一Identity的位置
        indexes = [

            index

            for index, record
            in enumerate(
                combined_sorted
            )

            if (
                record.identity
                == special_record.identity
            )
        ]

        if not indexes:

            print()

            print(
                "[WARNING] "
                "该Special没有出现在"
                "Combined结果中。"
            )

            continue

        for target_index in indexes:

            start = max(
                0,
                target_index - 3,
            )

            end = min(
                len(
                    combined_sorted
                ),
                target_index + 4,
            )

            print()

            for index in range(
                start,
                end,
            ):

                nearby = (
                    combined_sorted[
                        index
                    ]
                )

                if (
                    nearby.identity
                    == special_record.identity
                ):

                    marker = ">>> SPECIAL"

                else:

                    marker = "          "

                print(
                    f"{marker} | "
                    f"{nearby.valid} | "
                    f"{nearby.raw}"
                )


# ============================================================
# 15. Special分类交叉核验
# ============================================================

def audit_special_classification(
    routine: list[MetarRecord],
    special: list[MetarRecord],
    combined: list[MetarRecord],
) -> None:
    """
    专门判断：

    Special查询返回的报文：

        是否显式SPECI
        是否PREFIXLESS
        是否同时存在于Routine
        是否存在于Combined
    """

    banner(
        "Special Classification Audit"
    )

    if not special:

        print(
            "Special查询没有记录。"
        )

        return

    routine_ids = {
        record.identity
        for record in routine
    }

    combined_ids = {
        record.identity
        for record in combined
    }

    for index, record in enumerate(
        special,
        start=1,
    ):

        print()

        print(
            f"Special #{index}"
        )

        print(
            f"VALID："
            f"{record.valid}"
        )

        print(
            f"Source Type："
            f"{record.source_type}"
        )

        print(
            "存在于Routine："
            f"{record.identity in routine_ids}"
        )

        print(
            "存在于Combined："
            f"{record.identity in combined_ids}"
        )

        print(
            "SOURCE RAW："
        )

        print(
            record.raw
        )

        print(
            "PARSER FORM："
        )

        print(
            record.parser_form
        )


# ============================================================
# 16. 完整三路审计
# ============================================================

def full_audit(
    target_date: date,
) -> None:

    banner(
        "Stage 2："
        "Special日期完整三路审计"
    )

    print(
        f"审计日期："
        f"{target_date}"
    )

    print(
        "查询时区：UTC"
    )

    print()

    print(
        "本阶段执行："
    )

    print(
        "  A. Routine Only "
        "(report_type=3)"
    )

    print(
        "  B. Specials Only "
        "(report_type=4)"
    )

    print(
        "  C. Routine + Specials "
        "(report_type=3,4)"
    )


    # ========================================================
    # Probe A
    # ========================================================

    _, routine = run_probe(

        target_date=target_date,

        report_type="3",

        query_type="ROUTINE",

        show_output=True,
    )


    # 避免连续快速请求
    time.sleep(
        REQUEST_SLEEP_SECONDS
    )


    # ========================================================
    # Probe B
    # ========================================================

    _, special = run_probe(

        target_date=target_date,

        report_type="4",

        query_type="SPECIAL",

        show_output=True,
    )


    time.sleep(
        REQUEST_SLEEP_SECONDS
    )


    # ========================================================
    # Probe C
    # ========================================================

    _, combined = run_probe(

        target_date=target_date,

        report_type="3,4",

        query_type="COMBINED",

        show_output=True,
    )


    # ========================================================
    # 基础审计
    # ========================================================

    audit_records(
        "Routine",
        routine,
    )

    audit_records(
        "Special",
        special,
    )

    audit_records(
        "Combined",
        combined,
    )


    # ========================================================
    # 集合关系
    # ========================================================

    audit_set_relationship(
        routine,
        special,
        combined,
    )


    # ========================================================
    # Special分类
    # ========================================================

    audit_special_classification(
        routine,
        special,
        combined,
    )


    # ========================================================
    # COR / AMD
    # ========================================================

    audit_correction_signals(
        combined
    )


    # ========================================================
    # 打印Special
    # ========================================================

    print_samples(
        "Specials 全部样本",
        special,
        limit=100,
    )


    # ========================================================
    # 打印附近报文
    # ========================================================

    print_special_neighborhood(
        routine,
        special,
        combined,
    )


    # ========================================================
    # 最终判断
    # ========================================================

    banner(
        "V1.2.1 FINAL RESULT"
    )

    routine_ids = {
        record.identity
        for record in routine
    }

    special_ids = {
        record.identity
        for record in special
    }

    combined_ids = {
        record.identity
        for record in combined
    }

    union_ids = (
        routine_ids
        | special_ids
    )


    # --------------------------------------------------------
    # 条件1：必须存在Special
    # --------------------------------------------------------

    if not special:

        print(
            "RESULT：FAIL"
        )

        print()

        print(
            "原因："
        )

        print(
            "V1.2曾发现该日期"
            "存在Special，"
            "但本次report_type=4"
            "没有返回记录。"
        )

        print()

        print(
            "需要调查IEM返回稳定性"
            "或查询参数。"
        )

        return


    # --------------------------------------------------------
    # 条件2：Combined必须等于Union
    # --------------------------------------------------------

    if combined_ids != union_ids:

        print(
            "RESULT：WARN"
        )

        print()

        print(
            "Special已经确认存在，"
            "但："
        )

        print()

        print(
            "Routine ∪ Special "
            "与 Combined 不一致。"
        )

        print()

        print(
            "不能批准当前Combined"
            "查询作为正式历史采集逻辑。"
        )

        return


    # --------------------------------------------------------
    # PASS
    # --------------------------------------------------------

    print(
        "RESULT：PASS"
    )

    print()

    print(
        "已确认："
    )

    print(
        "1. ZUUU历史Special"
        "真实存在。"
    )

    print(
        "2. report_type=4"
        "能够返回Special记录。"
    )

    print(
        "3. Routine查询成功。"
    )

    print(
        "4. Special查询成功。"
    )

    print(
        "5. Combined查询成功。"
    )

    print(
        "6. Routine ∪ Special"
        "与Combined集合一致。"
    )

    print()

    print(
        "注意："
    )

    print(
        "本PASS只代表"
        "本测试日期的Source行为"
        "通过验证。"
    )

    print()

    print(
        "尚未批准："
    )

    print(
        "  - 正式历史批量采集"
    )

    print(
        "  - Bronze写入"
    )

    print(
        "  - Raw写入"
    )

    print(
        "  - Silver写入"
    )

    print(
        "  - TARGET_V1冻结"
    )


# ============================================================
# 17. Main
# ============================================================

def main() -> None:

    banner(
        "ZUUU Historical Source Probe V1.2.1"
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
        f"锁定审计日期："
        f"{AUDIT_DATE}"
    )

    print(
        "查询时区：UTC"
    )

    print()

    print(
        "V1.2历史发现："
    )

    print(
        "  2025-01-05"
        " 已发现1条Special。"
    )

    print()

    print(
        "本版本不重新扫描730天。"
    )

    print(
        "直接对该日期执行完整"
        "Routine / Special / Combined"
        "审计。"
    )

    print()

    print(
        "HTTP保护："
    )

    print(
        f"  Timeout："
        f"{HTTP_TIMEOUT}s"
    )

    print(
        f"  Max Retries："
        f"{MAX_RETRIES}"
    )

    print(
        f"  Retry Base："
        f"{RETRY_BASE_SECONDS}s"
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


    try:

        full_audit(
            AUDIT_DATE
        )

    except Exception as exc:

        banner(
            "V1.2.1 ABORTED"
        )

        print(
            "程序没有修改任何正式数据。"
        )

        print()

        print(
            "审计因为外部请求错误"
            "未能完成。"
        )

        print()

        print(
            f"错误类型："
            f"{type(exc).__name__}"
        )

        print(
            f"错误内容："
            f"{exc}"
        )

        print()

        print(
            "RESULT：INCONCLUSIVE"
        )

        print()

        print(
            "不要把网络失败解释为"
            "Special不存在。"
        )

        return


    # ========================================================
    # Safety Confirmation
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
        "本次运行仅进行了："
    )

    print(
        "  IEM历史源读取"
    )

    print(
        "  Routine审计"
    )

    print(
        "  Special审计"
    )

    print(
        "  Combined审计"
    )

    print(
        "  集合关系审计"
    )

    print(
        "  COR/AMD文本迹象检查"
    )

    print()

    print(
        "没有写入任何正式数据。"
    )


# ============================================================
# 18. 程序入口
# ============================================================

if __name__ == "__main__":

    main()