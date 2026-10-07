import requests

from datetime import datetime, timezone

from config.settings import ZUUU_BRONZE_DIR


URL = "https://aviationweather.gov/api/data/metar"

PARAMS = {
    "ids": "ZUUU",
    "format": "json",
}

HEADERS = {
    "User-Agent": "ZUUU-Prediction-System/1.0"
}


def fetch_zuuu_metar():

    print("=" * 60)
    print("正在请求 ZUUU METAR...")
    print("=" * 60)

    response = requests.get(
        URL,
        params=PARAMS,
        headers=HEADERS,
        timeout=20,
    )

    response.raise_for_status()

    print(
        f"HTTP状态码："
        f"{response.status_code}"
    )

    return response.text


def save_raw_to_bronze(raw_text):

    ZUUU_BRONZE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    now_utc = datetime.now(
        timezone.utc
    )

    timestamp = now_utc.strftime(
        "%Y%m%dT%H%M%S_%fZ"
    )

    filename = (
        f"zuuu_metar_{timestamp}.json"
    )

    file_path = (
        ZUUU_BRONZE_DIR / filename
    )

    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(raw_text)

    print()
    print("Bronze原始数据保存成功")

    print(
        f"文件位置：{file_path}"
    )

    return file_path


def collect_zuuu_metar():
    """
    正式 Collector 接口。

    返回：
        本轮实际生成的 Bronze 文件路径。

    Pipeline 后续必须使用这个明确路径，
    不允许自行猜测最新文件。
    """

    raw_text = fetch_zuuu_metar()

    print()
    print("服务器原始返回：")
    print("-" * 60)
    print(raw_text)
    print("-" * 60)

    bronze_file = save_raw_to_bronze(
        raw_text
    )

    return bronze_file


def main():

    bronze_file = collect_zuuu_metar()

    print()
    print("=" * 60)
    print("ZUUU METAR Collector执行完成")

    print(
        f"本轮Bronze：{bronze_file}"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()