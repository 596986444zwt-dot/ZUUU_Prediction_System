"""Causal daily-label review tests; real OOS metrics never use synthetic results."""
import copy
import json
import math
import sqlite3
from datetime import datetime,timedelta
import pytest
from src.data_v1.contracts import BJT,utc
from src.mos_review.contracts import RULE,LAG_HOURS,CONTRACT,SOURCES,OUTPUT
from src.mos_review.data import fingerprints,preserved_fingerprints
from src.mos_review.eligibility import eligibility_time,check_label,latest_eligible_date
from src.mos_review.walk_forward import evaluate,select_states
from src.mos_review.storage import populate,read,semantic_hash
from src.mos_review.assessment import summarize,assess
from src.builders.phase6_statistical_mos_v1_review_builder import prepare,build
from src.audit.phase6_statistical_mos_v1_review_audit import audit_connection,reference_metrics
from tests.test_phase6_statistical_mos_v1 import synthetic as old_synthetic

def synthetic(n=80,horizon='T0'):
    rows = old_synthetic(n,horizon)
    for r in rows:
        del r['settled_at_utc']; del r['settlement_verified']
        r.update(label_business_date=r['business_date_bjt'],label_eligibility_time_bjt=eligibility_time(r['business_date_bjt']).isoformat(),
            eligibility_rule=RULE,eligibility_lag=LAG_HOURS,label_eligibility_semantics='CAUSAL_DAILY_LABEL_ELIGIBILITY_NOT_OBSERVED_RECEIPT')
    return rows

@pytest.fixture(scope='module')
def real():
    return prepare()

def test_01_preserve_original_blocked(real):
    assert preserved_fingerprints() == json.loads(real['manifest'][0]['preserved_assets_json'])

def test_02_source_sha_before_after(real):
    assert fingerprints() == json.loads(real['manifest'][0]['source_sha_before_json'])

def test_03_integrity_and_foreign_keys(real):
    checks = json.loads(real['manifest'][0]['source_guardian_json'])
    assert all(v['integrity_check'] == ['ok'] and v['foreign_key_check'] == 0 for v in checks.values())

def test_04_semantics_and_manifests(real):
    checks = json.loads(real['manifest'][0]['source_guardian_json'])
    assert checks['phase4']['semantic']['DATA_V1'] == '4eea080168f102f30314b0b8a64123af533923fd7f4b7e7c6e9312b5582835dd'
    assert checks['phase5']['semantic']['BASELINE'] == '4f31fa80c704eed5100b1add4d37cebf120b1e56fe1c4e2ba65a140d9d8fd353'
    assert checks['production']['semantic']['TARGET_V1'] == 'b8548609e64d10b787fc09d9fddb28e20acd1808b165f47e62b574580cc67f3e'

@pytest.mark.parametrize('h,n',[('T0',729),('T1',729),('T2',728)])
def test_05_universe(real,h,n):
    assert sum(r['horizon'] == h for r in real['sample']) == n
    assert len(real['sample']) == 2186

def test_06_gap_preserved(real):
    assert not any(s['business_date_bjt'] == '2025-08-07' and s['horizon'] == 'T2' for s in real['sample'])
    gaps = json.loads(real['manifest'][0]['exclusion_json'])
    assert len(gaps) == 1 and gaps[0]['trajectory_valid_hours'] == 14

def test_07_benchmark_not_hardcoded(real):
    for row in json.loads(real['manifest'][0]['benchmark_json']):
        source = [s for s in real['sample'] if s['horizon'] == row['horizon']]
        refs = reference_metrics(source,'raw_ecmwf_tmax')
        assert all(row[k] == pytest.approx(v,abs=1e-12) for k,v in refs.items())

def test_08_24h_lag_after_natural_day_end():
    assert eligibility_time('2025-06-10').isoformat() == '2025-06-12T00:00:00+08:00'
    assert CONTRACT['eligibility_lag_hours_after_day_end'] == 24

def test_09_midnight_boundary():
    rows = synthetic()
    label,p = rows[0],copy.deepcopy(rows[4])
    boundary = eligibility_time(label['business_date_bjt'])
    p['issue_time_utc'] = (boundary-timedelta(seconds=1)).isoformat()
    assert not check_label(label,p)['is_label_eligible']
    p['issue_time_utc'] = boundary.isoformat()
    assert check_label(label,p)['is_label_eligible']
    assert utc(label['day_end_utc']) < utc(p['issue_time_utc'])

def test_10_day_end_not_immediate_availability():
    rows = synthetic()
    label,p = rows[0],copy.deepcopy(rows[4])
    p['issue_time_utc'] = (utc(label['day_end_utc'])+timedelta(hours=23,minutes=59)).isoformat()
    assert not check_label(label,p)['is_label_eligible']

@pytest.mark.parametrize('h,offset',[('T0',2),('T1',3),('T2',4)])
def test_11_real_horizon_causal_cutoff(real,h,offset):
    source = next(s for s in real['sample'] if s['business_date_bjt'] == '2025-06-10' and s['horizon'] == h)
    state = next(s for s in real['bias_state'] if s['target_business_date'] == '2025-06-10' and s['horizon'] == h and s['model'] == 'M1')
    end = (datetime(2025,6,10)-timedelta(days=offset)).date().isoformat()
    assert state['training_end'] == end
    used = json.loads(state['training_dates_json'])[-5:]
    lookup = {(r['business_date_bjt'],r['horizon']):r for r in real['sample']}
    assert len(used) == 5 and all(check_label(lookup[d,h],source)['is_label_eligible'] for d in used)

def test_12_required_audit_fields():
    rows = synthetic()
    row = check_label(rows[0],rows[10])
    assert set(row) == {'label_business_date','label_eligibility_time_bjt','prediction_issue_time','eligibility_rule','eligibility_lag','is_label_eligible'}
    assert row['is_label_eligible'] and row['eligibility_rule'] == RULE

def test_13_no_freeze_or_ingest_admission(real):
    keys = real['sample'][0]
    assert 'settled_at_utc' not in keys and 'settlement_verified' not in keys
    assert not any('freeze' in k or 'ingest' in k or 'receipt' in k for k in keys)
    assert all(r['label_eligibility_semantics'] == 'CAUSAL_DAILY_LABEL_ELIGIBILITY_NOT_OBSERVED_RECEIPT' for r in real['sample'])

def test_14_intraday_stays_blocked():
    assert 'HISTORICAL_ZUUU_INGEST_NOT_OBSERVED' in CONTRACT['Intraday']
    assert 'BLOCKED_FOR_STRICT_INTRADAY_REPLAY' in CONTRACT['Intraday']

@pytest.mark.parametrize('error',[-2.,2.])
def test_15_error_sign(error):
    rows = synthetic()
    for r in rows:
        r.update(raw_ecmwf_tmax=25+error,raw_error=error)
    p,_ = evaluate(rows)
    valid = [r for r in p if r['status'] == 'PREDICTED']
    assert valid and all(r['mos_continuous_tmax'] == 25 and r['mos_error'] == 0 for r in valid)

def test_16_expanding_past_only():
    rows = synthetic()
    bias,used,_,_ = select_states(rows[40],rows)['M1']
    assert bias == 1 and len(used) == 39 and used[-1] == rows[38]

def test_17_trailing7_calendar_window():
    rows = synthetic()
    _,used,path,_ = select_states(rows[40],rows)['M2']
    assert path == 'M2' and used == rows[32:39]

def test_18_trailing30_calendar_window():
    rows = synthetic()
    _,used,path,_ = select_states(rows[40],rows)['M3']
    assert path == 'M3' and used == rows[9:39]

def test_19_no_centered_or_global_future_fill():
    rows = synthetic()
    first = select_states(rows[40],rows)
    for r in rows[39:]:
        r['raw_error'] = 999999
    second = select_states(rows[40],rows)
    assert [first[m][0] for m in first] == [second[m][0] for m in second]

def test_20_current_target_not_in_own_prediction():
    rows = synthetic()
    p1,_ = evaluate(rows)
    rows[40].update(observed_tmax=-1000,raw_error=1026)
    p2,_ = evaluate(rows)
    assert [r['mos_continuous_tmax'] for r in p1 if r['target_business_date'] == rows[40]['business_date_bjt']] == [r['mos_continuous_tmax'] for r in p2 if r['target_business_date'] == rows[40]['business_date_bjt']]

def test_21_future_and_same_day_rejected():
    rows = synthetic()
    for r in rows[39:]:
        assert not check_label(r,rows[40])['is_label_eligible']

def test_22_season_group_and_fallback():
    rows = synthetic(180)
    _,used,path,_ = select_states(rows[120],rows)['M4']
    assert path == 'M4' and all(r['season'] == rows[120]['season'] and r['business_date_bjt'] < rows[120]['business_date_bjt'] for r in used)
    assert select_states(rows[60],rows)['M4'][2] == 'M4>M1'

def test_23_horizon_separation_and_M5_equivalence():
    rows = synthetic()
    other = synthetic(horizon='T1')
    for r in other:
        r['raw_error'] = -999
    states = select_states(rows[40],rows+other)
    assert states['M1'][0] == states['M5'][0] == 1

def test_24_cold_start_no_future_fill():
    rows = synthetic()
    assert select_states(rows[7],rows)['M1'][0] is None
    assert select_states(rows[8],rows)['M1'][0] == 1

def test_25_missing_day_trailing_fallback():
    rows = synthetic()
    rows = [r for i,r in enumerate(rows) if i != 35]
    target = next(r for r in rows if r['business_date_bjt'] == '2025-02-10')
    _,used,path,_ = select_states(target,rows)['M2']
    assert path == 'M2>M1' and len(used) > 7

def test_26_walk_forward_chronological_and_order_invariant():
    rows = synthetic()
    assert evaluate(rows) == evaluate(list(reversed(rows)))

def test_27_combined_fixed_equal_weights():
    rows = synthetic()
    for i,r in enumerate(rows):
        r['raw_error'] = float(i)
    states = select_states(rows[70],rows)
    assert states['M6'][0] == sum(states[m][0] for m in ('M1','M3','M4'))/3

def test_28_no_phase7_intraday_meteostat_inputs(real):
    prohibited = ['dew','wind','cloud','humidity','radiation','pressure','precipitation','solar','meteostat','revision','similarity','trajectory']
    assert not any(token in k.lower() for token in prohibited for k in real['sample'][0])

def test_29_run_lead_and_horizon_semantics(real):
    for r in real['sample']:
        assert utc(r['selected_ecmwf_run_time_utc']) <= utc(r['selected_ecmwf_source_available_time_utc']) <= utc(r['issue_time_utc'])
        start = utc(r['day_end_utc'])-timedelta(days=1)
        assert r['lead_start_hours'] == (start-utc(r['selected_ecmwf_run_time_utc'])).total_seconds()/3600
        assert r['lead_end_hours']-r['lead_start_hours'] == 23
        assert utc(r['issue_time_utc']) == utc(start.astimezone(BJT)+timedelta(hours=21,days=-int(r['horizon'][1])))

def test_30_same_subset_real_comparison(real):
    for row in real['metric']:
        subset = [p for p in real['prediction'] if p['model'] == row['model'] and p['horizon'] == row['horizon'] and p['status'] == 'PREDICTED']
        assert row['n'] == len(subset)
        assert row['raw_mae'] == pytest.approx(sum(abs(p['raw_ecmwf_tmax']-p['observed_tmax']) for p in subset)/len(subset),abs=1e-12)
        assert row['mae_improvement'] == row['raw_mae']-row['mos_mae']

def test_31_uniqueness_and_immutability(real):
    conn = sqlite3.connect(':memory:')
    try:
        populate(conn,real)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM phase6_mos_prediction')
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('UPDATE phase6_mos_prediction SET observed_tmax=999')
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO phase6_mos_prediction SELECT * FROM phase6_mos_prediction LIMIT 1')
    finally:
        conn.close()

def test_32_deterministic_rebuild_and_semantic(real):
    p,s = evaluate(real['sample'],'DIFFERENT_CLOCK')
    m,g = summarize(p)
    assert semantic_hash(real) == semantic_hash(dict(real,prediction=p,bias_state=s,metric=m,slice_metric=g))

def test_33_independent_real_audit_and_eligibility_view(real):
    conn = sqlite3.connect(':memory:')
    try:
        populate(conn,real)
        assert semantic_hash(read(conn)) == semantic_hash(real)
        result = audit_connection(conn,real['sample'])
        assert result['PHASE6_BUILD_STATUS'] == 'PASS' and result['future_label_count'] == result['same_day_unsettled_label_count'] == 0
        row = conn.execute("SELECT * FROM phase6_mos_label_eligibility_audit WHERE target_business_date='2025-06-10' AND horizon='T1' AND model='M1' ORDER BY label_business_date DESC LIMIT 1").fetchone()
        assert row['label_business_date'] == '2025-06-07' and row['is_label_eligible'] == 1 and row['eligibility_lag'] == 24
    finally:
        conn.close()

def test_34_audit_rejects_current_label(real):
    data = copy.deepcopy(real)
    state = next(s for s in data['bias_state'] if s['status'] == 'PREDICTED')
    dates = json.loads(state['training_dates_json'])+[state['target_business_date']]
    state['training_dates_json'] = json.dumps(dates); state['training_n'] = len(dates)
    conn = sqlite3.connect(':memory:')
    try:
        populate(conn,data)
        with pytest.raises(RuntimeError,match='eligibility'):
            audit_connection(conn,real['sample'],replay=False)
    finally:
        conn.close()

def test_35_refuse_frozen_original_and_existing_output(tmp_path):
    with pytest.raises(RuntimeError,match='READ ONLY'):
        build(True,next(iter(SOURCES.values()))[0])
    original = next(p for p in preserved_fingerprints() if p.endswith('phase6_statistical_mos_v1.db'))
    with pytest.raises(RuntimeError,match='READ ONLY'):
        build(True,original)
    existing = tmp_path/'existing.db'; existing.write_bytes(b'old artifact')
    with pytest.raises(RuntimeError,match='REFUSE OVERWRITE'):
        build(True,existing)
    assert existing.read_bytes() == b'old artifact'

def test_36_candidate_assessment_rejects_bad_slice():
    primary = [dict(model='M1',horizon=h,mae_improvement=.1,rmse_improvement=.1) for h in ('T1','T2')]
    slices = [dict(model='M1',horizon='T1',slice_type='season',n=50,mae_improvement=-.1)]
    assert assess(primary,slices)[0] == 'NONE'

def test_37_all_slices_disclosed(real):
    assert len(real['metric']) == 18 and len(real['slice_metric']) == 342
    assert {r['slice_type'] for r in real['slice_metric']} == {'season','month','year'}

def test_38_continuous_no_premature_rounding():
    rows = synthetic()
    for r in rows:
        r['raw_error'] = .37
    assert select_states(rows[40],rows)['M1'][0] == pytest.approx(.37)
    p,_ = evaluate(rows)
    assert next(r['mos_continuous_tmax'] for r in p if r['status'] == 'PREDICTED') == pytest.approx(25.63)

def test_39_source_and_blocked_after_all(real):
    assert fingerprints() == json.loads(real['manifest'][0]['source_sha_before_json'])
    assert preserved_fingerprints() == json.loads(real['manifest'][0]['preserved_assets_json'])
