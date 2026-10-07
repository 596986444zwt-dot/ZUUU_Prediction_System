"""Independent frozen Phase8 candidate and same-subset metric reproduction."""
import json
import numpy as np
from .common import *
FAMILIES=('RIDGE','RANDOM_FOREST','LIGHTGBM','XGBOOST','CATBOOST')
def calculate(rows,key):
    predicted=np.array([r[key] for r in rows]);actual=np.array([r['actual_target'] for r in rows]);base=metrics(predicted-actual)
    ae=abs(np.floor(predicted+.5)-actual)
    base.update(DiagnosticExact=float(np.mean(ae==0)),DiagnosticWithin1=float(np.mean(ae<=1)),DiagnosticWithin2=float(np.mean(ae<=2)))
    return base
def audit(s,m):
    c=s.get('phase8_machine_learning_v1.db');saved_metric=records(c,'phase8_metric');saved_slices=records(c,'phase8_slice_metric');comparisons=[];out=[];slice_out=[];bad=0;choices={};qual=[]
    for h in ('T1','T2'):
        qualified=[]
        for family in FAMILIES:
            rows=[r for r in m.predictions if r['horizon']==h and r['model_family']==family and r['ml_prediction'] is not None]
            maps={family:'ml_prediction','RAW':'raw_ecmwf_prediction','M6':'mos_prediction'};mm={k:calculate(rows,key) for k,key in maps.items()}
            for name,values in mm.items():
                saved=next(r for r in saved_metric if r['horizon']==h and r['comparison_model']==family and r['model']==name)
                b=sum(not close(v,saved[k],1e-9) for k,v in values.items());bad+=b;out.append(dict(horizon=h,family=family,benchmark=name,**values,mismatches=b))
            reasons=[]
            if len(rows)<365:reasons.append('COVERAGE_LT_365')
            if min(mm['RAW']['MAE'],mm['M6']['MAE'])-mm[family]['MAE']<.05:reasons.append('MAE_GAIN_LT_0.05_VS_BOTH')
            if mm[family]['RMSE']>min(mm['RAW']['RMSE'],mm['M6']['RMSE']):reasons.append('RMSE_NOT_BETTER_THAN_BOTH')
            if abs(mm[family]['Bias'])>abs(mm['RAW']['Bias'])+.10:reasons.append('BIAS_GATE')
            for kind in ('year','season'):
                value=lambda r:r['target_business_date'][:4] if kind=='year' else season(r['target_business_date'])
                for group in sorted({value(r) for r in rows}):
                    subset=[r for r in rows if value(r)==group];ss={k:calculate(subset,key) for k,key in maps.items()}
                    for name,values in ss.items():
                        saved=next(r for r in saved_slices if r['horizon']==h and r['comparison_model']==family and r['model']==name and r['slice_type']==kind and r['slice_value']==group)
                        b=sum(not close(v,saved[k],1e-9) for k,v in values.items());bad+=b;slice_out.append(dict(horizon=h,family=family,model=name,kind=kind,group=group,**values,mismatches=b))
                    if len(subset)>=30 and any(ss[family]['MAE']-ss[b]['MAE']>.15 for b in ('RAW','M6')):reasons.append('SLICE_DEGRADATION_'+kind+'_'+group)
            qual.append(dict(horizon=h,family=family,qualified=not reasons,reasons=reasons))
            if not reasons:qualified.append((family,mm[family]['MAE']))
        if not qualified:choices[h]='NONE'
        else:
            best=min(v for _,v in qualified);choices[h]=min((family for family,v in qualified if v<=best+.02),key=FAMILIES.index)
    manifest=records(c,'phase8_manifest')[0];selectionbad=int(choices!={'T1':manifest['t1_candidate'],'T2':manifest['t2_candidate']})
    s.counts['MODEL_SELECTION_LEAKAGE_COUNT']+=selectionbad;s.counts['PHASE8_METRIC_MISMATCH_COUNT']=bad
    output('PHASE8_INDEPENDENT_CANDIDATE_RULE_V2.json',dict(rule=json.loads((ROOT/'docs/phase8/PHASE8_CONTRACT.json').read_text())['candidate'],recalculated_candidates=choices,qualification=qual,selection_mismatches=selectionbad,metric_mismatches=bad,metrics=out,slices=slice_out,status='PASS' if not selectionbad and not bad else 'FAIL',interpretation='Descriptive future-phase selection from honest individual-model OOS under frozen predeclared rule; not an unbiased retroactively selected-model OOS claim. No candidate changed.'))
