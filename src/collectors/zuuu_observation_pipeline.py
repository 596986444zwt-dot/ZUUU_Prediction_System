import subprocess
import sys
from pathlib import Path

from src.collectors.zuuu_metar_collector import (
    collect_zuuu_metar,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parent
    .parent
    .parent
)

RAW_STORAGE = (
    PROJECT_ROOT
    / "src"
    / "collectors"
    / "zuuu_raw_storage.py"
)

SILVER_PROCESSOR = (
    PROJECT_ROOT
    / "src"
    / "parsers"
    / "zuuu_silver_processor.py"
)


def run_python_script(
    name,
    script_path,
    arguments=None
):
    """
    使用当前 Python 解释器运行指定脚本。

    arguments:
        需要传递给脚本的命令行参数。
    """

    if arguments is None:
        arguments = []

    print()
    print("=" * 72)
    print(f"开始：{name}")
    print("=" * 72)

    command = [
        sys.executable,
        "-m",
        ".".join(Path(script_path).relative_to(PROJECT_ROOT).with_suffix("").parts),
    ]

    command.extend(
        str(argument)
        for argument in arguments
    )

    result = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
    )

    if result.returncode != 0:

        print()

        print(
            f"[ERROR] {name}执行失败"
        )

        print(
            f"退出代码："
            f"{result.returncode}"
        )

        raise RuntimeError(
            f"{name}执行失败"
        )

    print()
    print(
        f"[PASS] {name}"
    )


def main():

    print("=" * 72)
    print("ZUUU Observation Pipeline V1.1")
    print("=" * 72)

    print(
        f"Python：{sys.executable}"
    )

    print(
        f"项目目录：{PROJECT_ROOT}"
    )

    # ======================================================
    # STEP 1
    # Collector
    # ======================================================
    #
    # 直接调用 Collector。
    #
    # Collector 返回本轮实际创建的 Bronze 文件。
    # ======================================================

    print()
    print("=" * 72)
    print("开始：ZUUU METAR Collector")
    print("=" * 72)

    bronze_file = (
        collect_zuuu_metar()
    )

    bronze_file = Path(
        bronze_file
    ).resolve()

    # ======================================================
    # Collector输出安全检查
    # ======================================================

    if not bronze_file.exists():

        raise FileNotFoundError(
            "Collector返回的Bronze文件不存在："
            f"{bronze_file}"
        )

    if not bronze_file.is_file():

        raise ValueError(
            "Collector返回的Bronze路径不是文件："
            f"{bronze_file}"
        )

    print()

    print(
        "[PASS] ZUUU METAR Collector"
    )

    print(
        "Pipeline收到明确Bronze文件："
    )

    print(
        bronze_file
    )

    # ======================================================
    # STEP 2
    # 指定 Bronze -> Raw
    # ======================================================
    #
    # 注意：
    # 这里明确把本轮 bronze_file
    # 传给 Raw Storage。
    #
    # Raw Storage 不需要猜最新文件。
    # ======================================================

    run_python_script(
        name="ZUUU Raw Storage",
        script_path=RAW_STORAGE,
        arguments=[
            bronze_file
        ],
    )

    # ======================================================
    # STEP 3
    # Raw -> Silver
    # ======================================================

    run_python_script(
        name="ZUUU Silver Processor",
        script_path=SILVER_PROCESSOR,
    )

    # ======================================================
    # 完成
    # ======================================================

    print()
    print("=" * 72)

    print(
        "ZUUU Observation Pipeline V1.1 "
        "执行完成"
    )

    print()

    print(
        "本轮数据链："
    )

    print(
        "AWC"
        " -> Bronze"
        " -> Raw"
        " -> Parser/QC"
        " -> Silver"
    )

    print()

    print(
        "本轮Bronze："
    )

    print(
        bronze_file
    )

    print("=" * 72)


if __name__ == "__main__":
    main()
