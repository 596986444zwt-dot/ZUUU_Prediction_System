import calendar
import math
from datetime import date
from .contracts import utc, BJT

def compute(definition,sample,hours,solar):
    name=definition['feature_name'];day=date.fromisoformat(sample['business_date_bjt']);doy=day.timetuple().tm_yday
    peak=max(range(24),key=lambda h:hours[h]['temperature_2m_c'])
    run=utc(sample['selected_ecmwf_run_time_utc'])
    season=next(i for i,months in enumerate(([12,1,2],[3,4,5],[6,7,8],[9,10,11])) if day.month in months)
    values={'calendar_month':day.month,'calendar_season_code':season,'calendar_day_of_year':doy,
        'calendar_doy_sin':math.sin(2*math.pi*(doy-1)/(366 if calendar.isleap(day.year) else 365)),
        'calendar_doy_cos':math.cos(2*math.pi*(doy-1)/(366 if calendar.isleap(day.year) else 365)),
        'run_age_hours':(utc(sample['issue_time_utc'])-run).total_seconds()/3600,
        'target_start_lead_hours':hours[0]['lead_hours'],'target_peak_lead_hours':hours[peak]['lead_hours'],
        'solar_day_length_min':solar[0]['daylight_duration'],'solar_max_elevation_deg':max(r['solar_elevation'] for r in solar),
        'solar_elevation_09_bjt_deg':solar[9]['solar_elevation'],'solar_elevation_15_bjt_deg':solar[15]['solar_elevation']}
    sunrise=utc(solar[0]['sunrise']).astimezone(BJT)
    values['solar_sunrise_hour_bjt']=sunrise.hour+sunrise.minute/60+sunrise.second/3600+sunrise.microsecond/3.6e9
    return values[name],None
