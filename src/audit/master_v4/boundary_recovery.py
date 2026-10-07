import pathlib,sys,json,copy,sqlite3
from datetime import datetime,timedelta,timezone
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from src.realtime.archive import Archive
from src.realtime.features import selection
from src.realtime.contracts import iso
from src.audit.master_v4.verify import save
OUT=ROOT/'temp/master_audit_v4';evidence=[]
issue=datetime.fromisoformat('2026-10-02T13:00:00+00:00');start=datetime.fromisoformat('2026-10-03T00:00:00+08:00');keys=[iso(start+timedelta(hours=h)) for h in range(24)]
run_old={'run_time_utc':'2026-10-02T00:00:00+00:00','source_available_time_utc':iso(issue-timedelta(hours=1)),'canonical_raw_run_id':'old','model':'IFS_HRES'}
run_new={'run_time_utc':'2026-10-02T06:00:00+00:00','source_available_time_utc':iso(issue),'canonical_raw_run_id':'new','model':'IFS_HRES'}
curve={k:{'target_time_utc':k,'temperature_2m_c':20} for k in keys}
for name,change,expected in [('availability_equal',None,'new'),('availability_future_microsecond','future','old'),('latest_missing_hour','missing','old'),('latest_null_temperature','null','old')]:
    new=copy.deepcopy(run_new);nc=copy.deepcopy(curve)
    if change=='future':new['source_available_time_utc']=iso(issue+timedelta(microseconds=1))
    if change=='missing':del nc[keys[-1]]
    if change=='null':nc[keys[-1]]['temperature_2m_c']=None
    actual,hours,choice=selection({'old':run_old,'new':new},{'old':curve,'new':nc},'2026-10-03',issue)
    evidence.append({'case':name,'expected':expected,'actual':actual['canonical_raw_run_id'],'choice':choice,'hours':len(hours)})
a=Archive(OUT/'extra_state_identity.db',namespace='SIMULATION',create=True)
with a.c:
    a.insert('probability_state',{'state_id':'A','record_type':'LINEAGE_ALIAS','canonical_state_id':'B'},'A')
    a.insert('probability_state',{'state_id':'B','record_type':'LINEAGE_ALIAS','canonical_state_id':'A'},'B')
for name,version in [('state_alias_cycle','A'),('state_absent','C')]:
    try:a.resolve_probability_state(version);actual='ACCEPTED'
    except ValueError as e:actual='REJECTED:'+str(e)
    evidence.append({'case':name,'expected':'REJECTED','actual':actual})
a.close()
save('boundary_recovery.json',{'evidence_status':'FINAL','cases':len(evidence),'mismatch_count':sum((r['expected']=='REJECTED' and not r['actual'].startswith('REJECTED')) or (r['expected']!='REJECTED' and r['expected']!=r['actual']) for r in evidence),'evidence':evidence})
# Prepare crash fixture using production schema only on simulation path.
a=Archive(OUT/'crash_transaction.db',namespace='SIMULATION',create=True);a.close()
print('boundary cases',len(evidence))
