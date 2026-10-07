import copy
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from src.baseline.source import prediction, fingerprints
from src.baseline.metrics import calculate
from src.baseline.storage import semantic_hash, read
from src.baseline.contracts import OUTPUT, PHASE4, SOURCES
from src.data_v1.source_io import open_snapshot
from src.builders.phase5_ecmwf_raw_baseline_builder import prepare, populate, build
from src.audit.phase5_baseline_v1_audit import audit_connection, reference_metrics

@pytest.fixture(scope='module')
def prepared():
    return prepare()

@pytest.fixture
def trajectory():
    day='2025-01-02'
    run='2025-01-01T00:00:00+00:00'
    available='2025-01-01T06:00:00+00:00'
    sample=dict(business_date_bjt=day,horizon='T1',sample_status='ELIGIBLE',selected_ecmwf_run_time_utc=run,
        selected_ecmwf_source_available_time_utc=available,issue_time_utc='2025-01-01T13:00:00+00:00',
        target_tmax_c=20,trajectory_valid_hours=24,trajectory_present_hours=24,meteostat_training_admission='BLOCKED',
        source_raw_run_id=1,ecmwf_availability_semantics='ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED')
    hours=[]
    solar={}
    start=datetime(2025,1,1,16,tzinfo=timezone.utc)
    for i in range(24):
        dt=start+timedelta(hours=i)
        key=dt.isoformat()
        hours.append(dict(business_date_bjt=day,horizon='T1',selected_ecmwf_run_time_utc=run,source_raw_run_id=1,
            source_available_time_utc=available,temperature_2m_c=float(i),target_time_utc=key,
            target_time_bjt=dt.astimezone(timezone(timedelta(hours=8))).isoformat(),lead_hours=16+i,solar_target_time=key))
        solar[key]=dict(business_date_bjt=day,season='winter',availability_basis='DETERMINISTIC_NOT_APPLICABLE')
    return sample,hours,{'daily_tmax_c':20},solar,'CLOCK'

def test_max_and_error(trajectory):
    p=prediction(*trajectory)
    assert (p['ecmwf_raw_tmax_c'],p['error_c'],p['absolute_error_c'],p['squared_error_c'])==(23,3,3,9)
    trajectory[2]['daily_tmax_c']=30
    trajectory[0]['target_tmax_c']=30
    assert prediction(*trajectory)['error_c']==-7

@pytest.mark.parametrize('fault',['23_hours','null','nan','cross_run','cross_raw','duplicate','future','available_mismatch','target','solar','issue','excluded'])
def test_reject_invalid_trajectory(trajectory,fault):
    s,h,t,z,_=trajectory
    if fault=='23_hours': h.pop()
    elif fault=='null': h[0]['temperature_2m_c']=None
    elif fault=='nan': h[0]['temperature_2m_c']=float('nan')
    elif fault=='cross_run': h[0]['selected_ecmwf_run_time_utc']='2025-01-01T06:00:00+00:00'
    elif fault=='cross_raw': h[0]['source_raw_run_id']=2
    elif fault=='duplicate': h[-1]=dict(h[0])
    elif fault=='future': s['selected_ecmwf_source_available_time_utc']='2025-01-01T14:00:00+00:00'
    elif fault=='available_mismatch': h[0]['source_available_time_utc']='2025-01-01T07:00:00+00:00'
    elif fault=='target': t['daily_tmax_c']=21
    elif fault=='solar': z[h[0]['solar_target_time']]['business_date_bjt']='2025-01-03'
    elif fault=='issue': s['issue_time_utc']='2025-01-01T14:00:00+00:00'
    elif fault=='excluded': s['sample_status']='EXCLUDED_INCOMPLETE_ECMWF'
    with pytest.raises(RuntimeError): prediction(*trajectory)

def test_peak_ties(trajectory):
    trajectory[1][0]['temperature_2m_c']=23.
    p=prediction(*trajectory)
    assert p['ecmwf_tmax_occurrence_count']==2
    assert p['ecmwf_tmax_first_time_utc']==trajectory[1][0]['target_time_utc']
    assert p['ecmwf_tmax_last_time_utc']==trajectory[1][-1]['target_time_utc']

def test_hand_calculated_metrics():
    rows=[dict(business_date_bjt=str(i),horizon='T1',error_c=e,ecmwf_raw_tmax_c=10+e,target_tmax_c=10) for i,e in enumerate([-.5,1.,-2.,4.])]
    m=calculate(rows)
    assert m['bias_c']==.625
    assert m['mae_c']==1.875
    assert m['rmse_c']==math.sqrt(21.25/4)
    assert m['median_ae_c']==1.5
    assert m['p90_ae_c']==pytest.approx(3.4)
    assert [m[f'ae_le_{x}_count'] for x in ('0_5','1','2')]==[1,2,3]
    assert [m[f'ae_le_{x}_rate'] for x in ('0_5','1','2')]==[.25,.5,.75]
    assert m['r2'] is None and m['pearson_r'] is None
    ref=reference_metrics(rows)
    for key in m:
        assert m[key]==pytest.approx(ref[key]) if ref[key] is not None else m[key] is None

def test_empty_and_single_metrics():
    assert calculate([])['n']==0 and calculate([])['mae_c'] is None
    p=dict(business_date_bjt='x',horizon='T0',error_c=1.,ecmwf_raw_tmax_c=2.,target_tmax_c=1.)
    assert calculate([p])['error_std_c']==0 and calculate([p])['r2'] is None

def test_frozen_counts_gap_and_metrics(prepared):
    d,_=prepared
    assert len(d['prediction'])==2186
    assert [m['n'] for m in d['metrics']]==[729,729,728]
    assert len({p['business_date_bjt'] for p in d['prediction']})==729
    assert sum(p['hour_count'] for p in d['prediction'])==52464
    assert len(d['exclusion'])==1
    g=d['exclusion'][0]
    assert (g['business_date_bjt'],g['horizon'],g['trajectory_valid_hours'])==('2025-08-07','T2',14)
    assert not any((p['business_date_bjt'],p['horizon'])==('2025-08-07','T2') for p in d['prediction'])
    assert all(not any('meteostat' in k.lower() for k in p) for p in d['prediction'])

def test_semantic_hash_runtime_and_order_independent(prepared):
    d,_=prepared
    changed=copy.deepcopy(d)
    for name in ('prediction','metrics','group_metrics','exclusion'):
        changed[name].reverse()
        for r in changed[name]:
            r['created_at_utc']='CHANGED'
            r['id']=999
    assert semantic_hash(d)==semantic_hash(changed)
    changed['prediction'][0]['ecmwf_raw_tmax_c']+=.1
    assert semantic_hash(d)!=semantic_hash(changed)

def test_dry_run_no_file(tmp_path,prepared):
    path=tmp_path/'formal.db'
    before=fingerprints()
    with patch('src.builders.phase5_ecmwf_raw_baseline_builder.prepare',return_value=prepared):
        result=build(False,path)
    assert result['result']=='PASS' and not path.exists()
    assert fingerprints()==before

def test_refuse_existing(tmp_path):
    path=tmp_path/'existing.db'
    path.write_bytes(b'KEEP')
    with pytest.raises(RuntimeError,match='REFUSE OVERWRITE'): build(True,path)
    assert path.read_bytes()==b'KEEP'

def test_source_output_refused():
    with pytest.raises(RuntimeError,match='READ ONLY'): build(True,PHASE4)

def test_transaction_rollback(prepared):
    conn=sqlite3.connect(':memory:')
    def fail(): raise RuntimeError('injected failure')
    with pytest.raises(RuntimeError,match='injected'): populate(conn,*prepared,fail_hook=fail)
    assert not conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()

def test_source_sqlite_read_only():
    before=fingerprints()
    conn=open_snapshot(PHASE4)
    try:
        with pytest.raises(sqlite3.OperationalError): conn.execute('CREATE TABLE forbidden(x)')
    finally: conn.close()
    assert fingerprints()==before

def test_independent_audit_and_metric_tamper(prepared):
    conn=sqlite3.connect(':memory:')
    populate(conn,*prepared)
    assert audit_connection(conn,prepared[1])['result']=='PASS'
    conn.execute('DROP TRIGGER phase5_baseline_v1_metrics_update')
    conn.execute("UPDATE phase5_baseline_v1_metrics SET mae_c=0 WHERE horizon='T1'")
    with pytest.raises(RuntimeError,match='Metric mismatch'): audit_connection(conn,prepared[1])
    conn.close()

def test_commit_failure_no_publication(tmp_path,prepared):
    output=tmp_path/'failed.db'
    with patch('src.builders.phase5_ecmwf_raw_baseline_builder.prepare',return_value=prepared), patch('src.builders.phase5_ecmwf_raw_baseline_builder.audit',side_effect=RuntimeError('audit rejection')):
        with pytest.raises(RuntimeError,match='audit rejection'): build(True,output)
    assert not output.exists() and not list(tmp_path.glob('*.building'))

def test_atomic_publish_and_dry_run_hash_match(tmp_path,prepared):
    output=tmp_path/'published.db'
    with patch('src.builders.phase5_ecmwf_raw_baseline_builder.prepare',return_value=prepared):
        dry=build(False,output)
        committed=build(True,output,expected_semantic=dry['phase5_semantic_sha256'])
    assert committed['phase5_semantic_sha256']==dry['phase5_semantic_sha256']
    assert output.exists() and not list(tmp_path.glob('*.building'))

def test_publish_race_refuses_overwrite(tmp_path,prepared):
    output=tmp_path/'race.db'
    def race(source,destination):
        output.write_bytes(b'OTHER PROCESS')
        raise FileExistsError('destination exists')
    with patch('src.builders.phase5_ecmwf_raw_baseline_builder.prepare',return_value=prepared), patch('src.builders.phase5_ecmwf_raw_baseline_builder.os.link',side_effect=race):
        with pytest.raises(FileExistsError): build(True,output)
    assert output.read_bytes()==b'OTHER PROCESS'

def test_prediction_tamper_audit_rejected(prepared):
    conn=sqlite3.connect(':memory:')
    populate(conn,*prepared)
    conn.execute('DROP TRIGGER phase5_baseline_v1_prediction_update')
    conn.execute("UPDATE phase5_baseline_v1_prediction SET ecmwf_raw_tmax_c=99 WHERE business_date_bjt='2024-09-03' AND horizon='T0'")
    with pytest.raises(RuntimeError,match='Prediction mismatch'): audit_connection(conn,prepared[1])
    conn.close()

def test_r2_and_correlation_definition():
    rows=[dict(business_date_bjt=str(i),horizon='T2',error_c=1.,ecmwf_raw_tmax_c=float(i+1),target_tmax_c=float(i)) for i in range(3)]
    m=calculate(rows)
    assert m['pearson_r']==pytest.approx(1.)
    assert m['r2']==pytest.approx(-.5)

def test_dry_commit_hash_mismatch_refused(tmp_path,prepared):
    output=tmp_path/'mismatch.db'
    with patch('src.builders.phase5_ecmwf_raw_baseline_builder.prepare',return_value=prepared):
        with pytest.raises(RuntimeError,match='semantic mismatch'): build(True,output,expected_semantic='0'*64)
    assert not output.exists()
