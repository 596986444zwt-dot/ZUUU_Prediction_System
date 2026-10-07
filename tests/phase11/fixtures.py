"""Explicit TEST_FIXTURE writers. Never accepts a production destination."""
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from src.gui.adapters import ROOT,now,BJT

BASE=ROOT/'docs/phase11/fixtures'

def create(name='normal',days=0):
    root=(BASE/name).resolve()
    assert root.is_relative_to(BASE.resolve())
    root.mkdir(parents=True,exist_ok=True)
    (root/'config').mkdir(exist_ok=True)
    for cfg in ('phase10_realtime_v1.json','t0_experimental_v1.json'):
        value=json.loads((ROOT/'config'/cfg).read_text(encoding='utf-8-sig'))
        (root/'config'/cfg).write_text(json.dumps(value),encoding='utf-8')
    inventory=json.loads((ROOT/'docs/phase11/SOURCE_INVENTORY.json').read_text(encoding='utf-8'))
    for name in ('phase10_realtime_v1.db','t0_forward_v1/t0_forward_v1.db'):
        path=root/'database'/name;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():path.unlink()  # Verified fixture-only path.
        c=sqlite3.connect(path)
        for table,detail in inventory[name].items():c.execute(detail['schema'])
        if name=='phase10_realtime_v1.db':formal(c)
        else:t0(c,days)
        c.commit();c.close()
    from tests.phase11.soak_fixture import seed
    seed(root)
    return root

def formal(c):
    stamp=now().isoformat();today=now().astimezone(BJT).date()
    c.execute('INSERT INTO schema_version VALUES (?,?)',('PHASE10_REALTIME_V1','TEST_FIXTURE'))
    def add(table,key,body):
        c.execute(f'INSERT INTO realtime_{table} VALUES (?,?,?)',(key,stamp,json.dumps(dict(body,namespace='TEST_FIXTURE'))))
    for h,offset,model in (('T1',1,'RIDGE'),('T2',2,'LIGHTGBM')):
        target=(today+timedelta(days=offset)).isoformat()
        add('model_registry',h+'/model',dict(record_id=h+'/model',model_family=model,model_version='TEST_FIXTURE',formal_horizon=h))
        add('continuous_predictions',h+'/ML',dict(continuous_prediction_c=23.2,model_state_id=h+'/model',target_business_date=target))
        add('probability_predictions',h+'/P',dict(pmf=[.05,.1,.2,.3,.2,.1,.05],support_min=20,support_max=26,status='CALIBRATED'))
        add('prediction_snapshots',h,dict(horizon=h,target_business_date=target,prediction_issue_time=stamp,continuous_id=h+'/ML',probability_id=h+'/P',status='OK',selected_run=stamp,probability_state_version='TEST_FIXTURE_STATE'))
    add('zuuu_normalized','obs',dict(qc_status='VALID',observation_time=stamp,temperature_c=21,dewpoint_c=14,actual_ingest_time=stamp,raw_report='TEST_FIXTURE METAR',cloud_layers=[],weather_phenomena=[]))
    add('ecmwf_raw_runs','run',dict(run_time=stamp,actual_ingest_time=stamp))
    for i in range(72):
        add('ecmwf_hourly','hour/'+str(i),dict(run_id='run',run_time_utc=stamp,target_time_utc=(now()+timedelta(hours=i)).isoformat(),temperature_2m_c=20+i%7))
    import os
    add('engine_state','cycle',dict(kind='CYCLE_COMPLETE',time=stamp,pid=os.getpid(),next_check=(now()+timedelta(seconds=60)).isoformat()))
    add('engine_health','health',dict(status='ENGINE_HEALTHY',time=stamp,pid=os.getpid(),disk_free=10000000000))
    for source in ('ZUUU','ECMWF'):add('source_health',source,dict(source=source,current_status='ONLINE',last_success_time=stamp,data_age_hours=0))

def t0(c,days):
    stamp=now().isoformat();today=now().astimezone(BJT).date()
    c.execute('INSERT INTO t0_metadata VALUES (?,?,?)',('T0_EXPERIMENTAL_V1','TEST_FIXTURE',stamp))
    def insert(table,body):
        keys=list(body);c.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',list(body.values()))
    for index in range(days+1):
        day=(today-timedelta(days=index)).isoformat();pid='fixture/'+day
        insert('t0_prediction_snapshot',dict(prediction_id=pid,prediction_time=stamp,target_date=day,cutoff_bjt='14:00',trigger_type='SCHEDULED',tmax_so_far=21,status='OK',code_version='TEST_FIXTURE',config_version='TEST_FIXTURE',method_version='TEST_FIXTURE',created_at=stamp,payload_json='{}'))
        for method in ('LEVEL0','L1_A'):
            insert('t0_prediction_method_output',dict(prediction_id=pid,method=method,prediction=23.,integer_prediction=23,status='OK',candidate_status='EXPERIMENTAL_ACTIVE_CANDIDATE',payload_json='{}'))
        if index:
            tid='truth/'+day
            insert('t0_ground_truth_evidence',dict(truth_id=tid,target_date=day,actual_integer_tmax=24,confirmation_time=stamp,status='CONFIRMED_EXPERIMENTAL_TARGET',payload_json='{}'))
            for method in ('LEVEL0','L1_A'):
                insert('t0_settlement',dict(settlement_id=pid+'/'+method,prediction_id=pid,method=method,truth_id=tid,actual_integer_tmax=24,continuous_error=-1.,absolute_error=1.,integer_prediction=23,integer_exact_hit=0,within_1c=1,created_at=stamp))
    import os
    insert('t0_worker_event',dict(event_id='heartbeat',kind='HEARTBEAT',created_at=stamp,payload_json=json.dumps(dict(pid=os.getpid(),time=stamp))))

def change(root,db,sql,params=()):
    assert Path(root).resolve().is_relative_to(BASE.resolve())
    c=sqlite3.connect(Path(root)/'database'/db);c.execute(sql,params);c.commit();c.close()

def payload(root,table,key,updates):
    assert Path(root).resolve().is_relative_to(BASE.resolve())
    c=sqlite3.connect(root/'database/phase10_realtime_v1.db')
    body=json.loads(c.execute(f'SELECT payload_json FROM realtime_{table} WHERE record_id=?',(key,)).fetchone()[0]);body.update(updates)
    c.execute(f'UPDATE realtime_{table} SET payload_json=? WHERE record_id=?',(json.dumps(body),key));c.commit();c.close()
