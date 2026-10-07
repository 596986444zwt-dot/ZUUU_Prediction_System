import copy
from datetime import timedelta
from src.realtime.contracts import utc, iso, targets
from src.ecmwf_contract import HOURLY_VARIABLES, EXPECTED_UNITS
from src.realtime.collectors import archive_ecmwf
from src.realtime.features import construct, runtime_context


def lawful(db, h='T1', issue='2026-10-02T13:00:00+00:00', state=None, value=21.):
    issue = utc(issue); day = targets(issue)[h]
    run = issue.replace(hour=6, minute=0, second=0, microsecond=0)
    payload = dict(utc_offset_seconds=0, hourly_units=EXPECTED_UNITS.copy(), hourly={
        'time': [(run+timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in range(72)],
        **{k: [20. if k == 'temperature_2m' else None]*72 for k in HOURLY_VARIABLES}})
    archive_ecmwf(db, payload, run, issue)
    runs, curves = runtime_context(db, issue)
    f = construct(day, h, issue, runs, curves, [])
    model = dict(record_id='model/'+h, horizon=h, formal_horizon=h,
                 model_family='RIDGE' if h == 'T1' else 'LIGHTGBM', model_version='MODEL_'+h+'_V1',
                 artifact_sha256='fixture-frozen-model-hash', training_cutoff='2026-09-01T00:00:00+00:00')
    s = copy.deepcopy(state) if state else dict(state_id='state', cutoff='2026-10-02T12:00:00+00:00', residuals=[], cases=[])
    with db.c:
        db.insert('model_registry', model, model['record_id'])
        db.insert('probability_state', s, s['state_id'])
    common = dict(horizon=h, target_business_date=day, prediction_issue_time=iso(issue))
    m = dict(common, feature_version='FEATURE_V1', continuous_prediction_c=value,
             raw_ecmwf_prediction=20., mos_prediction=None, model_state_id=model['record_id'],
             model_version=model['model_version'], model_asset_hash=model['artifact_sha256'], training_cutoff=model['training_cutoff'])
    p = dict(probability_state_version=s['state_id'], continuous_prediction=value, origin='ML',
             method='GAUSSIAN_EXPANDING', pmf=[1/161]*161, variants=[], history_ids=[], residual_n=0,
             calibration_n=0)
    meta = dict(common, probability_state_version=s['state_id'], daily_anchor=True, status='OK')
    return f, m, p, meta
