"""Additional direct upstream identity/actual-time checks; no probability builder calls."""
from pathlib import Path
from datetime import datetime,timedelta
import json
from collections import Counter
from src.data_v1.source_io import open_snapshot,sha256_file

ROOT=Path(__file__).resolve().parents[2];DOCS=ROOT/'docs/phase9'

def audit():
 counts=Counter();examples=[]
 def check(ok,key,where):
  if not ok:
   counts[key]+=1
   if len(examples)<30:examples.append(dict(key=key,where=where))
 def stamp(t):return datetime.fromisoformat(t.replace('Z','+00:00'))
 path=ROOT/'database/phase9_probability_v1.db';before=sha256_file(path);c=open_snapshot(path)
 def rows(t):return [json.loads(r[0]) for r in c.execute('SELECT payload_json FROM phase9_'+t)]
 pp={p['record_id']:p for p in rows('probability_prediction')};ss={s['sample_id']:s for s in rows('sample')};cal=rows('calibration_registry');selection=rows('candidate_selection');protocol=rows('protocol')[0]['protocol'];c.close()
 c=open_snapshot(ROOT/'database/zuuu_prediction.db');truth={r['business_date_bjt']:r['daily_tmax_c'] for r in c.execute('SELECT business_date_bjt,daily_tmax_c FROM zuuu_target_v1')};c.close()
 expected=set()
 methods=protocol['residual_methods']+[m+'_CAL' for m in protocol['residual_methods']]
 for sid in ss:
  expected.update(sid+'/'+origin+'/'+method for origin in ('ML','RAW','MOS') for method in methods)
  expected.update(sid+'/'+channel for channel in ('ENGINE','RAW_SELECTED','MOS_SELECTED'))
 check(expected==set(pp),'PROBABILITY_UNIVERSE_MISMATCH','all records')
 for rid,p in pp.items():
  s=ss[p['sample_id']]
  check(p['actual']==s['actual']==truth[s['target_business_date']],'TARGET_IDENTITY_MISMATCH',rid)
  check(p['horizon']==s['horizon'] and p['target_business_date']==s['target_business_date'] and stamp(p['issue'])==stamp(s['issue']),'PREDICTION_TIME_IDENTITY_MISMATCH',rid)
  if p['Brier'] is not None:
   expected_time=(datetime.fromisoformat(s['target_business_date']+'T00:00:00+08:00')+timedelta(days=2)).isoformat()
   check(p['eligibility']==expected_time==s['eligibility'],'ELIGIBILITY_IDENTITY_MISMATCH',rid)
   if p['origin'] in ('ML','RAW','MOS'):check(p['continuous_prediction']==s[p['origin']],'CONTINUOUS_IDENTITY_MISMATCH',rid)
   if p['status']=='FALLBACK':check(p['continuous_prediction']==s[p['probability_source_level']],'FALLBACK_VALUE_MISMATCH',rid)
 calibration_edges=0;selection_edges=0
 for r in cal:
  current=ss['/'.join(r['record_id'].split('/')[:2])]
  for pid in r['calibration_ids']:
   historical=ss[pp[pid]['sample_id']];calibration_edges+=1
   check(stamp(historical['eligibility'])<=stamp(current['issue']) and historical['target_business_date']<current['target_business_date'],'FUTURE_CALIBRATION_LABEL_COUNT',r['record_id'])
   check(historical['horizon']==current['horizon'],'CROSS_HORIZON_CONTAMINATION_COUNT',r['record_id'])
 for r in selection:
  current=ss['/'.join(r['record_id'].split('/')[:2])]
  for option in r['options']:
   for pid in option['validation_ids']:
    historical=ss[pp[pid]['sample_id']];selection_edges+=1
    check(stamp(historical['eligibility'])<=stamp(current['issue']) and historical['target_business_date']<current['target_business_date'],'FUTURE_SELECTION_LABEL_COUNT',r['record_id'])
    check(historical['horizon']==current['horizon'],'CROSS_HORIZON_CONTAMINATION_COUNT',r['record_id'])
 check(before==sha256_file(path),'SOURCE_MUTATION','phase9 DB')
 result=dict(status='FAIL' if counts else 'PASS',prediction_rows=len(pp),calibration_edges=calibration_edges,method_selection_edges=selection_edges,violation_counts=dict(counts),examples=examples,physical_sha256=before,method='Direct stored prediction actual/time/eligibility comparisons against frozen TARGET and sample identity; all inner calibration/selection labels independently checked at original D+2 eligibility')
 (DOCS/'PHASE9_PREDICTION_IDENTITY_AUDIT.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
 if counts:raise SystemExit(1)
 return result

if __name__=='__main__':audit()
