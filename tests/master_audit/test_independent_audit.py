import hashlib
import math
import sqlite3
from datetime import timedelta
import numpy as np
import pytest
from src.audit.master.common import ROOT,OUT,digest,snapshot,stamp,eligible,scores
from src.audit.master.database_audit import daily,report_temperature,Source
from src.audit.master.model_audit import independent_transform,tree_value
from src.audit.master.probability_audit import distribution,calibration,METHODS

def test_upstream_ram_read_only():
    p=ROOT/'database/phase10_realtime_v1.db';before=digest(p);c=snapshot(p)
    assert c.execute('PRAGMA query_only').fetchone()[0]==1
    with pytest.raises(sqlite3.DatabaseError):c.execute('DELETE FROM realtime_events')
    c.close();assert digest(p)==before

def test_sha_stream(tmp_path):
    p=tmp_path/'bytes';p.write_bytes(b'causal audit');assert digest(p)==hashlib.sha256(b'causal audit').hexdigest()

def test_daily_independent_report_max():
    rows=[dict(id=i+1,bronze_raw_id=i+10,observation_time_utc=f'2026-01-01T{h:02d}:00:00+00:00',temperature_c=t) for i,(h,t) in enumerate([(0,4),(1,8),(2,8)])]
    result=daily(rows);assert result['daily_tmax_c']==8 and result['tmax_occurrence_count']==2 and result['hourly_coverage_count']==3

@pytest.mark.parametrize('text,value',[('METAR ZUUU 010000Z 00000MPS CAVOK 37/20 Q1013',37),('METAR COR ZUUU 010000Z 00000MPS M02/M04 Q1013',-2),('no temperature',None)])
def test_report_temperature(text,value):assert report_temperature(text)==value

def test_newest_legal_complete_issue_rule():
    s=Source();s.runs_available=[dict(run_time_utc=f'2026-01-01T{h:02d}:00:00+00:00',source_available_time_utc=f'2026-01-01T{h+1:02d}:00:00+00:00',canonical_raw_run_id=h) for h in (0,6,12)]
    start=stamp('2026-01-02T00:00:00+08:00');curve={(start+timedelta(hours=i)).isoformat():{'temperature_2m_c':20} for i in range(24)}
    s.curves={0:curve,6:dict(list(curve.items())[:14]),12:curve}
    assert s.choose('2026-01-02','2026-01-01T12:00:00+00:00')['canonical_raw_run_id']==0

def test_eligibility_bjt_d_plus_two():assert eligible('2026-01-01')==stamp('2026-01-03T00:00:00+08:00')

def test_naive_time_rejected():
    with pytest.raises(ValueError):stamp('2026-01-01T00:00:00')

def test_ridge_saved_preprocessing_math():
    p=dict(indices=[0,1],family='RIDGE',medians=[2,3],indicator_indices=[1],means=[1,1,0],scales=[2,2,1])
    z=independent_transform([5,np.nan],p);assert np.array_equal(z,[2,1,1]);assert 10+np.dot([1,2,3],z)==17

def test_tree_missing_branch():
    node=dict(split_feature=0,missing_type='NaN',default_left=True,threshold=2,decision_type='<=',left_child={'leaf_value':-1},right_child={'leaf_value':3})
    assert tree_value(node,[np.nan])==-1 and tree_value(node,[4])==3

@pytest.mark.parametrize('method',['GAUSSIAN_EXPANDING','EMPIRICAL_EXPANDING','KDE_EXPANDING_BW075'])
def test_distribution_mass_cdf(method):
    p=distribution(34.6,[-1,0,1]*30,method);assert np.all(p>=0) and np.all(p<=1) and abs(sum(p)-1)<1e-12
    for a in (1,.8,1.2):
        cp=calibration(p,a);assert abs(sum(cp)-1)<1e-12 and np.all(np.diff(np.cumsum(cp))>=0)

def test_independent_score_formulas():
    p=np.array([.25,.75]);sc=scores(p,1,0);assert sc['brier']==.125 and sc['crps']==.0625 and sc['logloss']==-math.log(.75) and sc['top1']==1

def test_protocol_tie_order():assert METHODS==['GAUSSIAN_EXPANDING','GAUSSIAN_90CAL','EMPIRICAL_EXPANDING','KDE_EXPANDING_BW075','GAUSSIAN_EXPANDING_CAL','GAUSSIAN_90CAL_CAL','EMPIRICAL_EXPANDING_CAL','KDE_EXPANDING_BW075_CAL']

def test_audit_output_scope():assert OUT.resolve().is_relative_to(ROOT.resolve()) and OUT.name=='master_audit_v1'

def test_audit_has_no_upstream_sql_writes():
    text=(ROOT/'src/audit/master/common.py').read_text(encoding='utf-8');assert "sqlite3.connect(':memory:')" in text and 'p.read_bytes()' in text

def test_structured_findings_severity():
    from src.audit.master.report import finding
    r=finding('TEST','HIGH',10,'lineage','title','description',{'asset':'simulation'},'correct','broken')
    assert all(k in r for k in ('finding_id','severity','phase','component','status','title','description','evidence','affected_asset','expected','actual','framework_reference','recommendation'))
    with pytest.raises(ValueError):finding('TEST','GREEN',10,'x','x','x',{},'x','x')
