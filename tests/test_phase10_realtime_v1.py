"""Isolated SIMULATION storage, real frozen golden evidence, causal edge cases."""
import json
import sqlite3
from datetime import datetime,timedelta
from pathlib import Path
import numpy as np
import pytest
import requests
from src.realtime.contracts import *
from src.realtime.archive import Archive,TABLES
from src.realtime.collectors import Fetcher,archive_metar,archive_ecmwf
from src.realtime.features import selection,construct
from src.realtime.engine import InstanceLock,system_gate
from src.realtime.settlement import settle,evaluate,advance
from src.realtime.handoff import eligible,forward
from src.ecmwf_contract import HOURLY_VARIABLES,EXPECTED_UNITS

@pytest.fixture
def db(tmp_path):
    a=Archive(tmp_path/'simulation.db','SIMULATION',create=True)
    yield a
    a.close()

def payload(run):
    hourly={'time':[(utc(run)+timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in range(72)]}
    for k in HOURLY_VARIABLES:hourly[k]=[20. if k=='temperature_2m' else None for _ in range(72)]
    return {'utc_offset_seconds':0,'hourly':hourly,'hourly_units':EXPECTED_UNITS.copy()}

def metar(obs,raw=None):
    obs=utc(obs)
    return dict(icaoId='ZUUU',obsTime=obs.timestamp(),rawOb=raw if raw is not None else 'METAR ZUUU '+obs.strftime('%d%H%MZ')+' 00000MPS CAVOK 24/15 Q1013',receiptTime=iso(obs+timedelta(minutes=1)))

@pytest.mark.parametrize('before,expected',[
 ('2026-10-01T15:59:59+00:00','2026-10-01'),('2026-10-01T16:00:00+00:00','2026-10-02'),
 ('2026-10-31T15:59:59+00:00','2026-10-31'),('2026-10-31T16:00:00+00:00','2026-11-01'),
 ('2026-12-31T15:59:59+00:00','2026-12-31'),('2026-12-31T16:00:00+00:00','2027-01-01'),
 ('2028-02-28T16:00:00+00:00','2028-02-29'),('2028-02-29T16:00:00+00:00','2028-03-01')])
def test_midnight(before,expected):
    days=targets(before);assert days['T0']==expected
    assert (datetime.fromisoformat(days['T2'])-datetime.fromisoformat(days['T1'])).days==1

@pytest.mark.parametrize('stamp',[datetime(2026,1,1),'2026-01-01T00:00:00'])
def test_naive_time_rejected(stamp):
    with pytest.raises(ValueError):utc(stamp)

@pytest.mark.parametrize('table',TABLES)
def test_all_domain_rows_immutable(db,table):
    with db.c:db.insert(table,{'example':1},'id')
    with pytest.raises(sqlite3.IntegrityError):db.c.execute('UPDATE realtime_'+table+' SET payload_json=?',('{}',))
    db.c.rollback()
    with pytest.raises(sqlite3.IntegrityError):db.c.execute('DELETE FROM realtime_'+table)
    db.c.rollback()

def test_simulation_separation(db):
    assert db.namespace=='SIMULATION'
    with pytest.raises(ValueError):Archive(db.path,'PRODUCTION')

@pytest.mark.parametrize('prefix',['METAR','SPECI','METAR COR','SPECI COR',''])
def test_metar_classes_and_true_receipt(db,prefix):
    obs=utc('2026-10-01T03:00:00+00:00');received=obs+timedelta(days=1)
    raw=(prefix+' ZUUU 010300Z 00000MPS CAVOK 24/15 Q1013').strip()
    assert archive_metar(db,[metar(obs,raw)],received)==1
    r=db.rows('zuuu_raw')[0];assert r['actual_ingest_time']==iso(received) and r['actual_ingest_time']!=iso(obs)
    assert db.rows('zuuu_normalized')[0]['supersedes_raw_id'] is None

def test_duplicate_metar(db):
    obs=utc('2026-10-01T03:00:00+00:00');r=metar(obs)
    assert archive_metar(db,[r],obs)==1
    assert archive_metar(db,[r],obs+timedelta(hours=1))==0
    assert len(db.rows('zuuu_raw'))==1

def test_corrected_version_preserved(db):
    obs=utc('2026-10-01T03:00:00+00:00')
    archive_metar(db,[metar(obs)],obs)
    archive_metar(db,[metar(obs,'METAR COR ZUUU 010300Z 00000MPS CAVOK 25/15 Q1013')],obs+timedelta(hours=1))
    assert len(db.rows('zuuu_raw'))==len(db.rows('zuuu_normalized'))==2

@pytest.mark.parametrize('raw',['','garbage','METAR ZBAA 010300Z 00000MPS CAVOK 24/15 Q1013','METAR ZUUU 020300Z 00000MPS CAVOK 24/15 Q1013'])
def test_malformed_metar_raw_retained(db,raw):
    obs=utc('2026-10-01T03:00:00+00:00')
    assert archive_metar(db,[metar(obs,raw)],obs)==0
    assert len(db.rows('zuuu_raw'))==1 and len(db.rows('errors'))==1

def test_same_run_versions(db):
    run=utc('2026-10-01T00:00:00+00:00');p=payload(run)
    assert archive_ecmwf(db,p,run,run+timedelta(hours=1))
    assert not archive_ecmwf(db,p,run,run+timedelta(hours=2))
    p['hourly']['temperature_2m'][0]=21
    assert archive_ecmwf(db,p,run,run+timedelta(hours=3))
    assert len(db.rows('ecmwf_raw_runs'))==2 and len(db.rows('ecmwf_hourly'))==144

def test_quarantined_previous_run_produces_missing_revision(db):
    from src.realtime.features import runtime_context
    old=utc('2026-10-02T00:00:00+00:00');run=old+timedelta(hours=6);issue=old+timedelta(hours=13)
    archive_ecmwf(db,{},old,old+timedelta(hours=7))
    archive_ecmwf(db,payload(run),run,issue)
    runs,curves=runtime_context(db,issue)
    f=construct('2026-10-03','T1',issue,runs,curves,[])
    assert f['values']['ecmwf_tmax_revision_prev_run_c'] is None
    assert f['missing_reasons']['ecmwf_tmax_revision_prev_run_c']=='COMPARISON_TARGET_CURVE_INCOMPLETE'

@pytest.mark.parametrize('defect',['partial','empty','unit','future'])
def test_invalid_forecast_quarantined(db,defect):
    run=utc('2026-10-01T00:00:00+00:00');p=payload(run);receipt=run+timedelta(hours=1)
    if defect=='partial':p['hourly']['time'].pop()
    if defect=='empty':p={}
    if defect=='unit':p['hourly_units']['temperature_2m']='K'
    if defect=='future':receipt=run-timedelta(seconds=1)
    assert not archive_ecmwf(db,p,run,receipt)
    assert len(db.rows('ecmwf_raw_runs'))==1 and not db.rows('ecmwf_hourly')

@pytest.mark.parametrize('stage',['feature_snapshots','continuous_predictions','probability_predictions'])
def test_snapshot_atomic_rollback(db,stage):
    with pytest.raises(RuntimeError):db.snapshot('s',{'x':1},{'ml':2},{'pmf':[1]}, {},fail_after=stage)
    assert all(not db.rows(t) for t in ('feature_snapshots','continuous_predictions','probability_predictions','prediction_snapshots'))

def test_snapshot_idempotency(db):
    assert db.snapshot('s',{'x':1},{'ml':2},{'pmf':[1]}, {})
    assert not db.snapshot('s',{'x':1},{'ml':2},{'pmf':[1]}, {})
    assert db.integrity()==(['ok'],[])

def test_backup_consistent(db,tmp_path):
    with db.c:db.insert('zuuu_raw',{'raw':'hello'},'r')
    p=db.backup(tmp_path/'backup.db');c=sqlite3.connect(p)
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert c.execute('SELECT COUNT(*) FROM realtime_zuuu_raw').fetchone()[0]==1;c.close()
    p.unlink();assert not p.exists()  # Windows must have no retained backup handle.

def test_backup_timeout_leaves_no_false_success(db,tmp_path):
    p=tmp_path/'bad_backup.db'
    with pytest.raises(TimeoutError):db.backup(p,timeout=-1)
    assert not p.exists() and not list(tmp_path.glob('*.partial_*'))

def test_response_spool_recovers_actual_receipt(db):
    from src.realtime.spool import save,recover,directory
    received=utc('2026-10-01T03:01:00+00:00');data=json.dumps([metar(received-timedelta(minutes=1))]).encode()
    save(db,'ZUUU',data,received)
    assert not db.rows('zuuu_raw')
    assert recover(db)==1 and recover(db)==0
    assert db.rows('zuuu_raw')[0]['actual_ingest_time']==iso(received)
    assert len(list(directory(db).glob('*.json')))==1

def test_locked_database_preserves_spool(db):
    from src.realtime.spool import save,recover,directory
    received=utc('2026-10-01T03:01:00+00:00')
    save(db,'ZUUU',json.dumps([metar(received-timedelta(minutes=1))]).encode(),received)
    other=sqlite3.connect(db.path);other.execute('BEGIN IMMEDIATE');db.c.execute('PRAGMA busy_timeout=1')
    try:
        with pytest.raises(sqlite3.OperationalError):recover(db)
        assert len(list(directory(db).glob('*.json')))==1
    finally:other.rollback();other.close()
    assert recover(db)==1 and db.integrity()==(['ok'],[])

def test_stop_requested_during_initialization_is_preserved(tmp_path,monkeypatch):
    import src.realtime.engine as module
    monkeypatch.setattr(module,'ROOT',tmp_path)
    marker=tmp_path/'logs/phase10/stop.request';marker.parent.mkdir(parents=True);marker.write_text('STOP')
    a=Archive(tmp_path/'simulation.db','SIMULATION',create=True)
    engine=module.Engine.__new__(module.Engine);engine.db=a;engine.cfg=config();engine.stopping=False
    engine.cycle=lambda:pytest.fail('stop request must prevent a cycle')
    engine.run(seconds=1)
    c=sqlite3.connect(a.path);assert c.execute('SELECT COUNT(*) FROM realtime_engine_health').fetchone()[0]==1;c.close()

def test_corrected_prefix_with_remarks(db):
    obs=utc('2026-10-01T03:00:00+00:00')
    raw='COR ZUUU 010300Z 00000MPS CAVOK 24/15 Q1013 RMK COR'
    archive_metar(db,[metar(obs,raw)],obs)
    r=db.rows('zuuu_normalized')[0]
    assert r['message_class']=='COR' and r['raw_report']==raw and r['supersedes_raw_id'] is None

@pytest.mark.parametrize('namespace',['PRODUCTION','SIMULATION'])
def test_simulated_availability_never_leaks_to_production(tmp_path,namespace):
    from src.realtime.features import runtime_context
    db=Archive(tmp_path/(namespace+'.db'),namespace,create=True)
    try:
        with db.c:db.insert('ecmwf_raw_runs',dict(run_time='2026-01-01T00:00:00+00:00',actual_ingest_time='2026-10-01T00:00:00+00:00',replay_available_time='2026-01-01T07:00:00+00:00'),'r')
        runs,_=runtime_context(db,'2026-01-01T13:00:00+00:00')
        assert bool(runs)==(namespace=='SIMULATION')
    finally:db.close()

def test_single_instance(tmp_path):
    with InstanceLock(tmp_path/'engine.lock'):
        with pytest.raises(RuntimeError):
            with InstanceLock(tmp_path/'engine.lock'):pass
    with InstanceLock(tmp_path/'engine.lock'):pass

def test_crash_recovery_lock(tmp_path):
    import subprocess,sys
    p=tmp_path/'lock';code='from src.realtime.engine import InstanceLock; import time; x=InstanceLock('+repr(str(p))+');x.__enter__();print("LOCKED",flush=True);time.sleep(60)'
    proc=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,text=True)
    try:assert proc.stdout.readline().strip()=='LOCKED'
    finally:proc.kill();proc.wait()
    with InstanceLock(p):pass

@pytest.mark.parametrize('failure',['timeout','dns','429','500'])
def test_bounded_network_retries(failure):
    class Session:
        n=0
        def get(self,*a,**k):
            self.n+=1
            if failure=='timeout':raise requests.Timeout()
            if failure=='dns':raise requests.ConnectionError('DNS')
            r=requests.Response();r.status_code=int(failure);r._content=b'error';return r
    s=Session();delays=[];f=Fetcher(config(),s,delays.append)
    data,received,attempts=f.get('https://example.invalid',{})
    assert data is None and s.n==3 and len(delays)==2 and delays[1]>delays[0]
    assert len(attempts)==3

@pytest.mark.parametrize('delta,valid',[(-1,False),(0,True),(1,True)])
def test_d_plus_two_boundary(delta,valid):
    day='2026-10-01';stamp=eligibility(day)+timedelta(seconds=delta)
    r=dict(target_business_date=day,eligibility=iso(eligibility(day)),issue='2026-09-30T13:00:00+00:00')
    assert eligible(r,stamp,'2026-10-04')==valid

def test_future_case_ineligible():
    r=dict(target_business_date='2026-10-10',eligibility='2026-10-12T00:00:00+08:00',issue='2026-10-09T13:00:00+00:00')
    assert not eligible(r,'2026-10-02T13:00:00+00:00','2026-10-03')

@pytest.mark.parametrize('count',[0,1,59])
def test_probability_cold_start(count):
    r=[]
    for n in range(count):
        d=(datetime(2026,1,1)+timedelta(days=n)).date().isoformat()
        r.append(dict(record_id=d+'/T1/ML',horizon='T1',target_business_date=d,eligibility=iso(eligibility(d)),issue=d+'T00:00:00+00:00',origin='ML',residual=1))
    s=dict(cutoff='2026-09-01T00:00:00+00:00',residuals=r,cases=[])
    p,_=forward(s,'T1','2026-10-03','2026-10-02T13:00:00+00:00',{'ML':30,'RAW':30,'MOS':30})
    assert p['status']=='NO_FORECAST'

@pytest.mark.parametrize('origin',['MOS','RAW'])
def test_probability_declared_fallback(origin):
    r=[]
    for n in range(60):
        d=(datetime(2026,1,1)+timedelta(days=n)).date().isoformat()
        r.append(dict(record_id=d+'/T1/'+origin,horizon='T1',target_business_date=d,eligibility=iso(eligibility(d)),issue=d+'T00:00:00+00:00',origin=origin,residual=(-1)**n))
    s=dict(cutoff='2026-09-01T00:00:00+00:00',residuals=r,cases=[])
    p,_=forward(s,'T1','2026-10-03','2026-10-02T13:00:00+00:00',{'ML':30,'RAW':30,'MOS':30})
    assert p['status']=='FALLBACK' and p['origin']==origin and abs(sum(p['pmf'])-1)<1e-10

@pytest.mark.parametrize('n',[0,23,24])
def test_settlement_complete_gate(db,n):
    start=utc('2026-10-01T00:00:00+08:00')
    for i in range(n):archive_metar(db,[metar(start+timedelta(hours=i))],start+timedelta(hours=i,minutes=1))
    result=settle(db,start+timedelta(days=1))
    assert (result[0]['settlement_status']=='FINAL') if n==24 else not result or result[0]['settlement_status']=='INCOMPLETE'

def test_settlement_conflict_pending(db):
    start=utc('2026-10-01T00:00:00+08:00')
    for i in range(24):archive_metar(db,[metar(start+timedelta(hours=i))],start+timedelta(hours=i,minutes=1))
    obs=start+timedelta(hours=3)
    archive_metar(db,[metar(obs,'METAR COR ZUUU '+obs.strftime('%d%H%MZ')+' 00000MPS CAVOK 25/15 Q1013')],obs+timedelta(minutes=2))
    assert settle(db,start+timedelta(days=1))[0]['settlement_status']=='PENDING_VERSION_CONFLICT'

def test_late_correction_reopens_without_overwriting_final(db):
    start=utc('2026-10-01T00:00:00+08:00')
    for i in range(24):archive_metar(db,[metar(start+timedelta(hours=i))],start+timedelta(hours=i,minutes=1))
    assert settle(db,start+timedelta(days=1))[0]['settlement_status']=='FINAL'
    obs=start+timedelta(hours=3)
    archive_metar(db,[metar(obs,'METAR COR ZUUU '+obs.strftime('%d%H%MZ')+' 00000MPS CAVOK 25/15 Q1013')],start+timedelta(days=1,hours=1))
    assert settle(db,start+timedelta(days=1,hours=2))[0]['settlement_status']=='PENDING_AFTER_FINAL_DATA'
    assert [r['settlement_status'] for r in db.rows('daily_ground_truth')]==['FINAL','PENDING_AFTER_FINAL_DATA']
    assert not settle(db,start+timedelta(days=1,hours=3))

def test_clock_warning():
    with pytest.raises(ValueError,match='CLOCK_ERROR'):system_gate(config(),'1990-01-01T00:00:00+00:00')

@pytest.mark.parametrize('status,age,expected',[('ONLINE',0,False),('STALE',0,True),('ERROR',0,True),('ONLINE',10,True)])
def test_source_freshness_gate(db,status,age,expected):
    from src.realtime.engine import source_gate
    stamp=now()
    for s in ('ZUUU','ECMWF'):
        with db.c:db.insert('source_health',dict(source=s,current_status=status,last_new_data_time=iso(stamp-timedelta(hours=age))))
    degraded,health=source_gate(db,config(),now())
    assert degraded==expected and set(health)=={'ZUUU','ECMWF'}

def test_disk_critical(monkeypatch):
    import src.realtime.engine as module
    from collections import namedtuple
    monkeypatch.setattr(module.shutil,'disk_usage',lambda p:namedtuple('Usage','total used free')(100,99,1))
    with pytest.raises(ValueError,match='DISK_CRITICAL'):system_gate(config(),'2026-10-02T00:00:00+00:00')

@pytest.mark.parametrize('scenario',['complete','newer_partial','future_receipt','future_run','missing_temperature'])
def test_complete_same_run_selection(scenario):
    day='2026-10-03';issue=utc('2026-10-02T13:00:00+00:00')
    start=utc(day+'T00:00:00+08:00');runs={};trajectories={}
    for n in range(2):
        run=utc('2026-10-02T00:00:00+00:00')+timedelta(hours=6*n)
        available=run+timedelta(hours=7)
        if n and scenario=='future_receipt':available=issue+timedelta(seconds=1)
        if n and scenario=='future_run':run=issue+timedelta(hours=1)
        rid=str(n);runs[iso(run)]=dict(run_time_utc=iso(run),source_available_time_utc=iso(available),canonical_raw_run_id=rid)
        trajectories[rid]={iso(start+timedelta(hours=h)):dict(temperature_2m_c=20+n,lead_hours=h) for h in range(24)}
        if n and scenario=='newer_partial':trajectories[rid].pop(iso(start))
        if n and scenario=='missing_temperature':trajectories[rid][iso(start)]['temperature_2m_c']=None
    selected,hours,lineage=selection(runs,trajectories,day,issue)
    assert selected['canonical_raw_run_id']==('1' if scenario=='complete' else '0')
    assert len(hours)==24 and len({r['temperature_2m_c'] for r in hours})==1

@pytest.mark.parametrize('offset',[0,1,2])
def test_forward_state_settlement_eligibility(db,offset):
    day='2026-10-01';predissue=utc('2026-09-30T13:00:00+00:00');check=utc('2026-10-02T00:00:00+08:00')+timedelta(days=offset)
    pmf=np.ones(161)/161
    feature={};continuous=dict(continuous_prediction_c=24.,raw_ecmwf_prediction=23.,mos_prediction=23.5)
    probability=dict(pmf=pmf.tolist(),variants=[dict(origin='ML',method='GAUSSIAN_EXPANDING',pmf=pmf.tolist())])
    db.snapshot('s',feature,continuous,probability,dict(horizon='T1',target_business_date=day,prediction_issue_time=iso(predissue),daily_anchor=True))
    with db.c:db.insert('daily_ground_truth',dict(business_date_bjt=day,daily_tmax_c=25,settlement_status='FINAL',settlement_time='2026-10-02T00:00:00+08:00',label_eligibility_time=iso(eligibility(day))),'gt')
    state=dict(cutoff=iso(predissue),residuals=[],cases=[]);history=[]
    changed=advance(db,state,history,check)
    assert changed==(offset>=1)
    assert len(state['residuals'])==(3 if offset>=1 else 0)
    if changed:
        assert state['residuals'][0]['residual']==1
        assert not advance(db,state,history,check+timedelta(hours=1))
        assert len(history)==1

def test_all_snapshots_evaluated_same_target(db):
    from src.audit.phase10_independent_acceptance import independent_scores
    p=np.ones(161)/161
    for n in range(2):
        db.snapshot(str(n),{},dict(continuous_prediction_c=24+n),dict(pmf=p.tolist()),dict(horizon='T1',target_business_date='2026-10-01',prediction_issue_time='2026-09-30T13:00:00+00:00'))
    with db.c:db.insert('daily_ground_truth',dict(business_date_bjt='2026-10-01',daily_tmax_c=25,settlement_status='FINAL',settlement_time='2026-10-02T00:00:00+08:00'),'gt')
    evaluate(db,'2026-10-02T00:00:00+08:00');evaluate(db,'2026-10-02T01:00:00+08:00')
    scores=db.rows('daily_evaluation');assert len(scores)==2
    for r in scores:
        assert all(abs(r[k]-v)<1e-10 for k,v in independent_scores(p,25).items())

def test_frozen_sources_unchanged():
    from src.realtime.guardian import fingerprints
    before=json.loads((ROOT/'docs/phase10/PHASE10_SOURCE_GUARDIAN_BEFORE.json').read_text(encoding='utf-8'))
    current=fingerprints()
    assert all(current.get(k)==v for k,v in before['sha256'].items())

def test_no_training_or_market_ast():
    import ast
    for p in (ROOT/'src/realtime').glob('*.py'):
        tree=ast.parse(p.read_text(encoding='utf-8'))
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('fit','partial_fit','fit_transform') for n in ast.walk(tree))
        assert 'market_price' not in p.read_text() and 'orderbook' not in p.read_text()

@pytest.mark.parametrize('h',['T1','T2'])
@pytest.mark.parametrize('index',range(6))
@pytest.mark.parametrize('part',['feature','continuous','probability'])
def test_frozen_golden_reproduction(h,index,part):
    import csv
    rows=list(csv.DictReader((ROOT/'docs/phase10/PHASE10_GOLDEN_REPRODUCTION.csv').open(encoding='utf-8')))
    r=[r for r in rows if r['horizon']==h][index]
    field={'feature':'feature_max_difference','continuous':'continuous_difference','probability':'pmf_max_difference'}[part]
    assert float(r[field])<1e-8 and r['PASS']=='True' and int(r['causality_violations'])==0
