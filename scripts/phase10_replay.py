"""Explicit isolated historical simulation; never claims observed receipt time."""
import sys
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.realtime.contracts import *
from src.realtime.archive import Archive
from src.realtime.engine import Engine
from src.features.data import load
from src.data_v1.source_io import open_snapshot

def run(destination):
    require(not Path(destination).exists(),'REPLAY_OUTPUT_ALREADY_EXISTS')
    issue=utc('2026-08-18T13:00:00+00:00');source=load();a=Archive(destination,'SIMULATION',create=True)
    try:
        legal=sorted((r for r in source['runs'].values() if utc(r['source_available_time_utc'])<=issue),key=lambda r:r['run_time_utc'])[-8:]
        full={rid:dict(v) for rid,v in source['trajectories'].items()}
        for hours in source['hours'].values():
            for r in hours:full.setdefault(r['source_raw_run_id'],{})[r['target_time_utc']]=dict(r)
        with a.c:
            for r in legal:
                rid=str(r['canonical_raw_run_id']);virtual=r['source_available_time_utc']
                a.insert('ecmwf_raw_runs',dict(model=r['model'],run_time=r['run_time_utc'],actual_ingest_time=iso(now()),
                    source_available_time=None,replay_available_time=virtual,availability_basis='FROZEN_ESTIMATED_REPLAY_ONLY',
                    original_raw_run_id=r['canonical_raw_run_id'],source='FROZEN_SIMULATION'),rid)
                for n,row in enumerate(full[r['canonical_raw_run_id']].values()):
                    a.insert('ecmwf_hourly',dict(row,run_id=rid,actual_ingest_time=iso(now()),replay_available_time=virtual),rid+'/'+str(n))
        engine=Engine(a,config(),issue=issue)
        engine.cycle(issue=issue,network=False)
        snapshots=a.rows('prediction_snapshots');require(len(snapshots)==2,'REPLAY_PREDICTION_FAILURE')
        c=open_snapshot(ROOT/'database/phase8_machine_learning_v1.db');p8={r['record_id']:dict(r) for r in c.execute('SELECT * FROM phase8_prediction')};c.close()
        c=open_snapshot(ROOT/'database/phase9_probability_v1.db');p9={r['record_id']:r['pmf'] for r in c.execute('SELECT * FROM phase9_probability_mass')};c.close()
        ml={r['_id']:r for r in a.rows('continuous_predictions')};prob={r['_id']:r for r in a.rows('probability_predictions')}
        import numpy as np
        result=[]
        for s in snapshots:
            h=s['horizon'];day=s['target_business_date'];family='RIDGE' if h=='T1' else 'LIGHTGBM'
            err=abs(ml[s['continuous_id']]['continuous_prediction_c']-p8[day+'/'+h+'/'+family]['ml_prediction'])
            pe=float(np.max(np.abs(np.array(prob[s['probability_id']]['pmf'])-np.frombuffer(p9[day+'/'+h+'/ENGINE'],dtype='<f8'))))
            result.append(dict(namespace='SIMULATION',horizon=h,target=day,event_time=s['prediction_issue_time'],continuous_difference=err,pmf_difference=pe,
                               PASS=err<1e-8 and pe<1e-9,receipt_warning='Historical availability is frozen ESTIMATED; actual_ingest_time is real present replay loading time'))
        (ROOT/'docs/phase10/PHASE10_EVENT_REPLAY.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2));return result
    finally:a.close()

if __name__=='__main__':
    import tempfile
    with tempfile.TemporaryDirectory(prefix='phase10_replay_') as d:run(Path(d)/'simulation.db')
