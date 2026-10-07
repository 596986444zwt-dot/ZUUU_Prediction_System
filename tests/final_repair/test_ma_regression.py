import json
from datetime import timedelta
from unittest.mock import patch
import pytest
from src.realtime.archive import Archive
from src.realtime.engine import Engine
from src.realtime.contracts import ROOT, config, utc
from src.audit.master.common import snapshot, payloads, digest
from tests.test_phase10_ma_fixes import state
from tests.final_repair.fixtures import lawful


SCENARIOS = [(t+' first', [(t,())], False) for t in ('20:30','21:00','21:15','21:30','21:45','22:00')]
SCENARIOS += [(a+' -> '+b, [(a,()),(b,())], False) for a,b in
              [('21:00','21:15'),('21:00','21:45'),('21:30','21:45'),('21:30','22:00'),('21:45','22:00'),('20:30','21:00')]]
SCENARIOS += [('failed attempts then success',[('21:00',('T1','T2')),('21:15',('T1','T2')),('21:30',()),('21:45',())],False),
              ('T1 succeeds T2 fails independently',[('21:00',('T2',)),('21:15',()),('21:45',())],False),
              ('T2 succeeds T1 fails independently',[('21:00',('T1',)),('21:15',()),('21:45',())],False),
              ('degraded true anchor',[('21:30',())],True)]


@pytest.mark.parametrize('name,events,degraded', SCENARIOS)
def test_ma002_original_v3_matrix(tmp_path,name,events,degraded):
    a=Archive(tmp_path/'anchor.db','SIMULATION',create=True)
    s=state();s['state_id']='canonical'; fail=set()
    class Fixed:
        def predict(self,h,*args):
            if h in fail:raise ValueError('INJECTED_MODEL_FAILURE')
            return 30.125,next(r for r in a.rows('model_registry') if r['horizon']==h)
    def feature(day,h,issue,*args):return lawful(a,h,issue,state=s,value=30.125)[0]
    e=Engine.__new__(Engine);e.db=a;e.cfg=config();e.state=s;e.history=[];e.models=Fixed()
    expected=[];anchors=set()
    try:
        with patch('src.realtime.engine.runtime_context',return_value=({},{})),patch('src.realtime.engine.construct',side_effect=feature),\
             patch('src.realtime.engine.source_gate',return_value=(degraded,{})),\
             patch('src.realtime.engine.system_gate',return_value=('ENGINE_HEALTHY',10**12)):
            for n,(time, failures) in enumerate(events):
                fail=set(failures);issue='2026-10-03T'+time+':00+08:00'
                e.predict_event(dict(event_id=str(n),event_time=issue,status='PENDING'))
                for h in ('T1','T2'):
                    if h in fail:continue
                    anchor=time>='21:00' and h not in anchors
                    if anchor:anchors.add(h)
                    expected.append((h,anchor,'DEGRADED' if degraded or not anchor else 'OK',
                                     'OFF_FIXED_ISSUE_REGIME' if not anchor else 'SOURCE_DEGRADED' if degraded else None))
        rows=a.rows('prediction_snapshots')
        actual=[(r['horizon'],r['daily_anchor'],r['status'],r['reason_code']) for r in rows]
        assert sorted(actual)==sorted(expected)
        # Also compare to the original retained V3 oracle, without changing it.
        oracle=json.loads((ROOT/'docs/master_audit_v3/MA002_ANCHOR_STATE_MACHINE_AUDIT.json').read_text())
        original=next(r for r in oracle['scenarios'] if r['scenario']==name)
        assert len(rows)==original['expected_count']
        assert sorted(actual)==sorted((r['horizon'],r['daily_anchor'],r['status'],r['reason_code']) for r in original['snapshots'])
        m={r['_id']:r for r in a.rows('continuous_predictions')};p={r['_id']:r for r in a.rows('probability_predictions')}
        for h in ('T1','T2'):
            forecasts=[r for r in rows if r['horizon']==h]
            assert all(m[r['continuous_id']]['continuous_prediction_c']==30.125 for r in forecasts)
            assert all(p[r['probability_id']]['pmf']==p[forecasts[0]['probability_id']]['pmf'] for r in forecasts)
    finally:a.close()


def test_ma001_all_production_aliases_readonly():
    path=ROOT/'database/phase10_realtime_v1.db';before=digest(path);c=snapshot(path)
    try:
        states={r['_id']:r for r in payloads(c,'realtime_probability_state')}
        predictions={r['_id']:r for r in payloads(c,'realtime_probability_predictions')}
        rows=payloads(c,'realtime_prediction_snapshots');assert len(rows)==34
        def resolve(key):
            seen=set()
            while True:
                assert key not in seen;seen.add(key)
                s=states[key];assert s['state_id']==key
                if s.get('record_type')!='LINEAGE_ALIAS':return key
                key=s['canonical_state_id']
        for r in rows:
            assert resolve(r['probability_state_version'])==resolve(predictions[r['probability_id']]['probability_state_version'])
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    finally:c.close()
    assert digest(path)==before
