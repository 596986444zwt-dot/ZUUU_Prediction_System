"""Separate T0 CLI; never invokes Phase10 engine, init, repair or state advancement."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.realtime.t0.contracts import load_config, now
from src.realtime.t0.worker import Worker, paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['start','stop','status','init','collect-once'])
    parser.add_argument('--seconds', type=int)
    args = parser.parse_args()
    config = load_config()
    locations = paths(config)
    if args.command=='stop':
        locations['runtime'].mkdir(parents=True, exist_ok=True)
        (locations['runtime']/'stop.request').write_text('GRACEFUL_T0_STOP', encoding='ascii')
        print('T0_STOP_REQUESTED_ONLY')
        return
    if args.command=='status':
        if not locations['database'].exists():
            print('T0_NOT_INITIALIZED')
            return
        c = sqlite3.connect(locations['database'].as_uri()+'?mode=ro', uri=True)
        c.row_factory = sqlite3.Row
        try:
            counts = {t:c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in
                      ['t0_transport_receipt','t0_zuuu_receipt_ledger','t0_ecmwf_receipt_ledger',
                       't0_prediction_snapshot','t0_prediction_method_output','t0_settlement']}
            last = c.execute('SELECT payload_json FROM t0_worker_event ORDER BY rowid DESC LIMIT 1').fetchone()
            outputs = [dict(r) for r in c.execute('SELECT * FROM t0_prediction_method_output ORDER BY rowid DESC LIMIT 2')]
            print(json.dumps(dict(status='T0_EXPERIMENTAL', validation='FORWARD_VALIDATION', counts=counts,
                                  latest_heartbeat=json.loads(last[0]) if last else None, latest_outputs=outputs,
                                  T0_FORMAL_MODEL_STATUS='NOT_AUTHORIZED',T0_PROBABILITY_STATUS='NOT_CALIBRATED',
                                  T0_CHAMPION_STATUS='NOT_AUTHORIZED'), ensure_ascii=False, indent=2))
        finally:
            c.close()
        return
    worker = Worker(config)
    if args.command=='start':
        worker.run(args.seconds)
    elif args.command=='init':
        print(worker.open_database().integrity())
        worker.db.close()
    else:
        from src.realtime.t0.worker import InstanceLock
        with InstanceLock(locations['runtime']/'worker.lock'):
            worker.capture('ZUUU')
            worker.capture('ECMWF')
            print(json.dumps(worker.cycle(startup=True), indent=2))
            print(worker.db.integrity())
            worker.db.close()


if __name__=='__main__':
    main()
