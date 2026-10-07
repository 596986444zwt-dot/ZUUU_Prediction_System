import copy
import json
import sqlite3
import subprocess
import sys
from datetime import timedelta
import pytest
from src.realtime.archive import Archive
from src.realtime.contracts import iso, utc, config, ROOT
from src.realtime.collectors import archive_metar, poll_zuuu
from src.realtime.spool import save, recover, directory
from src.realtime.settlement import advance
from src.realtime.engine import Engine
from tests.final_repair.fixtures import lawful


@pytest.fixture
def db(tmp_path):
    a = Archive(tmp_path/'attack.db', 'SIMULATION', create=True)
    yield a
    a.close()


def metar(hour):
    obs = utc('2026-10-02T12:00:00+00:00')+timedelta(hours=hour)
    return dict(icaoId='ZUUU', obsTime=obs.timestamp(), rawOb='METAR ZUUU '+obs.strftime('%d%H%MZ')+' 00000MPS CAVOK 20/10 Q1013')


def anchor(db):
    parts = list(lawful(db))
    parts[2]['variants'] = [dict(parts[2], variants=[])]
    db.snapshot('anchor', *parts)
    with db.c: db.insert('daily_ground_truth', dict(business_date_bjt='2026-10-03', daily_tmax_c=22.,
        settlement_status='FINAL', settlement_time='2026-10-05T00:00:00+00:00',
        label_eligibility_time='2026-10-05T00:00:00+00:00'), 'truth')
    return dict(state_id='state', cutoff='2026-10-02T12:00:00+00:00', residuals=[], cases=[])


@pytest.mark.parametrize('failure', ['before_write', 'after_write', 'commit'])
def test_state_failure_retry_in_ordinary_cycle(db, monkeypatch, failure):
    state = anchor(db); history=[]; old=copy.deepcopy(state); before=db.semantic()
    original = db.insert
    def injected(table,*args,**kwargs):
        if table == 'probability_state' and failure == 'before_write': raise sqlite3.OperationalError('INJECTED_WRITE')
        result = original(table,*args,**kwargs)
        if table == 'probability_state' and failure == 'after_write': raise sqlite3.OperationalError('INJECTED_AFTER_WRITE')
        return result
    db.insert = injected
    if failure == 'commit':
        # Deferred FK failure occurs at COMMIT, after the state INSERT succeeded.
        db.c.execute('CREATE TABLE injected_commit_fault (id TEXT REFERENCES realtime_probability_state(record_id) DEFERRABLE INITIALLY DEFERRED)')
        db.c.execute("CREATE TRIGGER injected_state_commit AFTER INSERT ON realtime_probability_state BEGIN INSERT INTO injected_commit_fault VALUES ('missing'); END")
        db.c.commit()
    with pytest.raises(sqlite3.DatabaseError): advance(db,state,history,'2026-10-05T01:00:00+00:00')
    assert state == old and history == [] and db.semantic() == before
    assert db.resolve_probability_state('state') == dict(old, namespace='SIMULATION')
    db.insert = original
    if failure == 'commit': db.c.execute('DROP TRIGGER injected_state_commit'); db.c.commit()
    # Exercise ordinary cycle routing, with only network/prediction/backup adapters disabled.
    import src.realtime.engine as module
    e=Engine.__new__(Engine); e.db=db; e.cfg=config(); e.state=state; e.history=history; e.last_poll={}
    e.predict_event=lambda event: None; e.backup=lambda issue: None
    monkeypatch.setattr(module,'system_gate',lambda *args:('ENGINE_HEALTHY',10**12))
    e.cycle('2026-10-05T01:01:00+00:00',network=False)
    assert state['state_id'] != old['state_id']
    assert db.resolve_probability_state(state['state_id']) == dict(state, namespace='SIMULATION')
    assert len(state['residuals']) == 2 and len(state['cases']) == 1 and len(history) == 1
    assert len({r['record_id'] for r in state['residuals']}) == 2
    assert len({r['record_id'] for r in state['cases']}) == 1
    assert not advance(db,state,history,'2026-10-05T01:02:00+00:00')
    assert len(db.rows('probability_state')) == 2 and db.integrity() == (['ok'], [])


@pytest.mark.parametrize('members', [
    [0,'bad',1], ['bad',0,1], [0,1,'bad'], ['bad','bad'],
    [None,0,{},1], [0,{'rawOb':'garbage'},1]])
def test_mixed_batch_and_replay(db,members):
    payload=[metar(v) if isinstance(v,int) else 42 if v=='bad' else v for v in members]
    expected=sum(isinstance(v,int) for v in members)
    received=utc('2026-10-02T14:00:00+00:00')
    rid=save(db,'ZUUU',json.dumps(payload).encode(),received)
    assert recover(db) == expected and recover(db) == 0
    assert len(db.rows('zuuu_normalized')) == expected
    errors=db.rows('errors'); assert len(errors) == len(members)-expected
    assert all(r['status']=='QUARANTINED' and r['error_type'] and r['time'] and r['spool_id']==rid for r in errors)
    assert all('raw_id' in r or 'raw_payload_json' in r for r in errors)
    assert len(db.rows('engine_state')) == 1
    assert (directory(db)/(rid+'.json')).exists()


def test_two_poison_then_valid_recovery(db):
    received=utc('2026-10-02T14:00:00+00:00')
    for content in (b'[42]',b'[null]',b'{}',b'not json',json.dumps([metar(0)]).encode()):
        save(db,'ZUUU',content,received)
    assert recover(db) == 1
    for _ in range(3): assert recover(db) == 0
    assert len(db.rows('engine_state')) == 5 and len(db.rows('errors')) == 4
    assert len(db.rows('zuuu_normalized')) == 1
    assert len(list(directory(db).glob('*.json'))) == 5


def test_poll_valid_tail_after_bad_member(db):
    class Fake:
        def get(self,*args):
            return json.dumps([42,metar(0)]).encode(),utc('2026-10-02T14:00:00+00:00'),[
                dict(time='2026-10-02T14:00:00+00:00',received_time='2026-10-02T14:00:00+00:00',latency=0.,success=True)]
    assert poll_zuuu(db,Fake(),config()) == 1
    assert recover(db) == 0 and len(db.rows('zuuu_normalized')) == 1


def test_transient_failure_packet_not_terminal(db):
    received=utc('2026-10-02T14:00:00+00:00')
    save(db,'ZUUU',json.dumps([metar(0),42,metar(1)]).encode(),received)
    original=db.insert
    def fail(table,record,*args,**kwargs):
        if table=='zuuu_normalized' and record['observation_time'].endswith('13:00:00+00:00'):
            raise sqlite3.OperationalError('INJECTED_TRANSIENT')
        return original(table,record,*args,**kwargs)
    db.insert=fail
    with pytest.raises(sqlite3.OperationalError): recover(db)
    assert not db.rows('engine_state') and len(db.rows('zuuu_normalized')) == 1
    db.insert=original
    assert recover(db) == 1 and recover(db) == 0
    assert len(db.rows('zuuu_normalized')) == 2 and len(db.rows('errors')) == 1


def test_abrupt_process_crash_restart(tmp_path):
    path=tmp_path/'crash.db'; a=Archive(path,'SIMULATION',create=True)
    save(a,'ZUUU',json.dumps([metar(0),42,metar(1)]).encode(),utc('2026-10-02T14:00:00+00:00'));a.close()
    # Crash after inserting the second raw row, inside its transaction; first row is durable.
    code="""import os,sys
from src.realtime.archive import Archive
from src.realtime.spool import recover
a=Archive(sys.argv[1],'SIMULATION'); original=a.insert
def crash(table,record,*args,**kwargs):
    result=original(table,record,*args,**kwargs)
    if table=='zuuu_raw' and record['raw_payload']['rawOb'].find('021300Z')>=0: os._exit(78)
    return result
a.insert=crash; recover(a)
"""
    p=subprocess.run([sys.executable,'-B','-c',code,str(path)],cwd=ROOT,capture_output=True,text=True)
    assert p.returncode == 78, p.stderr
    a=Archive(path,'SIMULATION')
    try:
        assert len(a.rows('zuuu_normalized')) == 1 and not a.rows('engine_state')
        assert recover(a) == 1 and recover(a) == 0
        assert len(a.rows('zuuu_raw')) == len(a.rows('zuuu_normalized')) == 2
        assert len(a.rows('errors')) == 1 and a.integrity() == (['ok'], [])
    finally:a.close()
