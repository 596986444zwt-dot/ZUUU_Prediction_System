"""Pure parser for current Meteostat source-tagged hourly CSV (not legacy v1)."""
import csv
import io
import math
from datetime import datetime, timezone

from src.auxiliary.contracts import utc

PARSER_VERSION = "METEOSTAT_SOURCE_TAGGED_CSV_V1"
# Current 2026 provider contract. Snowfall and snow depth are distinct, in cm.
PARAMETERS = {
    "temp": ("temperature_c", "degC", -100, 70),
    "dwpt": ("dewpoint_c", "degC", -120, 70),
    "rhum": ("relative_humidity_pct", "%", 0, 100),
    "prcp": ("precipitation_mm", "mm", 0, 2000),
    "snow": ("snowfall_cm", "cm", 0, 2000),
    "snwd": ("snow_depth_cm", "cm", 0, 2000),
    "wdir": ("wind_direction_deg", "degree", 0, 360),
    "wspd": ("wind_speed_kmh", "km/h", 0, 500),
    "wpgt": ("wind_gust_kmh", "km/h", 0, 500),
    "pres": ("pressure_msl_hpa", "hPa", 800, 1100),
    "tsun": ("sunshine_minutes", "minute", 0, 60),
    "cldc": ("cloud_cover_okta", "okta", 0, 8),
    "coco": ("weather_condition_code", "code", 1, 27),
}
OBSERVATION_PROVIDERS = frozenset({"metar", "isd_lite"})
MODEL_PROVIDERS = frozenset({"metno_forecast", "dwd_mosmix"})


def source_kind(source):
    if source in OBSERVATION_PROVIDERS:
        return "OBSERVATION"
    if source in MODEL_PROVIDERS:
        return "MODEL"
    return "UNKNOWN"


def parse_hourly(text, station_id, *, ingest_time, snapshot_sha256):
    """Preserve missing/source values; absent hours are never generated.

    This historical parser always sets availability UNKNOWN. HTTP Last-Modified,
    observation time and ingestion now cannot prove a past publication time.
    """
    ingest = utc(ingest_time).isoformat()
    if len(snapshot_sha256) != 64 or any(c not in "0123456789abcdef" for c in snapshot_sha256):
        raise ValueError("Invalid snapshot SHA256")
    reader = csv.DictReader(io.StringIO(text), strict=True)
    fields = reader.fieldnames
    if not fields or len(fields) != len(set(fields)) or not {"year", "month", "day", "hour"} <= set(fields):
        raise ValueError("Invalid current hourly CSV structure")
    present_parameters = set(PARAMETERS) & set(fields)
    if not present_parameters:
        raise ValueError("No supported hourly parameters")
    for parameter in present_parameters:
        if parameter + "_source" not in fields:
            raise ValueError(f"Missing source provenance for {parameter}")
    result, seen = [], set()
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"Malformed CSV row {reader.line_num}")
        observation = datetime(*(int(row[k]) for k in ("year", "month", "day", "hour")), tzinfo=timezone.utc)
        if observation in seen:
            raise ValueError(f"Duplicate source hour: {observation}")
        seen.add(observation)
        values = {}
        for parameter, (name, unit, low, high) in PARAMETERS.items():
            raw_value = row.get(parameter, "")
            provider = row.get(parameter + "_source", "")
            value = None if raw_value.strip() == "" else float(raw_value)
            quality = "UNSUPPORTED" if parameter not in fields else "MISSING" if value is None else "VALID"
            if value is not None and (not math.isfinite(value) or not low <= value <= high):
                quality = "INVALID"
            if value is not None and parameter in ("coco", "cldc") and not value.is_integer():
                quality = "INVALID"
            values[parameter] = dict(parameter=parameter, name=name, unit=unit, value=value,
                raw_value=raw_value, provider=provider or None, value_kind=source_kind(provider), quality=quality)
        result.append(dict(station_id=station_id, observation_time=observation.isoformat(), target_time=None,
            source_available_time=None, availability_basis="UNKNOWN", ingest_time=ingest,
            timezone="UTC", source="Meteostat", source_version="sha256:" + snapshot_sha256,
            evidence_sha256=snapshot_sha256, parser_version=PARSER_VERSION,
            resolution_seconds=3600, interval_semantics="SOURCE_HOUR_LABEL", values=values))
    return result
