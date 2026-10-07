from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = ROOT / "database/zuuu_prediction.db"
AUXILIARY = ROOT / "database/phase3_auxiliary_v1.db"
GOLD = ROOT / "database/phase4_data_v1.db"
PRODUCTION_SHA = "2e0149050ed11fe2d750b6f4798f7a51b313bf8f59bb671da5a076daeb124367"
AUXILIARY_SHA = "ec8defe1778a66ed9ea20dc9e3825579de685c1818862a3eef113bba9a624f22"
VERSION = "DATA_V1"
CONTRACT_VERSION = "PHASE4_DATA_CONTRACT_V1"
BJT = ZoneInfo("Asia/Shanghai")
START = date(2024, 9, 3)
DATES = tuple((START + timedelta(days=i)).isoformat() for i in range(729))
HORIZONS = ("T0", "T1", "T2")
WEATHER = (
    "temperature_2m_c", "dew_point_2m_c", "relative_humidity_2m_pct", "surface_pressure_hpa",
    "pressure_msl_hpa", "cloud_cover_pct", "cloud_cover_low_pct", "cloud_cover_mid_pct",
    "cloud_cover_high_pct", "wind_speed_10m_kmh", "wind_direction_10m_deg", "wind_gusts_10m_kmh",
    "shortwave_radiation_wm2", "direct_radiation_wm2", "diffuse_radiation_wm2",
    "precipitation_mm", "rain_mm", "cape_jkg",
)
SOLAR_FEATURES = (
    "hour_bjt", "day_of_year", "month", "season", "sin_hour", "cos_hour", "sin_doy", "cos_doy",
    "sunrise", "sunset", "solar_elevation", "solar_azimuth", "daylight_duration",
    "minutes_since_sunrise", "minutes_to_sunset",
)
TARGET_FIELDS = (
    "business_date_bjt", "daily_tmax_c", "first_tmax_time_bjt", "last_tmax_time_bjt",
    "tmax_occurrence_count", "observation_count", "hourly_coverage_count", "has_correction",
    "tmax_has_correction", "has_recovery", "tmax_has_recovery", "rule_version", "target_version", "target_status",
)


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Explicit timezone required")
    return value.astimezone(timezone.utc)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)
