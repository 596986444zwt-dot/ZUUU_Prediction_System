"""Explicit Windows CLI commands; never installs autostart implicitly."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.realtime.contracts import config,now,utc

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['start','status','stop','init','backup'])
    p.add_argument('--seconds',type=int);args=p.parse_args();cfg=config()
    if args.command=='stop':
        path=ROOT/'logs/phase10/stop.request';path.parent.mkdir(parents=True,exist_ok=True);path.write_text('GRACEFUL_STOP',encoding='utf-8');print('STOP_REQUESTED');return
    from src.realtime.archive import Archive
    path=ROOT/cfg['database']
    if args.command=='status':
        if not path.exists():print('ENGINE NOT_INITIALIZED');return
        import sqlite3,os,shutil
        c=sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)
        def last(table):
            r=c.execute('SELECT payload_json FROM realtime_'+table+' ORDER BY created_at DESC LIMIT 1').fetchone()
            return json.loads(r[0]) if r else None
        health=last('engine_health');sources={}
        for raw in c.execute('SELECT payload_json FROM realtime_source_health ORDER BY created_at DESC'):
            r=json.loads(raw[0]);sources.setdefault(r['source'],r)
        models=[]
        for raw in c.execute('SELECT payload_json FROM realtime_model_registry'):
            r=json.loads(raw[0]);models.append({k:r[k] for k in ('horizon','model_family','model_version','training_cutoff','artifact_sha256','input_feature_count')})
        state_update=c.execute('SELECT created_at FROM realtime_probability_state ORDER BY created_at DESC LIMIT 1').fetchone()
        starts=[json.loads(r[0]) for r in c.execute('SELECT payload_json FROM realtime_engine_health') if json.loads(r[0])['status']=='ENGINE_STARTING']
        uptime=(utc(health['time'])-utc(starts[-1]['time'])).total_seconds() if starts and health else None
        latest_run=c.execute("SELECT MAX(json_extract(payload_json,'$.run_time')) FROM realtime_ecmwf_raw_runs").fetchone()[0]
        print(json.dumps({'说明':'后台状态；ENGINE_STOPPING 表示当前短测已正常停止。开机任务尚未安装。',
            'ENGINE':health,'UPTIME_SECONDS':uptime,'ECMWF_LATEST_RUN':latest_run,'LAST_CYCLE':last('engine_state'),
            'SOURCE_HEALTH':sources,'MODELS':models,
            'LAST_PREDICTIONS':[json.loads(r[0]) for r in c.execute('SELECT payload_json FROM realtime_prediction_snapshots ORDER BY created_at DESC LIMIT 2')],
            'LAST_PROBABILITY_STATE_UPDATE':state_update[0] if state_update else None,
            'LAST_SETTLEMENT':last('daily_settlement'),'ERROR_COUNT':c.execute('SELECT COUNT(*) FROM realtime_errors').fetchone()[0],
            'DISK_FREE':shutil.disk_usage(ROOT).free,'DATABASE_INTEGRITY':c.execute('PRAGMA integrity_check').fetchone()[0]},ensure_ascii=False,indent=2))
        c.close();return
    from src.realtime.engine import Engine,InstanceLock,configure_logging
    configure_logging(cfg)
    with InstanceLock(ROOT/'logs/phase10/engine.lock'):
        if args.command=='start':
            stopfile=ROOT/'logs/phase10/stop.request'
            if stopfile.exists():stopfile.unlink()
        db=Archive(path,create=args.command in ('init','start'))
        if args.command=='backup':
            dest=ROOT/'backups/phase10'/('manual_'+now().strftime('%Y%m%dT%H%M%S')+'.db');db.backup(dest);db.close();print(dest);return
        try:engine=Engine(db,cfg)
        except Exception as exc:
            import logging
            logging.getLogger('phase10.error').exception('STARTUP_BLOCKED')
            try:
                with db.c:db.insert('engine_health',dict(status='ENGINE_ERROR',reason=str(exc),time=now().isoformat()))
            finally:db.close()
            raise
        if args.command=='init':db.close();print('ENGINE_INITIALIZED');return
        engine.run(args.seconds)

if __name__=='__main__':main()
