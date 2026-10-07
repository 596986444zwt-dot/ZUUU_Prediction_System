"""Causal contracts, numerical feature formulas, mutation and no-overwrite tests."""
import ast
import copy
import json
import math
import sqlite3
from pathlib import Path
import pytest
from src.features.contracts import ROOT,CONTRACT,utc
from src.features.registry import REGISTRY,DEFERRED
from src.features.ecmwf_temperature import compute
from src.features.forecast_revision import comparison_run
from src.features.data import fingerprints,load
from src.features.build import assemble
from src.features.schema import populate,semantic_hash,read
from src.builders.phase7_feature_v1_builder import prepare,build
from src.audit.phase7_feature_v1_audit import audit_connection

@pytest.fixture(scope='module')
def real():return prepare()

@pytest.fixture(scope='module')
def database(real):
    data,source,before=real;c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;populate(c,data)
    yield c
    c.close()

@pytest.fixture(scope='module')
def audited(real,database):return audit_connection(database,real[1])

def definition(name):return next(r for r in REGISTRY if r['feature_name']==name)

def curve():
    rows=[]
    for h in range(24):
        rows.append(dict(temperature_2m_c=20-abs(h-14),dew_point_2m_c=3.,relative_humidity_2m_pct=50.,cloud_cover_pct=10.,cloud_cover_low_pct=5.,cloud_cover_mid_pct=6.,cloud_cover_high_pct=7.,wind_speed_10m_kmh=36.,wind_gusts_10m_kmh=72.,wind_direction_10m_deg=359.,surface_pressure_hpa=950.+h,pressure_msl_hpa=1010.,shortwave_radiation_wm2=100.,precipitation_mm=0.,rain_mm=0.,cape_jkg=200.))
    return rows

def test_01_source_sha_before_after(real):assert fingerprints()==real[2]
def test_02_source_integrity(real):assert all(c['integrity_check']==['ok'] and c['foreign_key_check']==0 for c in json.loads(real[0]['manifest'][0]['source_guardian_json']).values())
def test_03_semantic_hash(real,database):assert semantic_hash(read(database),CONTRACT)==real[0]['manifest'][0]['semantic_sha256']
@pytest.mark.parametrize('h,n',[('T1',729),('T2',728)])
def test_04_05_universe(real,h,n):assert sum(s['horizon']==h for s in real[0]['feature_sample'])==n
def test_06_known_gap(real):assert not any(s['sample_id']=='2025-08-07/T2' for s in real[0]['feature_sample'])
def test_07_target_isolated(database):assert 'label_tmax_c' not in [r[1] for r in database.execute('PRAGMA table_info(training_feature_view)')]
def test_08_no_observation_feature(real):assert all(r['source'] in ('ECMWF','SOLAR','CALENDAR','REVISION','PHASE6') for r in real[0]['feature_registry'])
def test_09_no_future_label(audited):assert audited['future_label_count']==audited['same_day_unsettled_count']==0
def test_10_no_future_run(audited):assert audited['future_ecmwf_run_usage_count']==0
def test_11_no_splice(audited):assert audited['cross_run_splice_count']==0
def test_12_no_intraday(audited):assert audited['unproven_intraday_zuuu_usage_count']==0
def test_13_no_meteostat(audited):assert audited['meteostat_training_usage_count']==0
def test_14_availability(audited):assert audited['availability_leakage_count']==0
def test_15_lineage(audited):assert audited['lineage_complete']
def test_16_units(real):assert all(r['unit'] for r in real[0]['feature_registry'])
def test_17_missing_zero_distinct():
    rows=curve();d=definition('ecmwf_precip_total_mm');assert compute(d,rows)==(0.,None)
    rows[0]['precipitation_mm']=None;assert compute(d,rows)==(None,'MISSING_SOURCE_INPUT')
def test_18_no_partial_mean():
    rows=curve();rows[4]['relative_humidity_2m_pct']=None
    assert compute(definition('ecmwf_rh_mean_pct'),rows)[0] is None
@pytest.mark.parametrize('name,expected',[('ecmwf_tmax_c',20),('ecmwf_tmin_c',6),('ecmwf_temp_range_c',14),('ecmwf_tmax_hour_bjt',14),('ecmwf_tmin_hour_bjt',0),('ecmwf_temp_14_bjt_c',20),('ecmwf_warming_06_12_c',6),('ecmwf_max_hourly_warming_c',1),('ecmwf_max_hourly_cooling_c',-1),('ecmwf_strongest_warming_hour_bjt',1),('ecmwf_strongest_cooling_hour_bjt',15),('ecmwf_peak_sharpness_c',1),('ecmwf_pre_peak_slope_c_per_h',1),('ecmwf_post_peak_slope_c_per_h',-1),('ecmwf_hours_within_05c_of_max',1),('ecmwf_hours_within_10c_of_max',3)])
def test_19_22_curve_formulas(name,expected):assert compute(definition(name),curve())[0]==pytest.approx(expected)
def test_23_dewpoint():assert compute(definition('ecmwf_dewpoint_at_tmax_c'),curve())[0]==3
def test_24_native_rh_not_derived():assert compute(definition('ecmwf_rh_mean_pct'),curve())[0]==50 and any(r['feature_name']=='derived_rh' and r['status']=='RESEARCH_ONLY' for r in DEFERRED)
def test_25_cloud():assert compute(definition('ecmwf_cloud_clear_hour_count'),curve())[0]==24
def test_26_radiation():assert compute(definition('ecmwf_shortwave_daily_energy'),curve())[0]==pytest.approx(8.64)
def test_27_wind_conversion():assert compute(definition('ecmwf_wind_mean_ms'),curve())[0]==10
def test_28_direction_circle():
    rows=curve();a=compute(definition('ecmwf_wind_direction_at_tmax_sin'),rows)[0];rows[14]['wind_direction_10m_deg']=1;b=compute(definition('ecmwf_wind_direction_at_tmax_sin'),rows)[0]
    assert abs(a-b)<.04
def test_29_pressure():assert compute(definition('ecmwf_pressure_morning_afternoon_change_hpa'),curve())[0]==6
def test_30_precip_cape():assert compute(definition('ecmwf_cape_max_jkg'),curve())[0]==200
@pytest.mark.parametrize('slot',['prev_run','6h','12h','24h'])
def test_31_35_revision_past(real,slot):
    s=real[1]['samples'][200];r=comparison_run(s,real[1]['runs'],slot)
    if r:assert utc(r['run_time_utc'])<utc(s['selected_ecmwf_run_time_utc']) and utc(r['source_available_time_utc'])<=utc(s['issue_time_utc'])
def test_36_bias_frozen(real):
    for v in real[0]['feature_value']:
        if v['feature_name']=='hist_bias_30d_c':
            day,h=v['sample_id'].split('/');assert v['value']==real[1]['states'][(day,h,'M3')]['bias_value']
def test_37_D2_rule(audited):assert audited['label_eligibility_violation_count']==0
def test_38_season(real):
    lookup={12:0,1:0,2:0,3:1,4:1,5:1,6:2,7:2,8:2,9:3,10:3,11:3}
    for v in real[0]['feature_value']:
        if v['feature_name']=='calendar_season_code':assert v['value']==lookup[int(v['sample_id'][5:7])]
def test_39_calendar_circle(real):
    values={(v['sample_id'],v['feature_name']):v['value'] for v in real[0]['feature_value']}
    for s in real[0]['feature_sample']:assert values[(s['sample_id'],'calendar_doy_sin')]**2+values[(s['sample_id'],'calendar_doy_cos')]**2==pytest.approx(1)
def test_40_solar_coordinates(real):assert all((b['solar_latitude'],b['solar_longitude'])==(30.576,103.95) for b in real[0]['feature_source_lineage'] if b['source']=='SOLAR')
def test_41_42_no_training_or_probability():
    for p in (ROOT/'src/features').glob('*.py'):
        tree=ast.parse(p.read_text(encoding='utf-8'))
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('fit','fit_predict','predict_proba') for n in ast.walk(tree))
        assert not any(isinstance(n,(ast.Import,ast.ImportFrom)) and any(w in ast.unparse(n).lower() for w in ('sklearn','xgboost','lightgbm','catboost','tensorflow','torch')) for n in ast.walk(tree))
def test_43_registry(audited):assert audited['registry_complete']
def test_44_lineage(audited):assert audited['lineage_complete']
def test_45_deterministic_assembly(real):assert semantic_hash(assemble(real[1]),CONTRACT)==real[0]['manifest'][0]['semantic_sha256']
def test_46_semantic_roundtrip(real,database):assert semantic_hash(read(database),CONTRACT)==semantic_hash(real[0],CONTRACT)
def test_47_sources_unchanged(real):assert fingerprints()==real[2]
def test_48_t0_blocker(audited):assert audited['intraday_blocker_preserved']
def test_49_no_overwrite(tmp_path):
    p=tmp_path/'present.db';p.write_bytes(b'keep')
    with pytest.raises(ValueError,match='not be overwritten'):build(p)
    assert p.read_bytes()==b'keep'
def test_50_label_mutation_invariance(real):
    source=dict(real[1]);source['labels']={k:999. for k in source['labels']}
    new=assemble(source)
    assert new['feature_value']==real[0]['feature_value'] and new['feature_source_lineage']==real[0]['feature_source_lineage']
def test_51_freeze_seal(database):
    with pytest.raises(sqlite3.IntegrityError):database.execute("UPDATE phase7_feature_value SET value=0")
def test_52_numerical_leakage_rejected(real):
    bad=copy.deepcopy(real[0]);bad['feature_value'][0]['feature_available_time']='2099-01-01T00:00:00+00:00';bad['manifest'][0]['semantic_sha256']=semantic_hash(bad,CONTRACT)
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;populate(c,bad)
    with pytest.raises(ValueError,match='Availability|availability|gate failed'):audit_connection(c,real[1])
    c.close()
def test_53_peak_endpoint_missing():
    rows=curve()
    for h in range(24):rows[h]['temperature_2m_c']=h
    assert compute(definition('ecmwf_peak_sharpness_c'),rows)==(None,'PEAK_AT_ENDPOINT')
