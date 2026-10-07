"""NOAA fractional-year approximation; no weather inputs or network calls.

Reference: https://gml.noaa.gov/grad/solcalc/solareqns.PDF
Elevation is geometric; rise/set use a fixed 90.833 degree zenith, no measured
pressure, temperature, terrain or elevation-dependent horizon adjustment.
"""
import calendar
import math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.auxiliary.contracts import utc, TIMEZONE, LATITUDE, LONGITUDE, COORDINATE_VERSION

ALGORITHM_VERSION = "NOAA_FRACTIONAL_YEAR_ZUUU_V1"
BJT = ZoneInfo(TIMEZONE)


def _terms(local):
    days = 366 if calendar.isleap(local.year) else 365
    hour = local.hour + local.minute / 60 + local.second / 3600 + local.microsecond / 3.6e9
    gamma = 2 * math.pi / days * (local.timetuple().tm_yday - 1 + (hour - 12) / 24)
    c, s = math.cos, math.sin
    eq = 229.18 * (.000075 + .001868*c(gamma) - .032077*s(gamma)
                   - .014615*c(2*gamma) - .040849*s(2*gamma))
    decl = (.006918 - .399912*c(gamma) + .070257*s(gamma) - .006758*c(2*gamma)
            + .000907*s(2*gamma) - .002697*c(3*gamma) + .00148*s(3*gamma))
    return eq, decl


def features(target_time, *, ingest_time):
    target = utc(target_time)
    local = target.astimezone(BJT)
    hour = local.hour + local.minute/60 + local.second/3600 + local.microsecond/3.6e9
    doy = local.timetuple().tm_yday
    days = 366 if calendar.isleap(local.year) else 365
    eq, decl = _terms(local)
    lat = math.radians(LATITUDE)
    true_solar_minutes = (hour*60 + eq + 4*LONGITUDE - 480) % 1440
    ha = math.radians(true_solar_minutes/4 - 180)
    sin_elevation = math.sin(lat)*math.sin(decl) + math.cos(lat)*math.cos(decl)*math.cos(ha)
    elevation = math.degrees(math.asin(max(-1, min(1, sin_elevation))))
    azimuth = (math.degrees(math.atan2(math.sin(ha), math.cos(ha)*math.sin(lat)
                                     - math.tan(decl)*math.cos(lat))) + 180) % 360
    midnight = datetime.combine(local.date(), time(), tzinfo=BJT)
    # Rise/set share noon terms, making daily duration identical for all 24 rows.
    eq_noon, decl_noon = _terms(midnight + timedelta(hours=12))
    cosine = (math.cos(math.radians(90.833)) / (math.cos(lat)*math.cos(decl_noon))
              - math.tan(lat)*math.tan(decl_noon))
    if not -1 <= cosine <= 1:
        raise ValueError("No rise/set at pinned coordinates; outside V1 contract")
    angle = math.degrees(math.acos(cosine))
    sunrise = midnight + timedelta(minutes=720 - 4*(LONGITUDE + angle) - eq_noon + 480)
    sunset = midnight + timedelta(minutes=720 - 4*(LONGITUDE - angle) - eq_noon + 480)
    return dict(target_time=target.isoformat(), observation_time=None,
        source_available_time=None, availability_basis="DETERMINISTIC_NOT_APPLICABLE",
        ingest_time=utc(ingest_time).isoformat(), timezone=TIMEZONE, source="NOAA_SOLAR_EQUATIONS",
        source_version=ALGORITHM_VERSION, latitude=LATITUDE, longitude=LONGITUDE,
        coordinate_version=COORDINATE_VERSION, business_date_bjt=local.date().isoformat(),
        hour_bjt=local.hour, day_of_year=doy, month=local.month,
        season=("winter" if local.month in (12,1,2) else "spring" if local.month in (3,4,5)
                else "summer" if local.month in (6,7,8) else "autumn"),
        sin_hour=math.sin(2*math.pi*hour/24), cos_hour=math.cos(2*math.pi*hour/24),
        sin_doy=math.sin(2*math.pi*(doy-1)/days), cos_doy=math.cos(2*math.pi*(doy-1)/days),
        sunrise=sunrise.isoformat(), sunset=sunset.isoformat(), solar_elevation=elevation,
        solar_azimuth=azimuth, daylight_duration=(sunset-sunrise).total_seconds()/60,
        minutes_since_sunrise=(local-sunrise).total_seconds()/60,
        minutes_to_sunset=(sunset-local).total_seconds()/60)
