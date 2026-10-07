"""Positive four-component transaction using actual frozen production payloads."""
from .common import *

def run():
    from src.realtime.archive import Archive
    c=snapshot(ROOT/'database/phase10_realtime_v1.db');snap=payloads(c,'realtime_prediction_snapshots')[-1]
    def item(table,key):return next(r for r in payloads(c,'realtime_'+table) if r['_id']==key)
    feature=item('feature_snapshots',snap['feature_id']);continuous=item('continuous_predictions',snap['continuous_id']);probability=item('probability_predictions',snap['probability_id']);state=item('probability_state',snap['probability_state_version'])
    clean=lambda row:{k:v for k,v in row.items() if k not in ('_id','_created','namespace')}
    feature,continuous,probability,metadata=map(clean,(feature,continuous,probability,snap));state=clean(state)
    source_pmf=digest_bytes(json.dumps(probability['pmf'],separators=(',',':')).encode())
    probability['probability_state_version']=metadata['probability_state_version']
    path=TEMP/'guard_rich_valid_frozen_payload.db'
    if path.exists():raise RuntimeError('FRESH_FIXTURE_REQUIRED')
    a=Archive(path,'SIMULATION',create=True)
    try:
        with a.c:a.insert('probability_state',state,state['state_id'])
        accepted=a.snapshot('FROZEN_FULL_VALID_CASE',feature,continuous,probability,metadata)
        counts={t:len(a.rows(t)) for t in ('feature_snapshots','continuous_predictions','probability_predictions','prediction_snapshots')}
        saved=a.rows('probability_predictions')[0];pmf_hash=digest_bytes(json.dumps(saved['pmf'],separators=(',',':')).encode())
        valid=accepted and all(n==1 for n in counts.values()) and len(feature['values'])==102 and len(saved['pmf'])==161 and source_pmf==pmf_hash and stamp(state['cutoff'])<=stamp(metadata['prediction_issue_time']) and all(eligible(r['target_business_date'])<=stamp(metadata['prediction_issue_time']) for r in state['residuals']+state['cases'])
        output('MA001_RICH_VALID_SNAPSHOT_FINAL_V3.json',dict(status='PASS' if valid else 'FAIL',accepted=accepted,components=counts,feature_count=len(feature['values']),pmf_support_count=len(saved['pmf']),original_pmf_sha256=source_pmf,saved_pmf_sha256=pmf_hash,state_cutoff=state['cutoff'],prediction_issue=metadata['prediction_issue_time'],state_id=state['state_id'],residual_n=len(state['residuals']),calibration_n=len(state['cases']),source_snapshot_id=snap['_id'],source_database_sha256=digest(ROOT/'database/phase10_realtime_v1.db'),fixture=str(path),namespace='SIMULATION',reference_policy='Original legacy alias reference in test copy resolved to the current canonical state; source/PMF/continuous payload unchanged. No production write.'))
        assert valid
    finally:a.close();c.close()
    print('RICH_VALID_SNAPSHOT PASS 102 features 161 PMF entries 4 components',flush=True)

def digest_bytes(data):return hashlib.sha256(data).hexdigest()
if __name__=='__main__':run()
