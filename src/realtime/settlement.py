"""Daily target adapter and forward-only eligible state advancement."""
import json
from datetime import date,timedelta
import numpy as np
from src.builders.zuuu_ground_truth_candidate_builder import build_candidate_for_day
from src.probability.distribution import score
from .contracts import utc,iso,BJT,eligibility,identity,require,now
from .features import season

def settle(db, issue):
    today=utc(issue).astimezone(BJT).date().isoformat()
    normalized=db.rows('zuuu_normalized')
    prior=db.rows('daily_ground_truth')
    result=[]
    for day in sorted({r['business_date'] for r in normalized if r['business_date']<today}):
        rows=[r for r in normalized if r['business_date']==day and utc(r['actual_ingest_time'])<=utc(issue)]
        prior_final=next((r for r in reversed(prior) if r['business_date_bjt']==day and r['settlement_status']=='FINAL'),None)
        raw_ids=sorted(r['raw_id'] for r in rows)
        if prior_final and set(raw_ids)==set(prior_final.get('source_raw_ids',prior_final['raw_identity_map'].values())):continue
        versions={}
        for r in rows:versions.setdefault(r['observation_time'],[]).append(r)
        # No evidence-based supersession exists: conflict blocks finalization.
        conflict=any(len({r['temperature_c'] for r in group})>1 for group in versions.values())
        canonical=[sorted(group,key=lambda r:r['actual_ingest_time'])[0] for group in versions.values()]
        inputs=[];mapping={}
        for n,r in enumerate(sorted(canonical,key=lambda r:r['observation_time']),1):
            mapping[str(n)]=r['raw_id']
            inputs.append(dict(id=n,bronze_raw_id=n,observation_time_bjt=utc(r['observation_time']).astimezone(BJT).isoformat(),
                temperature_c=r['temperature_c'],is_correction=int(r['is_corrected']),message_class='COR' if r['is_corrected'] else r['report_type'],recovery_reason=None))
        if not inputs:continue
        target=build_candidate_for_day(date.fromisoformat(day),inputs)
        status='PENDING_VERSION_CONFLICT' if conflict else 'FINAL' if target['hourly_coverage_count']==24 and target['observation_count']>=24 else 'INCOMPLETE'
        if prior_final:status='PENDING_AFTER_FINAL_DATA'
        settled=now() if db.namespace=='PRODUCTION' else utc(issue)
        record=dict(target,settlement_status=status,settlement_time=iso(settled),raw_identity_map=mapping,
                    source_raw_ids=raw_ids,label_eligibility_time=iso(max(eligibility(day),settled)),rule='DAILY_TMAX_RULE_V1')
        rid=identity({'day':day,'ids':sorted(r['raw_id'] for r in rows),'status':status})
        if any(r['_id']==rid for r in db.rows('daily_ground_truth')):continue
        with db.c:
            db.insert('daily_ground_truth',record,rid)
            db.insert('daily_settlement',dict(target_record_id=rid,date=day,status=status,time=iso(settled)),rid)
        result.append(record)
        import logging
        logging.getLogger('phase10.settlement').info('%s %s coverage=%d',day,status,target['hourly_coverage_count'])
    return result

def evaluate(db,issue):
    latest={r['business_date_bjt']:r for r in db.rows('daily_ground_truth') if utc(r['settlement_time'])<=utc(issue)}
    targets={day:r for day,r in latest.items() if r['settlement_status']=='FINAL'}
    old={r['_id'] for r in db.rows('daily_evaluation')}
    continuous={r['_id']:r for r in db.rows('continuous_predictions')}
    probability={r['_id']:r for r in db.rows('probability_predictions')}
    for snapshot in db.rows('prediction_snapshots'):
        day=snapshot['target_business_date'];rid=snapshot['_id']
        if day not in targets or rid in old:continue
        target=targets[day];ml=continuous[snapshot['continuous_id']];p=probability[snapshot['probability_id']]
        actual=target['daily_tmax_c'];error=ml['continuous_prediction_c']-actual
        metrics=score(np.array(p['pmf']),actual)
        with db.c:db.insert('daily_evaluation',dict(snapshot_id=rid,date=day,horizon=snapshot['horizon'],actual=actual,
            error=error,absolute_error=abs(error),squared_error=error*error,settlement_id=target['_id'],evaluation_time=iso(issue),**metrics),rid)

def advance(db,state,history,issue):
    """One immutable 21 BJT daily anchor; no multiplication by refresh count."""
    import copy
    require(not db.c.in_transaction, 'STATE_ADVANCE_REQUIRES_OWN_TRANSACTION')
    published_state, published_history = state, history
    state, history = copy.deepcopy(state), copy.deepcopy(history)
    require(utc(issue)>=utc(state['cutoff']),'STATE_CANNOT_MOVE_BACKWARD')
    latest={r['business_date_bjt']:r for r in db.rows('daily_ground_truth') if utc(r['settlement_time'])<=utc(issue)}
    finals={day:r for day,r in latest.items() if r['settlement_status']=='FINAL' and utc(r['label_eligibility_time'])<=utc(issue)}
    seen={r['record_id'] for r in state['residuals']}
    continuous={r['_id']:r for r in db.rows('continuous_predictions')}
    probabilities={r['_id']:r for r in db.rows('probability_predictions')}
    anchors={}
    for r in db.rows('prediction_snapshots'):
        if not r.get('daily_anchor'):continue
        anchors.setdefault((r['target_business_date'],r['horizon']),r)
    changed=False
    for (day,h),snap in sorted(anchors.items()):
        if day not in finals or day+'/'+h+'/ML' in seen:continue
        t=finals[day];actual=t['daily_tmax_c'];ml=continuous[snap['continuous_id']];p=probabilities[snap['probability_id']]
        for origin,key in [('ML','continuous_prediction_c'),('RAW','raw_ecmwf_prediction'),('MOS','mos_prediction')]:
            value=ml[key]
            if value is None:continue
            state['residuals'].append(dict(record_id=day+'/'+h+'/'+origin,horizon=h,target_business_date=day,issue=snap['prediction_issue_time'],
                eligibility=t['label_eligibility_time'],origin=origin,actual=actual,continuous_prediction=value,residual=actual-value,status='FORWARD_OOS',snapshot_id=snap['_id']))
        for variant in p.get('variants',[]):
            state['cases'].append(dict(variant,record_id=day+'/'+h+'/'+variant['origin']+'/'+variant['method'],
                horizon=h,target_business_date=day,issue=snap['prediction_issue_time'],eligibility=t['label_eligibility_time'],actual=actual,
                **score(np.array(variant['pmf']),actual)))
        history.append(dict(business_date_bjt=day,horizon=h,issue_time_utc=snap['prediction_issue_time'],day_end_utc=iso(datetime_end(day)),
            label_eligibility_time_bjt=t['label_eligibility_time'],settlement_time=t['settlement_time'],raw_error=ml['raw_ecmwf_prediction']-actual,season=season(day)))
        changed=True
    if changed:
        previous=state.get('state_id');state['cutoff']=iso(issue);state['previous_state_id']=previous
        state['state_id']=identity({k:v for k,v in state.items() if k!='state_id'})
        with db.c:db.insert('probability_state',state,state['state_id'])
        # Publish only after the transaction context has successfully COMMITted.
        published_state.clear(); published_state.update(state)
        published_history[:] = history
    return changed

def datetime_end(day):
    from datetime import datetime
    return datetime.fromisoformat(day+'T00:00:00+08:00')+timedelta(days=1)
