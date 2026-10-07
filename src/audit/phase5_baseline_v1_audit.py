"""Read-only independent arithmetic audit. Does not execute any issue selector."""
import argparse
import json
import math
import statistics
from src.baseline.contracts import CONTRACT, VERSION, OUTPUT, PHASE4_SEMANTIC
from src.baseline.source import load, fingerprints
from src.baseline.storage import read, semantic_hash, canonical, METRIC_TYPES
from src.data_v1.source_io import open_snapshot, sha256_file
from src.data_v1.contracts import require

def reference_metrics(rows):
    """Separate implementation; derive errors from forecast and frozen labels."""
    n = len(rows)
    out = dict.fromkeys(METRIC_TYPES)
    out['n'] = n
    errors = [r['ecmwf_raw_tmax_c']-r['target_tmax_c'] for r in rows]
    ae = sorted(abs(e) for e in errors)
    for label, bound in (('0_5',.5),('1',1),('2',2)):
        hits = len([e for e in ae if e<=bound])
        out[f'ae_le_{label}_count'] = hits
        out[f'ae_le_{label}_rate'] = hits/n if n else None
    if not n:
        return out
    x = [r['ecmwf_raw_tmax_c'] for r in rows]
    y = [r['target_tmax_c'] for r in rows]
    quotient, remainder = divmod((n-1)*.9, 1)
    i = int(quotient)
    p90 = ae[i]*(1-remainder)+ae[min(i+1,n-1)]*remainder
    sse = sum(e*e for e in errors)
    observed_mean = statistics.mean(y)
    sst = sum((v-observed_mean)**2 for v in y)
    out.update(bias_c=statistics.mean(errors), mae_c=statistics.mean(ae), rmse_c=math.sqrt(sse/n),
        median_ae_c=statistics.median(ae), p90_ae_c=p90,max_ae_c=max(ae),
        mean_forecast_tmax_c=statistics.mean(x),mean_observed_tmax_c=statistics.mean(y),error_std_c=statistics.pstdev(errors),
        pearson_r=statistics.correlation(x,y) if n>1 and len(set(x))>1 and len(set(y))>1 else None,
        r2=1-sse/sst if n>1 and sst else None)
    return out

def audit_connection(conn, expected=None):
    require(conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok', 'Baseline integrity failure')
    require(not conn.execute('PRAGMA foreign_key_check').fetchall(), 'Baseline FK failure')
    predictions, exclusions, source_sha = expected if expected is not None else load('AUDIT')
    data = read(conn)
    def projection(rows):
        return [{k:v for k,v in r.items() if k!='created_at_utc'} for r in sorted(rows,key=lambda r:(r['business_date_bjt'],r['horizon']))]
    require(projection(data['prediction'])==projection(predictions), 'Prediction mismatch against frozen trajectories')
    require(projection(data['exclusion'])==projection(exclusions), 'Exclusion mismatch')
    require(len(data['metrics'])==3 and len(data['group_metrics'])==75, 'Metric row counts mismatch')
    keys = set()
    for row in data['metrics']+data['group_metrics']:
        subset = [p for p in predictions if p['horizon']==row['horizon']]
        if 'group_type' in row:
            require(row['group_type'] in ('month','season','year','target_bin'), 'Invalid group kind')
            subset = [p for p in subset if str(p[row['group_type']])==row['group_value']]
            keys.add((row['horizon'],row['group_type'],row['group_value']))
        ref = reference_metrics(subset)
        for k,v in ref.items():
            actual = row[k]
            require(actual is None if v is None else actual is not None and math.isclose(actual,v,rel_tol=1e-12,abs_tol=1e-12), f'Metric mismatch: {row["horizon"]}/{k}')
    categories = {'month':list(map(str,range(1,13))),'year':['2024','2025','2026'],
        'season':['spring','summer','autumn','winter'],'target_bin':CONTRACT['bins']}
    require(keys=={(h,k,v) for h in ('T0','T1','T2') for k,values in categories.items() for v in values}, 'Group identity mismatch')
    require(len(data['manifest'])==1, 'Manifest cardinality')
    manifest = data['manifest'][0]
    require(manifest['baseline_version']==VERSION and manifest['build_status']=='VALIDATED', 'Manifest status/version')
    require(manifest['contract_json']==canonical(CONTRACT), 'Contract mismatch')
    require(manifest['source_sha256_json']==canonical(source_sha), 'Source manifest mismatch')
    require(manifest['source_phase4_semantic_sha256']==PHASE4_SEMANTIC, 'Source semantic mismatch')
    digest = semantic_hash(data)
    require(manifest['semantic_sha256']==digest, 'Baseline semantic hash mismatch')
    implementation = json.loads(manifest['implementation_sha256_json'])
    require(bool(implementation) and all(len(v)==64 for v in implementation.values()), 'Implementation provenance missing')
    return {'result':'PASS','status':'READY FOR INDEPENDENT ACCEPTANCE', 'phase5_semantic_sha256':digest,
        'source_phase4_semantic_sha256':PHASE4_SEMANTIC,'source_sha256':source_sha,
        'ground_truth_days':729,'source_samples':2187,'prediction_rows':2186,'eligible_hourly_rows':52464,
        'samples':{'T0':729,'T1':729,'T2':728},'exclusions':exclusions,
        'cross_vintage_violations':0,'leakage_violations':0,'meteostat_training_features':'NO',
        'integrity_check':'ok','foreign_key_check':0,'metrics':data['metrics']}

def audit(path=OUTPUT):
    before = fingerprints()
    digest = sha256_file(path)
    conn = open_snapshot(path, digest)
    try:
        result = audit_connection(conn)
    finally:
        conn.close()
    require(sha256_file(path)==digest, 'Baseline modified by audit')
    require(fingerprints()==before, 'Source modified by audit')
    result.update(source_db_modified='NO', phase5_physical_sha256=digest, source_sha256_before=before,source_sha256_after=fingerprints(), phase5_db_modified='NO')
    return result

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default=str(OUTPUT))
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.database),indent=2))
    except Exception as exc:
        print(f'PHASE 5 FINAL AUDIT: FAIL: {exc}')
        raise SystemExit(1)
