"""Independent runtime probes. All writes are isolated beneath the audit output."""
import json
import math
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import numpy as np
import requests
from .common import *

def run_probes():
    from src.realtime.archive import Archive
    from src.realtime.collectors import Fetcher,archive_metar,archive_ecmwf
    from src.realtime.settlement import settle,evaluate,advance
    from src.realtime.engine import Engine,InstanceLock,system_gate
    from src.realtime.spool import save,recover
    from src.ecmwf_contract import HOURLY_VARIABLES,EXPECTED_UNITS
    results=[];cfg=json.loads((ROOT/'config/phase10_realtime_v1.json').read_text(encoding='utf-8'))
    def record(name,condition,evidence,expected):results.append(dict(probe=name,status='PASS' if condition else 'FAIL',expected=expected,actual=evidence,namespace='ISOLATED_MASTER_AUDIT_SIMULATION'))
    with tempfile.TemporaryDirectory(prefix='master_probes_',dir=TEMP) as td:
        path=Path(td);a=Archive(path/'simulation.db','SIMULATION',create=True)
        try:
            nowissue=stamp('2026-10-03T13:30:00+00:00');obs=stamp('2026-10-01T03:00:00+00:00')
            metar=lambda t,temp=24:dict(icaoId='ZUUU',obsTime=t.timestamp(),rawOb='METAR ZUUU '+t.strftime('%d%H%MZ')+' 00000MPS CAVOK '+str(temp)+'/15 Q1013')
            n=archive_metar(a,[metar(obs)],nowissue);n2=archive_metar(a,[metar(obs)],nowissue+timedelta(hours=1));record('identical_metar_dedup',n==1 and n2==0,{'raw':len(a.rows('zuuu_raw')),'new':n2},'one raw row')
            packet=metar(obs,25);packet['rawOb']=packet['rawOb'].replace('METAR ZUUU','METAR COR ZUUU');archive_metar(a,[packet],nowissue);record('COR_preserved',len(a.rows('zuuu_raw'))==2 and any(r['is_corrected'] for r in a.rows('zuuu_normalized')) and all(r['supersedes_raw_id'] is None for r in a.rows('zuuu_normalized')),{'raw':len(a.rows('zuuu_raw')),'cor':any(r['is_corrected'] for r in a.rows('zuuu_normalized'))},'distinct corrected version; no guessed supersession')
            sp=save(a,'ZUUU',json.dumps([metar(obs+timedelta(hours=1))]).encode(),nowissue);before=len(a.rows('zuuu_raw'));added=recover(a);again=recover(a)
            record('durable_spool_replay_idempotent',added==1 and again==0 and len(a.rows('zuuu_raw'))==before+1 and a.rows('zuuu_raw')[-1]['actual_ingest_time']==nowissue.isoformat(),{'added':added,'again':again,'receipt':a.rows('zuuu_raw')[-1]['actual_ingest_time']},'original receipt unchanged; exactly once')
            blocker=sqlite3.connect(path/'simulation.db');blocker.execute('BEGIN IMMEDIATE');a.c.execute('PRAGMA busy_timeout=10')
            received=nowissue+timedelta(minutes=2);save(a,'ZUUU',json.dumps([metar(obs+timedelta(hours=2))]).encode(),received)
            rejected=False
            try:recover(a)
            except sqlite3.OperationalError:rejected=True
            blocker.rollback();blocker.close();a.c.rollback();new=recover(a);repeat=recover(a)
            record('DB_locked_spool_recovery',rejected and new==1 and repeat==0 and any(r['actual_ingest_time']==received.isoformat() for r in a.rows('zuuu_raw')),{'locked_rejected':rejected,'recovered':new,'repeat':repeat},'durable response survives DB lock; original receipt; exactly once')
            for component in ('feature_snapshots','continuous_predictions','probability_predictions'):
                try:a.snapshot('rollback_'+component,{}, {}, {}, {},fail_after=component)
                except RuntimeError:pass
                remains=sum(len([r for r in a.rows(t) if r['_id'].startswith('rollback_')]) for t in ('feature_snapshots','continuous_predictions','probability_predictions','prediction_snapshots'))
                record('atomic_rollback_'+component,remains==0,remains,'zero components left')
            with a.c:a.insert('engine_state',{'probe':1},'immutable')
            for action in ('UPDATE','DELETE'):
                blocked=False
                try:a.c.execute('UPDATE realtime_engine_state SET payload_json=\'{}\' WHERE record_id=\'immutable\'' if action=='UPDATE' else 'DELETE FROM realtime_engine_state WHERE record_id=\'immutable\'')
                except sqlite3.IntegrityError:blocked=True
                a.c.rollback();record('append_only_'+action,blocked,blocked,'SQLite rejects mutation')
            # Settlement conflict and incomplete coverage, then a different complete day.
            daystart=stamp('2026-10-02T00:00:00+08:00')
            archive_metar(a,[metar(daystart+timedelta(hours=h),20+h%8) for h in range(24)],nowissue)
            targets=settle(a,nowissue);final=next(r for r in a.rows('daily_ground_truth') if r['business_date_bjt']=='2026-10-02')
            record('complete_day_independent_target',final['settlement_status']=='FINAL' and final['daily_tmax_c']==27 and final['hourly_coverage_count']==24,{'status':final['settlement_status'],'tmax':final['daily_tmax_c'],'hours':final['hourly_coverage_count']},'FINAL27C,24 exact hours')
            conflict=next(r for r in a.rows('daily_ground_truth') if r['business_date_bjt']=='2026-10-01');record('COR_conflict_not_guessed',conflict['settlement_status']=='PENDING_VERSION_CONFLICT',conflict['settlement_status'],'PENDING_VERSION_CONFLICT')
            pmf=np.ones(161)/161
            for i in range(3):a.snapshot('evaluation_'+str(i),{},dict(continuous_prediction_c=25+i,raw_ecmwf_prediction=25,mos_prediction=25),dict(pmf=pmf.tolist(),variants=[]),dict(horizon='T1',target_business_date='2026-10-02',prediction_issue_time='2026-10-01T13:00:00+00:00',daily_anchor=i==0))
            evaluate(a,nowissue);evaluate(a,nowissue+timedelta(minutes=1));evaluations=a.rows('daily_evaluation');sc=scores(pmf,27)
            record('evaluate_all_snapshots_once',len(evaluations)==3 and all(close(r['Brier'],sc['brier']) and close(r['LogLoss'],sc['logloss']) and close(r['CRPS'],sc['crps']) for r in evaluations),{'n':len(evaluations),'anchor_n':1},'all3 scored, only1 anchor, no repeat')
            state={'cutoff':'2026-10-01T13:00:00+00:00','residuals':[],'cases':[]};history=[];advance(a,state,history,nowissue)
            record('Dplus2_state_wait',not state['residuals'],len(state['residuals']),'no residual before October4BJT')
            check=stamp('2026-10-04T00:00:00+08:00');advance(a,state,history,check);advance(a,state,history,check+timedelta(hours=1));record('anchor_only_state_update',len(state['residuals'])==3 and len(history)==1,{'residuals':len(state['residuals']),'bias_labels':len(history)},'3 origins for one daily anchor, no repeat')
            packet=metar(daystart+timedelta(hours=5),35);packet['rawOb']=packet['rawOb'].replace('METAR ZUUU','METAR COR ZUUU');archive_metar(a,[packet],check+timedelta(hours=2));settle(a,check+timedelta(hours=3));versions=[r for r in a.rows('daily_ground_truth') if r['business_date_bjt']=='2026-10-02']
            record('late_COR_pending_preserves_final',len(versions)==2 and versions[0]['settlement_status']=='FINAL' and versions[-1]['settlement_status']=='PENDING_AFTER_FINAL_DATA',{'versions':[r['settlement_status'] for r in versions]},'old final retained; new PENDING_AFTER_FINAL_DATA')
            # Backup handle must close; independent byte snapshot plus rename exercises Windows.
            backup=path/'backup.db';a.backup(backup);cc=snapshot(backup);ok=cc.execute('PRAGMA integrity_check').fetchone()[0]=='ok';cc.close();renamed=path/'renamed.db';backup.rename(renamed);record('backup_integrity_and_closed_handle',ok and renamed.exists(),ok,'consistent readable file; Windows rename succeeds')
            lock=path/'engine.lock'
            with InstanceLock(lock):
                second=False
                try:
                    with InstanceLock(lock):pass
                except RuntimeError as e:second=str(e)=='ENGINE_ALREADY_RUNNING'
            with InstanceLock(lock):released=True
            record('second_instance_then_lock_release',second and released,{'second_blocked':second,'release':released},'second blocked, subsequent acquisition succeeds')
            # Independent mocked networking, without any external request.
            class Session:
                def __init__(self,mode):self.mode=mode;self.calls=0
                def get(self,*args,**kwargs):
                    self.calls+=1
                    if self.mode=='DNS':raise requests.ConnectionError('isolated DNS failure')
                    if self.mode=='timeout':raise requests.Timeout('isolated timeout')
                    response=requests.Response();response.status_code=int(self.mode);response._content=b'{}';return response
            for mode in ('DNS','timeout','400','429','500'):
                ss=Session(mode);content,received,attempts=Fetcher(cfg,session=ss,sleeper=lambda _:None).get('https://invalid.audit.test',{});record('bounded_network_'+mode,content is None and len(attempts)==cfg['max_attempts'],{'calls':ss.calls,'attempts':attempts},'bounded failed attempts, no fake content')
            with patch('src.realtime.engine.shutil.disk_usage',return_value=type('Usage',(),{'free':1})()):
                blocked=False
                try:system_gate(cfg,nowissue)
                except ValueError as e:blocked=str(e)=='DISK_CRITICAL'
                record('disk_critical_gate',blocked,blocked,'reject prediction')
            blocked=False
            try:system_gate(cfg,'1990-01-01T00:00:00+00:00')
            except ValueError as e:blocked=str(e)=='CLOCK_ERROR'
            record('clock_gate',blocked,blocked,'reject obviously invalid clock')
        finally:a.close()
        # Status applicability probe at21:30; dummy inference only tests orchestration status, never accuracy.
        a=Archive(path/'status.db','SIMULATION',create=True)
        try:
            run=stamp('2026-10-03T06:00:00+00:00');issue=stamp('2026-10-03T13:30:00+00:00');hourly={'time':[(run+timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in range(72)]}
            for k in HOURLY_VARIABLES:hourly[k]=[20. if k=='temperature_2m' else None]*72
            archive_ecmwf(a,dict(hourly=hourly,hourly_units=EXPECTED_UNITS,utc_offset_seconds=0),run,issue-timedelta(minutes=1))
            for source in ('ZUUU','ECMWF'):
                with a.c:a.insert('source_health',dict(source=source,current_status='ONLINE',last_new_data_time=(issue-timedelta(minutes=1)).isoformat()),created=issue-timedelta(minutes=1))
            class Dummy:
                def predict(self,h,v,i):return 20.,dict(model_version='AUDIT_DUMMY',record_id='AUDIT_DUMMY',artifact_sha256='AUDIT_DUMMY',training_cutoff='2026-01-01T00:00:00+00:00')
            e=Engine.__new__(Engine);e.db=a;e.cfg=cfg;e.models=Dummy();e.history=[];residuals=[]
            for h in ('T1','T2'):
                for j in range(90):
                    day=(datetime(2026,5,1)+timedelta(days=j)).date().isoformat()
                    for origin in ('ML','RAW'):residuals.append(dict(record_id=day+'/'+h+'/'+origin,horizon=h,target_business_date=day,eligibility=eligible(day).isoformat(),issue='2026-01-01T13:00:00+00:00',origin=origin,residual=float(j%3-1)))
            e.state=dict(cutoff='2026-09-01T13:00:00+00:00',state_id='AUDIT_DUMMY',residuals=residuals,cases=[])
            with a.c:a.insert('probability_state',e.state,e.state['state_id'])
            e.predict_event(dict(event_id='status2130',event_time=issue.isoformat(),event_type='MANUAL_TEST_TRIGGER',source='AUDIT',payload_identity=None,status='PENDING'))
            later=issue+timedelta(minutes=15)
            e.predict_event(dict(event_id='status2145',event_time=later.isoformat(),event_type='MANUAL_TEST_TRIGGER',source='AUDIT',payload_identity=None,status='PENDING'))
            snapshots=a.rows('prediction_snapshots');off_anchor=[r for r in snapshots if not r['daily_anchor']]
            record('off_exact21_issue_status',len(off_anchor)==2 and all(r['status']=='DEGRADED' for r in off_anchor),{'initial_issue':issue.astimezone(BJT).isoformat(),'off_anchor_issue':later.astimezone(BJT).isoformat(),'saved_statuses':[r['status'] for r in snapshots],'anchors':[r['daily_anchor'] for r in snapshots]},'21:45 refresh after existing21:30 anchor is OFF_ANCHOR; frozen protocol requires DEGRADED')
        finally:a.close()
    return results
