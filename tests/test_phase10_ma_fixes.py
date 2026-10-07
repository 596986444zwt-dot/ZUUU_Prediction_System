"""Regression for MA-001/002: immutable history and bit-identical numbers."""
import json
from datetime import datetime,timedelta
from unittest.mock import patch
import numpy as np
import pytest
from src.realtime.archive import Archive
from src.realtime.contracts import ROOT,iso,identity,eligibility
from src.realtime.handoff import forward
from src.realtime.lineage_repair import plan_aliases,append_aliases
from src.realtime.engine import Engine
from tests.final_repair.fixtures import lawful

@pytest.fixture
def db(tmp_path):
    a=Archive(tmp_path/'simulation.db','SIMULATION',create=True)
    yield a
    a.close()

def state():
    rs=[]
    for h in ('T1','T2'):
        for i in range(90):
            d=(datetime(2026,1,1)+timedelta(days=i)).date().isoformat()
            rs.append(dict(record_id=d+'/'+h+'/ML',horizon=h,target_business_date=d,issue=d+'T00:00:00+00:00',eligibility=iso(eligibility(d)),origin='ML',residual=float(i%3-1)))
    return dict(cutoff='2026-09-01T00:00:00+00:00',residuals=rs,cases=[],created_at='2026-09-02T00:00:00+00:00')

@pytest.mark.parametrize('h',['T1','T2'])
def test_state_id_only_changes_metadata(h):
    s=state();args=(h,'2026-10-05','2026-10-03T13:00:00+00:00',{'ML':30,'RAW':30,'MOS':None})
    old,variants=forward(s,*args);s['state_id']='canonical';new,newvariants=forward(s,*args)
    assert new['probability_state_version']=='canonical'
    assert {k:v for k,v in new.items() if k!='probability_state_version'}=={k:v for k,v in old.items() if k!='probability_state_version'}
    assert variants==newvariants and np.array_equal(new['pmf'],old['pmf'])

def legacy_snapshot(db):
    s=state();s['state_id']=identity(s)
    with db.c:db.insert('probability_state',s,s['state_id'])
    old=identity({k:v for k,v in s.items() if k!='created_at'})
    # Fixture represents existing immutable old-format records, not new snapshot entrypoint.
    p=dict(pmf=[.25,.75],continuous_prediction=20.5,probability_state_version=old)
    meta=dict(probability_id='old/P',probability_state_version=s['state_id'])
    with db.c:db.insert('probability_predictions',p,'old/P');db.insert('prediction_snapshots',meta,'old')
    return s,old

def test_legacy_reference_repair_append_only_idempotent(db):
    s,old=legacy_snapshot(db);before=db.c.execute("SELECT payload_json FROM realtime_probability_predictions WHERE record_id='old/P'").fetchone()[0]
    aliases=plan_aliases(db.c);assert append_aliases(db,aliases)==1 and append_aliases(db,plan_aliases(db.c))==0
    assert db.resolve_probability_state(old)['state_id']==s['state_id']
    assert db.latest('probability_state')['state_id']==s['state_id']
    assert db.c.execute("SELECT payload_json FROM realtime_probability_predictions WHERE record_id='old/P'").fetchone()[0]==before

def test_unproven_alias_is_rejected(db):
    s,old=legacy_snapshot(db)
    with db.c:db.insert('probability_predictions',dict(probability_state_version='fabricated'),'bad/P');db.insert('prediction_snapshots',dict(probability_id='bad/P',probability_state_version=s['state_id']),'bad')
    with pytest.raises(ValueError,match='LEGACY_STATE_FINGERPRINT'):plan_aliases(db.c)

def test_snapshot_state_mismatch_writes_nothing(db):
    s=state();s['state_id']='state'
    with db.c:db.insert('probability_state',s,'state')
    with pytest.raises(ValueError,match='REFERENCE_MISMATCH'):db.snapshot('bad',{}, {},{'probability_state_version':'wrong'},{'probability_state_version':'state'})
    assert not db.rows('feature_snapshots') and not db.rows('prediction_snapshots')

def test_snapshot_unresolved_state_writes_nothing(db):
    with pytest.raises(ValueError,match='UNRESOLVED'):db.snapshot('bad',{}, {},{'probability_state_version':'missing'},{'probability_state_version':'missing'})
    assert not db.rows('probability_predictions')

def test_alias_cycle_rejected(db):
    with db.c:
        db.insert('probability_state',dict(state_id='a',record_type='LINEAGE_ALIAS',canonical_state_id='b'),'a')
        db.insert('probability_state',dict(state_id='b',record_type='LINEAGE_ALIAS',canonical_state_id='a'),'b')
    with pytest.raises(ValueError,match='ALIAS_CYCLE'):db.resolve_probability_state('a')

@pytest.mark.parametrize('first_hour,first_minute,expected',[(21,0,'OK'),(21,30,'OK'),(22,0,'OK'),(20,30,'DEGRADED')])
def test_anchor_and_same_hour_refresh_numbers_unchanged(db,first_hour,first_minute,expected):
    cfg=json.loads((ROOT/'config/phase10_realtime_v1.json').read_text(encoding='utf-8'));s=state();s['state_id']='canonical'
    with db.c:db.insert('probability_state',s,s['state_id'])
    # R1 now requires complete legal inputs, including run/model lineage.
    # Keep every MA-002 status, numeric and PMF assertion unchanged.
    def legal_feature(day,h,issue,*args):
        return lawful(db,h,issue,state=s,value=30.125)[0]
    class Frozen:
        def predict(self,h,*args):
            row=next(r for r in db.rows('model_registry') if r['horizon']==h)
            return 30.125,row
    e=Engine.__new__(Engine);e.db=db;e.cfg=cfg;e.models=Frozen();e.history=[];e.state=s
    first=f'2026-10-03T{first_hour:02d}:{first_minute:02d}:00+08:00';second=f'2026-10-03T{first_hour:02d}:{first_minute+15:02d}:00+08:00'
    with patch('src.realtime.engine.runtime_context',return_value=({},{})),patch('src.realtime.engine.construct',side_effect=legal_feature),patch('src.realtime.engine.source_gate',return_value=(False,{})),patch('src.realtime.engine.system_gate',return_value=('ENGINE_HEALTHY',10**12)):
        for index,issue in enumerate((first,second)):e.predict_event(dict(event_id=str(index),event_time=issue,status='PENDING'))
    snapshots=db.rows('prediction_snapshots');assert len(snapshots)==4
    for h in ('T1','T2'):
        a,b=[r for r in snapshots if r['horizon']==h];assert a['status']==expected
        assert b['status']=='DEGRADED' and b['reason_code']=='OFF_FIXED_ISSUE_REGIME'
        cp={r['_id']:r for r in db.rows('continuous_predictions')};pp={r['_id']:r for r in db.rows('probability_predictions')}
        assert cp[a['continuous_id']]['continuous_prediction_c']==cp[b['continuous_id']]['continuous_prediction_c']==30.125
        assert np.array_equal(pp[a['probability_id']]['pmf'],pp[b['probability_id']]['pmf'])
        assert pp[a['probability_id']]['probability_state_version']=='canonical'
