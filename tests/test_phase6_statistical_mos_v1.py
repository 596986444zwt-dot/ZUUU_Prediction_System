"""Real-source admission checks plus adversarial synthetic chronological tests.

Synthetic settlement evidence validates algorithms only, never admits real labels.
"""
import copy
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
import pytest
from src.mos.contracts import CONTRACT, SOURCES, MODELS, season
from src.mos.data import fingerprints
from src.mos.bias import eligible_history, select_bias
from src.mos.walk_forward import evaluate
from src.mos.metrics import calculate, summarize
from src.mos.schema import populate, read
from src.mos.semantic_hash import semantic_hash
from src.data_v1.contracts import BJT, utc
from src.builders.phase6_statistical_mos_v1_builder import prepare, build
from src.audit.phase6_statistical_mos_v1_audit import audit_connection

@pytest.fixture(scope='module')
def real():
    return prepare()

@pytest.fixture
def connection(real):
    conn = sqlite3.connect(':memory:')
    populate(conn, real)
    yield conn
    conn.close()

def synthetic(n=80, horizon='T0'):
    rows = []
    for i in range(n):
        day = datetime(2025, 1, 1, tzinfo=BJT)+timedelta(days=i)
        issue = day+timedelta(hours=21, days=-int(horizon[1]))
        run = day-timedelta(days=int(horizon[1]), hours=2)
        end = day+timedelta(days=1)
        rows.append(dict(business_date_bjt=day.date().isoformat(), horizon=horizon,
            issue_time_utc=issue.astimezone(timezone.utc).isoformat(),
            selected_ecmwf_run_time_utc=run.astimezone(timezone.utc).isoformat(),
            selected_ecmwf_source_available_time_utc=(run+timedelta(hours=6)).astimezone(timezone.utc).isoformat(),
            ecmwf_availability_semantics='SYNTHETIC_TEST_ONLY', raw_ecmwf_tmax=26., observed_tmax=25., raw_error=1.,
            month=day.month, year=day.year, season=season(day.month), lead_start_hours=2+24*int(horizon[1]),
            lead_end_hours=25+24*int(horizon[1]), day_end_utc=end.astimezone(timezone.utc).isoformat(),
            settled_at_utc=(end+timedelta(hours=6)).astimezone(timezone.utc).isoformat(), settlement_verified=True))
    return rows

def test_01_source_sha_before_after(real):
    assert json.loads(real['manifest'][0]['source_sha_before_json']) == fingerprints()

def test_02_source_integrity(real):
    assert all(r['integrity_check'] == ['ok'] for r in json.loads(real['manifest'][0]['source_guardian_json']).values())

def test_03_source_foreign_keys(real):
    assert all(r['foreign_key_check'] == 0 for r in json.loads(real['manifest'][0]['source_guardian_json']).values())

def test_04_phase4_semantic(real):
    assert json.loads(real['manifest'][0]['source_guardian_json'])['phase4']['semantic']['DATA_V1'] == '4eea080168f102f30314b0b8a64123af533923fd7f4b7e7c6e9312b5582835dd'

def test_05_phase5_semantic(real):
    assert json.loads(real['manifest'][0]['source_guardian_json'])['phase5']['semantic']['BASELINE'] == '4f31fa80c704eed5100b1add4d37cebf120b1e56fe1c4e2ba65a140d9d8fd353'

def test_06_T0_count(real):
    assert sum(r['horizon'] == 'T0' for r in real['sample']) == 729

def test_07_T1_count(real):
    assert sum(r['horizon'] == 'T1' for r in real['sample']) == 729

def test_08_T2_count(real):
    assert sum(r['horizon'] == 'T2' for r in real['sample']) == 728

def test_09_total_count(real):
    assert len(real['sample']) == 2186

def test_10_gap_excluded(real):
    assert not any(r['business_date_bjt'] == '2025-08-07' and r['horizon'] == 'T2' for r in real['sample'])
    gaps = json.loads(real['manifest'][0]['exclusion_json'])
    assert len(gaps) == 1 and gaps[0]['trajectory_valid_hours'] == 14 and gaps[0]['sample_status'] == 'EXCLUDED_INCOMPLETE_ECMWF'

def test_11_benchmark_reproducible(real):
    for row in json.loads(real['manifest'][0]['benchmark_json']):
        assert calculate([r['raw_ecmwf_tmax']-r['observed_tmax'] for r in real['sample'] if r['horizon'] == row['horizon']]) == {k:v for k,v in row.items() if k != 'horizon'}

@pytest.mark.parametrize('error', [-2., 2.])
def test_12_error_sign(error):
    rows = synthetic()
    for r in rows:
        r.update(raw_ecmwf_tmax=25+error, raw_error=error)
    predictions, _ = evaluate(rows)
    valid = [r for r in predictions if r['status'] == 'PREDICTED']
    assert valid and all(r['mos_continuous_tmax'] == 25 and r['mos_error'] == 0 for r in valid)

def test_13_expanding_past_only():
    rows = synthetic()
    bias, used, _, _ = select_bias(rows[40], rows, 'M1')
    assert bias == 1 and len(used) == 40 and max(r['business_date_bjt'] for r in used) < rows[40]['business_date_bjt']

def test_14_trailing7_past_only():
    rows = synthetic()
    _, used, path, _ = select_bias(rows[40], rows, 'M2')
    assert path == 'M2' and [r['business_date_bjt'] for r in used] == [r['business_date_bjt'] for r in rows[33:40]]

def test_15_trailing30_past_only():
    rows = synthetic()
    _, used, path, _ = select_bias(rows[40], rows, 'M3')
    assert path == 'M3' and len(used) == 30 and used[0] == rows[10] and used[-1] == rows[39]

def test_16_no_centered_rolling():
    rows = synthetic()
    past = select_bias(rows[40], rows, 'M2')
    for r in rows[40:]:
        r['raw_error'] = 99999
    assert select_bias(rows[40], rows, 'M2') == past

def test_17_current_label_not_in_own_bias():
    rows = synthetic()
    p1, _ = evaluate(rows)
    rows[40].update(observed_tmax=-1000, raw_error=1026)
    p2, _ = evaluate(rows)
    one = [r for r in p1 if r['target_business_date'] == rows[40]['business_date_bjt']]
    two = [r for r in p2 if r['target_business_date'] == rows[40]['business_date_bjt']]
    assert [r['mos_continuous_tmax'] for r in one] == [r['mos_continuous_tmax'] for r in two]

def test_18_future_ground_truth_unavailable():
    rows = synthetic()
    rows[0]['settled_at_utc'] = rows[-1]['settled_at_utc']
    assert rows[0] not in eligible_history(rows[40], rows)
    rows[1]['settlement_verified'] = False
    assert rows[1] not in eligible_history(rows[40], rows)
    rows[2]['settled_at_utc'] = rows[40]['issue_time_utc']
    assert rows[2] not in eligible_history(rows[40], rows)

def test_19_season_bias_past_only():
    rows = synthetic(180)
    _, used, path, _ = select_bias(rows[120], rows, 'M4')
    assert path == 'M4' and all(r['season'] == rows[120]['season'] and r['business_date_bjt'] < rows[120]['business_date_bjt'] for r in used)

def test_20_lead_horizon_semantics(real):
    for r in real['sample']:
        start = utc(r['day_end_utc'])-timedelta(days=1)
        assert r['lead_start_hours'] == (start-utc(r['selected_ecmwf_run_time_utc'])).total_seconds()/3600
        assert r['lead_end_hours']-r['lead_start_hours'] == 23
        issue = start.astimezone(BJT)+timedelta(hours=21, days=-int(r['horizon'][1]))
        assert utc(r['issue_time_utc']) == utc(issue)
    rows = synthetic()
    assert select_bias(rows[40], rows, 'M1')[0] == select_bias(rows[40], rows, 'M5')[0]

def test_21_horizon_separation():
    rows = synthetic()
    other = synthetic(horizon='T1')
    for r in other:
        r['raw_error'] = -999
    assert select_bias(rows[40], rows+other, 'M1')[0] == 1

def test_22_cold_start_no_leakage():
    rows = synthetic()
    assert select_bias(rows[6], rows, 'M1')[0] is None
    assert select_bias(rows[7], rows, 'M1')[0] == 1

def test_23_fallback_no_leakage():
    rows = synthetic()
    for r in rows[33:40]:
        r['settlement_verified'] = False
    bias, used, path, _ = select_bias(rows[40], rows, 'M2')
    assert bias == 1 and path == 'M2>M1' and len(used) == 33
    rows = synthetic(100)
    _, used, path, _ = select_bias(rows[60], rows, 'M4')
    assert path == 'M4>M1' and len(used) == 60

def test_24_chronological_walk_forward():
    rows = synthetic(50)
    p1, s1 = evaluate(rows)
    p2, s2 = evaluate(list(reversed(rows)))
    assert p1 == p2 and s1 == s2
    assert [r['issue_time'] for r in p1] == sorted(r['issue_time'] for r in p1)

@pytest.mark.parametrize('horizon', ['T0','T1','T2'])
def test_25_training_cutoff(horizon):
    rows = synthetic(horizon=horizon)
    _, states = evaluate(rows)
    for s in states:
        for r in json.loads(s['training_lineage_json']):
            assert utc(r['settled_at_utc']) < utc(s['training_cutoff'])
    target = rows[40]
    _, used, _, _ = select_bias(target, rows, 'M2')
    anchor = utc(target['issue_time_utc']).astimezone(BJT).date()-timedelta(days=1)
    assert max(r['business_date_bjt'] for r in used) == anchor.isoformat()

def test_26_no_meteostat(real):
    assert not any('meteostat' in k.lower() for k in real['sample'][0])
    assert 'Meteostat' in CONTRACT['forbidden_inputs']

def test_27_no_unproven_intraday_or_settlement(real):
    assert all(r['settled_at_utc'] is None and not r['settlement_verified'] for r in real['sample'])
    assert all(r['status'] == 'BLOCKED_LABEL_AVAILABILITY' and r['mos_continuous_tmax'] is None and r['training_n'] == 0 for r in real['prediction'])
    evidence = json.loads(real['manifest'][0]['label_availability_evidence_json'])
    assert evidence['verified_historical_settlement_days'] == 0
    assert all(utc(r['first_lineage_import']) > utc(real['sample'][-1]['issue_time_utc']) for r in evidence['daily_lineage'])

def test_28_no_phase7_feature_contamination(real):
    prohibited = ['dew', 'humidity', 'wind', 'cloud', 'radiation', 'pressure', 'precipitation', 'solar', 'revision', 'similarity', 'trajectory']
    assert not any(word in key.lower() for word in prohibited for key in real['sample'][0])

def test_29_no_future_run(real):
    assert all(utc(r['selected_ecmwf_run_time_utc']) <= utc(r['selected_ecmwf_source_available_time_utc']) <= utc(r['issue_time_utc']) for r in real['sample'])

def test_30_no_cross_run_splice(real):
    # prepare calls the existing per-hour lineage validator and exact Phase5 replay.
    checks = json.loads(real['manifest'][0]['source_guardian_json'])
    assert checks['phase4']['row_counts']['phase4_data_v1_ecmwf_hourly'] == 52478
    by_key = {(r['business_date_bjt'],r['horizon']):r['selected_ecmwf_run_time_utc'] for r in real['sample']}
    assert all(by_key[r['target_business_date'],r['horizon']] == r['selected_ecmwf_run'] for r in real['prediction'])

def test_31_uniqueness_and_immutability(connection):
    row = connection.execute('SELECT * FROM phase6_mos_prediction LIMIT 1').fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute('INSERT INTO phase6_mos_prediction VALUES ('+','.join('?' for _ in row)+')', tuple(row))
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute('DELETE FROM phase6_mos_prediction')
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE phase6_mos_prediction SET observed_tmax=999")

def test_32_metric_fair_subset_and_reference():
    rows = synthetic()
    p, _ = evaluate(rows)
    overall, _ = summarize(p)
    for metric in overall:
        if metric['horizon'] != 'T0':
            assert metric['n'] == 0 and metric['raw_mae'] is None
            continue
        subset = [r for r in p if r['model'] == metric['model'] and r['status'] == 'PREDICTED']
        assert metric['n'] == len(subset) == 73
        assert metric['raw_mae'] == sum(abs(r['raw_error']) for r in subset)/len(subset)
        assert metric['mos_mae'] == 0 and metric['mae_improvement'] == 1 and metric['mae_improvement_pct'] == 100
        assert metric['raw_rmse'] == math.sqrt(sum(r['raw_error']**2 for r in subset)/len(subset))

def test_33_deterministic_rebuild(real):
    p, s = evaluate(real['sample'], 'DIFFERENT_CLOCK')
    metrics, slices = summarize(p)
    rebuilt = dict(real, prediction=p, bias_state=s, metric=metrics, slice_metric=slices)
    assert semantic_hash(real) == semantic_hash(rebuilt)

def test_34_semantic_and_audit(connection, real):
    assert semantic_hash(read(connection)) == semantic_hash(real)
    result = audit_connection(connection, real['sample'])
    assert result['artifact_audit'] == 'PASS' and result['PHASE6_BUILD_STATUS'] == 'BLOCKED'
    assert result['valid_mos_oos_predictions'] == 0

def test_35_frozen_assets_unchanged(real):
    assert fingerprints() == json.loads(real['manifest'][0]['source_sha_before_json'])

def test_blocked_is_not_zero_error(real):
    assert all(r['n'] == 0 and r['mos_mae'] is None and r['raw_mae'] is None and r['mae_improvement_pct'] is None for r in real['metric'])
    assert all(r['stability'] == 'NOT_ESTIMABLE' for r in real['slice_metric'])

def test_combined_predeclared_weights():
    rows = synthetic()
    for i,r in enumerate(rows):
        r['raw_error'] = float(i)
    b, _, _, components = select_bias(rows[70], rows, 'M6')
    assert len(components) == 3 and b == sum(c['bias'] for c in components)/3

def test_does_not_overwrite_or_touch_sources(tmp_path):
    existing = tmp_path/'phase6.db'
    existing.write_bytes(b'existing formal artifact')
    with pytest.raises(RuntimeError, match='REFUSE OVERWRITE'):
        build(True, existing)
    assert existing.read_bytes() == b'existing formal artifact'
    with pytest.raises(RuntimeError, match='READ ONLY'):
        build(True, next(iter(SOURCES.values()))[0])

def test_settlement_cannot_predate_day_end():
    rows = synthetic()
    rows[0]['settled_at_utc'] = rows[0]['issue_time_utc']
    assert rows[0] not in eligible_history(rows[40], rows)

def test_delayed_label_7_calendar_days_not_last_7_records():
    rows = synthetic()
    rows[39]['settled_at_utc'] = rows[50]['settled_at_utc']
    _, used, path, _ = select_bias(rows[40], rows, 'M2')
    assert path == 'M2>M1' and len(used) == 39  # 6 window labels => fallback, not older seventh record.

def test_uncertified_past_does_not_become_available():
    rows = synthetic()
    for r in rows:
        r['settlement_verified'] = False
    p, _ = evaluate(rows)
    assert all(r['mos_continuous_tmax'] is None for r in p)

def test_independent_metric_quantiles_and_errors():
    result = calculate([-2., -1., .5, 4.])
    assert result['bias'] == .375 and result['mae'] == 1.875
    assert result['rmse'] == math.sqrt(21.25/4)
    assert result['median_ae'] == 1.5 and result['p90_ae'] == pytest.approx(3.4) and result['max_ae'] == 4

def test_audit_rejects_implementation_provenance_tamper(real):
    damaged = copy.deepcopy(real)
    hashes = json.loads(damaged['manifest'][0]['implementation_sha256_json'])
    hashes[next(iter(hashes))] = '0'*64
    damaged['manifest'][0]['implementation_sha256_json'] = json.dumps(hashes)
    conn = sqlite3.connect(':memory:')
    try:
        populate(conn, damaged)
        with pytest.raises(RuntimeError, match='implementation provenance'):
            audit_connection(conn, damaged['sample'])
    finally:
        conn.close()
