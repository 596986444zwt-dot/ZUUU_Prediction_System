from pathlib import Path
from zoneinfo import ZoneInfo


# =========================
# 项目基础信息
# =========================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PROJECT_NAME = "ZUUU Prediction System"
PROJECT_VERSION = "1.0"


# =========================
# 机场信息
# =========================

STATION_ID = "ZUUU"
STATION_NAME = "Chengdu Shuangliu International Airport"

STATION_LATITUDE = 30.576
STATION_LONGITUDE = 103.950
STATION_ELEVATION_M = 494


# =========================
# 时区
# =========================

UTC_TZ = ZoneInfo("UTC")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


# =========================
# 数据目录
# =========================

DATA_DIR = PROJECT_ROOT / "data"

BRONZE_DIR = DATA_DIR / "bronze"

ZUUU_BRONZE_DIR = BRONZE_DIR / "zuuu"

ECMWF_BRONZE_DIR = BRONZE_DIR / "ecmwf"


# =========================
# 数据库
# =========================

DATABASE_DIR = PROJECT_ROOT / "database"

DATABASE_PATH = (
    DATABASE_DIR
    / "zuuu_prediction.db"
)


# =========================
# ECMWF
# =========================

ECMWF_MODEL = "IFS_HRES"

ECMWF_SOURCE = "Open-Meteo Single Runs API"

ECMWF_SINGLE_RUN_START_DATE = "2024-03-14"

ECMWF_RUN_HOURS_UTC = (
    0,
    6,
    12,
    18,
)