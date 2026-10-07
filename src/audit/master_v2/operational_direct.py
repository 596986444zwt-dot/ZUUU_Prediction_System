"""Final direct runtime network evidence and run-identity parity, without inference/build."""
from .common import *
from .database_audit import Source
from .feature_audit import Features
from .realtime_audit import Realtime
from .model_audit import Models
from .probability_audit import Probability

def run():
    s=Source()
    try:
        s.load();f=Features(s);m=Models(s,f);p=Probability(s,m);r=Realtime(s,f,m,p)
        health=r.tables['source_health'];snaps=r.tables['prediction_snapshots'];raw=r.tables['ecmwf_raw_runs'];examples=[]
        for row in health:
            failed=[a for a in row.get('attempts',[]) if a.get('http_status')==400]
            if row.get('source')=='ECMWF' and failed:
                subsequent=[x for x in snaps if stamp(x['prediction_issue_time'])>=stamp(row['last_attempt_time'])]
                examples.append(dict(health_id=row['_id'],source_status=row['current_status'],last_attempt=row['last_attempt_time'],http400_count=len(failed),requests=failed,subsequent_snapshots=[dict(prediction_id=x['_id'],issue=x['prediction_issue_time'],status=x['status'],reason=x.get('reason_code'),selected_run=x['selected_run'],selected_version=x['selected_version']) for x in subsequent[:4]],remote_root_cause='NOT_YET_PROVEN: request attempts retain HTTP status but not per-attempt run parameters',no_fake_run_evidence='All current raw payload hashes and same-run completeness/availability were independently checked in MASTER_PHASE10_REALTIME_AUDIT.csv'))
        output('DEPLOYMENT_NETWORK_EVIDENCE_V2.json',dict(deployment_network_validation='PENDING_SOAK',mainland_network_is_hard_gate=False,source_health_count=len(health),ecmwf_http400_events=examples,last_snapshot_statuses=[dict(id=x['_id'],status=x['status'],reason=x.get('reason_code'),selected_run=x['selected_run']) for x in snaps[-4:]],root_cause_status='NOT_YET_PROVEN',evidence_boundary='Direct current DB rows; no live requests; original connectivity report is reference only'))
        from src.realtime.features import construct
        curves={r['canonical_raw_run_id']:{t:dict(v,target_time_utc=t,lead_hours=(stamp(t)-stamp(r['run_time_utc'])).total_seconds()/3600,id=i) for i,(t,v) in enumerate(s.curves[r['canonical_raw_run_id']].items())} for r in s.runs_available}
        with (OUT/'MASTER_HISTORICAL_REALTIME_PARITY.csv').open(encoding='utf-8-sig',newline='') as file:cases=list(csv.DictReader(file))
        checks=[]
        for row in cases:
            day,h,issue=row['sample_id'].split('/')[0],row['horizon'],row['issue']
            selected=s.choose(day,issue);subject=construct(day,h,issue,s.runs_by_time,curves,s.samples6)
            bad=subject['selected_version']!=selected['canonical_raw_run_id'] or subject['selected_run']!=selected['run_time_utc']
            checks.append(dict(sample_id=row['sample_id'],independent_raw_id=selected['canonical_raw_run_id'],runtime_raw_id=subject['selected_version'],mismatch=int(bad),status='FAIL' if bad else 'PASS'))
        output('PARITY_RUN_IDENTITY_V2.json',dict(status='PASS' if not any(x['mismatch'] for x in checks) else 'FAIL',checks=checks,mismatch_count=sum(x['mismatch'] for x in checks)))
        print('Direct network evidence retained; parity run identity checks',len(checks),'mismatches',sum(x['mismatch'] for x in checks),flush=True)
    finally:s.close()
if __name__=='__main__':run()
