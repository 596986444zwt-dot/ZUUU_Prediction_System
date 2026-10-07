"""SQL as-of first; same-vintage full-day contract, no estimated historical arrivals."""
import json
from datetime import datetime, timedelta
from .contracts import BJT, utc, iso, require


def observations(db, target_date, cutoff):
    cutoff = utc(cutoff)
    return [dict(r) for r in db.c.execute('''SELECT o.*,p.published_at AS available_at
        FROM t0_zuuu_receipt_ledger o JOIN t0_receipt_publication p ON p.receipt_id=o.receipt_id
        WHERE business_date_bjt=? AND first_seen_time<=? AND ingest_time<=?
        AND observation_time<=? AND p.published_at<=? AND qc_status='VALID'
        ORDER BY observation_time,first_seen_time,observation_id''',
        (target_date, iso(cutoff), iso(cutoff), iso(cutoff), iso(cutoff)))]


def select_run(db, target_date, cutoff):
    cutoff = utc(cutoff)
    start = datetime.fromisoformat(target_date+'T00:00:00+08:00').astimezone(cutoff.tzinfo)
    required = {start + timedelta(hours=i) for i in range(24)}
    legal = db.c.execute('''SELECT r.*,p.published_at AS available_at FROM t0_ecmwf_receipt_ledger r
        JOIN t0_receipt_publication p ON p.receipt_id=r.receipt_id
        WHERE availability_status='AVAILABLE' AND run_time<=? AND first_seen_time<=?
        AND download_complete_time<=? AND ready_at<=? AND p.published_at<=?
        ORDER BY run_time DESC,first_seen_time DESC,run_id DESC''', (iso(cutoff),)*5)
    for index, row in enumerate(legal):
        run = dict(row)
        curve = json.loads(run['payload_json'])['trajectory']
        values = {utc(r['valid_time']): r['temperature_c'] for r in curve}
        require(len(values) == len(curve), 'DUPLICATE_RUN_VALID_TIME')
        if required <= values.keys():
            return run, curve, index
    return None, [], None


def interpolate(curve, point):
    point = utc(point)
    values = {utc(row['valid_time']): row['temperature_c'] for row in curve}
    if point in values:
        return values[point]
    before = [t for t in values if t < point]
    after = [t for t in values if t > point]
    if not before or not after:
        return None
    lo, hi = max(before), min(after)
    require(hi-lo == timedelta(hours=1), 'FORECAST_INTERPOLATION_GAP')
    fraction = (point-lo).total_seconds() / 3600
    return values[lo] + fraction*(values[hi]-values[lo])


def remaining(curve, target_date, cutoff):
    cutoff = utc(cutoff)
    end = datetime.fromisoformat(target_date+'T00:00:00+08:00') + timedelta(days=1)
    result = [dict(r) for r in curve if cutoff <= utc(r['valid_time']) < end]
    if not any(utc(r['valid_time']) == cutoff for r in result):
        value = interpolate(curve, cutoff)
        if value is not None:
            result.insert(0, dict(valid_time=iso(cutoff), temperature_c=value, interpolated=True))
    return result
