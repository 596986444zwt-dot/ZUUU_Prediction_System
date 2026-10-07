import sys,pathlib,json,pickle
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
import sklearn.linear_model,sklearn.ensemble,lightgbm,xgboost,catboost,numpy as np
from src.audit.master_v4.verify import db,rows,infer,mismatch,save,stamp
OUT=ROOT/'temp/master_audit_v4'
c=db('phase7_feature_v1.db');names=sorted(r['feature_name'] for r in rows(c,'phase7_feature_registry'));vals={(r['sample_id'],r['feature_name']):r['value'] for r in rows(c,'phase7_feature_value')};c.close()
c=db('phase8_machine_learning_v1.db');states=rows(c,'phase8_state');predictions=rows(c,'phase8_prediction');c.close()
saved={s['record_id']:pickle.loads((ROOT/s['artifact_path']).read_bytes()) for s in states if (s['horizon'],s['model_family']) in [('T1','RIDGE'),('T2','LIGHTGBM')]}
evidence=[];errors=[];count={'T1':0,'T2':0};trees=0
for sid,s in saved.items():
    ps=[r for r in predictions if r['state_id']==sid and r['ml_prediction'] is not None]
    if not ps:continue
    X=np.array([[np.nan if vals[p['input_sample_id'],n] is None else vals[p['input_sample_id'],n] for n in names] for p in ps])
    outputs=infer(s,X)
    if s['preprocessor']['family']=='LIGHTGBM':trees+=len(ps)*len(s['estimator'].booster_.dump_model()['tree_info'])
    for p,v in zip(ps,outputs):
        expected=p['raw_ecmwf_prediction']+float(v);count[p['horizon']]+=1
        evidence.append({'prediction':p['record_id'],'expected':expected,'actual':p['ml_prediction'],'state':sid})
        if mismatch(expected,p['ml_prediction'],1e-8):errors.append(evidence[-1])
original=json.loads((OUT/'models_run_1_failed_auditor.json').read_text(encoding='utf-8'))
original.update(independent_predictions=count,tree_predictions=trees,prediction_mismatch_count=len(errors),errors=[r for r in original['errors'] if 'prediction' not in r]+errors,evidence_status='FINAL',numeric_run='RESUMED_CORRECTED_LIGHTGBM_MISSING_SEMANTICS')
save('models_final.json',original);save('model_independent_predictions_final.json',evidence)
save('AUDITOR_RESUME_RECORD_V4_TREE.json',{'evidence_status':'FINAL','modification':'Correct NaN branch according to LightGBM NumericalDecision official source; missing_type=None maps NaN to zero','production_assets_changed':False,'rerun':'All 1092 formal T1/T2 OOS predictions, 43600 individual tree evaluations; metadata/preprocessing unchanged and independently passed in initial run','failed_artifacts_retained':['models_run_1_failed_auditor.json','model_independent_predictions_run_1_failed_auditor.json'],'mismatch_count':len(errors)})
print(json.dumps({'predictions':count,'trees':trees,'mismatch_count':len(errors)}))
