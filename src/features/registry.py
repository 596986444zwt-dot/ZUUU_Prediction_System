"""Definitions precede construction and never depend on target outcomes."""
import json
from .contracts import VERSION

REGISTRY=[]
def add(name,family,formula,fields,unit='C',operation='',hours=None,category='B',source='ECMWF',status='ACCEPTED'):
    table={'ECMWF':'phase4_data_v1_ecmwf_hourly','SOLAR':'aux_v1_solar_time via phase4_data_v1_solar','CALENDAR':'target date / frozen issue and run times','REVISION':'ecmwf_archive_v1 + canonical ecmwf_hourly_forecasts','PHASE6':'phase6_mos_bias_state + phase6_mos_prediction'}[source]
    REGISTRY.append(dict(feature_name=name,family=family,description=formula,formula=formula,source=source,source_table=table,
        source_fields=json.dumps(fields),unit=unit,horizon_applicability='T1,T2; historical T0 definitions only',
        availability_rule='max(input lawful availability); <=issue; '+('D+2 causal eligibility' if source=='PHASE6' else 'deterministic computability' if category=='A' else 'frozen forecast dissemination semantics'),
        missing_rule='NULL + explicit reason when any required input absent/nonfinite or minimum history fails',leakage_risk='past-only labels' if category=='C' else 'past-run only' if source=='REVISION' else 'no target/observations',
        eligibility_category={'A':'SAFE_DETERMINISTIC','B':'SAFE_ECMWF_FORECAST','C':'SAFE_PAST_ONLY'}[category],status=status,version=VERSION,
        operation=operation,hours_json=json.dumps(hours)))

T=['temperature_2m_c']
for n,op,unit in [('tmax','max','C'),('tmin','min','C'),('mean_temp','mean','C'),('temp_range','range','C'),('tmax_hour_bjt','argmax','hour BJT'),('tmin_hour_bjt','argmin','hour BJT')]:
    add('ecmwf_'+n+('_c' if unit=='C' else ''),'Temperature Curve',op+' of all 24 temperature hours, earliest tie',T,unit,op)
for period,hs in [('morning',list(range(6,12))),('afternoon',list(range(12,18))),('evening',list(range(18,24)))]:
    for op in (['mean','max'] if period!='evening' else ['mean']):add('ecmwf_temp_'+period+'_'+op+'_c','Temperature Curve',op+' T over BJT hours '+str(hs),T,operation=op,hours=hs)
for a,b in [(6,12),(9,15),(12,15)]:add(f'ecmwf_warming_{a:02d}_{b:02d}_c','Temperature Curve',f'T[{b}]-T[{a}]',T,operation='delta',hours=[a,b])
for h in [6,9,12,14,15,16,18,21]:add(f'ecmwf_temp_{h:02d}_bjt_c','Temperature Curve',f'T[{h}]',T,operation='at',hours=[h])
shape={'morning_to_afternoon_delta_c':'mean(T[12:18])-mean(T[6:12])','max_hourly_warming_c':'max(T[h]-T[h-1]), h=1..23; signed','max_hourly_cooling_c':'min(T[h]-T[h-1]), h=1..23; signed',
 'strongest_warming_hour_bjt':'earliest ending hour attaining maximum adjacent delta','strongest_cooling_hour_bjt':'earliest ending hour attaining minimum adjacent delta',
 'peak_sharpness_c':'T[peak] - mean(T[peak-1],T[peak+1]); NULL at endpoint peak',
 'hours_within_05c_of_max':'count(T>=Tmax-0.5)','hours_within_10c_of_max':'count(T>=Tmax-1.0)',
 'pre_peak_slope_c_per_h':'(T[peak]-T[6])/(peak-6); NULL if peak<=6','post_peak_slope_c_per_h':'(T[21]-T[peak])/(21-peak); NULL if peak>=21',
 'morning_to_peak_warming_c':'Tmax-T[6]','peak_to_evening_cooling_c':'Tmax-T[21]'}
for n,f in shape.items():
    add('ecmwf_'+n,'Temperature Curve',f,T,
        unit='hour BJT' if n.endswith('hour_bjt') else 'count' if n.startswith('hours_') else 'C/h' if 'slope' in n else 'C',
        operation=n,status='CONDITIONAL' if n in ('peak_sharpness_c','pre_peak_slope_c_per_h','post_peak_slope_c_per_h') else 'ACCEPTED')

def weather(name,family,field,op='mean',hours=None,unit='C',formula=None):
    fields=[field]+(['temperature_2m_c'] if op in ('at_peak','sin_peak','cos_peak') else [])
    description=formula or (field+'[earliest argmax(T[0:24])]' if op=='at_peak' else op+' of '+field+(' over '+str(hours) if hours else ' over all 24 target-hour labels'))
    if op in ('at_peak','sin_peak','cos_peak'):description+='; peak determined from complete 24h temperature curve; require weather value at that peak hour'
    add(name,family,description,fields,unit,op,hours,status='CONDITIONAL')
for n,field,op,hs,u in [
 ('ecmwf_dewpoint_mean_c','dew_point_2m_c','mean',None,'C'),('ecmwf_dewpoint_at_tmax_c','dew_point_2m_c','at_peak',None,'C'),
 ('ecmwf_rh_mean_pct','relative_humidity_2m_pct','mean',None,'%'),('ecmwf_rh_at_tmax_pct','relative_humidity_2m_pct','at_peak',None,'%'),
 ('ecmwf_rh_morning_mean_pct','relative_humidity_2m_pct','mean',list(range(6,12)),'%'),('ecmwf_rh_afternoon_mean_pct','relative_humidity_2m_pct','mean',list(range(12,18)),'%')]:weather(n,'Moisture',field,op,hs,u)
for n,formula,fields in [('spread_min_c','min(T-dewpoint) across 24','temperature_2m_c,dew_point_2m_c'),('spread_mean_c','mean(T-dewpoint) across 24','temperature_2m_c,dew_point_2m_c'),('rh_change_pct','mean(RH[12:18])-mean(RH[6:12])','relative_humidity_2m_pct')]:
    add('ecmwf_'+n,'Moisture',formula,fields.split(','),unit='%' if 'rh_' in n else 'C',operation=n,status='CONDITIONAL')
for name,op,hs in [('mean','mean',None),('morning_mean','mean',list(range(6,12))),('afternoon_mean','mean',list(range(12,18))),('at_tmax','at_peak',None),('max','max',None),('clear_hour_count','clear',None)]:weather('ecmwf_cloud_'+name+('_pct' if op!='clear' else ''),'Cloud','cloud_cover_pct',op,hs,'%' if op!='clear' else 'count',formula='count(cloud<=10%) across 24' if op=='clear' else None)
for layer in ['low','mid','high']:weather('ecmwf_cloud_'+layer+'_mean_pct','Cloud','cloud_cover_'+layer+'_pct',unit='%')
for name,op,hs,u in [('daily_energy','energy',None,'MJ/m2'),('morning_energy','energy',list(range(6,12)),'MJ/m2'),('afternoon_energy','energy',list(range(12,18)),'MJ/m2'),('at_tmax','at_peak',None,'W/m2'),('max','max',None,'W/m2')]:weather('ecmwf_shortwave_'+name,'Radiation / Solar','shortwave_radiation_wm2',op,hs,u,formula='sum(hourly mean W/m2)*3600/1e6 over '+str(hs or list(range(24))) if op=='energy' else None)
for n,field,op,u in [('wind_mean_ms','wind_speed_10m_kmh','mean','m/s'),('wind_max_ms','wind_speed_10m_kmh','max','m/s'),('wind_at_tmax_ms','wind_speed_10m_kmh','at_peak','m/s'),('gust_max_ms','wind_gusts_10m_kmh','max','m/s'),('wind_direction_at_tmax_sin','wind_direction_10m_deg','sin_peak','1'),('wind_direction_at_tmax_cos','wind_direction_10m_deg','cos_peak','1')]:weather('ecmwf_'+n,'Wind',field,op,None,u,formula=op+' of '+field+('; km/h /3.6' if u=='m/s' else '; radians=degrees*pi/180' if '_peak' in op else ''))
for n,field,op in [('surface_pressure_mean_hpa','surface_pressure_hpa','mean'),('surface_pressure_at_tmax_hpa','surface_pressure_hpa','at_peak'),('msl_pressure_mean_hpa','pressure_msl_hpa','mean')]:weather('ecmwf_'+n,'Pressure',field,op,unit='hPa')
weather('ecmwf_pressure_morning_afternoon_change_hpa','Pressure','surface_pressure_hpa','period_delta',unit='hPa',formula='mean(P[12:18])-mean(P[6:12])')
for n,field,op,u in [('precip_total_mm','precipitation_mm','sum','mm'),('precip_occurrence','precipitation_mm','any_positive','0/1'),('precip_hour_count','precipitation_mm','positive_count','count'),('precip_hour_max_mm','precipitation_mm','max','mm'),('rain_total_mm','rain_mm','sum','mm'),('cape_max_jkg','cape_jkg','max','J/kg'),('cape_mean_jkg','cape_jkg','mean','J/kg')]:weather('ecmwf_'+n,'Precipitation / Instability',field,op,unit=u)
for slot in ['prev_run','6h','12h','24h']:add('ecmwf_tmax_revision_'+slot+'_c','Forecast Revision','current target-day Tmax - '+slot+' run target-day Tmax',T,source='REVISION',operation=slot,status='CONDITIONAL')
next(r for r in REGISTRY if r['feature_name']=='ecmwf_tmax_revision_24h_c')['horizon_applicability']='T1; T2 only if comparison covers full target day (normally impossible with frozen 72h lead limit); retain NULL mask'
for name,f,u in [('curve_revision_prev_mean_c','mean(current[h]-previous[h]) across aligned 24 hours','C'),('curve_revision_prev_max_abs_c','max(abs(current[h]-previous[h]))','C'),('peak_hour_revision_prev_h','current earliest peak hour - previous earliest peak hour; signed daily hour difference','hour'),('temp_14_revision_prev_c','current[14]-previous[14]','C')]:add('ecmwf_'+name,'Forecast Revision',f,T,u,source='REVISION',operation=name,status='CONDITIONAL')
for n,f in [('run_age_hours','issue-run'),('target_start_lead_hours','target day 00 BJT-run'),('target_peak_lead_hours','predicted peak target hour-run')]:add(n,'Lead / Run Age',f,['issue_time','run_time','target_time'],unit='hour',category='A',source='CALENDAR',operation=n)
for n,m,f in [('hist_expanding_bias_c','M1','saved M1 bias'),('hist_bias_7d_c','M2','saved M2 seven calendar-day bias with frozen fallback'),('hist_bias_30d_c','M3','saved M3 thirty calendar-day bias with frozen fallback'),('hist_season_bias_c','M4','saved same-season M4 with frozen fallback'),('hist_horizon_bias_c','M5','saved M5 horizon bias'),('m6_statistical_adjustment_c','M6','saved fixed (M1+M3+M4)/3 bias'),('m6_corrected_temperature_c','M6','saved Phase6 M6 continuous forecast; benchmark, not champion')]:add(n,'Historical Bias',f,['bias_value','training_dates_json'] if n!='m6_corrected_temperature_c' else ['mos_continuous_tmax','training_dates_json'],'C',source='PHASE6',category='C',operation=m,status='CONDITIONAL')
for n,f,u in [('calendar_month','target date month','month'),('calendar_season_code','DJF=0,MAM=1,JJA=2,SON=3','category code'),('calendar_day_of_year','target date ordinal day','day'),('calendar_doy_sin','sin(2*pi*(doy-1)/days_in_target_year)','1'),('calendar_doy_cos','cos(2*pi*(doy-1)/days_in_target_year)','1')]:add(n,'Calendar / Season',f,['target_business_date'],u,source='CALENDAR',category='A',operation=n)
for n,f,hs,u in [('solar_day_length_min','frozen NOAA day length',None,'minute'),('solar_max_elevation_deg','max hourly solar elevation',None,'degree'),('solar_elevation_09_bjt_deg','frozen elevation at 09',[9],'degree'),('solar_elevation_15_bjt_deg','frozen elevation at 15',[15],'degree'),('solar_sunrise_hour_bjt','sunrise local fractional hour',None,'hour BJT')]:add(n,'Radiation / Solar',f,['solar_elevation','daylight_duration','sunrise','latitude','longitude'],u,source='SOLAR',category='A',operation=n,hours=hs)

DEFERRED=[]
for name,family,category,status,reason in [
 ('derived_rh','Moisture','G','RESEARCH_ONLY','native RH exists; no redundant derivation'),
 ('radiation_deficit','Radiation / Solar','G','RESEARCH_ONLY','solar elevation is not clear-sky irradiance; no arbitrary potential formula'),
 ('pressure_24h_tendency','Pressure','G','RESEARCH_ONLY','defer same-run preceding-day coverage contract'),
 ('historical_similarity','Similarity','G','RESEARCH_ONLY','DEFER_TO_LATER_RESEARCH; no complex similarity predictor'),
 ('meteostat_weather','External Reference','E','BLOCKED','historical publication availability unproved'),
 ('weather_underground','External Reference','F','REJECTED','no frozen source'),
 ('current_temp','T0 Intraday Deferred','D','BLOCKED','historical arrival unproved'),('today_max_min','T0 Intraday Deferred','D','BLOCKED','historical arrival unproved'),
 ('temp_change_1h_2h_3h','T0 Intraday Deferred','D','BLOCKED','historical arrival unproved'),('warming_cooling_rate','T0 Intraday Deferred','D','BLOCKED','historical arrival unproved'),
 ('dewpoint_wind_cloud_pressure_changes','T0 Intraday Deferred','D','BLOCKED','historical arrival unproved'),('time_since_current_max','T0 Intraday Deferred','D','BLOCKED','historical arrival unproved'),
 ('remaining_ecmwf_trajectory','T0 Intraday Deferred','G','RESEARCH_ONLY','future production issue contract deferred'),('remaining_warming_potential','T0 Intraday Deferred','D','BLOCKED','needs current observation arrival')]:
    DEFERRED.append(dict(feature_name=name,family=family,eligibility_category=category,status=status,reason=reason,version=VERSION))
