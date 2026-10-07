"""After EXE close, watch only existing worker PID/heartbeat; no lifecycle calls."""
import json
import time
from .adapters import Adapter,ROOT,now,utc,process_status

def main():
    out=ROOT/'docs/phase11/windows_package'
    result=json.loads((out/'EXE_VISIBLE_SMOKE_RESULT.json').read_text(encoding='utf-8'))
    baseline=json.loads((out/'GUARDIAN_BEFORE.json').read_text(encoding='utf-8'))['live']
    launch=json.loads((out/'EXE_LAUNCH_FINAL.json').read_text(encoding='utf-8-sig'))
    close_time=utc(result['gui_close_time'])
    at_close=result['observations'][-1]
    adapter=Adapter();samples=[];deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        data=adapter.read();f=data.get('formal',{});t=data.get('t0',{})
        current=dict(time=now().isoformat(),formal=dict(pid=f.get('scheduler',{}).get('pid'),status=f.get('worker_status'),heartbeat=f.get('scheduler',{}).get('time')),
                     t0=dict(pid=t.get('event',{}).get('pid'),status=t.get('worker_status'),heartbeat=t.get('event',{}).get('time')))
        samples.append(current)
        checks={}
        for key,before in [('formal',at_close['phase10']),('t0',at_close['t0_worker'])]:
            item=current[key]
            checks[key+'_SAME_PID']=item['pid']==before['pid']==baseline[key]['pid']
            checks[key+'_RUNNING']=item['status']=='RUNNING'
            checks[key+'_HEARTBEAT_ADVANCED_AFTER_CLOSE']=bool(item['heartbeat'] and item['heartbeat']!=before['heartbeat'] and utc(item['heartbeat'])>close_time)
        checks['BOTH_EXE_PROCESSES_CLOSED']=all(process_status(launch[k])=='STOPPED' for k in ('visible_pid','layout_pid'))
        if all(checks.values()):break
        time.sleep(2)
    evidence=dict(PACKAGE_GUI_CLOSE_BACKEND_UNAFFECTED='PASS' if all(checks.values()) else 'FAIL',checks=checks,
                  gui_close_time=result['gui_close_time'],at_gui_close=dict(formal=at_close['phase10'],t0=at_close['t0_worker']),
                  baseline=baseline,samples=samples,after=samples[-1])
    (out/'PACKAGE_CLOSE_BACKEND_EVIDENCE.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(evidence,indent=2))
    return int(not all(checks.values()))

if __name__=='__main__':raise SystemExit(main())
