"""Guard all source references and equations before one atomic immutable insert."""
from .contracts import BJT, iso, utc, now, canonical, identity, METHOD_VERSION, code_version, require
from .features import context
from .methods import calculate


def save(db, cutoff, trigger_type, config, prediction_id=None, missed=False, expected_context=None, fail_after=None):
    require(trigger_type in ('SCHEDULED', 'EVENT', 'STARTUP', 'RECOVERY'), 'INVALID_TRIGGER')
    cutoff = utc(cutoff)
    created = now()
    require(cutoff <= created, 'FUTURE_PREDICTION_CUTOFF')
    features = context(db, cutoff, config)
    if expected_context is not None:
        require(canonical(expected_context) == canonical(features), 'SNAPSHOT_CONTEXT_TAMPERED')
    if missed:
        # A missed schedule is archived, never replayed later as a forward prediction.
        features = dict(features, status='NO_FORECAST_SCHEDULE_MISSED')
    outputs = calculate(features, cutoff)
    code = code_version()
    config_hash = identity(config)
    rid = prediction_id or identity({'namespace': db.namespace, 'cutoff': iso(cutoff), 'trigger': trigger_type})
    old = db.get('t0_prediction_snapshot', 'prediction_id', rid)
    if old:
        body = __import__('json').loads(old['payload_json'])
        require(body['data_cutoff_time'] == iso(cutoff) and old['trigger_type'] == trigger_type, 'CONFLICTING_SNAPSHOT_IDENTITY')
        return rid, False
    body = dict(features, prediction_id=rid, prediction_time=iso(cutoff), cutoff_bjt=cutoff.astimezone(BJT).strftime('%H:%M'),
                created_at=iso(created), trigger_type=trigger_type, snapshot_execution_delay_seconds=(created-cutoff).total_seconds(),
                code_version=code, config_version=config_hash, config_contract_version=config['version'], config=config,
                method_version=METHOD_VERSION, namespace=db.namespace, experiment_status='T0_EXPERIMENTAL',
                validation='FORWARD_VALIDATION', method_outputs=outputs,
                corrected_remaining_trajectory=[dict(r, temperature_c=r['temperature_c']+features['current_bias'])
                                                for r in features['remaining_trajectory']] if features['current_bias'] is not None else None)
    with db.c:
        db.insert('t0_prediction_snapshot', dict(prediction_id=rid, prediction_time=iso(cutoff), target_date=features['target_date'],
                  cutoff_bjt=body['cutoff_bjt'], trigger_type=trigger_type, latest_observation_id=features['latest_zuuu_observation_id'],
                  ecmwf_run_id=features['ecmwf_run_id'], tmax_so_far=features['Tmax_so_far'],
                  remaining_trajectory_hash=features['remaining_trajectory_identity'], status=features['status'],
                  code_version=code, config_version=config_hash, method_version=METHOD_VERSION,
                  created_at=iso(created), payload_json=canonical(body)))
        if fail_after == 'SNAPSHOT':
            raise RuntimeError('INJECTED_SNAPSHOT_FAILURE')
        for oid in features['observation_ids']:
            db.insert('t0_snapshot_observation', dict(prediction_id=rid, observation_id=oid))
        for output in outputs:
            db.insert('t0_prediction_method_output', dict(prediction_id=rid, method=output['method'], prediction=output['prediction'],
                      integer_prediction=output['integer_prediction'], status=output['status'],
                      candidate_status=output['candidate_status'], payload_json=canonical(output)))
            if fail_after == output['method']:
                raise RuntimeError('INJECTED_METHOD_FAILURE')
    return rid, True
