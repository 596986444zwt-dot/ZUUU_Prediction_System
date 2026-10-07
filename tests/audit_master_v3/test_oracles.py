import sqlite3
from pathlib import Path
import pytest
import numpy as np
from src.audit.master_v3.common import stamp,eligible,snapshot,scores
from src.audit.master_v3.adversarial import ident,resolve,anchor_expected
from src.audit.master_v3.probability_audit import distribution,calibration
from src.audit.master_v3.database_audit import report_temperature,daily

@pytest.mark.parametrize('day,boundary',[('2024-02-28','2024-03-01T00:00:00+08:00'),('2024-12-31','2025-01-02T00:00:00+08:00'),('2025-01-31','2025-02-02T00:00:00+08:00')])
def test_eligibility(day,boundary):assert eligible(day)==stamp(boundary)
def test_timezone_cross_midnight():assert stamp('2026-10-02T16:00:00+00:00')==stamp('2026-10-03T00:00:00+08:00')
def test_naive_rejected():
    with pytest.raises(ValueError,match='NAIVE'):stamp('2026-10-03T00:00:00')
def test_alias_valid():
    c={'state_id':'c','residuals':[]};a={'state_id':'a','record_type':'LINEAGE_ALIAS','canonical_state_id':'c','canonical_payload_sha256':ident(c)}
    assert resolve({'a':a,'c':c},'a')[0]=='c'
def test_alias_cycle():
    rows={'a':dict(state_id='a',record_type='LINEAGE_ALIAS',canonical_state_id='b'),'b':dict(state_id='b',record_type='LINEAGE_ALIAS',canonical_state_id='a')}
    with pytest.raises(ValueError,match='CYCLE'):resolve(rows,'a')
def test_alias_missing():
    with pytest.raises(ValueError,match='MISSING'):resolve({},'a')
def test_alias_hash_wrong():
    rows={'a':dict(state_id='a',record_type='LINEAGE_ALIAS',canonical_state_id='c',canonical_payload_sha256='wrong'),'c':dict(state_id='c')}
    with pytest.raises(ValueError,match='HASH'):resolve(rows,'a')
@pytest.mark.parametrize('time,expected',[('20:30',False),('21:00',True),('21:15',True),('21:30',True),('21:45',True),('22:00',True)])
def test_anchor_first(time,expected):assert anchor_expected('2026-10-03T'+time+':00+08:00','T1','2026-10-04',set())==expected
def test_anchor_failure_and_horizon():
    seen=set();issue='2026-10-03T21:30:00+08:00'
    assert not anchor_expected(issue,'T1','2026-10-04',seen,False)
    assert anchor_expected(issue,'T1','2026-10-04',seen)
    assert not anchor_expected(issue,'T1','2026-10-04',seen)
    assert anchor_expected(issue,'T2','2026-10-05',seen)
@pytest.mark.parametrize('method',['GAUSSIAN_EXPANDING','GAUSSIAN_90CAL','EMPIRICAL_EXPANDING','KDE_EXPANDING_BW075'])
def test_pmf(method):
    p=distribution(34.6,[-2,-1,0,1,2]*20,method);c=np.cumsum(p)
    assert p.min()>0 and abs(p.sum()-1)<1e-10 and np.all(np.diff(c)>=0)
    q=calibration(p,.8);assert q.min()>0 and abs(q.sum()-1)<1e-10
def test_score_perfect():
    p=np.zeros(161);p[110]=1;s=scores(p,30)
    assert s['brier']==s['logloss']==s['crps']==0 and s['top3']==1
def test_score_uniform():
    p=np.ones(161)/161;s=scores(p,30)
    assert abs(s['brier']-(1-1/161))<1e-10 and abs(s['logloss']-np.log(161))<1e-10
def test_read_only(tmp_path):
    p=tmp_path/'input.db';c=sqlite3.connect(p);c.execute('CREATE TABLE t (i INTEGER)');c.execute('INSERT INTO t VALUES (1)');c.commit();c.close();before=p.read_bytes()
    r=snapshot(p)
    with pytest.raises(sqlite3.DatabaseError):r.execute('UPDATE t SET i=2')
    assert r.execute('SELECT i FROM t').fetchone()[0]==1;r.close();assert p.read_bytes()==before
@pytest.mark.parametrize('raw,temp',[('METAR ZUUU 170500Z 00000MPS CAVOK 37/20 Q1013',37),('SPECI COR ZUUU 010000Z 00000MPS CAVOK M03/M05 Q1013',-3)])
def test_integer_parser(raw,temp):assert report_temperature(raw)==temp

def test_issue_selector_newest_complete_past_only():
    from src.audit.master_v3.database_audit import Source
    from src.audit.master_v3.common import BJT,UTC
    from datetime import datetime,timedelta
    s=Source();day='2026-10-04';start=datetime.fromisoformat(day).replace(tzinfo=BJT).astimezone(UTC)
    curve={(start+timedelta(hours=h)).isoformat():dict(temperature_2m_c=20+h/10) for h in range(24)}
    s.runs_available=[dict(run_time_utc='2026-10-02T00:00:00+00:00',source_available_time_utc='2026-10-02T06:00:00+00:00',canonical_raw_run_id='old'),dict(run_time_utc='2026-10-03T00:00:00+00:00',source_available_time_utc='2026-10-03T06:00:00+00:00',canonical_raw_run_id='new'),dict(run_time_utc='2026-10-03T12:00:00+00:00',source_available_time_utc='2026-10-03T19:00:00+00:00',canonical_raw_run_id='future')]
    s.curves={'old':curve,'new':dict(curve),'future':curve}
    assert s.choose(day,'2026-10-03T13:00:00+00:00')['canonical_raw_run_id']=='new'
    s.curves['new'].pop(next(iter(curve)))
    assert s.choose(day,'2026-10-03T13:00:00+00:00')['canonical_raw_run_id']=='old'

def test_daily_max_first_last_and_correction():
    rows=[dict(id=i+1,bronze_raw_id=i+10,observation_time_utc='2026-10-03T0'+str(i)+':00:00+00:00',temperature_c=v,message_class='COR' if i==2 else 'METAR') for i,v in enumerate([20,25,25])]
    result=daily(rows)
    assert result['daily_tmax_c']==25 and result['tmax_occurrence_count']==2 and result['tmax_has_correction']==1
    assert result['first_tmax_time_bjt'].endswith('09:00:00+08:00') and result['last_tmax_time_bjt'].endswith('10:00:00+08:00')

def test_ridge_arithmetic_preserves_missing_mask():
    from src.audit.master_v3.model_audit import independent_transform
    prep=dict(indices=[0,1],family='RIDGE',medians=[10,5],indicator_indices=[1],means=[10,5,0],scales=[2,1,1])
    z=independent_transform([12,float('nan')],prep)
    assert np.array_equal(z,[1,0,1])
    assert 3+np.dot([2,4,-1],z)==4

def test_alias_wrong_state_identity():
    with pytest.raises(ValueError):resolve({'c':dict(state_id='different')},'c')
