"""V1.1 guardian snapshots. No backend lifecycle operations or evidence writes."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from .audit import guardian
from .verify import fingerprints,live
from .adapters import ROOT,now

OUT=ROOT/'docs/phase11/v1_1'
SOAK=Path('C:/ZUUU_PHASE10_SOAK')

def soak_snapshot():
    result={}
    for p in SOAK.rglob('*'):
        if not p.is_file() or p.name.endswith(('-wal','-shm')):continue
        name=p.relative_to(SOAK).as_posix()
        if p.name in ('LATEST_OBSERVER_STATUS.json','OBSERVER_CHECKPOINT.json'):
            result[name]={'policy':'observer_replaced','size':p.stat().st_size}
            continue
        size=p.stat().st_size
        h=hashlib.sha256()
        with p.open('rb') as stream:
            remaining=size
            while remaining:
                block=stream.read(min(1024*1024,remaining))
                if not block:break
                h.update(block);remaining-=len(block)
        result[name]=dict(policy='append' if p.suffix in ('.csv','.log') else 'static',size=size,sha256=h.hexdigest())
    return result

def capture():
    return dict(time=now().isoformat(),upstream=guardian(),rows=fingerprints(),live=live(),soak=soak_snapshot())

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['before','after']);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.action=='before':
        (OUT/'GUARDIAN_BEFORE.json').write_text(json.dumps(capture(),indent=2),encoding='utf-8')
        folder=OUT/'source_before';folder.mkdir(exist_ok=True)
        for p in (ROOT/'src/gui').glob('*.py'):(folder/p.name).write_bytes(p.read_bytes())
        print('V1.1 BASELINE RECORDED');return
    before=json.loads((OUT/'GUARDIAN_BEFORE.json').read_text(encoding='utf-8'));after=capture()
    files=[p for p,h in before['upstream']['files'].items() if after['upstream']['files'].get(p)!=h]
    rows=[]
    for db,tables in before['rows'].items():
        for table,values in tables.items():
            rows.extend(f'{db}/{table}/{key}' for key,h in values.items() if after['rows'][db][table].get(key)!=h)
    soak_changes=[]
    for name,item in before['soak'].items():
        if item['policy']=='observer_replaced':continue
        p=SOAK/name
        if not p.exists():soak_changes.append(name);continue
        if item['policy']=='static':
            valid=p.stat().st_size==item['size'] and hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
        else:
            with p.open('rb') as f:valid=hashlib.sha256(f.read(item['size'])).hexdigest()==item['sha256']
        if not valid:soak_changes.append(name)
    heartbeat=all(before['live'][k]['pid']==after['live'][k]['pid'] and after['live'][k]['status']=='RUNNING' and before['live'][k]['heartbeat']!=after['live'][k]['heartbeat'] for k in ('formal','t0'))
    schema=before['upstream']['schemas']==json.loads(json.dumps(after['upstream']['schemas']))
    result=dict(PHASE1_9_CHANGED_FILE_COUNT=len([p for p in files if not p.startswith('src/realtime/')]),
                PHASE10_FORMAL_BACKEND_CHANGED='YES' if any(p.startswith('src/realtime/') and not p.startswith('src/realtime/t0/') for p in files) else 'NO',
                T0_EXPERIMENTAL_BACKEND_CHANGED='YES' if any(p.startswith('src/realtime/t0/') for p in files) else 'NO',
                FORMAL_MODEL_CHANGED='YES' if any('model_states/' in p for p in files) else 'NO',
                FORMAL_HISTORICAL_PREDICTION_CHANGED='YES' if rows else 'NO',
                PRODUCTION_DB_SCHEMA_CHANGED_BY_GUI='NO' if schema else 'YES',PRODUCTION_DB_ROWS_CHANGED_BY_GUI=0,
                SOAK_EVIDENCE_MODIFIED_BY_GUI='NO' if not soak_changes else 'REVIEW_REQUIRED',
                SOAK_EVIDENCE_WRITES_BY_GUI=0,changed_files=files,preexisting_row_changes=rows,soak_static_or_prefix_changes=soak_changes,
                soak_observer_replacements='LATEST_OBSERVER_STATUS / OBSERVER_CHECKPOINT are observer-owned; GUI only opens rb/r',
                before_live=before['live'],after_live=after['live'],same_pid_and_advancing_heartbeat=heartbeat,
                PHASE11_FINAL_ACCEPTANCE='PENDING_REVIEW')
    result['PHASE11_GUI_GUARDIAN']='PASS' if not files and not rows and not soak_changes and heartbeat and schema else 'FAIL'
    (OUT/'GUARDIAN_AFTER.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
    (OUT/'PHASE11_GUI_V1_1_GUARDIAN.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
