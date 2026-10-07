"""Frozen model and probability handoff. No estimator fitting."""
import json
import numpy as np
from datetime import timedelta
from src.data_v1.source_io import open_snapshot, sha256_file
from src.models.phase8.restoration import load_fitted_state
from src.models.phase8.pipeline import transform
from src.probability.contracts import METHODS, BASE_METHODS, ALPHAS, MIN_RESIDUAL, MIN_CALIBRATION, MIN_SELECTION, SUPPORT
from src.probability.distribution import distribution, calibrate
from .contracts import ROOT, utc, iso, now, eligibility, require, identity

MODELS = {'T1': 'RIDGE', 'T2': 'LIGHTGBM'}

class Models:
    def __init__(self, cutoff):
        c = open_snapshot(ROOT/'database/phase8_machine_learning_v1.db')
        self.states = [dict(r) for r in c.execute('SELECT * FROM phase8_state')]
        c.close()
        c = open_snapshot(ROOT/'database/phase7_feature_v1.db')
        self.names = sorted(r[0] for r in c.execute('SELECT feature_name FROM phase7_feature_registry'))
        c.close()
        require(len(self.names) == 102, 'FEATURE_CONTRACT_ERROR')
        self.assets = {}; self.active = {}
        for h, family in MODELS.items():
            legal = [r for r in self.states if r['horizon']==h and r['model_family']==family and utc(r['training_cutoff'])<=utc(cutoff)]
            require(legal, 'MODEL_LOAD_ERROR')
            self.active[h] = max(legal, key=lambda r: utc(r['training_cutoff']))
            self.restore(self.active[h])

    def restore(self, row):
        path = (ROOT/row['artifact_path']).resolve()
        require(path.is_relative_to(ROOT/'docs/phase8/model_states'), 'UNTRUSTED_MODEL_PATH')
        require(sha256_file(path)==row['artifact_sha256'], 'MODEL_HASH_MISMATCH')
        if row['record_id'] not in self.assets: self.assets[row['record_id']] = load_fitted_state(path)
        return self.assets[row['record_id']]

    def predict(self, h, values, issue, state_id=None):
        require(h in MODELS, 'T0_FORMAL_MODEL_BLOCKED')
        require(set(values)==set(self.names), 'FEATURE_CONTRACT_ERROR')
        row = self.active[h] if state_id is None else next(r for r in self.states if r['record_id']==state_id)
        require(row['horizon']==h and row['model_family']==MODELS[h] and utc(row['training_cutoff'])<=utc(issue), 'ILLEGAL_MODEL_STATE')
        saved = self.restore(row)
        X = np.array([[np.nan if values[n] is None else values[n] for n in self.names]], dtype=float)
        p = float(values['ecmwf_tmax_c'] + saved['estimator'].predict(transform(X, saved['preprocessor']))[0])
        require(np.isfinite(p), 'MODEL_NONFINITE')
        return p, dict(row)

def eligible(record, issue, target):
    return record['target_business_date']<target and utc(record['eligibility'])<=utc(issue) and utc(record['issue'])<utc(issue)

def bootstrap(cutoff):
    """Deserialize frozen prequential evidence; exclude later labels and cases."""
    c = open_snapshot(ROOT/'database/phase9_probability_v1.db')
    residuals = []
    for r in c.execute('SELECT payload_json FROM phase9_residual_history'):
        v = json.loads(r[0])
        if utc(v['eligibility'])<=utc(cutoff) and utc(v['issue'])<utc(cutoff): residuals.append(v)
    cases = []
    for r in c.execute('SELECT p.payload_json,m.pmf FROM phase9_probability_prediction p JOIN phase9_probability_mass m USING(record_id)'):
        v = json.loads(r[0])
        if v['origin'] not in ('ML','RAW','MOS') or v['method'] not in METHODS: continue
        if utc(v['eligibility'])<=utc(cutoff) and utc(v['issue'])<utc(cutoff):
            v['pmf'] = np.frombuffer(r[1],dtype='<f8').tolist(); cases.append(v)
    c.close()
    return {'version': 'PHASE9_FORWARD_STATE_V1', 'cutoff': iso(cutoff), 'residuals': residuals,
            'cases': cases, 'source_phase8_hash': sha256_file(ROOT/'database/phase8_machine_learning_v1.db'),
            'source_phase9_hash': sha256_file(ROOT/'database/phase9_probability_v1.db'),
            'selection_rule': 'PAST_ONLY_NLL_LAST60_TIE001', 'calibration_rule':'PAST_PREQUENTIAL_LAST90_CDF_ODDS',
            'created_at': iso(now())}

def forward(state, h, target, issue, forecasts):
    """Forward-only Phase9 protocol; no current actual is accepted."""
    require(h in MODELS and utc(state['cutoff'])<=utc(issue), 'PROBABILITY_STATE_ERROR')
    pool = [r for r in state['residuals'] if r['horizon']==h and eligible(r,issue,target) and r.get('residual') is not None]
    # Phase9 formal baselines share ML-available residual dates.
    ml_dates = {r['target_business_date'] for r in pool if r['origin']=='ML'}
    current = {}; evidence = {}
    for origin in ('ML','RAW','MOS'):
        if forecasts.get(origin) is None: continue
        hist = sorted((r for r in pool if r['origin']==origin and r['target_business_date'] in ml_dates),key=lambda r:r['target_business_date'])
        for method in BASE_METHODS:
            used = hist
            if '90CAL' in method:
                end = utc(issue).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date()-timedelta(days=2)
                lo = (end-timedelta(days=89)).isoformat()
                used = [r for r in hist if lo<=r['target_business_date']<=end.isoformat()]
            if len(used)<MIN_RESIDUAL: continue
            p, info = distribution(forecasts[origin],[r['residual'] for r in used],method)
            common = {'origin':origin,'method':method,'continuous_prediction':forecasts[origin],
                      'residual_n':len(used),'history_ids':[r['record_id'] for r in used],
                      'calibration_n':0,'calibration_alpha':1.,'status':'UNCALIBRATED',**info}
            current[origin,method] = dict(common, pmf=p.tolist())
            old = sorted((r for r in state['cases'] if r['horizon']==h and r['origin']==origin and r['method']==method and eligible(r,issue,target)),key=lambda r:r['target_business_date'])[-MIN_CALIBRATION:]
            if len(old)==MIN_CALIBRATION:
                scores = [float(np.mean([-np.log(calibrate(np.array(r['pmf']),a)[0][int(r['actual'])-SUPPORT[0]]) for r in old])) for a in ALPHAS]
                alpha = ALPHAS[min(range(len(scores)),key=lambda i:(scores[i],i))]
                q, extra = calibrate(p,alpha)
                current[origin,method+'_CAL'] = dict(common,method=method+'_CAL',pmf=q.tolist(),
                    calibration_alpha=alpha,calibration_n=len(old),calibration_ids=[r['record_id'] for r in old],calibration_scores=scores,status='CALIBRATED',**{'calibration_floor':extra})
        opts = []
        for method in METHODS:
            if (origin,method) not in current: continue
            old = sorted((r for r in state['cases'] if r['horizon']==h and r['origin']==origin and r['method']==method and eligible(r,issue,target)),key=lambda r:r['target_business_date'])[-MIN_SELECTION:]
            if len(old)==MIN_SELECTION: opts.append((method,float(np.mean([r['LogLoss'] for r in old])),[r['record_id'] for r in old]))
        name = 'GAUSSIAN_EXPANDING'
        if opts:
            best = min(r[1] for r in opts)
            name = next(m for m in METHODS if any(o[0]==m and o[1]<=best+.01 for o in opts))
        evidence[origin] = {'selected':name,'options':opts}
    pick = current.get(('ML',evidence.get('ML',{}).get('selected')))
    if pick is None:
        for origin in ('MOS','RAW'):
            hist = sorted((r for r in pool if r['origin']==origin),key=lambda r:r['target_business_date'])
            if forecasts.get(origin) is not None and len(hist)>=MIN_RESIDUAL:
                p, info = distribution(forecasts[origin],[r['residual'] for r in hist],'GAUSSIAN_EXPANDING')
                pick = dict(origin=origin,method='GAUSSIAN_EXPANDING',pmf=p.tolist(),residual_n=len(hist),
                            history_ids=[r['record_id'] for r in hist],calibration_n=0,calibration_alpha=1.,
                            continuous_prediction=forecasts[origin],status='FALLBACK',fallback_reason='ML_CONTINUOUS_UNAVAILABLE' if forecasts.get('ML') is None else 'ML_RESIDUAL_HISTORY_INSUFFICIENT',**info)
                break
    if pick is None: return {'status':'NO_FORECAST','reason':'PROBABILITY_HISTORY_INSUFFICIENT'}, list(current.values())
    p = np.array(pick['pmf']); require(np.isfinite(p).all() and (p>=0).all() and abs(p.sum()-1)<1e-10,'PMF_ERROR')
    return dict(pick,cdf=np.cumsum(p).tolist(),survival=(1-np.r_[0,np.cumsum(p)[:-1]]).tolist(),support_min=-80,support_max=80,
                probability_protocol_version='PROBABILITY_PROTOCOL_V1',random_seed=20261002,
                selection=evidence,probability_state_version=state.get('state_id') or identity({k:v for k,v in state.items() if k!='created_at'})), list(current.values())
