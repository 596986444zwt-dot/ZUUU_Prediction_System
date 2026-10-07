import json
import sqlite3
from datetime import date, timedelta
from unittest.mock import patch
import pytest
from src.data_v1.source_io import open_snapshot
from src.intraday_discovery.contracts import (ROOT, REPORT_DIR, SOURCE_HASHES, BJT, utc, issue_time,
    peak_relation, LABEL_ONLY, FEATURE_ALLOWED, OBSERVATION_CANDIDATES, validate_feature_candidates,
    fingerprints, quantile)
from src.intraday_discovery.ecmwf import legal_candidates, inspect_trajectory
from src.audit.ecmwf_issue_rule_final_audit import choose_latest_complete

@pytest.mark.parametrize('hour,expected',[(6,'2025-01-01T22:00:00+00:00'),(8,'2025-01-02T00:00:00+00:00'),(17,'2025-01-02T09:00:00+00:00')])
def test_snapshot_timezone(hour,expected):
    assert issue_time('2025-01-02',hour).isoformat()==expected
    assert issue_time('2025-01-02',hour).astimezone(BJT).hour==hour

def test_arbitrary_minute_and_leap_day():
    assert issue_time('2024-02-29',6,37).isoformat()=='2024-02-28T22:37:00+00:00'

@pytest.mark.parametrize('delta,expected',[(-1,'PRE_PEAK'),(0,'EQUAL_PEAK'),(1,'POST_PEAK')])
def test_strict_peak_partition(delta,expected):
    peak=issue_time('2025-01-02',14)
    assert peak_relation(peak+timedelta(microseconds=delta),peak)==expected

def test_naive_times_rejected():
    with pytest.raises(RuntimeError,match='Naive'): utc('2025-01-01T00:00:00')

@pytest.mark.parametrize('name',list(SOURCE_HASHES))
def test_source_database_read_only(name):
    before=fingerprints()
    conn=open_snapshot(ROOT/'database'/name,SOURCE_HASHES[name])
    try:
        assert conn.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError): conn.execute('CREATE TABLE discovery_forbidden(x)')
    finally: conn.close()
    assert fingerprints()==before

def test_hash_mismatch_stops_before_discovery():
    with patch('src.intraday_discovery.contracts.sha256_file',return_value='0'*64):
        with pytest.raises(RuntimeError,match='STOP DISCOVERY'): fingerprints()

def run_record(raw_id,run,available):
    return dict(canonical_raw_run_id=raw_id,canonical_status='AVAILABLE',archive_status='FROZEN',
        _run=utc(run),_available=utc(available),run_time_utc=run,source_available_time_utc=available)

def test_future_vintage_and_available_time():
    cutoff=utc('2025-01-02T06:00:00+00:00')
    good=run_record(1,'2025-01-02T00:00:00+00:00',cutoff.isoformat())
    late=run_record(2,'2025-01-02T00:00:00+00:00',(cutoff+timedelta(microseconds=1)).isoformat())
    future=run_record(3,'2025-01-02T12:00:00+00:00',cutoff.isoformat())
    assert legal_candidates([late,good,future],cutoff)==[good]

def test_canonical_unavailable_blocked():
    r=run_record(1,'2025-01-01T00:00:00+00:00','2025-01-01T06:00:00+00:00')
    r['canonical_status']='SOURCE_TEMPERATURE_UNAVAILABLE'
    assert legal_candidates([r],issue_time('2025-01-02',10))==[]

def test_no_cross_run_stitching():
    cutoff=issue_time('2025-01-02',10)
    newer=run_record(2,'2025-01-01T12:00:00+00:00','2025-01-01T18:12:00+00:00')
    older=run_record(1,'2025-01-01T06:00:00+00:00','2025-01-01T12:12:00+00:00')
    start=issue_time('2025-01-02',0)
    hourly={2:{start+timedelta(hours=h):10. for h in range(12)},1:{start+timedelta(hours=h):10. for h in range(12,24)}}
    selected=choose_latest_complete([newer,older],hourly,date(2025,1,2),cutoff)
    assert selected[0]['canonical_raw_run_id']==2 and selected[2]==12

def test_newest_vs_newest_complete_are_separate():
    cutoff=issue_time('2025-01-02',10)
    newer=run_record(2,'2025-01-01T18:00:00+00:00','2025-01-02T00:12:00+00:00')
    older=run_record(1,'2025-01-01T12:00:00+00:00','2025-01-01T18:12:00+00:00')
    start=issue_time('2025-01-02',0)
    hourly={2:{start+timedelta(hours=h):10. for h in range(2,24)},1:{start+timedelta(hours=h):10. for h in range(24)}}
    legal=legal_candidates([older,newer],cutoff)
    assert legal[0]['canonical_raw_run_id']==2
    assert choose_latest_complete(legal,hourly,date(2025,1,2),cutoff)[0]['canonical_raw_run_id']==1

def test_cross_vintage_row_rejected():
    r=run_record(1,'2025-01-01T12:00:00+00:00','2025-01-01T18:12:00+00:00')
    hour=issue_time('2025-01-02',0)
    with pytest.raises(RuntimeError,match='Cross-vintage'):
        inspect_trajectory(r,{1:{hour:{'raw_run_id':2,'run_time_utc':r['run_time_utc']}}},'2025-01-02',issue_time('2025-01-02',10))

def test_labels_and_meteostat_blocked():
    validate_feature_candidates(FEATURE_ALLOWED)
    assert not LABEL_ONLY & FEATURE_ALLOWED
    for field in [*LABEL_ONLY,*OBSERVATION_CANDIDATES,'meteostat_temp','meteostat_rhum','meteostat_model_fill']:
        with pytest.raises(RuntimeError): validate_feature_candidates([field])

def test_ground_truth_integer_and_frozen():
    c=open_snapshot(ROOT/'database/zuuu_prediction.db',SOURCE_HASHES['zuuu_prediction.db'])
    try:
        rows=c.execute('SELECT daily_tmax_c,target_status,target_version FROM zuuu_target_v1').fetchall()
        assert len(rows)==729
        assert all(float(r[0]).is_integer() and r[1]=='FROZEN' and r[2]=='ZUUU_TARGET_V1' for r in rows)
    finally: c.close()

def test_delay_percentile_definition():
    assert quantile([0,10],.9)==9
    assert quantile([0,10,20],.5)==10

@pytest.fixture(scope='module')
def report():
    return json.loads((REPORT_DIR/'INTRADAY_AVAILABILITY_AUDIT.json').read_text(encoding='utf-8'))

def test_report_hashes_and_blocked_conclusion(report):
    assert report['sources_before']==report['sources_after']==fingerprints()
    assert report['admission']=='BLOCKED_FOR_INTRADAY_DATA_V1'
    assert report['meteostat_training']=='BLOCKED'
    assert not report['formal_intraday_database_created']
    assert not list((ROOT/'database').glob('*intraday*.db'))

def test_actual_peak_snapshot_and_coverage_counts(report):
    obs=report['observations']
    assert sum(r['days'] for r in obs['peak_distribution'])==729
    assert len(obs['snapshots'])==12
    assert all(r['PRE_PEAK']+r['EQUAL_PEAK']+r['POST_PEAK']==729 for r in obs['snapshots'])
    assert obs['legacy_awc_records_in_729_days']==0
    assert len(report['ecmwf']['coverage'])==24
    assert len(report['ecmwf']['selections'])==729*12*2
    for r in report['ecmwf']['selections']:
        if r['selected_run_time_utc']:
            assert utc(r['selected_run_time_utc'])<=utc(r['source_available_time_utc'])<=utc(r['issue_time_utc'])
            assert r['availability_semantics']=='ESTIMATED_DISSEMINATION_TIME_NOT_OBSERVED'
    assert report['ecmwf']['source_temperature_null_count']==504
