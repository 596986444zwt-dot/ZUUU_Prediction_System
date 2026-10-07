import sys,pathlib,json,pickle
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
import sklearn.linear_model,sklearn.ensemble,lightgbm,xgboost,catboost
import src.audit.master_v4.verify as v
OUT=ROOT/'temp/master_audit_v4/runtime_final';OUT.mkdir(exist_ok=True);v.OUT=OUT
c=v.db('phase7_feature_v1.db');v.CACHE['feature_registry']={r['feature_name']:r for r in v.rows(c,'phase7_feature_registry')};c.close()
c=v.db('phase8_machine_learning_v1.db');states=v.rows(c,'phase8_state');c.close()
v.CACHE['model_assets']={s['record_id']:pickle.loads((ROOT/s['artifact_path']).read_bytes()) for s in states if (s['horizon'],s['model_family']) in [('T1','RIDGE'),('T2','LIGHTGBM')]}
v.stage('realtime_lineage',v.realtime);v.stage('adversarial',v.adversarial)
