from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from src.data_v1.contracts import require
from src.data_v1.source_io import sha256_file, sidecar_state

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT/'docs/intraday'
BJT = ZoneInfo('Asia/Shanghai')
HOURS = tuple(range(6,18))  # Candidate only; NOT a frozen production grid.
SOURCE_HASHES = {
    'zuuu_prediction.db':'2e0149050ed11fe2d750b6f4798f7a51b313bf8f59bb671da5a076daeb124367',
    'phase3_auxiliary_v1.db':'ec8defe1778a66ed9ea20dc9e3825579de685c1818862a3eef113bba9a624f22',
    'phase4_data_v1.db':'9d1901aa0f6c9c3106450cb9d41d885797745f087a80c497cff082f67094a892',
    'phase5_ecmwf_raw_baseline_v1.db':'851122d90d2202f6259d26d6ca08e35960ef295ad624a9464d020c38e6c507f5',
}
LABEL_ONLY = frozenset({'final_target_tmax_c','target_tmax_c','daily_tmax_c','first_tmax_time',
    'first_tmax_time_bjt','last_tmax_time','last_tmax_time_bjt','occurrence_count','tmax_occurrence_count',
    'hours_until_first_tmax','is_pre_peak','peak_relation','final_daily_statistics','final_daily_mean',
    'final_daily_min','final_observation_count','target_lineage','tmax_silver_ids','tmax_bronze_raw_ids'})
OBSERVATION_CANDIDATES = ('latest_observed_temperature','observed_tmax_so_far','observed_tmin_so_far',
    'trend_1h','trend_2h','trend_3h','observation_count_so_far')
WEATHER = ('temperature_2m_c','dew_point_2m_c','relative_humidity_2m_pct','surface_pressure_hpa',
    'pressure_msl_hpa','cloud_cover_pct','cloud_cover_low_pct','cloud_cover_mid_pct','cloud_cover_high_pct',
    'wind_speed_10m_kmh','wind_direction_10m_deg','wind_gusts_10m_kmh','shortwave_radiation_wm2',
    'direct_radiation_wm2','diffuse_radiation_wm2','precipitation_mm','rain_mm','cape_jkg')
FEATURE_ALLOWED = frozenset({'issue_time','latest_legal_run','ecmwf_target_day_trajectory','ecmwf_future_trajectory',
    'ecmwf_raw_daily_tmax','ecmwf_remaining_day_tmax','solar_elevation','solar_azimuth','daylight_duration',
    'hour_bjt','day_of_year','month','season','sin_hour','cos_hour','sin_doy','cos_doy','sunrise','sunset',
    'minutes_since_sunrise','minutes_to_sunset', *WEATHER})

def utc(value):
    dt = datetime.fromisoformat(value) if isinstance(value,str) else value
    require(dt.tzinfo is not None and dt.utcoffset() is not None, 'Naive timestamp prohibited')
    return dt.astimezone(timezone.utc)

def issue_time(day, hour, minute=0):
    return datetime.combine(datetime.fromisoformat(str(day)).date(),time(hour,minute),tzinfo=BJT).astimezone(timezone.utc)

def peak_relation(issue, first_peak):
    issue, peak = utc(issue), utc(first_peak)
    return 'PRE_PEAK' if issue<peak else 'EQUAL_PEAK' if issue==peak else 'POST_PEAK'

def validate_feature_candidates(names):
    require(set(names)<=FEATURE_ALLOWED, f'Label/blocked/unknown feature candidates: {set(names)-FEATURE_ALLOWED}')

def fingerprints():
    result = {}
    for name, expected in SOURCE_HASHES.items():
        path = ROOT/'database'/name
        actual = sha256_file(path)
        require(actual==expected, f'STOP DISCOVERY: SHA mismatch {name}: expected {expected}; actual {actual}')
        # JSON-native representation allows exact comparison after report reload.
        result[name] = {'sha256':actual,'size_bytes':path.stat().st_size,
            'sidecars':{suffix:list(state) for suffix,state in sidecar_state(path).items()}}
    return result

def quantile(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered)-1)*p
    lo = int(index)
    return ordered[lo]+(ordered[min(lo+1,len(ordered)-1)]-ordered[lo])*(index-lo)

def distribution(values):
    return {'n':len(values), **{key:quantile(values,p) for key,p in
        [('min',0),('P10',.1),('P25',.25),('median',.5),('P50',.5),('P75',.75),('P90',.9),('P95',.95),('P99',.99),('max',1)]}}
