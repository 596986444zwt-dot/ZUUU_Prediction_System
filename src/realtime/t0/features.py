"""Transparent current information; no daily truth, historical assumptions or learned state."""
from .contracts import BJT, iso, utc, identity
from .selection import observations, select_run, interpolate, remaining


def context(db, cutoff, config):
    cutoff = utc(cutoff)
    day = cutoff.astimezone(BJT).date().isoformat()
    obs = observations(db, day, cutoff)
    run, curve, depth = select_run(db, day, cutoff)
    latest = obs[-1] if obs else None  # Latest valid time, then latest locally seen version.
    floor = max((r['temperature_c'] for r in obs), default=None)
    first_max = next((r for r in obs if r['temperature_c'] == floor), None)
    known_max = min((r for r in obs if r['temperature_c'] == floor),
                    key=lambda r: utc(r['first_seen_time']), default=None)
    rest = remaining(curve, day, cutoff) if run else []
    aligned = interpolate(curve, latest['observation_time']) if run and latest else None
    bias = latest['temperature_c'] - aligned if aligned is not None else None
    status = 'OK'
    if not obs:
        status = 'MISSING_OBS'
    elif (cutoff-utc(latest['observation_time'])).total_seconds() > config['observation_stale_seconds']:
        status = 'STALE_OBS'
    elif not run:
        status = 'MISSING_ECMWF'
    elif (cutoff-utc(run['run_time'])).total_seconds() > config['ecmwf_stale_seconds']:
        status = 'STALE_ECMWF'
    elif not rest:
        status = 'MISSING_REMAINING_TRAJECTORY'
    inputs = [dict(observation_id=r['observation_id'], observation_time=r['observation_time'],
                   first_seen_time=r['first_seen_time'], ingest_time=r['ingest_time'],
                   available_at=r['available_at'],
                   temperature_c=r['temperature_c'], raw_payload_hash=r['raw_payload_hash'],
                   receipt_id=r['receipt_id'], source=r['source'], message_class=r['message_class'],
                   is_cor=r['is_cor'], is_speci=r['is_speci']) for r in obs]
    return dict(target_date=day, data_cutoff_time=iso(cutoff), observation_ids=[r['observation_id'] for r in obs],
                observations=inputs, latest_zuuu_observation_id=latest['observation_id'] if latest else None,
                latest_zuuu_observation_time=latest['observation_time'] if latest else None,
                latest_zuuu_first_seen_time=latest['first_seen_time'] if latest else None,
                latest_temperature_c=latest['temperature_c'] if latest else None,
                Tmax_so_far=floor, time_of_tmax_so_far=first_max['observation_time'] if first_max else None,
                time_tmax_first_became_known=known_max['first_seen_time'] if known_max else None,
                ecmwf_run_id=run['run_id'] if run else None, ecmwf_run_time=run['run_time'] if run else None,
                ecmwf_first_seen_time=run['first_seen_time'] if run else None,
                ecmwf_download_complete_time=run['download_complete_time'] if run else None,
                ecmwf_ready_at=run['ready_at'] if run else None,
                ecmwf_available_at=run['available_at'] if run else None,
                ecmwf_receipt_id=run['receipt_id'] if run else None,
                ecmwf_file_hash=run['file_sha256'] if run else None,
                ecmwf_product_identity=run['product_identity'] if run else None,
                fallback_depth=depth, aligned_ecmwf_temperature=aligned, current_bias=bias,
                remaining_trajectory=rest, remaining_trajectory_identity=identity(rest),
                remaining_day_max=max((r['temperature_c'] for r in rest), default=None),
                source_identities={'zuuu': 'AWC', 'ecmwf': 'Open-Meteo Single Runs API'},
                status=status, version_policy='ALL_KNOWN_VERSIONS_FLOOR_LATEST_LOCALLY_SEEN_VERSION_FOR_CURRENT_BIAS')
