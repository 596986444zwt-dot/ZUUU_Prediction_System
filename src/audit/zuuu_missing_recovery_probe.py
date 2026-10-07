"""
ZUUU Missing Observation Recovery Probe V1.0
============================================

目标：
    调查 IEM 缺失的两个 ZUUU 历史观测：

    2025-09-24 03:00 UTC
    2025-09-24 04:00 UTC

第二来源：
    OGIMET getmetar

原则：
    只读
    不写正式数据库
    不写 Bronze
    不写 Raw
    不写 Silver

注意：
    本程序只验证第二来源是否存在对应历史METAR。
    找到的数据暂时不能直接进入正式Ground Truth。
"""

from __future__ import annotations

import csv
import io
import time

from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ============================================================
# 1. 基础配置
# ============================================================

STATION = "ZUUU"

OGIMET_URL = (
    "https://www.ogimet.com/cgi-bin/getmetar"
)

HTTP_TIMEOUT = 45

MAX_RETRIES = 5

RETRY_BASE_SECONDS = 2

USER_AGENT = (
    "Mozilla/5.0 "
    "ZUUU-Prediction-System/"
    "Missing-Recovery-Probe-V1.0"
)


# ============================================================
# 2. 调查窗口
# ============================================================

# 不只查03:00/04:00，
# 而是把前后几个小时一起拉出来，
# 用于核对时间连续性。

BEGIN = "202509240100"

END = "202509240700"


TARGET_TIMES = {

    datetime(
        2025,
        9,
        24,
        3,
        0,
        tzinfo=timezone.utc,
    ),

    datetime(
        2025,
        9,
        24,
        4,
        0,
        tzinfo=timezone.utc,
    ),
}


# ============================================================
# 3. 打印
# ============================================================

def banner(title: str) -> None:

    print()

    print("=" * 78)

    print(title)

    print("=" * 78)


# ============================================================
# 4. URL
# ============================================================

def build_url() -> str:

    params = {

        "begin": BEGIN,

        "end": END,

        "lang": "eng",

        "header": "yes",

        "icao": STATION,
    }

    return (
        OGIMET_URL
        + "?"
        + urlencode(params)
    )


# ============================================================
# 5. 下载
# ============================================================

def download() -> str:

    url = build_url()

    print(
        "Requesting independent archive..."
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

                    "Accept": (
                        "text/plain,"
                        "text/csv,"
                        "*/*"
                    ),

                    "Connection": "close",
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

            print(
                f"Download successful | "
                f"{len(text)} chars"
            )

            return text

        except (
            HTTPError,
            URLError,
            TimeoutError,
            ConnectionError,
        ) as exc:

            last_error = exc

            print(
                f"[RETRY] "
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
                20,
            )

            print(
                f"{wait_seconds}s 后重试..."
            )

            time.sleep(
                wait_seconds
            )

    raise RuntimeError(
        f"OGIMET download failed: "
        f"{last_error}"
    )


# ============================================================
# 6. 时间解析
# ============================================================

def build_datetime(
    year: str,
    month: str,
    day: str,
    hour: str,
    minute: str,
) -> datetime:

    return datetime(

        int(year),

        int(month),

        int(day),

        int(hour),

        int(minute),

        tzinfo=timezone.utc,
    )


# ============================================================
# 7. CSV解析
# ============================================================

def parse_ogimet(
    text: str,
):

    records = []

    reader = csv.reader(
        io.StringIO(text)
    )

    for row in reader:

        if not row:

            continue

        # 去除首尾空格
        row = [
            item.strip()
            for item in row
        ]

        # OGIMET文档格式：
        #
        # ICAOIND,
        # YEAR,
        # MONTH,
        # DAY,
        # HOUR,
        # MIN,
        # REPORT

        if len(row) < 7:

            continue

        station = (
            row[0]
            .strip()
            .upper()
        )

        # 跳过Header
        if station in {
            "ICAOIND",
            "ICAO",
        }:

            continue

        if station != STATION:

            continue

        try:

            valid = build_datetime(

                row[1],

                row[2],

                row[3],

                row[4],

                row[5],
            )

        except Exception:

            continue

        # REPORT中理论上可能含逗号，
        # 所以把后面的列重新合并。

        report = ",".join(
            row[6:]
        ).strip()

        records.append(
            (
                valid,
                report,
            )
        )

    return records


# ============================================================
# 8. 显示全部窗口
# ============================================================

def print_records(
    records,
) -> None:

    banner(
        "OGIMET WINDOW RECORDS"
    )

    if not records:

        print(
            "No ZUUU records returned."
        )

        return

    for valid, report in records:

        marker = (
            ">>>"
            if valid in TARGET_TIMES
            else "   "
        )

        print(
            f"{marker} "
            f"{valid.isoformat()} | "
            f"{report}"
        )


# ============================================================
# 9. 精确目标检查
# ============================================================

def check_targets(
    records,
) -> None:

    banner(
        "EXACT TARGET CHECK"
    )

    by_time = {}

    for valid, report in records:

        by_time.setdefault(
            valid,
            []
        ).append(
            report
        )


    for target in sorted(
        TARGET_TIMES
    ):

        matches = (
            by_time.get(
                target,
                []
            )
        )

        print()

        print(
            f"TARGET："
            f"{target.isoformat()}"
        )

        print(
            f"OGIMET MATCHES："
            f"{len(matches)}"
        )

        for report in matches:

            print(
                f"  {report}"
            )


# ============================================================
# 10. 最终报告
# ============================================================

def final_report(
    records,
) -> None:

    banner(
        "ZUUU MISSING OBSERVATION "
        "RECOVERY V1.0 FINAL REPORT"
    )

    by_time = {}

    for valid, report in records:

        by_time.setdefault(
            valid,
            []
        ).append(
            report
        )


    recovered = []

    missing = []


    for target in sorted(
        TARGET_TIMES
    ):

        matches = (
            by_time.get(
                target,
                []
            )
        )

        if matches:

            recovered.append(
                (
                    target,
                    matches,
                )
            )

        else:

            missing.append(
                target
            )


    print(
        f"Target timestamps："
        f"{len(TARGET_TIMES)}"
    )

    print(
        f"Recovered："
        f"{len(recovered)}"
    )

    print(
        f"Still missing："
        f"{len(missing)}"
    )

    print()


    for target, reports in recovered:

        print(
            f"[RECOVERED] "
            f"{target.isoformat()}"
        )

        for report in reports:

            print(
                f"  {report}"
            )


    for target in missing:

        print(
            f"[NOT FOUND] "
            f"{target.isoformat()}"
        )


    print()


    if len(recovered) == 2:

        print(
            "RESULT："
            "SECOND SOURCE RECOVERED BOTH"
        )

        print()

        print(
            "两个IEM缺失时间点"
            "均在独立历史来源中找到。"
        )

        print()

        print(
            "下一步："
            "逐条验证Raw报文和温度字段，"
            "再决定正式历史补洞策略。"
        )


    elif len(recovered) == 1:

        print(
            "RESULT："
            "PARTIAL RECOVERY"
        )

        print()

        print(
            "两个目标时间点中"
            "只恢复了一条。"
        )


    else:

        print(
            "RESULT："
            "NO SECOND-SOURCE RECOVERY"
        )

        print()

        print(
            "OGIMET当前响应中"
            "也没有找到两个目标时间点。"
        )

        print()

        print(
            "这仍然不能证明"
            "ZUUU当时没有发布报文。"
        )


# ============================================================
# 11. Main
# ============================================================

def main() -> None:

    banner(
        "ZUUU Missing Observation Recovery Probe V1.0"
    )

    print(
        f"Station：{STATION}"
    )

    print(
        "Primary archive gap：IEM"
    )

    print(
        "Independent archive probe：OGIMET"
    )

    print()

    print(
        "Target 1："
        "2025-09-24 03:00 UTC "
        "(北京时间11:00)"
    )

    print(
        "Target 2："
        "2025-09-24 04:00 UTC "
        "(北京时间12:00)"
    )

    print()

    print(
        "正式数据库写入：NO"
    )

    print(
        "Bronze写入：NO"
    )

    print(
        "Raw写入：NO"
    )

    print(
        "Silver写入：NO"
    )


    # ========================================================
    # Download
    # ========================================================

    text = download()


    # ========================================================
    # 为了审计透明，
    # 显示服务器原始响应前部。
    # ========================================================

    banner(
        "RAW RESPONSE PREVIEW"
    )

    print(
        text[:3000]
    )


    # ========================================================
    # Parse
    # ========================================================

    records = parse_ogimet(
        text
    )


    # ========================================================
    # Window
    # ========================================================

    print_records(
        records
    )


    # ========================================================
    # Targets
    # ========================================================

    check_targets(
        records
    )


    # ========================================================
    # Final
    # ========================================================

    final_report(
        records
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


if __name__ == "__main__":
    main()