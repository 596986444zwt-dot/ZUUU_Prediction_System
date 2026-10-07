"""Append experimental daily truth evidence and scores, never edit predictions or formal truth."""
import json
from datetime import date, datetime, timedelta
from src.builders.zuuu_ground_truth_candidate_builder import build_candidate_for_day
from .contracts import BJT, utc, iso, now, identity, canonical, require


def settle(db, issue, config):
    issue = utc(issue)
    today = issue.astimezone(BJT).date().isoformat()
    days = [r[0] for r in db.c.execute('''SELECT DISTINCT business_date_bjt FROM t0_zuuu_receipt_ledger
        WHERE business_date_bjt<? AND qc_status='VALID' ORDER BY business_date_bjt''', (today,))]
    changes = 0
    for day in days:
        end = datetime.fromisoformat(day+'T00:00:00+08:00') + timedelta(days=config['settlement_delay_days'])
        if issue < end:
            continue
        rows = [dict(r) for r in db.c.execute('''SELECT o.* FROM t0_zuuu_receipt_ledger o
            JOIN t0_receipt_publication p ON p.receipt_id=o.receipt_id
            WHERE business_date_bjt=? AND qc_status='VALID' AND first_seen_time<=? AND ingest_time<=?
            AND p.published_at<=? ORDER BY observation_time,first_seen_time,observation_id''', (day, iso(issue), iso(issue), iso(issue)))]
        if not rows:
            continue
        ids = [r['observation_id'] for r in rows]
        prior = db.c.execute('SELECT * FROM t0_ground_truth_evidence WHERE target_date=? ORDER BY rowid DESC LIMIT 1', (day,)).fetchone()
        if prior and json.loads(prior['payload_json'])['observation_ids'] == ids:
            continue
        groups = {}
        for r in rows:
            groups.setdefault(r['observation_time'], set()).add(r['temperature_c'])
        conflict = any(len(values) > 1 for values in groups.values())
        # Reuse the audited pure DAILY_TMAX_RULE_V1 calculation; no writer invoked.
        inputs = [dict(id=n, bronze_raw_id=n, observation_time_bjt=utc(r['observation_time']).astimezone(BJT).isoformat(),
                       temperature_c=r['temperature_c'], is_correction=r['is_cor'],
                       message_class=r['message_class'], recovery_reason=None) for n, r in enumerate(rows, 1)]
        target = build_candidate_for_day(date.fromisoformat(day), inputs)
        complete = target['hourly_coverage_count'] == 24 and len(groups) >= 24
        status = 'PENDING_VERSION_CONFLICT' if conflict else 'CONFIRMED_EXPERIMENTAL_TARGET' if complete else 'INCOMPLETE'
        if prior and prior['status'] in ('CONFIRMED_EXPERIMENTAL_TARGET', 'PENDING_AFTER_CONFIRMED_DATA'):
            status = 'PENDING_AFTER_CONFIRMED_DATA'
        tid = identity({'date': day, 'ids': ids, 'status': status, 'rule': 'DAILY_TMAX_RULE_V1'})
        body = dict(target, observation_ids=ids, source_identity_map={str(n): r['observation_id'] for n, r in enumerate(rows, 1)},
                    label_eligibility_time=iso(end), confirmation_basis='24_EXACT_HOURLY_SLOTS_NO_VERSION_CONFLICT_DPLUS2',
                    experiment_status='T0_EXPERIMENTAL', validation='FORWARD_VALIDATION',
                    formal_ground_truth_modified=False, target_admission='REQUIRES_INDEPENDENT_AUDIT_BEFORE_FORMAL_ML')
        if not db.get('t0_ground_truth_evidence', 'truth_id', tid):
            with db.c:
                db.insert('t0_ground_truth_evidence', dict(truth_id=tid, target_date=day,
                          actual_integer_tmax=int(target['daily_tmax_c']) if status=='CONFIRMED_EXPERIMENTAL_TARGET' else None,
                          confirmation_time=iso(now()), status=status, supersedes_truth_id=prior['truth_id'] if prior else None,
                          payload_json=canonical(body)))
            changes += 1
    # Every successful snapshot gets two independent scores against currently confirmed evidence.
    latest = {r['target_date']: dict(r) for r in db.c.execute('SELECT * FROM t0_ground_truth_evidence ORDER BY rowid')}
    for day, truth in latest.items():
        if truth['status'] != 'CONFIRMED_EXPERIMENTAL_TARGET' or utc(truth['confirmation_time']) > issue:
            continue
        actual = truth['actual_integer_tmax']
        outputs = db.c.execute('''SELECT m.* FROM t0_prediction_method_output m
            JOIN t0_prediction_snapshot s ON s.prediction_id=m.prediction_id
            WHERE s.target_date=? AND m.prediction IS NOT NULL''', (day,)).fetchall()
        for output in outputs:
            sid = identity({'prediction_id': output['prediction_id'], 'method': output['method'], 'truth': truth['truth_id']})
            if db.get('t0_settlement', 'settlement_id', sid):
                continue
            error = output['prediction'] - actual
            ip = output['integer_prediction']
            with db.c:
                db.insert('t0_settlement', dict(settlement_id=sid, prediction_id=output['prediction_id'], method=output['method'],
                          truth_id=truth['truth_id'], actual_integer_tmax=actual, continuous_error=error,
                          absolute_error=abs(error), integer_prediction=ip, integer_exact_hit=int(ip==actual),
                          within_1c=int(abs(ip-actual)<=1), created_at=iso(now())))
            changes += 1
    return changes
