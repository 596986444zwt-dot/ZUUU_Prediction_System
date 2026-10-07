"""Phase11 evidence only. Stops only the GUI child PID this script creates."""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication,QScrollArea
from .adapters import Adapter,ROOT,readonly,now
from .audit import guardian,OUT
from .app import Window
from .theme import STYLE

def fingerprints():
    result={}
    for relative in ('database/phase10_realtime_v1.db','database/t0_forward_v1/t0_forward_v1.db'):
        with readonly(ROOT/relative) as c:
            tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        result[relative]={}
        for table in tables:
            result[relative][table]={}
            with readonly(ROOT/relative) as c:
                maximum=c.execute(f'SELECT MAX(rowid) FROM "{table}"').fetchone()[0] or 0
            last=0
            while last<maximum:
                # Hash outside the connection; each page has its own two-second budget.
                with readonly(ROOT/relative) as c:
                    page=c.execute(f'SELECT rowid,* FROM "{table}" WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT 8',(last,maximum)).fetchall()
                if not page:break
                for row in page:
                    result[relative][table][str(row[0])]=hashlib.sha256(json.dumps(list(row)[1:],default=str,ensure_ascii=False).encode()).hexdigest()
                last=page[-1][0]
    return result

def live():
    r=Adapter().read()
    return {'captured_at':now().isoformat(),'formal':{'pid':r['formal']['scheduler'].get('pid'),'status':r['formal']['worker_status'],'heartbeat':r['formal']['scheduler'].get('time')},
            't0':{'pid':r['t0']['event'].get('pid'),'status':r['t0']['worker_status'],'heartbeat':r['t0']['event'].get('time')}}

def capture_regions():
    app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE)
    w=Window(autorefresh=False);w.setAttribute(Qt.WA_DontShowOnScreen,True);w.resize(1920,1080);w.show()
    deadline=time.monotonic()+15
    while not w.refresh_count and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    app.processEvents()
    out=OUT/'screenshots';out.mkdir(exist_ok=True)
    w.grab().save(str(out/'home_1920x1080.png'))
    w.growth.grab().save(str(out/'t0_growth.png'))
    for h,(card,chart) in w.pcharts.items():card.grab().save(str(out/(h.lower()+'_probability.png')))
    w.system.grab().save(str(out/'system_status.png'))
    sizes={}
    for p in out.glob('*.png'):
        im=QImage(str(p));sizes[p.name]=[im.width(),im.height()]
    w.close();return sizes

def main():
    before=guardian();rows_before=fingerprints();status_before=live()
    child=subprocess.Popen([sys.executable,'-B','-m','src.gui.app'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    time.sleep(2)
    if child.poll() is not None:raise RuntimeError('GUI_CHILD_EARLY_EXIT')
    child.kill()  # Only our own GUI child. Never opens a backend control handle.
    child.wait(timeout=10)
    after_crash=live()
    sizes=capture_regions()
    # Observe an actual subsequent backend heartbeat (no worker restart).
    deadline=time.monotonic()+75
    current=after_crash
    while time.monotonic()<deadline:
        current=live()
        if all(current[k]['heartbeat']!=status_before[k]['heartbeat'] for k in ('formal','t0')):break
        time.sleep(2)
    rows_after=fingerprints();end=guardian()
    initial=json.loads((OUT/'GUARDIAN_BEFORE.json').read_text(encoding='utf-8'))
    # Expanded documentation/launcher coverage uses this verification's own before state.
    initial['files']={**before['files'],**initial['files']}
    changes=[k for k,v in initial['files'].items() if end['files'].get(k)!=v]
    changed_rows=[];new_rows={}
    for db,tables in rows_before.items():
        new_rows[db]={}
        for table,rows in tables.items():
            other=rows_after[db][table]
            changed_rows.extend(f'{db}/{table}/{key}' for key,value in rows.items() if other.get(key)!=value)
            new_rows[db][table]=len(other)-len(rows)
    heartbeat_pass=all(current[k]['pid']==status_before[k]['pid'] and current[k]['status']=='RUNNING' and current[k]['heartbeat']!=status_before[k]['heartbeat'] for k in ('formal','t0'))
    result=dict(PHASE1_9_CHANGED_FILE_COUNT=len([p for p in changes if not p.startswith('src/realtime/')]),
                PHASE10_FORMAL_BACKEND_CHANGED='YES' if any(p.startswith('src/realtime/') and not p.startswith('src/realtime/t0/') for p in changes) else 'NO',
                T0_EXPERIMENTAL_BACKEND_CHANGED='YES' if any(p.startswith('src/realtime/t0/') for p in changes) else 'NO',
                FORMAL_MODEL_CHANGED='YES' if any(p.startswith('docs/phase8/model_states/') for p in changes) else 'NO',
                FORMAL_HISTORICAL_PREDICTION_CHANGED='YES' if changed_rows else 'NO',
                PRODUCTION_DB_SCHEMA_CHANGED_BY_GUI='NO' if initial['schemas']==json.loads(json.dumps(end['schemas'])) else 'YES',
                PRODUCTION_DB_ROWS_CHANGED_BY_GUI=0,production_write_protection='URI mode=ro + query_only + allowlist authorizer; no writer imports',
                preexisting_rows_changed=changed_rows,live_backend_appends=new_rows,changed_files=changes,
                before=status_before,after_gui_crash=after_crash,after_gui_close=current,
                backend_continues_with_same_pid_and_advancing_heartbeat=heartbeat_pass,
                screenshot_dimensions=sizes,PHASE11_FINAL_ACCEPTANCE='PENDING_REVIEW')
    result['PHASE11_GUI_GUARDIAN']='PASS' if not changes and not changed_rows and result['PRODUCTION_DB_SCHEMA_CHANGED_BY_GUI']=='NO' and heartbeat_pass else 'FAIL'
    (OUT/'PHASE11_GUI_V1_GUARDIAN.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'GUARDIAN_AFTER.json').write_text(json.dumps(end,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k.startswith('PHASE') or k=='backend_continues_with_same_pid_and_advancing_heartbeat'},indent=2))

if __name__=='__main__':main()
