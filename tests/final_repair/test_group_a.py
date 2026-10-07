import copy
import json
import sqlite3
from pathlib import Path
import pytest
from src.realtime.archive import Archive, TABLES
from src.realtime.contracts import ROOT, canonical
from tests.final_repair.fixtures import lawful


@pytest.fixture
def db(tmp_path):
    a = Archive(tmp_path/'attack.db', 'SIMULATION', create=True)
    yield a
    a.close()


def unchanged(db, before):
    assert db.semantic() == before
    assert db.integrity() == (['ok'], [])
    assert not db.c.execute('SELECT * FROM snapshot_components').fetchall()
    for t in ('feature_snapshots', 'continuous_predictions', 'probability_predictions', 'prediction_snapshots'):
        assert not db.rows(t)


@pytest.mark.parametrize('attack,reason', [
    ('horizon', 'HORIZON_MISMATCH'), ('target', 'TARGET_BUSINESS_DATE_MISMATCH'),
    ('issue', 'ISSUE_TIME_MISMATCH'), ('future_model', 'MODEL_CUTOFF'),
    ('wrong_model_family', 'MODEL_IDENTITY'), ('invalid_pmf', 'PMF_STRUCTURE'),
    ('negative_pmf', 'PMF_NEGATIVE'), ('mass', 'PMF_SUM'), ('cdf', 'CDF_RELATION'),
    ('probability_continuous', 'CONTINUOUS_MISMATCH'), ('future_state', 'STATE_CUTOFF'),
    ('future_feature', 'FUTURE_FEATURE'), ('feature_lineage', 'AVAILABILITY'),
    ('missing_lineage', 'UNRESOLVED_ECMWF'), ('probability_horizon', 'HORIZON_MISMATCH'),
    ('state_horizon', 'STATE_HORIZON'), ('state_target', 'STATE_TARGET'),
    ('missing_residual', 'UNRESOLVED_RESIDUAL'), ('foreign_residual', 'LINEAGE_SEMANTICS'),
    ('future_calibration', 'LINEAGE_SEMANTICS')])
def test_normalized_v4_attacks(db, attack, reason):
    f, m, p, meta = lawful(db)
    if attack == 'horizon': m['horizon'] = 'T2'
    if attack == 'target': m['target_business_date'] = '2026-10-04'
    if attack == 'issue': m['prediction_issue_time'] = '2026-10-02T14:00:00+00:00'
    if attack == 'future_model': m['training_cutoff'] = '2026-10-03T00:00:00+00:00'
    if attack == 'wrong_model_family': m['model_state_id'] = 'model/T2'; lawful(db, 'T2')
    if attack == 'invalid_pmf': p['pmf'] = [-1, 2]
    if attack == 'negative_pmf': p['pmf'][0] = -1.
    if attack == 'mass': p['pmf'] = [0.]*161
    if attack == 'cdf': p['cdf'] = [0.]*161
    if attack == 'probability_continuous': p['continuous_prediction'] = 100.
    if attack == 'future_feature': f['run_available_time'] = '2026-10-03T00:00:00+00:00'
    if attack == 'feature_lineage': next(iter(f['lineage'].values()))['feature_available_time'] = '2026-10-03T00:00:00+00:00'
    if attack == 'missing_lineage': next(iter(f['lineage'].values()))['input_ids'] = ['absent']
    if attack == 'probability_horizon': p['horizon'] = 'T2'
    if attack in ('future_state', 'state_horizon', 'state_target', 'foreign_residual', 'future_calibration'):
        s = dict(state_id=attack, cutoff='2026-10-02T12:00:00+00:00', residuals=[], cases=[])
        if attack == 'future_state': s['cutoff'] = '2026-10-03T00:00:00+00:00'
        if attack == 'state_horizon': s['horizon'] = 'T2'
        if attack == 'state_target': s['target_business_date'] = '2026-10-04'
        r = dict(record_id='past', horizon='T2' if attack == 'foreign_residual' else 'T1',
                 target_business_date='2026-09-01', issue='2026-08-31T13:00:00+00:00',
                 eligibility='2026-10-03T00:00:00+00:00' if attack == 'future_calibration' else '2026-09-03T00:00:00+08:00',
                 origin='ML', method='GAUSSIAN_EXPANDING', residual=1.)
        if attack == 'foreign_residual': s['residuals'] = [r]; p.update(history_ids=['past'], residual_n=1)
        if attack == 'future_calibration': s['cases'] = [r]; p.update(calibration_ids=['past'], calibration_n=1)
        with db.c: db.insert('probability_state', s, attack)
        meta['probability_state_version'] = p['probability_state_version'] = attack
    if attack == 'missing_residual': p.update(history_ids=['absent'], residual_n=1)
    before = db.semantic()
    with pytest.raises((ValueError, KeyError), match=reason): db.snapshot('attack', f, m, p, meta)
    unchanged(db, before)


@pytest.mark.parametrize('index', range(9))
def test_literal_original_v4_inputs_rejected(db, index):
    cases = json.loads((ROOT/'temp/master_audit_v4/runtime_final/adversarial_cases.json').read_text())
    v = cases[index]['input']
    with db.c: db.insert('probability_state', v['state'], 'state')
    before = db.semantic()
    with pytest.raises((ValueError, KeyError)): db.snapshot('original', v['feature'], v['continuous'], v['probability'], v['metadata'])
    unchanged(db, before)


@pytest.mark.parametrize('h', ['T1', 'T2'])
def test_legal_accept_and_duplicate_conflict(db, h):
    f, m, p, meta = lawful(db, h)
    assert db.snapshot('valid', f, m, p, meta)
    before = db.semantic()
    assert not db.snapshot('valid', f, m, p, meta)
    for index in range(4):
        parts = copy.deepcopy([f, m, p, meta]); parts[index]['changed_body'] = True
        with pytest.raises(ValueError, match='CONFLICTING_DUPLICATE'): db.snapshot('valid', *parts)
        assert db.semantic() == before


@pytest.mark.parametrize('stage', ['feature_snapshots', 'continuous_predictions', 'probability_predictions'])
def test_snapshot_transaction_rollback(db, stage):
    parts = lawful(db); before = db.semantic()
    with pytest.raises(RuntimeError, match='TRANSACTION_FAILURE'): db.snapshot('rollback', *parts, fail_after=stage)
    unchanged(db, before)


@pytest.mark.parametrize('table', ['feature_snapshots', 'continuous_predictions', 'probability_predictions', 'prediction_snapshots', 'probability_state'])
@pytest.mark.parametrize('recursive', [0, 1])
def test_sql_and_api_immutable(db, table, recursive):
    with db.c: db.insert(table, {'value':21}, 'old')
    db.c.execute(f'PRAGMA recursive_triggers={recursive}')
    old = tuple(db.c.execute(f'SELECT * FROM realtime_{table}').fetchone())
    assert not db.insert(table, {'value':21}, 'old')
    with pytest.raises(ValueError, match='CONFLICT'): db.insert(table, {'value':99}, 'old')
    for sql, args in [
        (f'INSERT OR REPLACE INTO realtime_{table} VALUES (?,?,?)', (old[0], old[1], canonical({'value':99}))),
        (f'UPDATE realtime_{table} SET payload_json=? WHERE record_id=?', ('{}', 'old')),
        (f'DELETE FROM realtime_{table} WHERE record_id=?', ('old',)),
        (f'INSERT INTO realtime_{table} VALUES (?,?,?) ON CONFLICT(record_id) DO UPDATE SET payload_json=excluded.payload_json', (old[0], old[1], '{}'))]:
        with pytest.raises(sqlite3.IntegrityError):
            with db.c: db.c.execute(sql, args)
        assert tuple(db.c.execute(f'SELECT * FROM realtime_{table}').fetchone()) == old
    with db.c: db.c.execute(f'INSERT OR REPLACE INTO realtime_{table} VALUES (?,?,?)', old)
    assert tuple(db.c.execute(f'SELECT * FROM realtime_{table}').fetchone()) == old


def test_formal_components_on_isolated_copy(tmp_path):
    # Immutable mode avoids even shared-memory lock writes; reject active WAL first.
    path = ROOT/'database/phase10_realtime_v1.db'
    assert not Path(str(path)+'-wal').exists() or Path(str(path)+'-wal').stat().st_size == 0
    source = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True)
    dest = tmp_path/'formal-copy.db'; c = sqlite3.connect(dest)
    source.backup(c); source.close(); c.execute("UPDATE schema_version SET namespace='SIMULATION'"); c.commit(); c.close()
    a = Archive(dest, 'SIMULATION')
    try:
        snap = a.rows('prediction_snapshots')[0]
        clean = lambda r: {k:v for k,v in r.items() if not k.startswith('_') and k != 'namespace'}
        f = clean(next(r for r in a.rows('feature_snapshots') if r['_id'] == snap['feature_id']))
        m = clean(next(r for r in a.rows('continuous_predictions') if r['_id'] == snap['continuous_id']))
        p = clean(next(r for r in a.rows('probability_predictions') if r['_id'] == snap['probability_id']))
        meta = clean(snap); meta['probability_state_version'] = p['probability_state_version']
        assert a.snapshot('control', f, m, p, meta)
        for name in ('horizon', 'pmf'):
            mm, pp = copy.deepcopy(m), copy.deepcopy(p)
            if name == 'horizon': mm['horizon'] = 'T2' if m['horizon'] == 'T1' else 'T1'
            else: pp['pmf'] = [-1., 2.]
            before = a.semantic()
            with pytest.raises(ValueError): a.snapshot(name, f, mm, pp, meta)
            assert a.semantic() == before
    finally: a.close()
