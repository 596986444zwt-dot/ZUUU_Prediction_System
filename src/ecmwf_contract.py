"""Shared ECMWF archive identity and validation. No network or database I/O."""

import math
from datetime import datetime, timedelta, timezone

DATA_SPEC_VERSION = "ECMWF_18_VARIABLE_V2"
MODEL = "IFS_HRES"
SOURCE = "Open-Meteo Single Runs API"
FORECAST_HOURS = 72
HOURLY_VARIABLES = [
    "temperature_2m", "dew_point_2m", "relative_humidity_2m",
    "surface_pressure", "pressure_msl", "cloud_cover", "cloud_cover_low",
    "cloud_cover_mid", "cloud_cover_high", "wind_speed_10m",
    "wind_direction_10m", "wind_gusts_10m", "shortwave_radiation",
    "direct_radiation", "diffuse_radiation", "precipitation", "rain", "cape",
]
EXPECTED_UNITS = dict(zip(HOURLY_VARIABLES, [
    "°C", "°C", "%", "hPa", "hPa", "%", "%", "%", "%", "km/h",
    "°", "km/h", "W/m²", "W/m²", "W/m²", "mm", "mm", "J/kg",
]))
FIELD_MAP = dict(zip(HOURLY_VARIABLES, [
    "temperature_2m_c", "dew_point_2m_c", "relative_humidity_2m_pct",
    "surface_pressure_hpa", "pressure_msl_hpa", "cloud_cover_pct",
    "cloud_cover_low_pct", "cloud_cover_mid_pct", "cloud_cover_high_pct",
    "wind_speed_10m_kmh", "wind_direction_10m_deg", "wind_gusts_10m_kmh",
    "shortwave_radiation_wm2", "direct_radiation_wm2", "diffuse_radiation_wm2",
    "precipitation_mm", "rain_mm", "cape_jkg",
]))
CYCLE_50R1_START = datetime(2026, 5, 12, 6, tzinfo=timezone.utc)
MINIMUM_RUN = datetime(2024, 3, 14, tzinfo=timezone.utc)


def parse_utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("Expected a datetime or ISO time string")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value):
    return parse_utc(value).isoformat()


def archive_cycle(run_time, api_model="ecmwf_ifs"):
    run_time = parse_utc(run_time)
    if api_model == "ecmwf_ifs" and run_time < CYCLE_50R1_START:
        return "49r1_hindcast"
    if run_time >= CYCLE_50R1_START:
        return "50r1"
    # Do not apply operational-cycle dates to an unverified archive product.
    return "unresolved_archive_cycle"


def validate_payload(data, run_time, *, require_usable=True):
    """Validate the complete 0..71 hour contract, retaining legitimate nulls.

    Entirely empty responses are quarantined by collectors. Silver may retain
    them with explicit ERROR QC, so it calls with require_usable=False.
    """
    run_time = parse_utc(run_time)
    if not isinstance(data, dict):
        raise ValueError("Response must be a JSON object")
    hourly, units = data.get("hourly"), data.get("hourly_units")
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        raise ValueError("hourly and hourly_units must be objects")
    if data.get("utc_offset_seconds", 0) != 0:
        raise ValueError("Expected UTC response")
    times = hourly.get("time")
    if not isinstance(times, list) or len(times) != FORECAST_HOURS:
        raise ValueError("Expected 72 hourly timestamps")
    for lead, value in enumerate(times):
        if not isinstance(value, str) or parse_utc(value) != run_time + timedelta(hours=lead):
            raise ValueError(f"Invalid target time at lead {lead}: {value!r}")
    non_null = 0
    for field in HOURLY_VARIABLES:
        values = hourly.get(field)
        if not isinstance(values, list) or len(values) != FORECAST_HOURS:
            raise ValueError(f"{field}: expected 72 values")
        if units.get(field) != EXPECTED_UNITS[field]:
            raise ValueError(f"{field}: unexpected unit {units.get(field)!r}")
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{field}: expected finite numeric values or null")
            non_null += 1
    if require_usable and non_null == 0:
        raise ValueError("All 18 weather variables are null")
    return hourly
