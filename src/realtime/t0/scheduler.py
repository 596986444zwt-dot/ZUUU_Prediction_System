"""Persist every due schedule slot; no backfill disguised as a forward forecast."""
from datetime import datetime
from .contracts import BJT, utc, identity
from .snapshot import save


def due(db, issue, config):
    issue = utc(issue)
    local = issue.astimezone(BJT)
    results = []
    for hour in config['scheduled_hours_bjt']:
        cutoff = datetime.fromisoformat(local.date().isoformat()+f'T{hour:02}:00:00+08:00')
        if cutoff > issue:
            continue
        rid = identity({'namespace': db.namespace, 'target_date': local.date().isoformat(), 'scheduled_hour': hour})
        if db.get('t0_prediction_snapshot', 'prediction_id', rid):
            continue
        late = (issue-cutoff).total_seconds() > config['schedule_grace_seconds']
        results.append(save(db, cutoff, 'SCHEDULED', config, prediction_id=rid, missed=late))
    return results
