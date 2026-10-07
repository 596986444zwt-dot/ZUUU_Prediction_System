"""R1 precommit checks. IMPLEMENTATION DETAIL: no model/protocol change."""
import json
import math
from .contracts import require, utc, targets
from src.features.registry import REGISTRY
from src.probability.contracts import SUPPORT, METHODS, MODELS


def _row(db, table, key):
    row = db.c.execute(f'SELECT payload_json FROM realtime_{table} WHERE record_id=?', (key,)).fetchone()
    require(row is not None, 'UNRESOLVED_' + table.upper())
    return json.loads(row[0])


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _identity(part, metadata, required=True):
    for key in ('horizon', 'target_business_date', 'prediction_issue_time'):
        if required or key in part:
            require(key in part, 'MISSING_COMPONENT_IDENTITY')
            a, b = part[key], metadata[key]
            require(utc(a) == utc(b) if key == 'prediction_issue_time' else a == b,
                    'SNAPSHOT_' + key.upper() + '_MISMATCH')


def _mass(p):
    pmf = p.get('pmf')
    require(isinstance(pmf, list) and len(pmf) == len(SUPPORT), 'PMF_STRUCTURE')
    require(all(_finite(v) and v >= 0 for v in pmf), 'PMF_NEGATIVE_OR_NONFINITE')
    require(abs(math.fsum(pmf) - 1) < 1e-10, 'PMF_SUM')
    require(p.get('support_min', SUPPORT[0]) == SUPPORT[0] and
            p.get('support_max', SUPPORT[-1]) == SUPPORT[-1], 'PMF_SUPPORT')
    cumulative = 0.; cdf = []
    for v in pmf:
        cumulative += v; cdf.append(cumulative)
    for key, expected in [('cdf', cdf), ('survival', [1.] + [1-v for v in cdf[:-1]])]:
        if key in p:
            values = p[key]
            require(isinstance(values, list) and len(values) == len(expected) and
                    all(_finite(v) and abs(v-e) <= 1e-12 for v, e in zip(values, expected)),
                    'PMF_' + key.upper() + '_RELATION')


def validate_snapshot(db, feature, continuous, probability, metadata):
    """Read only. Fail before inserting any component or advancing any state.

    Phase9 probability bodies and global states do not contain a current horizon /
    target / issue. Their identity is established by the resolved state, eligible
    history and corresponding forecast; optional explicit identities are checked.
    """
    require(all(isinstance(p, dict) for p in (feature, continuous, probability, metadata)), 'SNAPSHOT_STRUCTURE')
    h = metadata.get('horizon'); day = metadata.get('target_business_date')
    require(h in MODELS, 'SNAPSHOT_HORIZON')
    issue = utc(metadata['prediction_issue_time'])
    require(targets(issue)[h] == day, 'SNAPSHOT_TARGET_ISSUE_RELATION')
    _identity(feature, metadata); _identity(continuous, metadata); _identity(probability, metadata, False)
    require(feature.get('feature_version') == continuous.get('feature_version') == 'FEATURE_V1', 'FEATURE_VERSION')
    names = {r['feature_name'] for r in REGISTRY}
    require(set(feature.get('values', {})) == names and set(feature.get('lineage', {})) == names, 'FEATURE_CONTRACT_ERROR')
    require(utc(feature['run_available_time']) <= issue and utc(feature['selected_run']) <= issue, 'FUTURE_FEATURE')
    run = _row(db, 'ecmwf_raw_runs', feature['selected_version'])
    def availability(r):
        return r.get('replay_available_time', r['actual_ingest_time']) if db.namespace == 'SIMULATION' else r['actual_ingest_time']
    require(utc(availability(run)) <= issue and utc(availability(run)) == utc(feature['run_available_time']) and
            utc(run['run_time']) == utc(feature['selected_run']), 'FEATURE_RUN_LINEAGE')
    for key in ('selected_run', 'selected_version'):
        if key in metadata: require(metadata[key] == feature[key], 'SNAPSHOT_RUN_MISMATCH')
    for name, lineage in feature['lineage'].items():
        require(utc(lineage['feature_available_time']) <= issue and utc(lineage['prediction_issue_time']) == issue, 'FEATURE_AVAILABILITY_VIOLATION')
        value = feature['values'][name]
        require(value is None or _finite(value), 'FEATURE_NONFINITE')
        for reference in lineage.get('input_ids', []):
            if isinstance(reference, str):
                r = _row(db, 'ecmwf_raw_runs', reference)
                require(utc(availability(r)) <= issue and utc(r['run_time']) <= issue, 'FEATURE_INPUT_FUTURE')
            else:
                require(isinstance(reference, dict) and reference['horizon'] == h and reference['date'] < day and
                        utc(reference['eligibility']) <= issue, 'FEATURE_LABEL_LINEAGE')
    model = _row(db, 'model_registry', continuous['model_state_id'])
    require(model.get('record_id') == continuous['model_state_id'] and model.get('horizon') == h and
            model.get('formal_horizon', h) == h and model.get('model_family') == MODELS[h], 'ILLEGAL_MODEL_IDENTITY_OR_FAMILY')
    require(model['model_version'] == continuous['model_version'] and
            model['artifact_sha256'] == continuous['model_asset_hash'], 'MODEL_IDENTITY_CONFLICT')
    require(utc(model['training_cutoff']) == utc(continuous['training_cutoff']) and
            utc(model['training_cutoff']) <= issue, 'FUTURE_MODEL_CUTOFF')
    version = metadata.get('probability_state_version')
    require(version and version == probability.get('probability_state_version'), 'PROBABILITY_STATE_REFERENCE_MISMATCH')
    state = db.resolve_probability_state(version)
    require(utc(state['cutoff']) <= issue, 'FUTURE_STATE_CUTOFF')
    if 'horizon' in state: require(state['horizon'] == h, 'STATE_HORIZON')
    if 'target_business_date' in state: require(state['target_business_date'] == day, 'STATE_TARGET')
    if 'data_cutoff_time' in metadata: require(utc(metadata['data_cutoff_time']) <= issue, 'FUTURE_SNAPSHOT_CUTOFF')
    forecasts = {'ML': continuous.get('continuous_prediction_c'), 'RAW': continuous.get('raw_ecmwf_prediction'), 'MOS': continuous.get('mos_prediction')}
    require(_finite(forecasts['ML']), 'CONTINUOUS_NONFINITE')
    require(forecasts['RAW'] == feature['values']['ecmwf_tmax_c'] and
            forecasts['MOS'] == feature['values']['m6_corrected_temperature_c'], 'CONTINUOUS_FEATURE_VALUE_MISMATCH')
    residuals = {r['record_id']: r for r in state['residuals']}
    cases = {r['record_id']: r for r in state['cases']}
    def eligible(r, origin, method=None):
        require(r['horizon'] == h and r['target_business_date'] < day and
                utc(r['eligibility']) <= issue and utc(r['eligibility']) <= utc(state['cutoff']) and
                utc(r['issue']) < issue and r['origin'] == origin and
                (method is None or r['method'] == method), 'PROBABILITY_LINEAGE_SEMANTICS')
    def validate_probability(p):
        _identity(p, metadata, False); _mass(p)
        if 'probability_state_version' in p: require(p['probability_state_version'] == version, 'PROBABILITY_STATE_REFERENCE_MISMATCH')
        origin = p.get('origin'); method = p.get('method')
        require(origin in forecasts and method in METHODS and _finite(forecasts[origin]) and
                p.get('continuous_prediction') == forecasts[origin], 'PROBABILITY_CONTINUOUS_MISMATCH')
        ids = p.get('history_ids', [])
        require(len(ids) == len(set(ids)) and p.get('residual_n', len(ids)) == len(ids), 'RESIDUAL_COUNT')
        for key in ids:
            require(key in residuals, 'UNRESOLVED_RESIDUAL'); eligible(residuals[key], origin)
        calibration = p.get('calibration_ids', [])
        require(len(calibration) == len(set(calibration)) and p.get('calibration_n', 0) == len(calibration), 'CALIBRATION_COUNT')
        for key in calibration:
            require(key in cases, 'UNRESOLVED_CALIBRATION'); eligible(cases[key], origin, method.removesuffix('_CAL'))
        for level, selection in p.get('selection', {}).items():
            require(level in forecasts, 'SELECTION_ORIGIN')
            for option in selection['options']:
                require(option[0] in METHODS and len(option[2]) == len(set(option[2])), 'SELECTION_STRUCTURE')
                for key in option[2]:
                    require(key in cases, 'UNRESOLVED_SELECTION'); eligible(cases[key], level, option[0])
    validate_probability(probability)
    for variant in probability.get('variants', []): validate_probability(variant)
