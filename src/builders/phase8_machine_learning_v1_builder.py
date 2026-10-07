"""Train only the predeclared nested T1/T2 experiment and publish new assets."""
import os
import json
import csv
import hashlib
import warnings
from datetime import datetime,timezone
from pathlib import Path
from src.models.phase8.contracts import *
from src.models.phase8.data import guardian,load,fingerprints,dependencies
from src.models.phase8.walk_forward import run
from src.models.phase8.metrics import summarize,candidates
from src.models.phase8.storage import semantic,write

def csvfile(name,rows):
 with (DOCS/name).open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def main():
 require(not OUTPUT.exists(),'Phase8 official database already exists; refuse overwrite')
 frozen=DOCS/'PHASE8_CONTRACT.json'
 require(frozen.exists() and json.loads(frozen.read_text(encoding='utf-8'))==CONTRACT,'Contract must be frozen before training')
 before,checks=guardian();source=load()
 (DOCS/'PHASE8_SOURCE_SHA_BEFORE.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
 (DOCS/'PHASE8_SOURCE_GUARDIAN_PREFLIGHT.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
 warnings.filterwarnings('ignore',message='X does not have valid feature names, but LGBMRegressor was fitted with feature names')
 data=run(source,DOCS/'model_states')
 metrics,slices,compare=summarize(data['prediction']);candidate,decisions=candidates(compare,slices)
 data['registry']=[dict(record_id=f,model_family=f,grid_json=canonical(GRIDS[f]),random_seed=SEED,contract_json=canonical(CONTRACT)) for f in FAMILIES]
 for table,rs in (('metric',metrics),('slice_metric',slices),('comparison',compare),('candidate',decisions)):
  data[table]=[dict(record_id=table+'/'+str(i),**{k:canonical(v) if isinstance(v,(dict,list)) else v for k,v in r.items()}) for i,r in enumerate(rs)]
 digest=semantic(data)
 implementation={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'src/models/phase8').glob('*.py'))}
 implementation['src/builders/phase8_machine_learning_v1_builder.py']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
 data['manifest']=[dict(record_id=VERSION,build_status='BUILT_PENDING_INDEPENDENT_ACCEPTANCE',contract_json=canonical(CONTRACT),contract_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),semantic_sha256=digest,dependencies_json=canonical(dependencies()),source_sha_before_json=canonical(before),source_sha_after_json=canonical(fingerprints()),implementation_sha_json=canonical(implementation),t1_candidate=candidate['T1'],t2_candidate=candidate['T2'],created_at=datetime.now(timezone.utc).isoformat())]
 require(fingerprints()==before,'Source mutation: stop before DB publication')
 staged=OUTPUT.with_suffix('.building.db');require(not staged.exists(),'Staged file exists; preserve it and stop')
 write(staged,data);require(fingerprints()==before,'Source mutation: stop before publication')
 os.link(staged,OUTPUT);staged.unlink()
 (DOCS/'PHASE8_SOURCE_SHA_AFTER.json').write_text(json.dumps(fingerprints(),indent=2),encoding='utf-8')
 csvfile('PHASE8_MODEL_COMPARISON.csv',compare)
 for h in ('T1','T2'):csvfile('PHASE8_'+h+'_RESULTS.csv',[p for p in data['prediction'] if p['horizon']==h])
 csvfile('PHASE8_WALK_FORWARD_AUDIT.csv',data['split']);csvfile('PHASE8_HYPERPARAMETER_AUDIT.csv',data['hyperparameter'])
 csvfile('PHASE8_INNER_VALIDATION_PREDICTIONS.csv',data['inner_prediction']);csvfile('PHASE8_SLICE_METRICS.csv',slices)
 csvfile('PHASE8_MODEL_STATES.csv',data['state'])
 preprocessing=[]
 for r in data['preprocessing']:
  prep=json.loads(r['preprocessing_json']);z=source[r['horizon']];sp=next(s for s in data['split'] if s['record_id']==r['split_id'])
  statePred=[p for p in data['prediction'] if p['state_id']==r['state_id']] if r['stage']=='OUTER' else []
  training_ids=set(json.loads(sp['training_ids_json']))
  for j,name in enumerate(z['names']):
   ix=prep['indices'].index(j) if j in prep['indices'] else None
   preprocessing.append(dict(state_id=r['state_id'],split_id=r['split_id'],stage=r['stage'],horizon=r['horizon'],model=r['model_family'],training_cutoff=r['training_cutoff'],feature_name=name,training_valid=prep['valid_counts'][j],training_null=prep['null_counts'][j],oos_null=sum(name in json.loads(p['input_null_features_json']) for p in statePred),handling=prep['dropped'].get(name,prep['imputation_policy']),variance=prep['variance'][j],training_median=prep['medians'][ix] if ix is not None and r['model_family']=='RIDGE' else None,scaling_policy=prep['scaler_policy'],input_missing_reason_counts=canonical(__import__('collections').Counter(reason for (sid,n),reason in z['missing_reasons'].items() if n==name and sid in training_ids))))
 csvfile('PHASE8_FEATURE_PREPROCESSING_AUDIT.csv',preprocessing)
 physical=hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
 (DOCS/'PHASE8_DATABASE_SHA256.json').write_text(json.dumps(dict(PHASE8_PHYSICAL_SHA256=physical,PHASE8_SEMANTIC_SHA256=digest),indent=2),encoding='utf-8')
 text=['# PHASE8 BUILD REPORT','IMPLEMENTATION DETAIL — no framework deviation. Frozen architecture pages 6/7/9/10 reviewed. T1/T2 only. Each model learns the local continuous residual (observed minus raw), then adds it to raw ECMWF.','Minimum history 180: roughly six months of settled labels, avoiding tiny-sample fits; fixed before results. Inner minimum 120. Expanding 14-calendar-day blocks deliberately keep fitted state unchanged throughout each block. Every daily prediction references the block-first cutoff and independent past-only tuning; this is chronological blocked Walk-Forward, not daily refit.','Parameter grids, candidate gates, preprocessing rules and seed were written in PHASE8_CONTRACT.json before the first official fit. No grid changes are allowed in this run.','Ridge training-only medians, missing indicators and scaling; RF/LightGBM/XGBoost/CatBoost native NaN. All-null, exact duplicate and zero-variance rules use X_train only. No full-history correlation filtering or target selection. FEATURE_V1 NULL and masks remain intact.','Radiation and precipitation preceding-hour interval semantics inherited unchanged. T0 arrival blocker remains; Meteostat absent. M6 is a frozen benchmark and Phase6 candidate stays NONE.','Candidate decisions are descriptive selections for future use based on this experiment, never retroactively claimed as past-time selected model predictions. Candidate rule predeclared in contract. No system champion is created.','## Comparison',json.dumps(compare,indent=2),'## Candidate decision',json.dumps(dict(candidates=candidate,reasons=decisions),indent=2),'## Source Guardian','PASS: before == after for every frozen source file.','## Reproduction',f'Physical SHA256: {physical}\nSemantic SHA256: {digest}','All model states persist estimator/preprocessor artifacts, seed, parameter choice, training IDs, both inner splits and their predictions. Feature importance/coefficients are POST-HOC DIAGNOSTIC ONLY and never alter training.','Build complete; independent acceptance is still required. STOP AT PHASE8.']
 (DOCS/'PHASE8_BUILD_REPORT.md').write_text('\n\n'.join(text),encoding='utf-8')
 print(json.dumps(dict(build='COMPLETE_PENDING_AUDIT',candidates=candidate,comparison=compare,physical_sha256=physical,semantic_sha256=digest),indent=2),flush=True)
if __name__=='__main__':main()
