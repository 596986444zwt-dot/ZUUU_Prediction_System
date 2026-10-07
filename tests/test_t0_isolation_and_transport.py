"""Real forward probability routines stay identical while T0 fails; HTTP edge evidence."""
import io
import json
import sqlite3
from http.client import IncompleteRead
from datetime import timedelta
import pytest
from src.realtime.t0.contracts import utc, iso, digest, canonical, load_config
from src.realtime.t0.source_adapter import Fetcher
from src.realtime.t0 import source_adapter, receipt_ledger
from src.realtime.t0.worker import Worker
from tests.test_t0_experimental_v1 import CUT, RUN, feed, forecast, metar, packet, basic, db, cfg
from src.realtime.t0.features import context


def test_repeated_weather_with_changed_generation_time_keeps_first_seen(db,cfg,monkeypatch):
    p=forecast();p['generationtime_ms']=1
    feed(db,monkeypatch,p,RUN+timedelta(hours=7),'ECMWF',{'run':iso(RUN),'models':'ecmwf_ifs025'})
    old=dict(db.c.execute('SELECT * FROM t0_ecmwf_receipt_ledger').fetchone())
    p['generationtime_ms']=2
    feed(db,monkeypatch,p,RUN+timedelta(hours=8),'ECMWF',{'run':iso(RUN),'models':'ecmwf_ifs025'})
    assert db.c.execute('SELECT COUNT(*) FROM t0_ecmwf_receipt_ledger').fetchone()[0]==1
    assert dict(db.c.execute('SELECT * FROM t0_ecmwf_receipt_ledger').fetchone())==old
    assert db.c.execute('SELECT COUNT(*) FROM t0_ecmwf_sighting').fetchone()[0]==2


def test_activity_database_outside_existing_phase10_guardian_glob():
    from src.realtime.t0.contracts import ROOT
    from src.realtime.t0.worker import paths
    target=paths(load_config())['database']
    assert target.parent != ROOT/'database'
    assert target not in list((ROOT/'database').glob('*.db'))


def test_receive_and_ingest_before_cutoff_but_publish_after_excluded(db,cfg,monkeypatch):
    basic(db,monkeypatch)
    envelope=dict(packet('ZUUU',[metar(CUT-timedelta(minutes=1),40)],CUT-timedelta(seconds=2)),
                  namespace='TEST_FIXTURE',capture_id='publication-boundary',capture_version='TEST')
    from src.realtime.t0.contracts import identity
    rid=identity(envelope)
    monkeypatch.setattr(receipt_ledger,'now',lambda:CUT-timedelta(seconds=1))
    receipt_ledger._archive_packet(db,rid,envelope)
    assert context(db,CUT,cfg)['Tmax_so_far']==24
    monkeypatch.setattr(receipt_ledger,'now',lambda:CUT+timedelta(seconds=1))
    receipt_ledger.archive_packet(db,rid,envelope)
    assert context(db,CUT,cfg)['Tmax_so_far']==24
    assert context(db,CUT+timedelta(seconds=2),cfg)['Tmax_so_far']==40


def test_prefixless_native_provider_speci_preserved(db,monkeypatch):
    member=metar(CUT-timedelta(hours=1),prefix='')
    member['metarType']='SPECI'
    feed(db,monkeypatch,[member],CUT-timedelta(minutes=50))
    row=db.c.execute('SELECT * FROM t0_zuuu_receipt_ledger').fetchone()
    assert row['message_class']=='SPECI' and row['is_speci']==1


def test_additive_publication_upgrade_never_changes_first_seen(db,cfg,monkeypatch):
    from src.realtime.t0.archive import Archive
    basic(db,monkeypatch)
    original=[tuple(r) for r in db.c.execute('SELECT * FROM t0_zuuu_receipt_ledger')]
    packets=[dict(r) for r in db.c.execute('SELECT * FROM t0_transport_receipt')]
    with db.c:
        for action in ('INSERT','UPDATE','DELETE'):
            db.c.execute('DROP TRIGGER guard_t0_receipt_publication_'+action)
        db.c.execute('DROP TABLE t0_receipt_publication')  # Reconstruct only the pre-release fixture schema.
    path=db.path;db.close();db.c=sqlite3.connect(':memory:')
    restarted=Archive(path,'TEST_FIXTURE')
    try:
        assert [tuple(r) for r in restarted.c.execute('SELECT * FROM t0_zuuu_receipt_ledger')]==original
        assert context(restarted,CUT,cfg)['Tmax_so_far'] is None
        monkeypatch.setattr(receipt_ledger,'now',lambda:CUT+timedelta(minutes=1))
        for r in packets:receipt_ledger.archive_packet(restarted,r['receipt_id'],json.loads(r['payload_json']))
        assert context(restarted,CUT,cfg)['Tmax_so_far'] is None
        assert context(restarted,CUT+timedelta(minutes=2),cfg)['Tmax_so_far']==24
        assert [tuple(r) for r in restarted.c.execute('SELECT * FROM t0_zuuu_receipt_ledger')]==original
    finally:restarted.close()


def test_new_legal_complete_run_switches_only_after_publication(db,cfg,monkeypatch):
    old=RUN-timedelta(hours=6)
    feed(db,monkeypatch,forecast(old),old+timedelta(hours=7),'ECMWF',{'run':iso(old),'models':'ecmwf_ifs025'})
    feed(db,monkeypatch,[metar(CUT-timedelta(hours=1))],CUT-timedelta(minutes=50))
    feed(db,monkeypatch,forecast(RUN),CUT+timedelta(minutes=1),'ECMWF',{'run':iso(RUN),'models':'ecmwf_ifs025'})
    assert context(db,CUT,cfg)['ecmwf_run_time']==iso(old)
    assert context(db,CUT+timedelta(minutes=2),cfg)['ecmwf_run_time']==iso(RUN)


def test_zero_forward_samples_visible_for_every_main_cutoff(db):
    from src.realtime.t0.evaluation import evaluate
    result=evaluate(db)
    for method in ('LEVEL0','L1_A'):
        rows=[r for r in result['metrics'] if r['method']==method and r['trigger_type']=='SCHEDULED' and r['group_type']=='cutoff']
        assert {r['group_label'] for r in rows}=={'08:00','10:00','12:00','14:00','16:00','18:00'}
        assert all(r['N']==0 and r['mae'] is None and r['top1_rate'] is None for r in rows)


@pytest.mark.parametrize('fault',['database_corrupt','collector_exception','prediction_exception'])
def test_t1_t2_real_probability_routines_unaffected(tmp_path,cfg,monkeypatch,fault):
    from src.realtime.handoff import forward
    from tests.test_phase10_ma_fixes import state
    frozen=state()
    args=[(h,'2026-10-05','2026-10-03T13:00:00+00:00',{'ML':30.,'RAW':31.,'MOS':None}) for h in ('T1','T2')]
    before=[forward(frozen,*item) for item in args]
    pristine=canonical(frozen)
    class Fail:
        def get(self,*args):raise OSError('synthetic collector failure')
    worker=Worker(cfg,fixture_root=tmp_path,fetcher=Fail())
    if fault=='database_corrupt':
        worker.paths['database'].write_bytes(b'fixture corrupt')
        with pytest.raises(sqlite3.DatabaseError):worker.cycle(CUT)
    elif fault=='collector_exception':
        with pytest.raises(OSError):worker.capture('ZUUU')
    else:
        import src.realtime.t0.worker as module
        monkeypatch.setattr(module,'due',lambda *args:(_ for _ in ()).throw(ValueError('synthetic prediction failure')))
        with pytest.raises(ValueError):worker.cycle(CUT)
        worker.db.close()
    after=[forward(frozen,*item) for item in args]
    assert before==after and canonical(frozen)==pristine


class Response:
    status=200
    def __init__(self,data,claimed_length=None,partial=False):
        self.data=io.BytesIO(data)
        self.headers={'Content-Length':str(len(data) if claimed_length is None else claimed_length)}
        self.partial=partial
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n):
        if self.partial:
            self.partial=False
            raise IncompleteRead(self.data.read(),100)
        return self.data.read(n)


@pytest.mark.parametrize('fault',['complete','length_mismatch','incomplete_read','oversize','timeout'])
def test_transport_evidence_boundaries(cfg,monkeypatch,fault):
    times=iter([CUT-timedelta(seconds=2),CUT])
    monkeypatch.setattr(source_adapter,'now',lambda:next(times))
    cfg['http_max_bytes']=4 if fault=='oversize' else 1000
    body=b'[{"test":true}]'
    def opener(request,timeout):
        assert 'ZUUU-T0-Experimental' in request.headers['User-agent']
        if fault=='timeout':raise TimeoutError('fixture timeout')
        return Response(body,claimed_length=100 if fault=='length_mismatch' else None,partial=fault=='incomplete_read')
    result=Fetcher(cfg,opener).get('ZUUU','https://fixture.invalid',{'ids':'ZUUU'})
    assert result['download_start_time']==iso(CUT-timedelta(seconds=2))
    assert result['download_complete_time']==iso(CUT)
    assert result['body_complete']==(fault=='complete')
    assert result['payload_sha256']==digest(bytes.fromhex(result['content_hex']))
    if fault=='incomplete_read':assert bytes.fromhex(result['content_hex'])==body
