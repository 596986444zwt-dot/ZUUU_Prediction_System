"""Independent calendar/lineage arithmetic audit plus frozen-source replay."""
import argparse
import json
import math
import statistics
from datetime import date, datetime, timedelta
from src.data_v1.contracts import BJT, utc, require
from src.data_v1.source_io import open_snapshot, sha256_file
from src.mos_review.contracts import ROOT, OUTPUT, VERSION, CONTRACT, RULE, LAG_HOURS
from src.mos_review.data import load, fingerprints, preserved_fingerprints
from src.mos_review.storage import read, semantic_hash, canonical, statements
from src.mos_review.walk_forward import evaluate
from src.mos_review.assessment import summarize, assess

def implementation_files():
    return sorted((ROOT/'src/mos_review').glob('*.py')) + [ROOT/'src/builders/phase6_statistical_mos_v1_review_builder.py',
        ROOT/'src/audit/phase6_statistical_mos_v1_review_audit.py', ROOT/'tests/test_phase6_statistical_mos_v1_review.py',
        ROOT/'src/mos/data.py', ROOT/'src/mos/metrics.py', ROOT/'src/mos/schema.py', ROOT/'src/mos/semantic_hash.py']

def reference_metrics(rows, field):
    errors = [r[field]-r['observed_tmax'] for r in rows]
    n = len(errors)
    if not n:
        return {k:None for k in ('bias','mae','rmse','median_ae','p90_ae','max_ae')}
    ae = sorted(abs(e) for e in errors)
    q = .9*(n-1)
    i = int(q)
    return dict(bias=statistics.mean(errors), mae=statistics.mean(ae), rmse=math.sqrt(sum(e*e for e in errors)/n),
        median_ae=statistics.median(ae), p90_ae=ae[i]*(1-(q-i))+ae[min(i+1,n-1)]*(q-i), max_ae=max(ae))

def audit_connection(conn, samples=None, replay=True):
    require([r[0] for r in conn.execute('PRAGMA integrity_check')] == ['ok'], 'Review integrity failure')
    require(not conn.execute('PRAGMA foreign_key_check').fetchall(), 'Review foreign key failure')
    data = read(conn)
    require(len(data['manifest']) == 1, 'Manifest cardinality')
    manifest = data['manifest'][0]
    require(manifest['model_version'] == VERSION and manifest['contract_json'] == canonical(CONTRACT), 'Review contract mismatch')
    require(manifest['build_status'] == 'PASS', 'Review status must reflect completed experiment')
    require(json.loads(manifest['source_sha_before_json']) == fingerprints(), 'Source manifest mismatch')
    require(json.loads(manifest['preserved_assets_json']) == preserved_fingerprints(), 'Original blocker preservation failure')
    expected_files = {p.relative_to(ROOT).as_posix():sha256_file(p) for p in implementation_files()}
    require(json.loads(manifest['implementation_sha256_json']) == expected_files, 'Implementation provenance mismatch')
    if samples is None:
        samples, _ = load()
    def projection(rows):
        return sorted(canonical({k:v for k,v in r.items() if k != 'created_at'}) for r in rows)
    require(projection(data['sample']) == projection(samples), 'Frozen sample reproduction failure')
    by_key = {(s['business_date_bjt'],s['horizon']):s for s in samples}
    total_edges = 0
    future = same_day = 0
    for state in data['bias_state']:
        issue = utc(state['training_cutoff'])
        dates = json.loads(state['training_dates_json'])
        require(dates == sorted(set(dates)) and len(dates) == state['training_n'], 'Training key/count mismatch')
        require(state['eligibility_rule'] == RULE and state['eligibility_lag'] == LAG_HOURS, 'Eligibility rule mismatch')
        errors = []
        for label_day in dates:
            label = by_key[label_day,state['horizon']]
            # Independent date arithmetic, not the training-gate implementation.
            boundary = datetime.combine(date.fromisoformat(label_day)+timedelta(days=1), datetime.min.time(),tzinfo=BJT)
            eligible_at = boundary + timedelta(hours=24)
            future += int(label_day >= state['target_business_date'])
            same_day += int(boundary >= issue or label_day >= issue.astimezone(BJT).date().isoformat())
            require(eligible_at <= issue and boundary < issue, 'Label eligibility exceeds prediction issue')
            require(label['label_eligibility_time_bjt'] == eligible_at.isoformat(), 'Label eligibility metadata mismatch')
            require(utc(label['issue_time_utc']) < issue, 'Historical forecast issue violation')
            errors.append(label['raw_ecmwf_tmax']-label['observed_tmax'])
        total_edges += len(dates)
        if state['status'] == 'PREDICTED':
            require(len(dates) >= 7, 'Cold-start leakage')
            if state['model'] != 'M6':
                expected_bias = math.fsum(errors)/len(errors)
            else:
                components = json.loads(state['components_json'])
                require([c['model'] for c in components] == ['M1','M3','M4'], 'Combined model components changed')
                component_biases = []
                union = set()
                for c in components:
                    require(set(c['dates']) <= set(dates) and len(c['dates']) >= 7, 'Combined component lineage')
                    vals = [by_key[d,state['horizon']]['raw_error'] for d in c['dates']]
                    mean = math.fsum(vals)/len(vals)
                    require(math.isclose(c['bias'],mean,abs_tol=1e-12), 'Combined component arithmetic')
                    component_biases.append(mean)
                    union.update(c['dates'])
                require(union == set(dates), 'Combined union lineage mismatch')
                expected_bias = math.fsum(component_biases)/3
            require(math.isclose(state['bias_value'], expected_bias,abs_tol=1e-12), 'Bias arithmetic mismatch')
            require(state['training_start'] == dates[0] and state['training_end'] == dates[-1], 'Training bounds mismatch')
        else:
            require(state['status'] == 'INSUFFICIENT_HISTORY' and not dates and state['bias_value'] is None, 'Invalid cold start')
    require(future == same_day == 0, 'Future/same-day label leakage')
    predictions = data['prediction']
    indexed = {(r['target_business_date'],r['horizon'],r['model']):r for r in data['bias_state']}
    for p in predictions:
        state = indexed[p['target_business_date'],p['horizon'],p['model']]
        source = by_key[p['target_business_date'],p['horizon']]
        require(p['raw_ecmwf_tmax'] == source['raw_ecmwf_tmax'] and p['observed_tmax'] == source['observed_tmax'], 'Forecast/target lineage mismatch')
        require(p['selected_ecmwf_run'] == source['selected_ecmwf_run_time_utc'], 'Cross-run substitution')
        require(utc(p['selected_ecmwf_run']) <= utc(p['available_time']) <= utc(p['issue_time']), 'Future ECMWF run')
        if p['status'] == 'PREDICTED':
            require(p['mos_continuous_tmax'] == p['raw_ecmwf_tmax']-state['bias_value'], 'MOS sign/value mismatch')
            require(p['mos_error'] == p['mos_continuous_tmax']-p['observed_tmax'], 'MOS error mismatch')
        else:
            require(p['mos_continuous_tmax'] is None and p['mos_error'] is None, 'Invalid cold-start prediction')
    # Independent statistics for every disclosed overall/season/month/year slice.
    for metric in data['metric']+data['slice_metric']:
        subset = [p for p in predictions if p['model'] == metric['model'] and p['horizon'] == metric['horizon'] and p['status'] == 'PREDICTED']
        if metric['slice_type'] != 'ALL':
            subset = [p for p in subset if str(p[metric['slice_type']]) == metric['slice_value']]
        require(metric['n'] == len(subset), 'Comparable subset count mismatch')
        for prefix, field in [('raw','raw_ecmwf_tmax'),('mos','mos_continuous_tmax')]:
            for key, value in reference_metrics(subset,field).items():
                actual = metric[prefix+'_'+key]
                require(actual is None if value is None else actual is not None and math.isclose(actual,value,abs_tol=1e-12,rel_tol=1e-12), 'Independent metric mismatch')
    metrics, slices = summarize(predictions)
    require(projection(metrics) == projection(data['metric']) and projection(slices) == projection(data['slice_metric']), 'Improvement/diagnostic metric reproduction mismatch')
    candidate, decisions = assess(metrics,slices)
    require(manifest['candidate'] == candidate and json.loads(manifest['candidate_assessment_json']) == decisions, 'Candidate assessment mismatch')
    if replay:
        replay_p, replay_s = evaluate(samples,'INDEPENDENT_REPLAY')
        require(projection(replay_p) == projection(predictions) and projection(replay_s) == projection(data['bias_state']), 'Chronological prediction/state reproduction failure')
    digest = semantic_hash(data)
    require(digest == manifest['semantic_sha256'], 'Review semantic mismatch')
    for sql in statements(data):
        require(conn.execute('SELECT 1 FROM sqlite_master WHERE sql=?',(sql,)).fetchone() is not None, 'Review schema/view/trigger mismatch')
    return dict(artifact_audit='PASS', PHASE6_BUILD_STATUS='PASS', PHASE6_MOS_CANDIDATE=candidate,
        NEXT_PHASE_STATUS='READY_FOR_PHASE7_FEATURE_ENGINEERING', phase6_semantic_sha256=digest,
        future_label_count=future, same_day_unsettled_label_count=same_day, audited_training_label_edges=total_edges,
        valid_mos_predictions=sum(p['status'] == 'PREDICTED' for p in predictions), prediction_attempts=len(predictions),
        integrity_check='ok', foreign_key_check=0, candidate_assessment=decisions,
        intraday_status=CONTRACT['Intraday'], metrics=metrics)

def audit(path=OUTPUT):
    before = fingerprints()
    preserved = preserved_fingerprints()
    physical = sha256_file(path)
    conn = open_snapshot(path,physical)
    try:
        result = audit_connection(conn)
    finally:
        conn.close()
    require(before == fingerprints() and preserved == preserved_fingerprints() and physical == sha256_file(path), 'Source/blocked/output mutation')
    return dict(result,phase6_physical_sha256=physical,SOURCE_SHA_BEFORE=before,SOURCE_SHA_AFTER=fingerprints(),
                original_blocked_assets_preserved=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database',default=str(OUTPUT))
    args = parser.parse_args()
    print(json.dumps(audit(args.database),indent=2,ensure_ascii=False))
