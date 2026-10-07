"""
ZUUU Historical Completeness Audit V1.0
=======================================

项目：
    ZUUU 成都双流机场温度预测系统

阶段：
    Phase 1 - ZUUU Ground Truth

用途：
    在正式建立 Historical Raw Archive 之前，
    审计 IEM 历史 ZUUU 数据源的时间完整性。

本程序检查：

    1. 每个 UTC 日 Routine 数量
    2. Routine 是否覆盖 00~23 UTC
    3. 是否存在缺失整点
    4. 是否存在非整点 Routine
    5. 是否存在重复记录
    6. 是否存在同 valid 多个不同 Raw
    7. 每日 Special 数量
    8. 最大观测时间间隔
    9. 跨 UTC 日边界是否连续
   10. 网络失败日期是否存在

重要：
    本程序只做历史数据源审计。

    不写：
        正式数据库
        Bronze
        Raw
        Silver

    不生成：
        Ground Truth
        TARGET_V1

数据源：
    Iowa Environmental Mesonet (IEM)

站点：
    ZUUU

Network：
    CN_ASOS

查询：
    report_type=3 -> Routine
    report_type=4 -> Special
"""

from __future__ import annotations

import csv
import io
import time

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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


# ============================================================
# 2. 审计范围
# ============================================================

# 与前面的 V1.3 两年窗口基本保持一致。
#
# 2024-09-02 ~ 2026-09-01
# 包含首尾，共 730 个 UTC 自然日。

AUDIT_START_DATE = date(2024, 9, 2)
AUDIT_END_DATE = date(2026, 9, 1)


# ============================================================
# 3. HTTP配置
# ============================================================

HTTP_TIMEOUT = 45

MAX_RETRIES = 6

RETRY_BASE_SECONDS = 2

# 每次HTTP请求之间稍微暂停，避免给IEM造成过大压力。
REQUEST_SLEEP_SECONDS = 0.75

USER_AGENT = (
    "ZUUU-Prediction-System/"
    "Historical-Completeness-Audit-V1.0 "
    "(research; historical observation audit)"
)


# ============================================================
# 4. 审计参考阈值
# ============================================================

# 根据前面的真实样本：
#
# ZUUU Routine通常为每小时1条，
# 因此正常UTC日通常为24条。
#
# 注意：
# 24只是审计参考值，
# 不是最终Ground Truth硬规则。

EXPECTED_ROUTINE_PER_UTC_DAY = 24


# 相邻所有观测时间超过90分钟：
# 进入WARNING。

GAP_WARNING_MINUTES = 90


# 超过120分钟：
# 进入CRITICAL。

GAP_CRITICAL_MINUTES = 120


# ============================================================
# 5. 数据结构
# ============================================================

@dataclass(frozen=True)
class Observation:
    valid_text: str
    valid_utc: datetime
    raw: str

    # IEM查询来源：
    # ROUTINE / SPECIAL
    source_query_class: str

    # Raw报文分类：
    # METAR / SPECI / COR / AMD / PREFIXLESS / OTHER
    message_class: str

    station: str

    @property
    def identity(self) -> tuple[str, str]:
        return (
            self.valid_text,
            self.raw,
        )


@dataclass
class DayAudit:
    target_date: date

    routine_request_ok: bool
    special_request_ok: bool

    routine_count: int
    special_count: int
    total_count: int

    routine_full_hour_count: int
    routine_non_hour_count: int

    missing_routine_hours: list[int]
    duplicate_routine_hours: list[int]

    duplicate_identity_count: int
    same_valid_multi_raw_count: int

    max_gap_minutes: float | None
    warning_gap_count: int
    critical_gap_count: int

    routine_error: str | None
    special_error: str | None


# ============================================================
# 6. 打印工具
# ============================================================

def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def normalize_space(text: str) -> str:
    return " ".join(
        text.strip().split()
    )


# ============================================================
# 7. Message Classification
# ============================================================

def classify_message(raw: str) -> str:
    """
    只根据Raw文本本身分类。

    注意：

        source_query_class
        与
        message_class

    是两个不同维度。

    例如：

        source_query_class = SPECIAL
        message_class = COR

    这是我们在 V1.2.1 / V1.3
    已经实际观察到的情况。
    """

    text = normalize_space(raw).upper()

    if text.startswith("METAR "):
        return "METAR"

    if text.startswith("SPECI "):
        return "SPECI"

    if text.startswith("COR "):
        return "COR"

    if text.startswith("AMD "):
        return "AMD"

    if text.startswith(STATION + " "):
        return "PREFIXLESS"

    return "OTHER"


# ============================================================
# 8. Station提取
# ============================================================

def extract_station(raw: str) -> str:
    """
    支持：

        ZUUU ...
        METAR ZUUU ...
        SPECI ZUUU ...
        COR ZUUU ...
        AMD ZUUU ...

    当前仅用于Probe/Audit。
    正式Parser后续单独建设。
    """

    text = normalize_space(raw).upper()

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
# 9. 构建IEM URL
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
        ("station", STATION),
        ("data", "metar"),

        ("year1", str(target_date.year)),
        ("month1", str(target_date.month)),
        ("day1", str(target_date.day)),

        ("year2", str(next_day.year)),
        ("month2", str(next_day.month)),
        ("day2", str(next_day.day)),

        ("tz", "UTC"),
        ("format", "onlycomma"),

        ("latlon", "no"),
        ("elev", "no"),

        ("missing", "null"),
        ("trace", "null"),

        ("direct", "no"),
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
# 10. HTTP下载
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
                raw_bytes = response.read()

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
                * (2 ** (attempt - 1)),
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
        f"IEM request failed | "
        f"date={target_date} | "
        f"type={report_type} | "
        f"error={last_error}"
    )


# ============================================================
# 11. valid时间解析
# ============================================================

def parse_valid_utc(text: str) -> datetime:
    """
    IEM valid通常类似：

        2025-01-05 14:30

    当前明确解释为UTC。

    后续正式Time Normalization阶段，
    再统一转换Asia/Shanghai。
    """

    text = text.strip()

    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(
                text,
                fmt,
            )

            return dt.replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            continue

    raise ValueError(
        f"Unknown valid datetime format: {text}"
    )


# ============================================================
# 12. CSV解析
# ============================================================

def parse_response(
    text: str,
    source_query_class: str,
) -> list[Observation]:

    observations: list[Observation] = []

    if not text.strip():
        return observations

    reader = csv.DictReader(
        io.StringIO(text)
    )

    if reader.fieldnames is None:
        return observations

    field_map = {
        name.lower().strip(): name
        for name in reader.fieldnames
        if name is not None
    }

    valid_field = field_map.get("valid")
    metar_field = field_map.get("metar")

    if not valid_field:
        raise RuntimeError(
            "IEM response missing 'valid' field"
        )

    if not metar_field:
        raise RuntimeError(
            "IEM response missing 'metar' field"
        )

    for row in reader:

        valid_text = (
            row.get(valid_field)
            or ""
        ).strip()

        raw = (
            row.get(metar_field)
            or ""
        ).strip()

        if not valid_text:
            continue

        if not raw:
            continue

        if raw.lower() in {
            "null",
            "none",
            "nan",
        }:
            continue

        # 注意：
        # 当前Probe为了比较方便做空格标准化。
        #
        # 正式Bronze阶段不能这样做。
        # 正式Bronze必须永久保存HTTP原始响应。
        normalized_raw = normalize_space(
            raw
        )

        valid_utc = parse_valid_utc(
            valid_text
        )

        message_class = classify_message(
            normalized_raw
        )

        station = extract_station(
            normalized_raw
        )

        observations.append(
            Observation(
                valid_text=valid_text,
                valid_utc=valid_utc,
                raw=normalized_raw,
                source_query_class=(
                    source_query_class
                ),
                message_class=message_class,
                station=station,
            )
        )

    return observations


# ============================================================
# 13. 请求Routine
# ============================================================

def query_routine_day(
    target_date: date,
) -> list[Observation]:

    text = download_text(
        target_date,
        "3",
    )

    return parse_response(
        text,
        "ROUTINE",
    )


# ============================================================
# 14. 请求Special
# ============================================================

def query_special_day(
    target_date: date,
) -> list[Observation]:

    text = download_text(
        target_date,
        "4",
    )

    return parse_response(
        text,
        "SPECIAL",
    )


# ============================================================
# 15. 单日完整性审计
# ============================================================

def audit_day(
    target_date: date,
    routine: list[Observation],
    special: list[Observation],
    routine_request_ok: bool,
    special_request_ok: bool,
    routine_error: str | None,
    special_error: str | None,
) -> DayAudit:

    observations = (
        routine
        + special
    )

    # --------------------------------------------------------
    # Station检查
    # --------------------------------------------------------

    for observation in observations:

        if observation.station != STATION:

            print(
                f"[STATION WARNING] "
                f"{target_date} | "
                f"source="
                f"{observation.source_query_class} | "
                f"class="
                f"{observation.message_class} | "
                f"station="
                f"{observation.station!r} | "
                f"{observation.raw}"
            )


    # --------------------------------------------------------
    # Routine整点检查
    # --------------------------------------------------------

    routine_full_hour = [
        item
        for item in routine
        if (
            item.valid_utc.minute == 0
            and item.valid_utc.second == 0
        )
    ]

    routine_non_hour = [
        item
        for item in routine
        if not (
            item.valid_utc.minute == 0
            and item.valid_utc.second == 0
        )
    ]


    # --------------------------------------------------------
    # Routine小时覆盖
    # --------------------------------------------------------

    hour_counter = Counter(
        item.valid_utc.hour
        for item in routine_full_hour
    )

    missing_hours = [
        hour
        for hour in range(24)
        if hour_counter.get(
            hour,
            0,
        ) == 0
    ]

    duplicate_hours = [
        hour
        for hour, count
        in sorted(
            hour_counter.items()
        )
        if count > 1
    ]


    # --------------------------------------------------------
    # 完全重复identity
    # --------------------------------------------------------

    identity_counter = Counter(
        item.identity
        for item in observations
    )

    duplicate_identity_count = sum(
        count - 1
        for count
        in identity_counter.values()
        if count > 1
    )


    # --------------------------------------------------------
    # 同valid多个不同Raw
    # --------------------------------------------------------

    by_valid: dict[
        str,
        set[str],
    ] = defaultdict(set)

    for item in observations:
        by_valid[
            item.valid_text
        ].add(
            item.raw
        )

    same_valid_multi_raw = {
        valid: raws
        for valid, raws
        in by_valid.items()
        if len(raws) > 1
    }


    # --------------------------------------------------------
    # 时间Gap
    #
    # 使用Routine + Special的全部唯一观测时间。
    # --------------------------------------------------------

    unique_times = sorted({
        item.valid_utc
        for item in observations
    })

    gaps: list[float] = []

    for previous, current in zip(
        unique_times,
        unique_times[1:],
    ):

        gap_minutes = (
            current
            - previous
        ).total_seconds() / 60.0

        gaps.append(
            gap_minutes
        )

    max_gap = (
        max(gaps)
        if gaps
        else None
    )

    warning_gap_count = sum(
        1
        for gap in gaps
        if gap > GAP_WARNING_MINUTES
    )

    critical_gap_count = sum(
        1
        for gap in gaps
        if gap > GAP_CRITICAL_MINUTES
    )


    return DayAudit(
        target_date=target_date,

        routine_request_ok=(
            routine_request_ok
        ),

        special_request_ok=(
            special_request_ok
        ),

        routine_count=len(routine),

        special_count=len(special),

        total_count=len(observations),

        routine_full_hour_count=(
            len(routine_full_hour)
        ),

        routine_non_hour_count=(
            len(routine_non_hour)
        ),

        missing_routine_hours=(
            missing_hours
        ),

        duplicate_routine_hours=(
            duplicate_hours
        ),

        duplicate_identity_count=(
            duplicate_identity_count
        ),

        same_valid_multi_raw_count=(
            len(same_valid_multi_raw)
        ),

        max_gap_minutes=max_gap,

        warning_gap_count=(
            warning_gap_count
        ),

        critical_gap_count=(
            critical_gap_count
        ),

        routine_error=routine_error,

        special_error=special_error,
    )


# ============================================================
# 16. 判断某日是否异常
# ============================================================

def is_abnormal_day(
    day: DayAudit,
) -> bool:

    if not day.routine_request_ok:
        return True

    if not day.special_request_ok:
        return True

    if (
        day.routine_count
        != EXPECTED_ROUTINE_PER_UTC_DAY
    ):
        return True

    if day.missing_routine_hours:
        return True

    if day.routine_non_hour_count > 0:
        return True

    if day.duplicate_routine_hours:
        return True

    if day.duplicate_identity_count > 0:
        return True

    if day.same_valid_multi_raw_count > 0:
        return True

    if day.warning_gap_count > 0:
        return True

    return False


# ============================================================
# 17. 打印异常日
# ============================================================

def print_day_warning(
    day: DayAudit,
) -> None:

    print()

    print(
        f"[AUDIT WARNING] "
        f"{day.target_date}"
    )

    print(
        f"  Routine Request："
        f"{'OK' if day.routine_request_ok else 'UNKNOWN'}"
    )

    print(
        f"  Special Request："
        f"{'OK' if day.special_request_ok else 'UNKNOWN'}"
    )

    print(
        f"  Routine："
        f"{day.routine_count}"
    )

    print(
        f"  Special："
        f"{day.special_count}"
    )

    print(
        f"  Total："
        f"{day.total_count}"
    )

    print(
        f"  Missing Hours："
        f"{day.missing_routine_hours}"
    )

    print(
        f"  Duplicate Hours："
        f"{day.duplicate_routine_hours}"
    )

    print(
        f"  Non-hour Routine："
        f"{day.routine_non_hour_count}"
    )

    print(
        f"  Duplicate Identity："
        f"{day.duplicate_identity_count}"
    )

    print(
        f"  Same Valid Multi Raw："
        f"{day.same_valid_multi_raw_count}"
    )

    print(
        f"  Max Gap："
        f"{day.max_gap_minutes}"
    )

    if day.routine_error:
        print(
            f"  Routine Error："
            f"{day.routine_error}"
        )

    if day.special_error:
        print(
            f"  Special Error："
            f"{day.special_error}"
        )


# ============================================================
# 18. 主扫描
# ============================================================

def run_audit() -> tuple[
    list[DayAudit],
    list[Observation],
]:

    banner(
        "Stage 1：Historical Completeness Scan"
    )

    total_days = (
        AUDIT_END_DATE
        - AUDIT_START_DATE
    ).days + 1

    print(
        f"开始日期："
        f"{AUDIT_START_DATE}"
    )

    print(
        f"结束日期："
        f"{AUDIT_END_DATE}"
    )

    print(
        f"UTC自然日数量："
        f"{total_days}"
    )

    print()

    print(
        "Routine：IEM report_type=3"
    )

    print(
        "Special：IEM report_type=4"
    )

    print()

    print(
        "失败请求记为UNKNOWN，"
        "绝不解释为0条记录。"
    )

    audits: list[DayAudit] = []

    all_observations: list[
        Observation
    ] = []

    current_date = (
        AUDIT_START_DATE
    )

    index = 0

    while (
        current_date
        <= AUDIT_END_DATE
    ):

        index += 1

        routine: list[
            Observation
        ] = []

        special: list[
            Observation
        ] = []

        routine_ok = False
        special_ok = False

        routine_error = None
        special_error = None


        # ====================================================
        # Routine
        # ====================================================

        try:

            routine = query_routine_day(
                current_date
            )

            routine_ok = True

        except Exception as exc:

            routine_error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            print()

            print(
                f"[UNKNOWN ROUTINE] "
                f"{current_date}"
            )

            print(
                f"  {routine_error}"
            )


        # ----------------------------------------------------
        # 两个HTTP请求之间暂停
        # ----------------------------------------------------

        time.sleep(
            REQUEST_SLEEP_SECONDS
        )


        # ====================================================
        # Special
        # ====================================================

        try:

            special = query_special_day(
                current_date
            )

            special_ok = True

        except Exception as exc:

            special_error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            print()

            print(
                f"[UNKNOWN SPECIAL] "
                f"{current_date}"
            )

            print(
                f"  {special_error}"
            )


        # ====================================================
        # Audit
        # ====================================================

        day = audit_day(
            target_date=current_date,

            routine=routine,

            special=special,

            routine_request_ok=(
                routine_ok
            ),

            special_request_ok=(
                special_ok
            ),

            routine_error=(
                routine_error
            ),

            special_error=(
                special_error
            ),
        )

        audits.append(
            day
        )

        # 只有成功请求的数据才进入
        # 当前临时内存序列。

        if routine_ok:
            all_observations.extend(
                routine
            )

        if special_ok:
            all_observations.extend(
                special
            )


        # ====================================================
        # 异常打印
        # ====================================================

        if is_abnormal_day(
            day
        ):
            print_day_warning(
                day
            )


        # ====================================================
        # 进度
        # ====================================================

        if (
            index == 1
            or index % 30 == 0
            or index == total_days
        ):

            print()

            print(
                f"[PROGRESS] "
                f"{index}/"
                f"{total_days} | "
                f"{current_date}"
            )


        current_date += (
            timedelta(days=1)
        )

        time.sleep(
            REQUEST_SLEEP_SECONDS
        )

    return (
        audits,
        all_observations,
    )


# ============================================================
# 19. 整个时间序列Gap
# ============================================================

def calculate_full_series_gaps(
    observations: list[Observation],
) -> list[
    tuple[
        datetime,
        datetime,
        float,
    ]
]:

    unique_times = sorted({
        item.valid_utc
        for item in observations
    })

    results = []

    for previous, current in zip(
        unique_times,
        unique_times[1:],
    ):

        gap_minutes = (
            current
            - previous
        ).total_seconds() / 60.0

        if gap_minutes > GAP_WARNING_MINUTES:

            results.append(
                (
                    previous,
                    current,
                    gap_minutes,
                )
            )

    return results


# ============================================================
# 20. Summary
# ============================================================

def print_summary(
    audits: list[DayAudit],
    observations: list[Observation],
) -> None:

    banner(
        "Historical Completeness Summary"
    )

    total_days = len(
        audits
    )

    fully_successful_days = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.special_request_ok
        )
    ]

    routine_unknown_days = [
        day
        for day in audits
        if not day.routine_request_ok
    ]

    special_unknown_days = [
        day
        for day in audits
        if not day.special_request_ok
    ]

    print(
        f"审计UTC自然日："
        f"{total_days}"
    )

    print(
        f"Routine+Special均成功："
        f"{len(fully_successful_days)}"
    )

    print(
        f"Routine UNKNOWN："
        f"{len(routine_unknown_days)}"
    )

    print(
        f"Special UNKNOWN："
        f"{len(special_unknown_days)}"
    )

    print()

    print(
        f"内存中总观测记录："
        f"{len(observations)}"
    )


    # ========================================================
    # Routine每日数量分布
    # ========================================================

    routine_count_distribution = Counter(
        day.routine_count
        for day in audits
        if day.routine_request_ok
    )

    print()

    print(
        "每日Routine数量分布："
    )

    for count, days in sorted(
        routine_count_distribution.items()
    ):

        print(
            f"  {count:>3} 条："
            f"{days} 天"
        )


    routine_24_days = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.routine_count
            == EXPECTED_ROUTINE_PER_UTC_DAY
        )
    ]

    routine_non24_days = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.routine_count
            != EXPECTED_ROUTINE_PER_UTC_DAY
        )
    ]

    print()

    print(
        f"Routine=24："
        f"{len(routine_24_days)} 天"
    )

    print(
        f"Routine!=24："
        f"{len(routine_non24_days)} 天"
    )


    # ========================================================
    # 小时完整性
    # ========================================================

    missing_hour_days = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.missing_routine_hours
        )
    ]

    non_hour_days = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.routine_non_hour_count > 0
        )
    ]

    duplicate_hour_days = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.duplicate_routine_hours
        )
    ]

    print()

    print(
        f"存在缺整点小时："
        f"{len(missing_hour_days)} 天"
    )

    print(
        f"存在非整点Routine："
        f"{len(non_hour_days)} 天"
    )

    print(
        f"存在重复Routine小时："
        f"{len(duplicate_hour_days)} 天"
    )


    # ========================================================
    # Duplicate
    # ========================================================

    duplicate_identity_days = [
        day
        for day in audits
        if (
            day.duplicate_identity_count > 0
        )
    ]

    multi_raw_days = [
        day
        for day in audits
        if (
            day.same_valid_multi_raw_count > 0
        )
    ]

    print()

    print(
        f"存在完全重复Identity："
        f"{len(duplicate_identity_days)} 天"
    )

    print(
        f"存在同valid多Raw："
        f"{len(multi_raw_days)} 天"
    )


    # ========================================================
    # Special
    # ========================================================

    special_days = [
        day
        for day in audits
        if (
            day.special_request_ok
            and day.special_count > 0
        )
    ]

    total_special = sum(
        day.special_count
        for day in audits
        if day.special_request_ok
    )

    print()

    print(
        f"存在Special的日期："
        f"{len(special_days)} 天"
    )

    print(
        f"Special总记录："
        f"{total_special}"
    )


    # ========================================================
    # Gap
    # ========================================================

    warning_gap_days = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.warning_gap_count > 0
        )
    ]

    critical_gap_days = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.critical_gap_count > 0
        )
    ]

    print()

    print(
        f"存在>{GAP_WARNING_MINUTES}"
        f"分钟日内Gap："
        f"{len(warning_gap_days)} 天"
    )

    print(
        f"存在>{GAP_CRITICAL_MINUTES}"
        f"分钟严重日内Gap："
        f"{len(critical_gap_days)} 天"
    )


    # ========================================================
    # 整体Gap
    # ========================================================

    full_gaps = (
        calculate_full_series_gaps(
            observations
        )
    )

    print()

    print(
        f"完整时间序列>"
        f"{GAP_WARNING_MINUTES}"
        f"分钟Gap："
        f"{len(full_gaps)}"
    )

    if full_gaps:

        largest_gap = max(
            full_gaps,
            key=lambda row: row[2],
        )

        print()

        print(
            "全周期最大Gap："
        )

        print(
            f"  FROM："
            f"{largest_gap[0].isoformat()}"
        )

        print(
            f"  TO："
            f"{largest_gap[1].isoformat()}"
        )

        print(
            f"  GAP："
            f"{largest_gap[2]:.1f}分钟"
        )


# ============================================================
# 21. Abnormal Day Details
# ============================================================

def print_abnormal_days(
    audits: list[DayAudit],
) -> None:

    banner(
        "Abnormal Day Details"
    )

    abnormal_days = [
        day
        for day in audits
        if is_abnormal_day(day)
    ]

    print(
        f"异常/待审计日期总数："
        f"{len(abnormal_days)}"
    )

    if not abnormal_days:

        print()

        print(
            "当前规则下没有发现异常日期。"
        )

        return

    for day in abnormal_days:

        print()

        print(
            "-" * 78
        )

        print(
            f"DATE："
            f"{day.target_date}"
        )

        print(
            f"Routine Request："
            f"{'OK' if day.routine_request_ok else 'UNKNOWN'}"
        )

        print(
            f"Special Request："
            f"{'OK' if day.special_request_ok else 'UNKNOWN'}"
        )

        print(
            f"Routine："
            f"{day.routine_count}"
        )

        print(
            f"Special："
            f"{day.special_count}"
        )

        print(
            f"Missing Hours："
            f"{day.missing_routine_hours}"
        )

        print(
            f"Duplicate Hours："
            f"{day.duplicate_routine_hours}"
        )

        print(
            f"Non-hour Routine："
            f"{day.routine_non_hour_count}"
        )

        print(
            f"Duplicate Identity："
            f"{day.duplicate_identity_count}"
        )

        print(
            f"Same Valid Multi Raw："
            f"{day.same_valid_multi_raw_count}"
        )

        print(
            f"Max Gap："
            f"{day.max_gap_minutes}"
        )

        if day.routine_error:

            print(
                f"Routine Error："
                f"{day.routine_error}"
            )

        if day.special_error:

            print(
                f"Special Error："
                f"{day.special_error}"
            )


# ============================================================
# 22. Full-Series Gap Details
# ============================================================

def print_full_series_gaps(
    observations: list[Observation],
) -> None:

    gaps = calculate_full_series_gaps(
        observations
    )

    if not gaps:
        return

    banner(
        "Cross-Day / Full-Series Gap Details"
    )

    for (
        previous,
        current,
        gap_minutes,
    ) in gaps:

        print(
            f"{previous.isoformat()} "
            f"→ "
            f"{current.isoformat()} "
            f"| "
            f"{gap_minutes:.1f} min"
        )


# ============================================================
# 23. Final Report
# ============================================================

def final_report(
    audits: list[DayAudit],
    observations: list[Observation],
) -> None:

    banner(
        "ZUUU HISTORICAL COMPLETENESS "
        "AUDIT V1.0 FINAL REPORT"
    )

    total_days = len(
        audits
    )

    routine_unknown = [
        day
        for day in audits
        if not day.routine_request_ok
    ]

    special_unknown = [
        day
        for day in audits
        if not day.special_request_ok
    ]

    non24 = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.routine_count
            != EXPECTED_ROUTINE_PER_UTC_DAY
        )
    ]

    missing = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.missing_routine_hours
        )
    ]

    non_hour = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.routine_non_hour_count > 0
        )
    ]

    duplicate_hours = [
        day
        for day in audits
        if (
            day.routine_request_ok
            and day.duplicate_routine_hours
        )
    ]

    duplicate_identity = [
        day
        for day in audits
        if day.duplicate_identity_count > 0
    ]

    multi_raw = [
        day
        for day in audits
        if day.same_valid_multi_raw_count > 0
    ]

    critical_gaps = [
        day
        for day in audits
        if day.critical_gap_count > 0
    ]

    total_special = sum(
        day.special_count
        for day in audits
        if day.special_request_ok
    )


    print(
        f"审计范围："
        f"{AUDIT_START_DATE} "
        f"→ "
        f"{AUDIT_END_DATE}"
    )

    print(
        "时间语义：UTC"
    )

    print()

    print(
        f"总UTC自然日："
        f"{total_days}"
    )

    print(
        f"Routine UNKNOWN："
        f"{len(routine_unknown)}"
    )

    print(
        f"Special UNKNOWN："
        f"{len(special_unknown)}"
    )

    print()

    print(
        f"总观测记录："
        f"{len(observations)}"
    )

    print(
        f"Special记录："
        f"{total_special}"
    )

    print()

    print(
        f"Routine != 24："
        f"{len(non24)} 天"
    )

    print(
        f"缺整点小时："
        f"{len(missing)} 天"
    )

    print(
        f"非整点Routine："
        f"{len(non_hour)} 天"
    )

    print(
        f"重复Routine小时："
        f"{len(duplicate_hours)} 天"
    )

    print(
        f"完全重复Identity："
        f"{len(duplicate_identity)} 天"
    )

    print(
        f"同valid多Raw："
        f"{len(multi_raw)} 天"
    )

    print(
        f">{GAP_CRITICAL_MINUTES}"
        f"分钟严重Gap："
        f"{len(critical_gaps)} 天"
    )

    print()


    # ========================================================
    # 状态
    # ========================================================

    if (
        routine_unknown
        or special_unknown
    ):

        status = (
            "INCONCLUSIVE"
        )

        reason = (
            "存在HTTP请求UNKNOWN日期，"
            "不能完成完整性判断。"
        )

    elif (
        non24
        or missing
        or non_hour
        or duplicate_hours
        or duplicate_identity
        or multi_raw
        or critical_gaps
    ):

        status = (
            "REVIEW REQUIRED"
        )

        reason = (
            "发现需要进一步解释的"
            "历史时间完整性异常。"
        )

    else:

        status = (
            "PASS FOR TEST WINDOW"
        )

        reason = (
            "当前测试窗口没有发现"
            "上述完整性异常。"
        )


    print(
        "SOURCE COMPLETENESS STATUS："
        f"{status}"
    )

    print()

    print(
        f"原因：{reason}"
    )

    print()

    print(
        "重要："
    )

    print(
        "本结果只评价历史源完整性。"
    )

    print(
        "不代表TARGET_V1已经冻结。"
    )

    print()

    print(
        "本程序没有生成Ground Truth。"
    )

    print(
        "本程序没有修改任何正式数据。"
    )


# ============================================================
# 24. Main
# ============================================================

def main() -> None:

    banner(
        "ZUUU Historical Completeness Audit V1.0"
    )

    print(
        f"Station：{STATION}"
    )

    print(
        f"Network：{NETWORK}"
    )

    print(
        f"Audit："
        f"{AUDIT_START_DATE} "
        f"→ "
        f"{AUDIT_END_DATE}"
    )

    print()

    print(
        "Routine：report_type=3"
    )

    print(
        "Special：report_type=4"
    )

    print(
        "Source Timezone：UTC"
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
        "本程序只进行"
        "Historical Source Completeness Audit。"
    )


    # ========================================================
    # Stage 1
    # ========================================================

    audits, observations = (
        run_audit()
    )


    # ========================================================
    # Stage 2
    # ========================================================

    print_summary(
        audits,
        observations,
    )


    # ========================================================
    # Stage 3
    # ========================================================

    print_abnormal_days(
        audits
    )


    # ========================================================
    # Stage 4
    # ========================================================

    print_full_series_gaps(
        observations
    )


    # ========================================================
    # Final
    # ========================================================

    final_report(
        audits,
        observations,
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
        "本次只读取IEM历史数据"
        "并执行完整性审计。"
    )


# ============================================================
# 25. Entry
# ============================================================

if __name__ == "__main__":
    main()