"""Phase9 only: refuses existing output and verifies pre-frozen probability protocol."""
import json,csv,hashlib
from src.probability.contracts import *
from src.probability.data import load,fingerprints,reporting_audit
from src.probability.walk_forward import run
from src.probability.assessment import evaluate
from src.probability.storage import semantic,write
from src.data_v1.source_io import sha256_file

def export_csv(path,rows):
 if not rows:return
 fields=list(dict.fromkeys(k for r in rows for k in r))
 with path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:canonical(v) if isinstance(v,(list,dict)) else v for k,v in r.items()} for r in rows)

def build():
 require(not OUTPUT.exists(),'REFUSE OVERWRITE: Phase9 already exists')
 before=json.loads((DOCS/'SOURCE_SHA_BEFORE.json').read_text(encoding='utf-8'))
 require(fingerprints()==before,'Frozen source changed before build')
 protocol_path=DOCS/'PHASE9_PROBABILITY_PROTOCOL_V1.md';ph=json.loads((DOCS/'PHASE9_PROTOCOL_SHA256.json').read_text())['sha256']
 require(sha256_file(protocol_path)==ph,'Protocol physical identity')
 embedded=json.loads(protocol_path.read_text(encoding='utf-8').split('```json\n')[1].split('\n```')[0]);require(embedded==PROTOCOL,'Protocol/runtime mismatch')
 implementation={p.relative_to(ROOT).as_posix():sha256_file(p) for p in list((ROOT/'src/probability').glob('*.py'))+[Path(__file__)]}
 source=load();data=run(source);assessment=evaluate(data)
 data['distribution_registry']=[dict(record_id=m,method=m,definition=PROTOCOL) for m in METHODS]
 data['trajectory_registry']=[dict(record_id='trajectory',status='BLOCKED_FOR_DATA',reason='Phase8 scalar daily Tmax OOS archive has no hourly OOS residual vectors; unproved METAR historical arrival; cannot estimate hourly covariance',trajectory_count=0,tmax_time='BLOCKED',independent_hour_sampling=False)]
 data['protocol']=[dict(record_id='PROBABILITY_PROTOCOL_V1',protocol=PROTOCOL,physical_sha256=ph)]
 digest=semantic(data)
 data['manifest']=[dict(record_id='PROBABILITY_V1',build_status='BUILT_PENDING_INDEPENDENT_ACCEPTANCE',semantic_sha256=digest,protocol_sha256=ph,source_sha_before=before,implementation_sha256=implementation,candidate_decision=assessment['candidates'])]
 require(fingerprints()==before,'Source mutation before publication')
 write(OUTPUT,data);physical=sha256_file(OUTPUT)
 for name,key in [('PHASE9_PROBABILITY_RESULTS.csv','probability_prediction'),('PHASE9_CALIBRATION_TABLE.csv','calibration_bin'),('PHASE9_INTERVAL_COVERAGE.csv','interval_metric'),('PHASE9_SLICE_METRICS.csv','slice_metric'),('PHASE9_METHOD_METRICS.csv','probability_metric'),('PHASE9_RESIDUAL_LINEAGE.csv','residual_history')]:export_csv(DOCS/name,data[key])
 for name,key in [('PHASE9_OVERCONFIDENCE_AUDIT.csv','confidence'),('PHASE9_CATASTROPHIC_CONFIDENCE_AUDIT.csv','catastrophic'),('PHASE9_EXTREME_AUDIT.csv','extreme'),('PHASE9_COMPARISON.csv','comparison'),('PHASE9_BLOCK_BOOTSTRAP.csv','bootstrap')]:export_csv(DOCS/name,assessment[key])
 (DOCS/'PHASE9_BUILD_ASSESSMENT.json').write_text(json.dumps(assessment,ensure_ascii=False,indent=2),encoding='utf-8')
 (DOCS/'PHASE9_DATABASE_SHA256.json').write_text(json.dumps(dict(physical_sha256=physical,semantic_sha256=digest),indent=2),encoding='utf-8')
 after=fingerprints();require(after==before,'Source mutation after build');(DOCS/'SOURCE_SHA_AFTER.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
 print('Phase9 built; independent acceptance required.',physical,digest,flush=True)

if __name__=='__main__':build()
