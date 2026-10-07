"""Check probability status/method parity in addition to completed arithmetic replay."""
from .common import *
import csv

def audit(s,f,m,p):
    from src.realtime.handoff import forward
    samples=json.loads((OUT/'MASTER_SAMPLE_MANIFEST.json').read_text(encoding='utf-8'))['sample_ids'];rows=[]
    for sid in samples:
        row=m.formal[sid];issue=row['prediction_issue_time'];h=row['horizon'];day=row['target_business_date'];old=p.engine[sid]
        state={'cutoff':issue,'residuals':[r for r in p.residuals if stamp(r['eligibility'])<=stamp(issue) and stamp(r['issue'])<stamp(issue)],'cases':[]}
        for r in p.pred.values():
            if r['origin'] in ('ML','RAW','MOS') and r['record_id'] in p.mass and eligible(r['target_business_date'])<=stamp(issue) and stamp(r['issue'])<stamp(issue):state['cases'].append(dict(r,pmf=p.mass[r['record_id']]['pmf'].tolist()))
        live,_=forward(state,h,day,issue,{'ML':row['ml_prediction'],'RAW':row['raw_ecmwf_prediction'],'MOS':row['mos_prediction']})
        mismatch=live['status']!=old['status'] or live['method']!=old['method']
        rows.append(dict(sample_id=sid,horizon=h,issue=issue,historical_status=old['status'],realtime_probability_status=live['status'],historical_method=old['method'],realtime_method=live['method'],status='FAIL' if mismatch else 'PASS'))
    s.counts['PARITY_PROBABILITY_STATUS_CHECK_COUNT']=len(rows);s.counts['PARITY_PROBABILITY_STATUS_MISMATCH_COUNT']=sum(r['status']=='FAIL' for r in rows)
    output('MASTER_PARITY_STATUS_AUDIT.csv',rows)
    existing=list(csv.DictReader((OUT/'MASTER_HISTORICAL_REALTIME_PARITY.csv').open(encoding='utf-8-sig')));by={r['sample_id']:r for r in rows}
    for row in existing:
        item=by[row['sample_id']];row.update({k:v for k,v in item.items() if k in ('historical_status','realtime_probability_status','historical_method','realtime_method')})
        row['status_mismatch']=int(item['status']=='FAIL')
    output('MASTER_HISTORICAL_REALTIME_PARITY.csv',existing)
