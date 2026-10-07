"""Windows command-line engine; collectors/inference only, no training."""
import logging
import os
import shutil
import signal
import time
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from .contracts import *
from .archive import Archive
from .collectors import Fetcher,poll_zuuu,poll_ecmwf
from .features import construct,runtime_context,historical_bias_history
from .handoff import Models,bootstrap,forward
from .settlement import settle,evaluate,advance

class InstanceLock:
    def __init__(self,path):self.path=Path(path);self.handle=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.handle=self.path.open('a+b')
        try:
            self.handle.seek(0)
            if self.path.stat().st_size==0:self.handle.write(b'0');self.handle.flush()
            self.handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.handle.close();self.handle=None;raise RuntimeError('ENGINE_ALREADY_RUNNING')
        return self
    def __exit__(self,*args):
        if self.handle:
            self.handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
            self.handle.close();self.handle=None

def configure_logging(cfg):
    folder=ROOT/'logs/phase10';folder.mkdir(parents=True,exist_ok=True)
    for name in ('engine','zuuu_collector','ecmwf_collector','prediction','settlement','error'):
        logger=logging.getLogger('phase10.'+name);logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler=RotatingFileHandler(folder/(name+'.log'),maxBytes=cfg['log_max_bytes'],backupCount=cfg['log_backups'],encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logger.addHandler(handler)

def system_gate(cfg,issue):
    require(2026<=utc(issue).year<=2100,'CLOCK_ERROR')
    require(utc(issue).astimezone(BJT).utcoffset()==timedelta(hours=8),'CLOCK_ERROR')
    free=shutil.disk_usage(ROOT).free
    require(free>cfg['disk_critical_bytes'],'DISK_CRITICAL')
    return 'ENGINE_DEGRADED' if free<cfg['disk_warning_bytes'] else 'ENGINE_HEALTHY',free

def source_gate(db,cfg,issue):
    latest={r['source']:r for r in db.rows('source_health') if utc(r['_created'])<=utc(issue)}
    degraded=False
    for source in ('ZUUU','ECMWF'):
        r=latest.get(source)
        if not r:degraded=True;continue
        prefix='zuuu' if source=='ZUUU' else 'ecmwf'
        stamp=r.get('last_new_data_time')
        stale=not stamp or (utc(issue)-utc(stamp)).total_seconds()/3600>cfg[prefix+'_stale_hours']
        degraded=degraded or stale or r['current_status'] in ('ERROR','STALE')
    return degraded,latest

class Engine:
    def __init__(self,db,cfg,issue=None,models=None):
        import json
        from .guardian import fingerprints
        before=json.loads((ROOT/'docs/phase10/PHASE10_SOURCE_GUARDIAN_BEFORE.json').read_text(encoding='utf-8'))
        current=fingerprints()
        require(all(current.get(k)==v for k,v in before['sha256'].items()),'SOURCE_GUARDIAN_FAILURE')
        self.db=db;self.cfg=cfg;self.started=utc(issue or now());self.models=models or Models(self.started)
        saved=db.latest('probability_state')
        self.state={k:v for k,v in saved.items() if not k.startswith('_')} if saved else bootstrap(self.started)
        require(utc(self.state['cutoff'])<=self.started,'PROBABILITY_STATE_ERROR')
        if not saved:
            self.state['state_id']=identity(self.state)
            with db.c:db.insert('probability_state',self.state,self.state['state_id'])
        self.history=historical_bias_history()
        # Recover forward historical bias from already eligible daily anchors.
        from .features import season
        from .settlement import datetime_end
        for r in self.state['residuals']:
            if r.get('status')=='FORWARD_OOS' and r['origin']=='RAW':
                self.history.append(dict(business_date_bjt=r['target_business_date'],horizon=r['horizon'],issue_time_utc=r['issue'],
                    day_end_utc=iso(datetime_end(r['target_business_date'])),label_eligibility_time_bjt=r['eligibility'],settlement_time=r['eligibility'],
                    season=season(r['target_business_date']),raw_error=-r['residual']))
        configure_logging(cfg);self.fetcher=Fetcher(cfg)
        self.last_poll={};self.stopping=False
        with db.c:
            for h,row in self.models.active.items():db.insert('model_registry',dict(row,formal_horizon=h),row['record_id'])
            db.insert('engine_health',dict(status='ENGINE_STARTING',time=iso(self.started),pid=os.getpid()))
        self.event('IMPORTANT_INPUT_CHANGE',self.started,{'reason':'ENGINE_RESTART_RECOVERY'})
        logging.getLogger('phase10.engine').info('START namespace=%s pid=%d cutoff=%s',db.namespace,os.getpid(),self.state['cutoff'])

    def event(self,kind,issue,payload=None,event_id=None):
        rid=event_id or identity({'type':kind,'time':iso(issue),'payload':payload})
        if event_id and event_id.startswith('HOUR/'):
            existing=next((r for r in self.db.rows('events') if r['_id']==rid+'/PENDING'),None)
            if existing:return {k:v for k,v in existing.items() if not k.startswith('_') and k!='namespace'}
        record=dict(event_id=rid,event_type=kind,event_time=iso(issue),source='SCHEDULER',payload_identity=payload,status='PENDING')
        with self.db.c:self.db.insert('events',record,rid+'/PENDING',issue)
        return record

    def predict_event(self,event):
        rid=event['event_id'];issue=utc(event['event_time'])
        original_issue=issue
        if self.db.namespace=='PRODUCTION' and (now()-issue).total_seconds()>2*self.cfg['poll_seconds']:issue=now()
        if any(r.get('event_id')==rid and r['status']=='DONE' for r in self.db.rows('events')):return
        runs,trajectories=runtime_context(self.db,issue)
        errors=[]
        for h in ('T1','T2'):
            day=targets(issue)[h];key=identity({'horizon':h,'issue':iso(issue),'target':day})
            try:
                tick=time.monotonic()
                health,free=system_gate(self.cfg,now() if self.db.namespace=='SIMULATION' else issue)
                sources_degraded,source_health=source_gate(self.db,self.cfg,issue)
                if sources_degraded:health='ENGINE_DEGRADED'
                latest={r['business_date_bjt']:r for r in self.db.rows('daily_ground_truth') if utc(r['settlement_time'])<=issue}
                require(not any(r['settlement_status']=='PENDING_AFTER_FINAL_DATA' for r in latest.values()),'GROUND_TRUTH_VERSION_CONFLICT')
                feature=construct(day,h,issue,runs,trajectories,self.history)
                feature_seconds=time.monotonic()-tick;tick=time.monotonic()
                age=(issue-utc(feature['selected_run'])).total_seconds()/3600
                require(age<=self.cfg['ecmwf_stale_hours'],'ECMWF_STALE')
                value,row=self.models.predict(h,feature['values'],issue)
                model_seconds=time.monotonic()-tick;tick=time.monotonic()
                forecasts={'ML':value,'RAW':feature['values']['ecmwf_tmax_c'],'MOS':feature['values']['m6_corrected_temperature_c']}
                probability,variants=forward(self.state,h,day,issue,forecasts)
                probability_seconds=time.monotonic()-tick
                require(probability['status']!='NO_FORECAST','PROBABILITY_HISTORY_INSUFFICIENT')
                anchor=issue.astimezone(BJT).hour>=21
                old=any(r['horizon']==h and r['target_business_date']==day and r.get('daily_anchor') for r in self.db.rows('prediction_snapshots'))
                anchor=anchor and not old
                status='DEGRADED' if health=='ENGINE_DEGRADED' or feature['fallback_depth']>0 or age>self.cfg['ecmwf_delayed_hours'] or not anchor else 'OK'
                continuous=dict(horizon=h,target_business_date=day,prediction_issue_time=iso(issue),continuous_prediction_c=value,
                    raw_ecmwf_prediction=forecasts['RAW'],mos_prediction=forecasts['MOS'],model_version=row['model_version'],model_state_id=row['record_id'],
                    model_asset_hash=row['artifact_sha256'],feature_version='FEATURE_V1',training_cutoff=row['training_cutoff'],preprocessing_frozen=True)
                metadata=dict(engine_version=VERSION,horizon=h,target_business_date=day,prediction_issue_time=iso(issue),data_cutoff_time=iso(issue),
                    event_id=rid,status=status,daily_anchor=anchor,selected_run=feature['selected_run'],selected_version=feature['selected_version'],
                    original_event_time=iso(original_issue),recovery_current_issue=issue!=original_issue,
                    probability_state_version=self.state['state_id'],source_health=source_health,
                    latency_seconds={'feature':feature_seconds,'model':model_seconds,'probability':probability_seconds},
                    engine_health=health,reason_code='OFF_FIXED_ISSUE_REGIME' if not anchor else 'SOURCE_DEGRADED' if sources_degraded else 'OLDER_COMPLETE_FALLBACK' if feature['fallback_depth'] else None)
                self.db.snapshot(key,feature,continuous,dict(probability,variants=variants),metadata)
                logging.getLogger('phase10.prediction').info('%s %s %s %.6f %s',h,day,key,value,status)
            except Exception as exc:
                reason=str(exc);errors.append(reason)
                with self.db.c:
                    self.db.insert('errors',dict(event_id=rid,horizon=h,time=iso(now()),reason=reason,status='NO_FORECAST'))
                logging.getLogger('phase10.error').exception('Prediction failed %s %s',h,day)
        with self.db.c:self.db.insert('events',dict(event,event_id=rid,status='DONE',errors=errors),rid+'/DONE')

    def cycle(self,issue=None,network=True):
        issue=utc(issue or now())
        health,free=system_gate(self.cfg,now() if self.db.namespace=='SIMULATION' else issue)
        if source_gate(self.db,self.cfg,issue)[0]:health='ENGINE_DEGRADED'
        latest={r['business_date_bjt']:r for r in self.db.rows('daily_ground_truth') if utc(r['settlement_time'])<=issue}
        if any(r['settlement_status']=='PENDING_AFTER_FINAL_DATA' for r in latest.values()):health='ENGINE_DEGRADED'
        with self.db.c:self.db.insert('engine_health',dict(status=health,time=iso(issue),disk_free=free,pid=os.getpid()))
        from .spool import recover
        recovered=recover(self.db)
        if recovered:self.event('IMPORTANT_INPUT_CHANGE',issue,{'recovered_source_records':recovered})
        if network:
            for source,period,poller,kind in [('ZUUU',self.cfg['zuuu_poll_seconds'],poll_zuuu,'NEW_ZUUU'),('ECMWF',self.cfg['ecmwf_poll_seconds'],poll_ecmwf,'NEW_ECMWF_RUN')]:
                if source not in self.last_poll or (issue-self.last_poll[source]).total_seconds()>=period:
                    try:
                        n=poller(self.db,self.fetcher,self.cfg)
                        self.last_poll[source]=issue
                        if n:self.event(kind,now(),{'new_count':n})
                    except Exception as exc:
                        with self.db.c:self.db.insert('errors',dict(source=source,time=iso(now()),reason=type(exc).__name__))
                        logging.getLogger('phase10.error').exception('Collector cycle failed %s',source)
        current=now() if network else issue
        local=current.astimezone(BJT)
        self.event('BJT_MIDNIGHT' if local.hour==0 else 'HOUR_BOUNDARY',current,event_id='HOUR/'+local.strftime('%Y-%m-%dT%H'))
        done={r['event_id'] for r in self.db.rows('events') if r['status']=='DONE'}
        for event in self.db.rows('events'):
            if event['status']=='PENDING' and event['event_id'] not in done:self.predict_event(event)
        settle(self.db,current);evaluate(self.db,current)
        if advance(self.db,self.state,self.history,current):self.event('IMPORTANT_INPUT_CHANGE',current,{'state':self.state['state_id']})
        self.backup(current)
        with self.db.c:self.db.insert('engine_state',dict(kind='CYCLE_COMPLETE',time=iso(current),next_check=iso(current+timedelta(seconds=self.cfg['poll_seconds'])),pid=os.getpid()))

    def backup(self,issue):
        folder=ROOT/'backups/phase10' if self.db.namespace=='PRODUCTION' else self.db.path.parent/'simulation_backups'
        day=utc(issue).astimezone(BJT).date()
        path=folder/(day.isoformat()+'.db')
        if not path.exists():self.db.backup(path,timeout=self.cfg['backup_timeout_seconds'])
        if folder.exists():
            for old in folder.glob('????-??-??.db'):
                from datetime import date
                if (day-date.fromisoformat(old.stem)).days>self.cfg['backup_retention_days']:old.unlink()

    def run(self,seconds=None):
        stopfile=ROOT/'logs/phase10/stop.request'
        limit=time.monotonic()+seconds if seconds else None
        def stop(*args):self.stopping=True
        signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
        try:
            while not self.stopping and not stopfile.exists() and (limit is None or time.monotonic()<limit):
                try:self.cycle()
                except Exception as exc:
                    logging.getLogger('phase10.error').exception('Engine cycle error')
                    try:
                        with self.db.c:
                            self.db.insert('errors',dict(reason=type(exc).__name__,time=iso(now())))
                            self.db.insert('engine_health',dict(status='ENGINE_ERROR',time=iso(now()),reason=str(exc)))
                    except Exception:
                        logging.getLogger('phase10.error').exception('Database unavailable; receipt spool retained; retry next check')
                until=time.monotonic()+self.cfg['poll_seconds']
                while time.monotonic()<until and not self.stopping and not stopfile.exists() and (limit is None or time.monotonic()<limit):time.sleep(.25)
        finally:
            try:
                with self.db.c:self.db.insert('engine_health',dict(status='ENGINE_STOPPING',time=iso(now()),pid=os.getpid()))
            except Exception:logging.getLogger('phase10.error').exception('Shutdown state write unavailable')
            finally:self.db.close();logging.shutdown()
