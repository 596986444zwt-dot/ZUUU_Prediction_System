"""Adversarial fixture-only tests. No synthetic receipt may enter the forward DB."""
import copy
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from src.ecmwf_contract import HOURLY_VARIABLES, EXPECTED_UNITS
from src.realtime.t0.contracts import ROOT, utc, iso, identity, canonical, digest, load_config, integer_center
from src.realtime.t0.archive import Archive, TABLES
from src.realtime.t0 import receipt_ledger, settlement, snapshot
from src.realtime.t0.receipt_ledger import persist, recover, archive_packet
from src.realtime.t0.features import context
from src.realtime.t0.methods import calculate
from src.realtime.t0.snapshot import save
from src.realtime.t0.scheduler import due
from src.realtime.t0.evaluation import evaluate
from src.realtime.t0.worker import Worker, InstanceLock, paths

RUN = utc('2026-10-01T12:00:00+00:00')
CUT = utc('2026-10-02T06:00:00+00:00')


@pytest.fixture
def db(tmp_path):
    archive = Archive(tmp_path/'fixture.db', 'TEST_FIXTURE', create=True)
    yield archive
    archive.close()


@pytest.fixture
def cfg():
    config = load_config()
    config['namespace'] = 'TEST_FIXTURE'
    return config


def metar(obs, temperature=24, prefix='METAR', extra=''):
    obs = utc(obs)
    value = f'M{abs(temperature):02}' if temperature<0 else f'{temperature:02}'
    return dict(icaoId='ZUUU',obsTime=obs.timestamp(),
                rawOb=f'{prefix} ZUUU {obs:%d%H%MZ} 00000MPS CAVOK {value}/15 Q1013 {extra}'.strip(),
                receiptTime=iso(obs+timedelta(minutes=1)))


def forecast(run=RUN, peak=30):
    hourly={'time':[(run+timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in range(72)]}
    for key in HOURLY_VARIABLES:
        hourly[key]=[20. if key=='temperature_2m' else None for _ in range(72)]
    hour=int((utc('2026-10-02T08:00:00+00:00')-run).total_seconds()/3600)
    if 0<=hour<72:
        hourly['temperature_2m'][hour]=float(peak)
    return dict(utc_offset_seconds=0,hourly=hourly,hourly_units=EXPECTED_UNITS.copy())


def packet(source,payload,seen,params=None,complete=True):
    content=json.dumps(payload).encode()
    return dict(source=source,endpoint='fixture://'+source,request_params=params or {},
                download_start_time=iso(utc(seen)-timedelta(seconds=1)),download_complete_time=iso(seen),
                body_complete=complete,http_status=200 if complete else None,response_headers={},
                payload_sha256=digest(content),content_hex=content.hex(),error=None)


def feed(db,monkeypatch,payload,seen,source='ZUUU',params=None):
    seen=utc(seen)
    monkeypatch.setattr(receipt_ledger,'now',lambda:seen+timedelta(seconds=1))
    envelope=dict(packet(source,payload,seen,params),namespace='TEST_FIXTURE',capture_id=identity({'seen':iso(seen),'payload':payload}),
                  capture_version='TEST_FIXTURE_ONLY')
    rid=identity(envelope)
    return archive_packet(db,rid,envelope)


def basic(db,monkeypatch):
    feed(db,monkeypatch,forecast(),RUN+timedelta(hours=7),source='ECMWF',params={'run':iso(RUN),'models':'ecmwf_ifs025'})
    feed(db,monkeypatch,[metar(CUT-timedelta(hours=1))],CUT-timedelta(minutes=50))


def test_level0_l1_math(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    features=context(db,CUT,cfg)
    outputs={r['method']:r for r in calculate(features,CUT)}
    assert features['current_bias']==4
    assert outputs['LEVEL0']['prediction']==30
    assert outputs['L1_A']['prediction']==34
    assert features['remaining_day_max']==30


def test_hard_floor_both_methods(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    feed(db,monkeypatch,[metar(CUT-timedelta(hours=2),temperature=39)],CUT-timedelta(minutes=40))
    outputs=calculate(context(db,CUT,cfg),CUT)
    assert all(r['prediction']==39 and r['integer_prediction']==39 for r in outputs)


@pytest.mark.parametrize('prefix,cor,speci',[('METAR',0,0),('SPECI',0,1),('METAR COR',1,0),('SPECI COR',1,1),('COR',1,0),('',0,0)])
def test_native_message_classes(db,monkeypatch,prefix,cor,speci):
    feed(db,monkeypatch,[metar(CUT-timedelta(hours=1),prefix=prefix)],CUT-timedelta(minutes=50))
    r=db.c.execute('SELECT * FROM t0_zuuu_receipt_ledger').fetchone()
    assert r['is_cor']==cor and r['is_speci']==speci and r['qc_status']=='VALID'


def test_duplicate_preserves_first_seen_and_payload(db,monkeypatch):
    msg=metar(CUT-timedelta(hours=1))
    feed(db,monkeypatch,[msg],CUT-timedelta(minutes=50))
    first=dict(db.c.execute('SELECT * FROM t0_zuuu_receipt_ledger').fetchone())
    feed(db,monkeypatch,[msg,msg],CUT-timedelta(minutes=40))
    assert dict(db.c.execute('SELECT * FROM t0_zuuu_receipt_ledger').fetchone())==first
    assert db.c.execute('SELECT COUNT(*) FROM t0_zuuu_sighting').fetchone()[0]==3


def test_cor_later_does_not_change_old_snapshot(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    rid,_=save(db,CUT,'SCHEDULED',cfg)
    old=dict(db.get('t0_prediction_snapshot','prediction_id',rid))
    corrected=metar(CUT-timedelta(hours=1),temperature=28,prefix='METAR COR')
    feed(db,monkeypatch,[corrected],CUT+timedelta(minutes=19))
    assert context(db,CUT,cfg)['latest_temperature_c']==24
    assert context(db,CUT+timedelta(minutes=20),cfg)['latest_temperature_c']==28
    assert db.get('t0_prediction_snapshot','prediction_id',rid)==old
    assert db.c.execute('SELECT COUNT(*) FROM t0_zuuu_receipt_ledger').fetchone()[0]==2


def test_out_of_order_observation_retrieval(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    feed(db,monkeypatch,[metar(CUT-timedelta(hours=3),temperature=35)],CUT-timedelta(minutes=30))
    features=context(db,CUT,cfg)
    assert features['latest_temperature_c']==24 and features['Tmax_so_far']==35


def test_future_receipt_excluded(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    feed(db,monkeypatch,[metar(CUT-timedelta(minutes=1),temperature=40)],CUT+timedelta(seconds=1))
    assert context(db,CUT,cfg)['Tmax_so_far']==24


def test_received_before_cutoff_ingested_after_excluded(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    p=packet('ZUUU',[metar(CUT-timedelta(minutes=1),temperature=40)],CUT-timedelta(seconds=1))
    p.update(namespace='TEST_FIXTURE',capture_id='late-ingest',capture_version='TEST')
    monkeypatch.setattr(receipt_ledger,'now',lambda:CUT+timedelta(seconds=1))
    archive_packet(db,identity(p),p)
    assert context(db,CUT,cfg)['Tmax_so_far']==24


def test_future_observation_never_becomes_valid(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    feed(db,monkeypatch,[metar(CUT+timedelta(hours=1),temperature=45)],CUT-timedelta(minutes=1))
    row=db.c.execute("SELECT * FROM t0_zuuu_receipt_ledger WHERE temperature_c=45").fetchone()
    assert row['qc_status']=='FUTURE_OBSERVATION'
    assert context(db,CUT+timedelta(hours=2),cfg)['Tmax_so_far']==24


@pytest.mark.parametrize('fault',['future_run','partial_hours','temperature_null','invalid_unit','wrong_product'])
def test_illegal_ecmwf_never_selected(db,cfg,monkeypatch,fault):
    basic(db,monkeypatch)
    run=RUN+timedelta(hours=6);seen=CUT-timedelta(minutes=10)
    if fault=='future_run':run=CUT+timedelta(hours=6)
    p=forecast(run)
    if fault=='partial_hours':p['hourly']['time'].pop()
    if fault=='temperature_null':p['hourly']['temperature_2m'][24]=None
    if fault=='invalid_unit':p['hourly_units']['temperature_2m']='K'
    params={'run':iso(run),'models':'wrong' if fault=='wrong_product' else 'ecmwf_ifs025'}
    feed(db,monkeypatch,p,seen,'ECMWF',params)
    assert context(db,CUT,cfg)['ecmwf_run_time']==iso(RUN)
    assert db.c.execute("SELECT COUNT(*) FROM t0_ecmwf_receipt_ledger WHERE availability_status='QUARANTINED'").fetchone()[0]==1


def test_ecmwf_future_first_seen_not_selected(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    run=RUN+timedelta(hours=6)
    feed(db,monkeypatch,forecast(run),CUT+timedelta(minutes=1),'ECMWF',{'run':iso(run),'models':'ecmwf_ifs025'})
    assert context(db,CUT,cfg)['ecmwf_run_time']==iso(RUN)
    # It starts 02 BJT and fails full-day coverage even after legal availability.
    assert context(db,CUT+timedelta(minutes=2),cfg)['ecmwf_run_time']==iso(RUN)


def test_new_same_run_version_only_after_actual_arrival(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    old=context(db,CUT,cfg)['ecmwf_run_id']
    p=forecast(peak=33)
    feed(db,monkeypatch,p,CUT+timedelta(minutes=1),'ECMWF',{'run':iso(RUN),'models':'ecmwf_ifs025'})
    assert context(db,CUT,cfg)['ecmwf_run_id']==old
    assert context(db,CUT+timedelta(minutes=2),cfg)['ecmwf_run_id']!=old


def test_snapshot_complete_and_idempotent(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    rid,added=save(db,CUT,'EVENT',cfg)
    assert added and save(db,CUT,'EVENT',cfg)==(rid,False)
    assert db.c.execute('SELECT COUNT(*) FROM t0_prediction_method_output').fetchone()[0]==2
    body=json.loads(db.get('t0_prediction_snapshot','prediction_id',rid)['payload_json'])
    assert body['experiment_status']=='T0_EXPERIMENTAL' and body['validation']=='FORWARD_VALIDATION'
    assert body['ecmwf_first_seen_time']<=body['data_cutoff_time']


@pytest.mark.parametrize('fault',['future_obs','future_run','fake_bias','fake_floor','missing_input'])
def test_synthetic_future_leakage_or_tampered_context(db,cfg,monkeypatch,fault):
    basic(db,monkeypatch)
    features=context(db,CUT,cfg)
    if fault=='future_obs':features['observations'][0]['first_seen_time']=iso(CUT+timedelta(seconds=1))
    elif fault=='future_run':features['ecmwf_first_seen_time']=iso(CUT+timedelta(seconds=1))
    elif fault=='fake_bias':features['current_bias']=100
    elif fault=='fake_floor':features['Tmax_so_far']=0
    else:features['observation_ids']=[]
    with pytest.raises(ValueError,match='SNAPSHOT_CONTEXT_TAMPERED'):
        save(db,CUT,'EVENT',cfg,expected_context=features)
    assert db.c.execute('SELECT COUNT(*) FROM t0_prediction_snapshot').fetchone()[0]==0


@pytest.mark.parametrize('stage',['SNAPSHOT','LEVEL0','L1_A'])
def test_snapshot_atomic_failure(db,cfg,monkeypatch,stage):
    basic(db,monkeypatch)
    with pytest.raises(RuntimeError):save(db,CUT,'EVENT',cfg,fail_after=stage)
    assert all(db.c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]==0 for t in
               ['t0_prediction_snapshot','t0_prediction_method_output','t0_snapshot_observation'])


def test_restart_keeps_first_seen(db,tmp_path,monkeypatch):
    msg=metar(CUT-timedelta(hours=1));seen=CUT-timedelta(minutes=50)
    feed(db,monkeypatch,[msg],seen)
    path=db.path;first=dict(db.c.execute('SELECT * FROM t0_zuuu_receipt_ledger').fetchone())
    db.close();db.c=sqlite3.connect(':memory:')  # Fixture teardown remains safe.
    reopened=Archive(path,'TEST_FIXTURE')
    try:
        feed(reopened,monkeypatch,[msg],seen+timedelta(hours=1))
        assert dict(reopened.c.execute('SELECT * FROM t0_zuuu_receipt_ledger').fetchone())==first
    finally:reopened.close()


def test_durable_spool_restart_and_duplicate_recovery(db,tmp_path,monkeypatch):
    seen=CUT-timedelta(minutes=50);p=packet('ZUUU',[metar(CUT-timedelta(hours=1))],seen)
    rid=persist(tmp_path/'spool',p,'TEST_FIXTURE')
    monkeypatch.setattr(receipt_ledger,'now',lambda:seen+timedelta(minutes=2))
    assert recover(db,tmp_path/'spool')['ZUUU']==1
    assert recover(db,tmp_path/'spool')['ZUUU']==0
    r=db.c.execute('SELECT * FROM t0_zuuu_receipt_ledger').fetchone()
    assert r['first_seen_time']==iso(seen) and r['ingest_time']==iso(seen+timedelta(minutes=2))
    assert (tmp_path/'spool'/f'{rid}.json').exists()


def test_crash_after_pending_fsync_recovers_original_time(db,tmp_path,monkeypatch):
    seen=CUT-timedelta(minutes=50);folder=tmp_path/'spool'
    rid=persist(folder,packet('ZUUU',[metar(CUT-timedelta(hours=1))],seen),'TEST_FIXTURE')
    (folder/(rid+'.json')).rename(folder/(rid+'.pending'))
    monkeypatch.setattr(receipt_ledger,'now',lambda:seen+timedelta(hours=1))
    assert recover(db,folder)['ZUUU']==1
    assert db.c.execute('SELECT first_seen_time FROM t0_zuuu_receipt_ledger').fetchone()[0]==iso(seen)


def test_poison_member_does_not_drop_next_valid_record(db,monkeypatch):
    assert feed(db,monkeypatch,['bad',metar(CUT-timedelta(hours=1))],CUT-timedelta(minutes=50))['ZUUU']==1
    assert db.c.execute("SELECT COUNT(*) FROM t0_worker_event WHERE kind='QUARANTINED'").fetchone()[0]==1


def test_partial_download_keeps_transport_not_forecast(db,tmp_path,monkeypatch):
    seen=CUT-timedelta(minutes=50);p=packet('ECMWF',forecast(),seen,{'run':iso(RUN),'models':'ecmwf_ifs025'},complete=False)
    persist(tmp_path/'spool',p,'TEST_FIXTURE');monkeypatch.setattr(receipt_ledger,'now',lambda:seen+timedelta(seconds=1))
    assert recover(db,tmp_path/'spool')['ECMWF']==0
    assert db.c.execute('SELECT COUNT(*) FROM t0_transport_receipt').fetchone()[0]==1
    assert db.c.execute('SELECT COUNT(*) FROM t0_ecmwf_receipt_ledger').fetchone()[0]==0


def test_spool_hash_tamper_and_poison_packet_quarantined(db,tmp_path,monkeypatch):
    folder=tmp_path/'spool';folder.mkdir()
    (folder/'bad.json').write_text('{}')
    seen=CUT-timedelta(minutes=50)
    persist(folder,packet('ZUUU',[metar(CUT-timedelta(hours=1))],seen),'TEST_FIXTURE')
    monkeypatch.setattr(receipt_ledger,'now',lambda:seen+timedelta(seconds=1))
    assert recover(db,folder)['ZUUU']==1
    assert (folder/'bad.json').exists()


def test_sqlite_lock_keeps_spool_for_retry(db,tmp_path,monkeypatch):
    seen=CUT-timedelta(minutes=50);folder=tmp_path/'spool'
    persist(folder,packet('ZUUU',[metar(CUT-timedelta(hours=1))],seen),'TEST_FIXTURE')
    monkeypatch.setattr(receipt_ledger,'now',lambda:seen+timedelta(seconds=1))
    other=sqlite3.connect(db.path);other.execute('BEGIN IMMEDIATE');db.c.execute('PRAGMA busy_timeout=1')
    try:
        with pytest.raises(sqlite3.OperationalError):recover(db,folder)
    finally:other.rollback();other.close()
    assert recover(db,folder)['ZUUU']==1


@pytest.mark.parametrize('x,expected',[(-2.5,-2),(-2.51,-3),(-.5,0),(.49,0),(.5,1),(24.5,25),(24.499,24)])
def test_integer_mapping(x,expected):assert integer_center(x)==expected


def full_day(db,monkeypatch):
    start=utc('2026-10-01T16:00:00+00:00')
    records=[metar(start+timedelta(hours=h),24) for h in range(24)]
    feed(db,monkeypatch,records,utc('2026-10-03T00:00:00+00:00'))


def test_settlement_all_predictions_and_evaluation(db,cfg,monkeypatch):
    basic(db,monkeypatch);save(db,CUT,'SCHEDULED',cfg);save(db,CUT+timedelta(minutes=1),'EVENT',cfg)
    full_day(db,monkeypatch);issue=utc('2026-10-04T00:00:00+00:00')
    monkeypatch.setattr(settlement,'now',lambda:issue)
    assert settlement.settle(db,issue,cfg)==5
    assert settlement.settle(db,issue,cfg)==0
    assert db.c.execute('SELECT COUNT(*) FROM t0_settlement').fetchone()[0]==4
    evaluation=evaluate(db)
    assert evaluation['N_settled_method_outputs']==4
    assert {r['trigger_type'] for r in evaluation['metrics']}=={'EVENT','SCHEDULED'}
    level0=next(r for r in evaluation['metrics'] if r['method']=='LEVEL0' and r['group_type']=='overall' and r['trigger_type']=='SCHEDULED')
    assert level0['N']==1 and level0['mae']==6 and level0['bias']==6 and level0['top1_rate']==0


def test_no_early_or_incomplete_settlement(db,cfg,monkeypatch):
    basic(db,monkeypatch);save(db,CUT,'SCHEDULED',cfg)
    assert settlement.settle(db,utc('2026-10-03T00:00:00+00:00'),cfg)==0
    issue=utc('2026-10-04T00:00:00+00:00');monkeypatch.setattr(settlement,'now',lambda:issue)
    settlement.settle(db,issue,cfg)
    assert db.c.execute('SELECT status FROM t0_ground_truth_evidence').fetchone()[0]=='INCOMPLETE'
    assert db.c.execute('SELECT COUNT(*) FROM t0_settlement').fetchone()[0]==0


def test_late_cor_reopens_truth_without_rewriting_scores(db,cfg,monkeypatch):
    basic(db,monkeypatch);rid,_=save(db,CUT,'SCHEDULED',cfg);full_day(db,monkeypatch)
    issue=utc('2026-10-04T00:00:00+00:00');monkeypatch.setattr(settlement,'now',lambda:issue)
    settlement.settle(db,issue,cfg)
    old=[tuple(r) for r in db.c.execute('SELECT * FROM t0_settlement')]
    feed(db,monkeypatch,[metar(CUT-timedelta(hours=1),temperature=25,prefix='METAR COR')],issue+timedelta(minutes=1))
    monkeypatch.setattr(settlement,'now',lambda:issue+timedelta(minutes=2))
    settlement.settle(db,issue+timedelta(minutes=2),cfg)
    assert [tuple(r) for r in db.c.execute('SELECT * FROM t0_settlement')]==old
    assert db.c.execute('SELECT status FROM t0_ground_truth_evidence ORDER BY rowid DESC LIMIT 1').fetchone()[0]=='PENDING_AFTER_CONFIRMED_DATA'
    assert evaluate(db)['N_settled_method_outputs']==0
    assert db.get('t0_prediction_snapshot','prediction_id',rid) is not None


def test_conflicting_versions_block_confirmation(db,cfg,monkeypatch):
    full_day(db,monkeypatch)
    feed(db,monkeypatch,[metar(CUT-timedelta(hours=1),temperature=26,prefix='METAR COR')],utc('2026-10-03T01:00:00+00:00'))
    issue=utc('2026-10-04T00:00:00+00:00');monkeypatch.setattr(settlement,'now',lambda:issue)
    settlement.settle(db,issue,cfg)
    assert db.c.execute('SELECT status FROM t0_ground_truth_evidence').fetchone()[0]=='PENDING_VERSION_CONFLICT'


@pytest.mark.parametrize('table',TABLES)
def test_all_tables_immutable_and_replace_resistant(db,cfg,monkeypatch,table):
    basic(db,monkeypatch);save(db,CUT,'EVENT',cfg);full_day(db,monkeypatch)
    issue=utc('2026-10-04T00:00:00+00:00');monkeypatch.setattr(settlement,'now',lambda:issue)
    settlement.settle(db,issue,cfg);evaluate(db);db.event('TEST',{'value':1})
    # Ensure snapshot link, source sightings, scores, state, metadata each have an existing row.
    row=db.c.execute(f'SELECT * FROM {table} LIMIT 1').fetchone();assert row is not None
    column=row.keys()[0]
    for sql,args in [(f'UPDATE {table} SET {column}={column}',()),(f'DELETE FROM {table}',()),
                     (f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' for _ in row)})",tuple(row))]:
        with pytest.raises(sqlite3.IntegrityError):db.c.execute(sql,args)
        db.c.rollback()


def test_morning_shadow_and_afternoon_not_champion(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    features=context(db,CUT,cfg)
    assert calculate(features,utc('2026-10-02T02:00:00+00:00'))[1]['candidate_status']=='EXPERIMENTAL_SHADOW'
    assert calculate(features,CUT)[1]['candidate_status']=='EXPERIMENTAL_ACTIVE_CANDIDATE'


def test_missing_both_methods_are_archived(db,cfg):
    rid,_=save(db,CUT,'SCHEDULED',cfg)
    assert db.get('t0_prediction_snapshot','prediction_id',rid)['status']=='MISSING_OBS'
    assert [r[0] for r in db.c.execute('SELECT prediction FROM t0_prediction_method_output')]==[None,None]


def test_missed_schedule_no_retroactive_forecasts(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    due(db,CUT+timedelta(minutes=10),cfg)
    assert db.c.execute('SELECT COUNT(*) FROM t0_prediction_snapshot').fetchone()[0]==4
    assert all(r[0] is None for r in db.c.execute('SELECT prediction FROM t0_prediction_method_output'))
    assert not due(db,CUT+timedelta(minutes=11),cfg)


def test_namespace_and_formal_paths_rejected(tmp_path,cfg):
    with pytest.raises(ValueError):Archive(tmp_path/'fixture.db','FORWARD_VALIDATION',create=True)
    with pytest.raises(ValueError):Archive(ROOT/'database/phase10_realtime_v1.db','TEST_FIXTURE',create=True)
    forward=load_config();forward['database']='database/phase10_realtime_v1.db'
    with pytest.raises(ValueError):paths(forward)
    with pytest.raises(ValueError):paths(cfg)


def test_collector_continues_with_t0_database_corrupt(tmp_path,cfg,monkeypatch):
    seen=CUT-timedelta(minutes=50)
    class Fake:
        def get(self,*args):return packet('ZUUU',[metar(CUT-timedelta(hours=1))],seen)
    worker=Worker(cfg,fixture_root=tmp_path,fetcher=Fake())
    worker.paths['database'].write_bytes(b'corrupt fixture only')
    with pytest.raises(sqlite3.DatabaseError):worker.open_database()
    assert worker.capture('ZUUU') and len(list(worker.paths['spool'].glob('*.json')))==1
    assert worker.paths['database'].read_bytes()==b'corrupt fixture only'
    assert worker.paths['database'] != ROOT/'database/phase10_realtime_v1.db'


def test_only_pure_upstream_dependencies():
    # Guard implementation dependency surface: no formal engine/model/archive import.
    import ast
    forbidden=('src.realtime.engine','src.realtime.archive','src.realtime.handoff','src.probability','src.models')
    for path in (ROOT/'src/realtime/t0').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node,ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(p) for p in forbidden)


def test_worker_restart_recovers_spool(tmp_path,cfg,monkeypatch):
    seen=CUT-timedelta(minutes=50);worker=Worker(cfg,fixture_root=tmp_path)
    persist(worker.paths['spool'],packet('ZUUU',[metar(CUT-timedelta(hours=1))],seen),'TEST_FIXTURE')
    monkeypatch.setattr(receipt_ledger,'now',lambda:seen+timedelta(seconds=1))
    assert worker.cycle(CUT)['ZUUU']==1
    first=worker.db.c.execute('SELECT first_seen_time FROM t0_zuuu_receipt_ledger').fetchone()[0]
    worker.db.close();worker.db=None
    replacement=Worker(cfg,fixture_root=tmp_path)
    assert replacement.cycle(CUT+timedelta(minutes=1))['ZUUU']==0
    assert replacement.db.c.execute('SELECT first_seen_time FROM t0_zuuu_receipt_ledger').fetchone()[0]==first
    replacement.db.close()


def test_single_instance_and_lock_release(tmp_path):
    with InstanceLock(tmp_path/'own.lock'):
        with pytest.raises(RuntimeError):
            with InstanceLock(tmp_path/'own.lock'):pass
    with InstanceLock(tmp_path/'own.lock'):pass


def test_sql_foreign_keys_and_integrity(db,cfg,monkeypatch):
    basic(db,monkeypatch);save(db,CUT,'EVENT',cfg)
    assert db.integrity()==dict(integrity=['ok'],foreign_keys=[])


@pytest.mark.parametrize('bad',['2026-10-02T06:00:00',datetime(2026,10,2,6)])
def test_naive_time_rejected(bad):
    with pytest.raises(ValueError):utc(bad)
