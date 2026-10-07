import json
import math
from collections import defaultdict
from datetime import timedelta,date
import numpy as np
from scipy.special import erf
from .common import *

K=np.arange(-80,81);EPS=1e-12
BASE_METHODS=['GAUSSIAN_EXPANDING','GAUSSIAN_90CAL','EMPIRICAL_EXPANDING','KDE_EXPANDING_BW075']
METHODS=BASE_METHODS+[m+'_CAL' for m in BASE_METHODS]

def floor(p):
    p=np.maximum(np.asarray(p,float),0);p=p/p.sum();return (1-len(p)*EPS)*p+EPS

def normal_cdf(z):return .5*(1+erf(z/math.sqrt(2)))

def distribution(forecast,residual,method):
    r=np.array(residual,float);edges=K+.5
    if method.startswith('GAUSSIAN'):f=normal_cdf((edges-forecast-r.mean())/max(r.std(ddof=1),.5))
    elif method.startswith('KDE'):f=normal_cdf((edges[:,None]-forecast-r[None,:])/.75).mean(axis=1)
    else:
        sorted_latent=np.sort(forecast+r);f=np.searchsorted(sorted_latent,edges,side='left')/len(r)
    mass=np.r_[f[0],np.diff(f)];mass[-1]+=1-f[-1];return floor(mass)

def calibration(p,alpha):
    u=np.clip(np.cumsum(p)[:-1],1e-300,1-1e-16)
    # Independent power ratio, monotone transform; numerical reference separately uses stable logistic.
    ua=u**alpha;va=(1-u)**alpha;cdf=np.r_[ua/(ua+va),1.]
    return floor(np.diff(np.r_[0.,cdf]))

def calibration_q(p,actual,alpha):
    """Only two CDF thresholds needed for NLL; stable arithmetic at declared floor."""
    c=np.cumsum(p);i=int(actual)+80
    def at(j):
        if j<0:return 0.
        if j==160:return 1.
        u=min(1-1e-16,max(1e-300,float(c[j])));z=alpha*(math.log(u)-math.log1p(-u))
        return 1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
    return (1-161*EPS)*max(0,at(i)-at(i-1))+EPS

class Probability:
    def __init__(self,s,models):
        self.s=s;self.m=models;c=s.get('phase9_probability_v1.db')
        def load(t):return [json.loads(r['payload_json']) for r in c.execute('SELECT * FROM phase9_'+t)]
        self.samples={r['sample_id']:r for r in load('sample')};self.pred={r['record_id']:r for r in load('probability_prediction')};self.residuals=load('residual_history');self.ds={r['record_id']:r for r in load('distribution_state')};self.cal={r['record_id']:r for r in load('calibration_registry')};self.selection=load('candidate_selection')
        self.mass={r['record_id']:{k:np.frombuffer(r[k],dtype='<f8').copy() for k in ('pmf','cdf','survival')} for r in c.execute('SELECT * FROM phase9_probability_mass')};self.independent={};self.engine={r['sample_id']:r for r in self.pred.values() if r['origin']=='ENGINE'}
        self.eligible_cache={};self.repro_rows=[]

    def allowed(self,current,origin,matched=True,method='GAUSSIAN_EXPANDING'):
        key=(current['sample_id'],origin,matched,method)
        if key not in self.eligible_cache:
            rows=[r for r in self.samples.values() if r['horizon']==current['horizon'] and r['target_business_date']<current['target_business_date'] and eligible(r['target_business_date'])<=stamp(current['issue']) and stamp(r['issue'])<stamp(current['issue']) and r[origin] is not None and (not matched or r['ML'] is not None)]
            if method=='GAUSSIAN_90CAL':
                # Frozen implementation uses the issue UTC calendar date after UTC normalization.
                # Contract calls this BJT; fixed21BJT issues have same UTC/BJT date.
                end=stamp(current['issue']).date()-timedelta(days=2);start=end-timedelta(days=89);rows=[r for r in rows if start<=date.fromisoformat(r['target_business_date'])<=end]
            self.eligible_cache[key]=sorted(rows,key=lambda r:r['issue'])
        return self.eligible_cache[key]

    def reconstruct(self,r):
        rid=r['record_id']
        if rid in self.independent:return self.independent[rid]
        if r.get('selected_probability_id'):
            p=self.reconstruct(self.pred[r['selected_probability_id']]);self.independent[rid]=p;return p
        current=self.samples[r['sample_id']];d=self.ds.get(r.get('distribution_state'));base=r['method'].removesuffix('_CAL');origin=d['origin'] if d else r.get('probability_source_level',r['origin'])
        matched=r['status']!='FALLBACK';pool=self.allowed(current,origin,matched,base)
        p=distribution(r['continuous_prediction'],[x['actual']-x[origin] for x in pool],base)
        if r['method'].endswith('_CAL'):p=calibration(p,r['calibration_alpha'])
        self.independent[rid]=p;return p

    def audit(self):
        residualbad=0;residmap={(r['sample_id'],r['origin']):r for r in self.residuals}
        for r in self.residuals:
            sample=self.samples[r['sample_id']];origin=r['origin'];p=self.m.formal.get(r['sample_id'])
            expected=None if sample[origin] is None else sample['actual']-sample[origin]
            residualbad+=int(not close(expected,r['residual']) or eligible(r['target_business_date'])!=stamp(r['eligibility']) or r['actual']!=self.s.truth[r['target_business_date']]['daily_tmax_c'] or origin=='ML' and (p is None or not close(p['ml_prediction'],r['continuous_prediction']) or p['ml_prediction'] is not None and p['status']!='OOS'))
        edge=0;future=0;cross=0;statebad=0;stateout=[]
        for rid,d in self.ds.items():
            current=next((r for r in self.samples.values() if rid.startswith(r['sample_id']+'/')),None)
            if current is None:statebad+=1;continue
            matched='/FALLBACK/' not in rid;expected=self.allowed(current,d['origin'],matched,d['method']);ids=d['history_ids'];edge+=len(ids)
            bad=int(ids!=[x['sample_id'] for x in expected] or d['history_n']!=len(ids))
            for sid in ids:
                r=self.samples[sid];future+=eligible(r['target_business_date'])>stamp(current['issue']) or r['target_business_date']>=current['target_business_date'] or stamp(r['issue'])>=stamp(current['issue']);cross+=r['horizon']!=current['horizon']
            statebad+=bad;stateout.append(dict(state_id=rid,horizon=d['horizon'],cutoff=d['cutoff'],history_n=len(ids),history_pool_mismatch=bad))
        mathout=[];negative=0;sumbad=0;cdfbad=0;scorebad=0
        for rid,m in self.mass.items():
            p=m['pmf'];r=self.pred[rid];neg=int(not np.isfinite(p).all() or p.min()<0 or p.max()>1);summismatch=int(abs(p.sum()-1)>1e-10);cdfmismatch=int(np.any(np.diff(m['cdf'])<-1e-10) or abs(m['cdf'][-1]-1)>1e-10 or np.max(np.abs(np.cumsum(p)-m['cdf']))>1e-10 or np.max(np.abs(1-np.r_[0,np.cumsum(p)[:-1]]-m['survival']))>1e-10)
            independent=scores(p,r['actual']);mapping={'brier':'Brier','logloss':'LogLoss','crps':'CRPS','top1':'top1_hit','top2':'top2_hit','top3':'top3_hit','actual_probability':'assigned_probability'}
            mismatch=sum(not close(v,r.get(mapping[k]),1e-9) for k,v in independent.items());negative+=neg;sumbad+=summismatch;cdfbad+=cdfmismatch;scorebad+=mismatch
            mathout.append(dict(prediction_id=rid,negative=neg,mass_sum=float(p.sum()),sum_mismatch=summismatch,cdf_survival_mismatch=cdfmismatch,score_mismatches=mismatch))
        # Every saved calibration edge and selected alpha, without refitting any official state.
        calfuture=0;calbad=0;caledges=0
        for rid,r in self.cal.items():
            ids=r['calibration_ids'];caledges+=len(ids)
            if not ids:continue
            current=self.pred[rid];calfuture+=sum(eligible(self.pred[x]['target_business_date'])>stamp(current['issue']) or self.pred[x]['target_business_date']>=current['target_business_date'] or self.pred[x]['horizon']!=current['horizon'] for x in ids)
            losses=[float(np.mean([-math.log(calibration_q(self.mass[x]['pmf'],self.pred[x]['actual'],a)) for x in ids])) for a in (1.,.8,1.2)]
            best=min(range(3),key=lambda j:(losses[j],j));calbad+=int(r['alpha']!=(1.,.8,1.2)[best] or len(ids)!=90)
        selectbad=0;selectedges=0
        for r in self.selection:
            options=r['options'];selectedges+=sum(len(o['validation_ids']) for o in options)
            for o in options:
                ids=o['validation_ids'];rows=[self.pred[x] for x in ids];calfuture+=sum(eligible(x['target_business_date'])>stamp(r['cutoff']) or x['horizon']!=r['horizon'] or stamp(x['issue'])>=stamp(r['cutoff']) for x in rows)
                selectbad+=int(len(ids)!=60 or not close(np.mean([scores(self.mass[x['record_id']]['pmf'],x['actual'])['logloss'] for x in rows]),o['LogLoss'],1e-9))
            expected=next((m for m in METHODS if any(o['method']==m and o['LogLoss']<=min(x['LogLoss'] for x in options)+.01 for o in options)),'GAUSSIAN_EXPANDING')
            actual=r['selected'];selectbad+=int(actual is not None and actual!=expected)
        # Independently reconstruct all PMFs, not only the required stratified100.
        reprobad=0;maxdiff=0.
        for rid in self.mass:
            r=self.pred[rid];p=self.reconstruct(r);diff=float(np.max(np.abs(p-self.mass[rid]['pmf'])));maxdiff=max(maxdiff,diff);b=diff>1e-9;reprobad+=b
            self.repro_rows.append(dict(prediction_id=rid,horizon=r['horizon'],date=r['target_business_date'],method=r['method'],origin=r['origin'],status=r['status'],residual_n=r.get('residual_n'),calibration_n=r.get('calibration_n'),pmf_max_difference=diff,reproduction='FAIL' if b else 'PASS'))
        c=self.s.counts;c['RESIDUAL_EDGE_CHECK_COUNT']=edge;c['FUTURE_RESIDUAL_COUNT']=future;c['INVALID_RESIDUAL_COUNT']=residualbad+statebad;c['CROSS_HORIZON_RESIDUAL_COUNT']=cross;c['PMF_ROW_COUNT']=len(self.mass);c['PMF_NEGATIVE_COUNT']=negative;c['PMF_SUM_MISMATCH_COUNT']=sumbad;c['CDF_MONOTONICITY_VIOLATION_COUNT']=cdfbad;c['PROBABILITY_REPRO_CHECK_COUNT']=len(self.mass);c['PROBABILITY_REPRO_MISMATCH_COUNT']=reprobad;c['PROBABILITY_SCORE_MISMATCH_COUNT']=scorebad;c['FUTURE_CALIBRATION_LABEL_COUNT']=calfuture;c['CALIBRATION_SELECTION_MISMATCH_COUNT']=calbad+selectbad
        self.s.evidence['phase9']=dict(residual_edge_count=edge,calibration_edge_count=caledges,selection_edge_count=selectedges,pmf_rows=len(self.mass),max_independent_pmf_difference=maxdiff,methods=METHODS,integer_bin='[k-.5,k+.5); report mechanism approximation')
        output('MASTER_PROBABILITY_MATH_AUDIT.csv',mathout);output('MASTER_PROBABILITY_REPRODUCTION.csv',self.repro_rows);output('MASTER_PROBABILITY_CAUSALITY.csv',stateout)

    def forward_independent(self,state,h,target,issue,forecasts):
        """Protocol reconstruction using past residual/case evidence, no production functions."""
        def permitted(r):return r['horizon']==h and r['target_business_date']<target and stamp(r['eligibility'])<=stamp(issue) and stamp(r['issue'])<stamp(issue)
        past=[r for r in state['residuals'] if permitted(r) and r['residual'] is not None];cases=[r for r in state['cases'] if permitted(r)]
        matched={r['target_business_date'] for r in past if r['origin']=='ML'};current={};options={}
        for origin in ('ML','RAW','MOS'):
            for base in BASE_METHODS:
                pool=[r for r in past if r['origin']==origin and r['target_business_date'] in matched]
                if base=='GAUSSIAN_90CAL':
                    end=stamp(issue).astimezone(BJT).date()-timedelta(days=2);start=end-timedelta(days=89);pool=[r for r in pool if start<=date.fromisoformat(r['target_business_date'])<=end]
                if forecasts.get(origin) is None or len(pool)<60:continue
                p=distribution(forecasts[origin],[r['residual'] for r in pool],base);current[origin,base]=p
                cc=sorted([r for r in cases if r['origin']==origin and r['method']==base],key=lambda r:r['issue'])[-90:]
                if len(cc)==90:
                    losses=[np.mean([-math.log(calibration_q(np.array(x['pmf']),x['actual'],a)) for x in cc]) for a in (1.,.8,1.2)];a=(1.,.8,1.2)[min(range(3),key=lambda j:(losses[j],j))];current[origin,base+'_CAL']=calibration(p,a)
            opts=[]
            for method in METHODS:
                if (origin,method) not in current:continue
                old=sorted([r for r in cases if r['origin']==origin and r['method']==method],key=lambda r:r['issue'])[-60:]
                if len(old)==60:opts.append((method,np.mean([-math.log(max(float(x['pmf'][int(x['actual'])+80]),EPS)) for x in old])))
            name=next((m for m in METHODS if any(x[0]==m and x[1]<=min(z[1] for z in opts)+.01 for x in opts)),'GAUSSIAN_EXPANDING');options[origin]=current.get((origin,name))
        if options.get('ML') is not None:return options['ML']
        for origin in ('MOS','RAW'):
            pool=[r for r in past if r['origin']==origin]
            if forecasts.get(origin) is not None and len(pool)>=60:return distribution(forecasts[origin],[r['residual'] for r in pool],'GAUSSIAN_EXPANDING')
        return None
