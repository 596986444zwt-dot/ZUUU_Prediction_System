"""Scheduled and event scores stay separate; aggregate only current confirmed truth versions."""
import csv
import json
import math
import os
import statistics
import uuid
from collections import defaultdict
from pathlib import Path
from .contracts import iso, now, identity, canonical


def season(day):
    month = int(day[5:7])
    return 'DJF' if month in (12, 1, 2) else 'MAM' if month in (3, 4, 5) else 'JJA' if month in (6, 7, 8) else 'SON'


def evaluate(db, export_dir=None):
    latest = {r['target_date']: dict(r) for r in db.c.execute('SELECT * FROM t0_ground_truth_evidence ORDER BY rowid')}
    settlements = [dict(r) for r in db.c.execute('''SELECT e.*,s.target_date,s.cutoff_bjt,s.trigger_type,
        s.prediction_time,m.candidate_status FROM t0_settlement e
        JOIN t0_prediction_snapshot s ON s.prediction_id=e.prediction_id
        JOIN t0_prediction_method_output m ON m.prediction_id=e.prediction_id AND m.method=e.method''')
        if latest[r['target_date']]['truth_id'] == r['truth_id']
        and latest[r['target_date']]['status']=='CONFIRMED_EXPERIMENTAL_TARGET']
    groups = defaultdict(list)
    for row in settlements:
        for kind, label in [('overall', 'ALL'), ('cutoff', row['cutoff_bjt']), ('date', row['target_date']),
                            ('month', row['target_date'][:7]), ('season', season(row['target_date']))]:
            groups[(row['method'], row['trigger_type'], row['candidate_status'], kind, label)].append(row)
    result = []
    for (method, trigger, candidate, kind, label), rows in sorted(groups.items()):
        result.append(dict(method=method, trigger_type=trigger, candidate_status=candidate, group_type=kind, group_label=label,
                           N=len(rows), top1_rate=statistics.fmean(r['integer_exact_hit'] for r in rows),
                           within_1c_rate=statistics.fmean(r['within_1c'] for r in rows),
                           mae=statistics.fmean(r['absolute_error'] for r in rows),
                           rmse=math.sqrt(statistics.fmean(r['continuous_error']**2 for r in rows)),
                           bias=statistics.fmean(r['continuous_error'] for r in rows)))
    # An absent score is not zero error: keep all six main scheduled cutoffs visible.
    existing = {(r['method'],r['trigger_type'],r['candidate_status'],r['group_type'],r['group_label']) for r in result}
    for method in ('LEVEL0','L1_A'):
        for hour in (8,10,12,14,16,18):
            candidate = 'EXPERIMENTAL_SHADOW' if method=='L1_A' and hour<12 else 'EXPERIMENTAL_ACTIVE_CANDIDATE'
            key = (method,'SCHEDULED',candidate,'cutoff',f'{hour:02}:00')
            if key not in existing:
                result.append(dict(method=method,trigger_type='SCHEDULED',candidate_status=candidate,
                                   group_type='cutoff',group_label=f'{hour:02}:00',N=0,top1_rate=None,
                                   within_1c_rate=None,mae=None,rmse=None,bias=None))
    result.sort(key=lambda r:(r['method'],r['trigger_type'],r['candidate_status'],r['group_type'],r['group_label']))
    coverage = [dict(r) for r in db.c.execute('''SELECT m.method,s.cutoff_bjt,s.trigger_type,m.status,
        COUNT(*) AS attempted_N,SUM(m.prediction IS NOT NULL) AS valid_N
        FROM t0_prediction_method_output m JOIN t0_prediction_snapshot s ON s.prediction_id=m.prediction_id
        GROUP BY m.method,s.cutoff_bjt,s.trigger_type,m.status ORDER BY s.cutoff_bjt,m.method''')]
    basis = dict(settlement_ids=sorted(r['settlement_id'] for r in settlements),
                 latest_truth={day: r['truth_id'] for day, r in latest.items()}, coverage=coverage,
                 evaluation_version='T0_FORWARD_EVALUATION_V1_EXPLICIT_EMPTY_CUTOFFS',
                 experiment_status='T0_EXPERIMENTAL', validation='FORWARD_VALIDATION')
    eid = identity(basis)
    body = dict(basis, metrics=result, N_settled_method_outputs=len(settlements),
                N_attempted_method_outputs=sum(r['attempted_N'] for r in coverage),
                N_missing_or_unsettled_method_outputs=sum(r['attempted_N'] for r in coverage)-len(settlements))
    if not db.get('t0_evaluation_state', 'evaluation_id', eid):
        with db.c:
            db.insert('t0_evaluation_state', dict(evaluation_id=eid, created_at=iso(now()), payload_json=canonical(body)))
    if export_dir:
        folder = Path(export_dir)
        folder.mkdir(parents=True, exist_ok=True)
        # Immutable version exports first; latest pointer is explicitly an operational convenience.
        version = folder / (eid+'.json')
        if not version.exists():
            with version.open('x', encoding='utf-8') as handle:
                handle.write(canonical(body))
        for name, rows, headers in [('T0_FORWARD_EVALUATION.csv', result,
                ['method','trigger_type','candidate_status','group_type','group_label','N','top1_rate','within_1c_rate','mae','rmse','bias']),
                ('T0_FORWARD_COVERAGE.csv', coverage,['method','cutoff_bjt','trigger_type','status','attempted_N','valid_N'])]:
            tmp = folder / (name+'.'+uuid.uuid4().hex+'.tmp')
            with tmp.open('x', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp, folder/name)
    return body
