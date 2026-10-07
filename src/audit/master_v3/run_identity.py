"""Explicit same raw-version identity comparison on the frozen V3 draw."""
from .common import *
from .database_audit import Source

def run():
    from src.realtime.features import construct
    s=Source()
    try:
        s.load();curves={r['canonical_raw_run_id']:{t:dict(v,target_time_utc=t,lead_hours=(stamp(t)-stamp(r['run_time_utc'])).total_seconds()/3600,id=i) for i,(t,v) in enumerate(s.curves[r['canonical_raw_run_id']].items())} for r in s.runs_available}
        with (OUT/'MASTER_HISTORICAL_REALTIME_PARITY.csv').open(encoding='utf-8-sig',newline='') as f:cases=list(csv.DictReader(f))
        checks=[]
        for row in cases:
            day,h,issue=row['sample_id'].split('/')[0],row['horizon'],row['issue'];expected=s.choose(day,issue)
            actual=construct(day,h,issue,s.runs_by_time,curves,s.samples6)
            bad=actual['selected_version']!=expected['canonical_raw_run_id'] or actual['selected_run']!=expected['run_time_utc']
            checks.append(dict(sample_id=row['sample_id'],horizon=h,independent_raw=expected['canonical_raw_run_id'],runtime_raw=actual['selected_version'],independent_run=expected['run_time_utc'],runtime_run=actual['selected_run'],mismatch=int(bad)))
        output('PARITY_RUN_IDENTITY_V3.json',dict(status='PASS' if not any(x['mismatch'] for x in checks) else 'FAIL',checks=checks,mismatch_count=sum(x['mismatch'] for x in checks),scope='Same V3 frozen220 draw, source selector independently implemented; runtime construct is subject only'))
        print('RUN_IDENTITY_CHECK',len(checks),'MISMATCH',sum(x['mismatch'] for x in checks),flush=True)
    finally:s.close()
if __name__=='__main__':run()
