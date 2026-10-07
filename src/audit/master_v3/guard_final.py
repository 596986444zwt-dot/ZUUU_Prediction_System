"""Fresh isolated attack cases; never opens a formal DB for writing."""
import copy
from .common import *

COMPONENTS=('feature_snapshots','continuous_predictions','probability_predictions','prediction_snapshots')

def exercise(name, mutate=None, fail_after=None):
    from src.realtime.archive import Archive
    path=TEMP/('guard_final_'+name+'.db')
    if path.exists(): raise RuntimeError('FRESH_FIXTURE_REQUIRED: '+str(path))
    a=Archive(path,'SIMULATION',create=True)
    state=dict(state_id='VALID',cutoff='2026-10-01T00:00:00+00:00',residuals=[],cases=[])
    with a.c:a.insert('probability_state',state,'VALID')
    context=dict(horizon='T1',target_business_date='2026-10-04',prediction_issue_time='2026-10-03T13:00:00+00:00')
    feature=dict(context,values={'ecmwf_tmax_c':25.})
    continuous=dict(context,continuous_prediction_c=25.,model_version='MODEL_T1_V1')
    probability=dict(context,probability_state_version='VALID',support=[24,25,26],pmf=[.2,.6,.2])
    metadata=dict(context,probability_state_version='VALID')
    if mutate:mutate(a,feature,continuous,probability,metadata)
    error=None;accepted=False
    try:accepted=a.snapshot('CASE',feature,continuous,probability,metadata,fail_after=fail_after)
    except Exception as exc:error=type(exc).__name__+': '+str(exc)
    counts={t:len(a.rows(t)) for t in COMPONENTS}
    foreign=a.integrity();component_links=a.c.execute('SELECT COUNT(*) FROM snapshot_components').fetchone()[0]
    result=dict(case=name,accepted=accepted,rejected=error is not None,error=error,persisted_components=counts,component_links=component_links,integrity=foreign,partial_snapshot_count=int(0<sum(counts.values())<4),orphan_count=int(sum(counts.values())!=4*component_links),fixture=str(path),namespace='ISOLATED_SIMULATION')
    a.close();return result

def run():
    cases=[]
    def versions(p,m,pv,mv):p['probability_state_version']=pv;m['probability_state_version']=mv
    cases.append(exercise('missing_state',lambda a,f,c,p,m:versions(p,m,'MISSING','MISSING')))
    cases.append(exercise('wrong_state_reference',lambda a,f,c,p,m:versions(p,m,'UNKNOWN','VALID')))
    cases.append(exercise('wrong_metadata',lambda a,f,c,p,m:versions(p,m,'VALID','WRONG')))
    def wrong_identity(a,f,c,p,m):
        with a.c:a.insert('probability_state',dict(state_id='OTHER',cutoff='2026-10-01T00:00:00+00:00',residuals=[],cases=[]),'BAD_ID')
        versions(p,m,'BAD_ID','BAD_ID')
    cases.append(exercise('state_payload_id_mismatch',wrong_identity))
    cases.append(exercise('probability_state_mismatch',lambda a,f,c,p,m:p.update(probability_state_version='WRONG')))
    cases.append(exercise('state_prediction_horizon_mismatch',lambda a,f,c,p,m:c.update(horizon='T2',model_version='MODEL_T2_V1')))
    cases.append(exercise('state_prediction_target_mismatch',lambda a,f,c,p,m:p.update(target_business_date='2026-10-05')))
    def future_state(a,f,c,p,m):
        with a.c:a.insert('probability_state',dict(state_id='FUTURE',cutoff='2026-11-01T00:00:00+00:00',residuals=[],cases=[]),'FUTURE')
        versions(p,m,'FUTURE','FUTURE')
    cases.append(exercise('registered_future_state',future_state))
    cases.append(exercise('forced_transaction_failure',fail_after='continuous_predictions'))
    valid=exercise('valid_complete')
    for r in cases:r['status']='PASS' if r['rejected'] and sum(r['persisted_components'].values())==0 and not r['orphan_count'] else 'FAIL'
    valid['status']='PASS' if valid['accepted'] and sum(valid['persisted_components'].values())==4 and valid['component_links']==1 and not valid['orphan_count'] else 'FAIL'
    # Reproduce the V2 harness error without touching its database: a valid
    # snapshot already exists, rejection leaves it unchanged, rerun returns
    # False for its idempotent ID. Total rows are not failed-write leftovers.
    from src.realtime.archive import Archive
    path=TEMP/'v2_harness_reuse_reproduction.db'
    if path.exists():raise RuntimeError('FRESH_FIXTURE_REQUIRED')
    a=Archive(path,'SIMULATION',create=True)
    with a.c:a.insert('probability_state',dict(state_id='VALID',cutoff='2026-01-01T00:00:00+00:00',residuals=[],cases=[]),'VALID')
    first=a.snapshot('VALID_SNAPSHOT',{}, {},{'probability_state_version':'VALID'},{'probability_state_version':'VALID'})
    before={t:len(a.rows(t)) for t in COMPONENTS};rejected=[]
    for ref,meta in [('MISSING','MISSING'),('WRONG','VALID'),('VALID','WRONG')]:
        try:a.snapshot(ref+meta,{}, {},{'probability_state_version':ref},{'probability_state_version':meta});rejected.append(False)
        except ValueError:rejected.append(True)
    after={t:len(a.rows(t)) for t in COMPONENTS};second=a.snapshot('VALID_SNAPSHOT',{}, {},{'probability_state_version':'VALID'},{'probability_state_version':'VALID'});a.close()
    prior=ROOT/'docs/master_audit_v2/MA001_NEW_PREDICTION_GUARD_AUDIT.json'
    old=json.loads(prior.read_text(encoding='utf-8-sig'))
    legacy=ROOT/'temp/master_audit_v2/reference_guards.db';legacy_rows={}
    if legacy.exists():
        c=snapshot(legacy);legacy_rows={t:[dict(r) for r in c.execute('SELECT * FROM realtime_'+t)] for t in COMPONENTS};c.close()
    source={str(p.relative_to(ROOT)):p.read_text(encoding='utf-8-sig') for p in [ROOT/'src/audit/master_v2/adversarial.py',ROOT/'src/audit/master_v2/post.py',ROOT/'src/audit/master_v2/resume.py'] if p.exists()}
    conflict=dict(prior_artifact=old,prior_sha256=digest(prior),prior_fixture_rows=legacy_rows,fresh_reuse_reproduction=dict(first_valid=first,before=before,illegal_rejected=rejected,after=after,second_valid=second),mechanism='Reused fixture contained a prior complete snapshot; V2 counts all rows as rejection leftovers and mistakes idempotent False for a valid write failure. Post processor reads older checkpoint counters while final manifest includes newer FAIL evidence.',source=source,root_cause='REPORT_AGGREGATION_BUG',contributing_cause='AUDITOR_BUG',production_reference_rejection_failure_explains_prior_artifact=False)
    counts=dict(NEW_REFERENCE_GUARD_ILLEGAL_CASE_COUNT=len(cases),NEW_REFERENCE_GUARD_REJECTED_COUNT=sum(r['rejected'] for r in cases),NEW_REFERENCE_GUARD_HALF_COMPONENT_COUNT=sum(sum(r['persisted_components'].values()) for r in cases if r['partial_snapshot_count'] or r['orphan_count']),NEW_REFERENCE_GUARD_ILLEGAL_PERSISTED_COMPONENT_COUNT=sum(sum(r['persisted_components'].values()) for r in cases),NEW_REFERENCE_GUARD_VALID_CASE_SUCCESS_COUNT=int(valid['status']=='PASS'),NEW_REFERENCE_GUARD_FAILURE_COUNT=sum(r['status']=='FAIL' for r in cases)+int(valid['status']=='FAIL'))
    result=dict(status='FAIL' if counts['NEW_REFERENCE_GUARD_FAILURE_COUNT'] else 'PASS',cases=cases,valid=valid,counts=counts,V2_CONFLICT_ROOT_CAUSE=conflict['root_cause'],v2_conflict=conflict,interpretation='Illegal accepted snapshots are structurally complete, not orphan/partial rows. Persisted illegal components nevertheless violate the required rejection postcondition. The production DB is unchanged.')
    output('MA001_NEW_PREDICTION_GUARD_FINAL_V3.json',result)
    print('FRESH_GUARD',json.dumps(counts),flush=True);return result

if __name__=='__main__':run()
