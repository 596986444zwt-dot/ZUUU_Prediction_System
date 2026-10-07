"""Snapshot reads, hash protection, and scalar OOS capability inventory."""
import hashlib,json,re,base64,zlib
from datetime import datetime
from .contracts import *
from src.data_v1.source_io import open_snapshot,sha256_file

def fingerprints():
 paths=[]
 for folder in ('database','docs','src','tests','config'):
  for p in (ROOT/folder).rglob('*'):
   rel=p.relative_to(ROOT).as_posix()
   if p.is_file() and '__pycache__' not in rel and '/phase9/' not in rel and 'phase9_' not in p.name and not rel.startswith('src/probability/'):
    paths.append(p)
 return {p.relative_to(ROOT).as_posix():sha256_file(p) for p in sorted(paths)}

def pdf_pages():
 p=next((ROOT/'docs/architecture').glob('*.pdf'));b=p.read_bytes();pages=[]
 for m in re.finditer(rb'stream\r?\n(.*?)endstream',b,re.S):
  try:s=zlib.decompress(base64.a85decode(m[1].strip(),adobe=True))
  except Exception:continue
  texts=[]
  for q in re.finditer(rb'\(((?:\\.|[^\\)])*)\)\s*Tj',s,re.S):
   raw=re.sub(rb'\\([0-7]{1,3})',lambda x:bytes([int(x[1],8)]),q[1]);raw=raw.replace(b'\\(',b'(').replace(b'\\)',b')').replace(b'\\\\',b'\\')
   try:t=raw.decode('utf-16-be') if any(v==0 for v in raw) or any(v>127 for v in raw) else raw.decode()
   except UnicodeError:continue
   texts.append(t)
  pages.append(' '.join(texts))
 return pages

def guardian():
 before=fingerprints()
 from src.models.phase8.contracts import SOURCES
 expected=dict(SOURCES,**{'database/phase8_machine_learning_v1.db':'5e3b84c3f86bace44790a4bd8daec55c891439f7aa3169a56bfdf1e6a1e82e58'})
 checks={}
 for path,h in expected.items():
  require(before[path]==h,'Frozen SHA mismatch '+path)
  c=open_snapshot(ROOT/path,h);checks[path]={'integrity':[r[0] for r in c.execute('PRAGMA integrity_check')],'foreign_key_count':len(c.execute('PRAGMA foreign_key_check').fetchall()),'schema_table_counts':{r[0]:c.execute('SELECT COUNT(*) FROM "'+r[0]+'"').fetchone()[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}}
  require(checks[path]['integrity']==['ok'] and checks[path]['foreign_key_count']==0,'Source integrity '+path);c.close()
 for phase in (7,8):
  manifest=json.loads((ROOT/f'docs/phase{phase}/PHASE{phase}_FILE_MANIFEST.json').read_text(encoding='utf-8'))
  for path,h in manifest['sha256'].items():require(sha256_file(ROOT/path)==h,'Frozen manifest mismatch '+path)
 from src.models.phase8.storage import semantic,TABLES
 c=open_snapshot(ROOT/'database/phase8_machine_learning_v1.db');d={t:[dict(r) for r in c.execute('SELECT * FROM phase8_'+t)] for t in TABLES};c.close()
 for r in d['candidate']:r['qualified']=bool(r['qualified'])
 require(semantic(d)=='b7d10f09efb029c01552f4a5dceef954df49d712bb0e6cb1aaed159a6230cf16','Phase8 semantic mismatch')
 require({'T1':d['manifest'][0]['t1_candidate'],'T2':d['manifest'][0]['t2_candidate']}==MODELS,'Phase8 candidate identity')
 pages=pdf_pages();require(len(pages)==11 and any('Correlated Trajectory' in p for p in pages) and any('Phase 9 - Probability' in p for p in pages),'Architecture extraction')
 require(fingerprints()==before,'Preflight source mutation')
 return before,checks,pages

def load():
 c=open_snapshot(ROOT/'database/phase8_machine_learning_v1.db')
 samples={r['record_id']:dict(r) for r in c.execute('SELECT * FROM phase8_sample')}
 predictions={r['input_sample_id']:dict(r) for r in c.execute("SELECT * FROM phase8_prediction WHERE (horizon='T1' AND model_family='RIDGE') OR (horizon='T2' AND model_family='LIGHTGBM')")};c.close()
 c=open_snapshot(ROOT/'database/zuuu_prediction.db');truth={r['business_date_bjt']:dict(r) for r in c.execute('SELECT * FROM zuuu_target_v1')};c.close()
 out={}
 for h,n in (('T1',729),('T2',728)):
  rows=[]
  for s in sorted((s for s in samples.values() if s['horizon']==h),key=lambda s:s['issue_time_utc']):
   p=predictions[s['sample_id']];t=truth[s['target_business_date']]
   require(t['daily_tmax_c']==p['actual_target']==s['actual_target'],'Target mapping')
   require(s['label_eligibility_time']==eligible_time(s['target_business_date']),'Eligibility inherited')
   require(p['ml_prediction'] is None or p['status']=='OOS','OOS only')
   rows.append(dict(sample_id=s['sample_id'],horizon=h,target_business_date=s['target_business_date'],issue=s['issue_time_utc'],eligibility=s['label_eligibility_time'],actual=int(t['daily_tmax_c']),ML=p['ml_prediction'],RAW=p['raw_ecmwf_prediction'],MOS=p['mos_prediction'],phase8_prediction_id=p['prediction_id'],phase8_state=p['state_id'],selected_run=p['selected_run'],run_available_time=p['run_available_time'],season=('DJF' if int(s['target_business_date'][5:7]) in (12,1,2) else 'MAM' if int(s['target_business_date'][5:7]) in (3,4,5) else 'JJA' if int(s['target_business_date'][5:7]) in (6,7,8) else 'SON')))
  require(len(rows)==n,'Universe '+h);out[h]=rows
 require(not any(r['target_business_date']=='2025-08-07' for r in out['T2']),'Known excluded T2 gap')
 return out

def eligible(rows,current,matched=True):
 return [r for r in rows if (not matched or r['ML'] is not None) and r['target_business_date']<current['target_business_date'] and stamp(r['eligibility'])<=stamp(current['issue']) and stamp(r['issue'])<stamp(current['issue'])]

def reporting_audit():
 c=open_snapshot(ROOT/'database/zuuu_prediction.db')
 targets=[dict(r) for r in c.execute('SELECT * FROM zuuu_target_v1')];silver={r['id']:dict(r) for r in c.execute('SELECT * FROM zuuu_silver_observation')}
 selected=[silver[i] for t in targets for i in json.loads(t['all_silver_ids'])]
 cor=[]
 for t in targets:
  if not t['has_correction']:continue
  ids=json.loads(t['all_silver_ids']);obs=[silver[i] for i in ids];changed=[]
  for r in obs:
   if r['is_correction'] and r['supersedes_raw_id']:
    old=c.execute('SELECT * FROM zuuu_silver_observation WHERE bronze_raw_id=?',(r['supersedes_raw_id'],)).fetchone()
    changed.append(dict(corrected_temperature=r['temperature_c'],superseded_temperature=old['temperature_c'] if old else None))
  cor.append(dict(date=t['business_date_bjt'],has_correction=t['has_correction'],tmax_has_correction=t['tmax_has_correction'],daily_tmax=t['daily_tmax_c'],changes=changed))
 c.close()
 return dict(target_days=len(targets),selected_observations=len(selected),target_decimal_count=sum(t['daily_tmax_c']!=int(t['daily_tmax_c']) for t in targets),selected_observation_decimal_count=sum(r['temperature_c']!=int(r['temperature_c']) for r in selected),all_silver_decimal_count=sum(r['temperature_c']!=int(r['temperature_c']) for r in silver.values()),cor_days=cor,multiple_tmax_days=sum(t['tmax_occurrence_count']>1 for t in targets),interpretation='Daily maximum of reported integer METAR temperatures. Half-degree latent intervals are a probability approximation, not proof of actual rounding or historical receipt time.')
