"""Freeze guard and separate X/Y snapshots; no feature regeneration."""
import json
import hashlib
import importlib.metadata
import numpy as np
from .contracts import ROOT,SOURCES,utc,require
from src.data_v1.source_io import open_snapshot,sha256_file,sidecar_state

def protected_paths():
 paths=set(ROOT/p for p in SOURCES)
 for folder in ('src','tests','config','docs'):
  for p in (ROOT/folder).rglob('*'):
   rel=p.relative_to(ROOT).as_posix()
   if p.is_file() and '__pycache__' not in rel and '/phase8/' not in rel and 'phase8_' not in p.name and not rel.startswith('docs/phase8/'):
    paths.add(p)
 for p in (ROOT/'database').glob('*'):
  if p.is_file() and 'phase8' not in p.name:paths.add(p)
 return sorted(paths)
def fingerprints():return {p.relative_to(ROOT).as_posix():sha256_file(p) for p in protected_paths()}
def guardian():
 before=fingerprints();checks={}
 for path,expected in SOURCES.items():
  require(before[path]==expected,'Frozen physical hash mismatch: '+path)
  c=open_snapshot(ROOT/path,expected)
  checks[path]=dict(integrity=[r[0] for r in c.execute('PRAGMA integrity_check')],foreign_keys=len(c.execute('PRAGMA foreign_key_check').fetchall()),sidecars=sidecar_state(ROOT/path))
  require(checks[path]['integrity']==['ok'] and checks[path]['foreign_keys']==0,'Source integrity: '+path);c.close()
 fm=json.loads((ROOT/'docs/phase7/PHASE7_FILE_MANIFEST.json').read_text(encoding='utf-8'))
 for path,h in fm['sha256'].items():require(sha256_file(ROOT/path)==h,'Phase7 manifest mismatch '+path)
 c=open_snapshot(ROOT/'database/phase7_feature_v1.db')
 from src.features.schema import KEYS,canonical as c7
 m=dict(c.execute('SELECT * FROM phase7_manifest').fetchone());dig=hashlib.sha256((c7(json.loads(m['contract_json']))+'\n').encode())
 for table,keys in KEYS.items():
  if table=='manifest':continue
  dig.update((table+'\n').encode())
  for row in c.execute('SELECT * FROM phase7_'+table+' ORDER BY '+','.join(keys)):dig.update((c7({k:v for k,v in dict(row).items() if k!='created_at'})+'\n').encode())
 require(dig.hexdigest()=='5de92e79d6c073b16024c46114ec9027cc1c0597239f57ee0364baedbff20c6e','Phase7 semantic mismatch');c.close()
 require('PHASE7_ACCEPTANCE = PASS_WITH_WARNINGS' in (ROOT/'docs/phase7/PHASE7_FINAL_INDEPENDENT_ACCEPTANCE.md').read_text(encoding='utf-8'),'Independent acceptance missing')
 require(fingerprints()==before,'Source changed in preflight')
 return before,checks
def dependencies():return {n:importlib.metadata.version(n) for n in ('numpy','pandas','scikit-learn','lightgbm','xgboost','catboost')}
def load():
 c=open_snapshot(ROOT/'database/phase7_feature_v1.db')
 names=sorted(r[0] for r in c.execute('SELECT feature_name FROM phase7_feature_registry'))
 require(len(names)==102,'Feature count')
 result={}
 for h,n in (('T1',729),('T2',728)):
  samples=[dict(r) for r in c.execute('SELECT * FROM phase7_feature_sample WHERE horizon=? ORDER BY issue_time_utc,target_business_date',(h,))]
  require(len(samples)==n,'Universe '+h)
  ids=[s['sample_id'] for s in samples];xx={r['sample_id']:dict(r) for r in c.execute('SELECT * FROM training_feature_view')}
  yy={r['sample_id']:dict(r) for r in c.execute('SELECT * FROM training_label_view')}
  labels={r['sample_id']:dict(r) for r in c.execute('SELECT * FROM phase7_feature_label')}
  result[h]=dict(samples=samples,ids=ids,names=names,X=np.array([[np.nan if xx[i][f] is None else xx[i][f] for f in names] for i in ids],dtype=float),y=np.array([yy[i]['label_tmax_c'] for i in ids]),eligibility=[labels[i]['label_eligibility_time_bjt'] for i in ids])
  result[h]['missing_reasons']={(r['sample_id'],r['feature_name']):r['missing_reason'] for r in c.execute('SELECT v.sample_id,v.feature_name,v.missing_reason FROM phase7_feature_value v JOIN phase7_feature_sample s USING(sample_id) WHERE s.horizon=? AND v.value IS NULL',(h,))}
  for s in samples:require(utc(s['run_available_time'])<=utc(s['issue_time_utc']),'Forecast future')
  require(not any(s['target_business_date']=='2025-08-07' and h=='T2' for s in samples),'Known gap')
 c.close()
 b=open_snapshot(ROOT/'database/phase5_ecmwf_raw_baseline_v1.db');bm={(r['business_date_bjt'],r['horizon']):dict(r) for r in b.execute('SELECT * FROM phase5_baseline_v1_prediction')};b.close()
 q=open_snapshot(ROOT/'database/phase6_statistical_mos_v1_review.db');qm={(r['target_business_date'],r['horizon']):dict(r) for r in q.execute("SELECT * FROM phase6_mos_prediction WHERE model='M6'")};q.close()
 for h,z in result.items():
  z['raw']=np.array([bm[(s['target_business_date'],h)]['ecmwf_raw_tmax_c'] for s in z['samples']])
  z['mos']=np.array([np.nan if qm[(s['target_business_date'],h)]['mos_continuous_tmax'] is None else qm[(s['target_business_date'],h)]['mos_continuous_tmax'] for s in z['samples']])
  require(np.allclose(z['raw'],z['X'][:,names.index('ecmwf_tmax_c')]),'Raw mapping')
 return result
def eligible(z,cutoff,target_date):
 return np.array([i for i,s in enumerate(z['samples']) if utc(z['eligibility'][i])<=utc(cutoff) and utc(s['issue_time_utc'])<utc(cutoff) and s['target_business_date']<target_date],dtype=int)
