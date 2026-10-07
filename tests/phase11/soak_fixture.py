"""Synthetic Soak evidence writers restricted to the TEST_FIXTURE directory."""
import csv
import json
import os
from datetime import timedelta
from src.gui.adapters import ROOT,now,BJT

def seed(root, hours=12, findings=None):
    root=root.resolve()
    assert root.is_relative_to((ROOT/'docs/phase11/fixtures').resolve())
    folder=root/'soak';(folder/'evidence').mkdir(parents=True,exist_ok=True)
    current=now();start=current-timedelta(hours=hours)
    manifest=dict(SOAK_START_TIME_UTC=start.isoformat(),SOAK_START_TIME_BJT=start.astimezone(BJT).isoformat(),SOAK_TARGET_MINIMUM_HOURS=72,
                  MINIMUM_72H_COMPLETION_TIME_BJT=(start+timedelta(hours=72)).astimezone(BJT).isoformat(),
                  PHASE10_SOAK_STATUS='RUNNING',PHASE10_OPERATIONAL_ACCEPTANCE='PENDING_SOAK',namespace='TEST_FIXTURE')
    observer=dict(sample_time=current.isoformat(),findings=findings or [],namespace='TEST_FIXTURE')
    for name,body in [('SOAK_START_MANIFEST.json',manifest),('SOAK_PROCESS_INFO.json',{'phase10':{'pid':os.getpid()},'namespace':'TEST_FIXTURE'}),
                      ('R4_PRODUCTION_ACTIVATION.json',{'status':'PASS','namespace':'TEST_FIXTURE'}),('evidence/LATEST_OBSERVER_STATUS.json',observer)]:
        (folder/name).write_text(json.dumps(body),encoding='utf-8')
    fields=['observed_at_utc','observed_at_bjt','kind','source','record_id','severity','details_json']
    def csv_file(name,rows):
        with (folder/name).open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    csv_file('SOAK_HEARTBEAT.csv',[dict(kind='SAMPLING_COMPLETE',details_json=json.dumps(observer))])
    csv_file('SOAK_ERRORS.csv',[dict(kind='RESOLVED_OLD',record_id='RESOLVED_OLD',severity='CRITICAL',details_json='{}'),
                                dict(kind='ACTIVE_HIGH',record_id='ACTIVE_HIGH',severity='HIGH',details_json='{}')])
    return folder
