"""Opt-in EXE observation: real Window, original timers, read-only live Adapter.

Only GUI-owned evidence is written. Closing uses Window.close(), never process kill.
"""
import json
import logging
import math
import sys
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase,QFontInfo
from PySide6.QtWidgets import QLabel
from .adapters import ROOT,fmt,now
from .paths import ASSETS

class Errors(logging.Handler):
    def __init__(self):super().__init__(logging.ERROR);self.records=[]
    def emit(self,record):self.records.append(record.getMessage())

def observe(w,app,offscreen=False):
    out=ROOT/'docs/phase11/windows_package';out.mkdir(parents=True,exist_ok=True)
    errors=Errors();logging.getLogger('phase11').addHandler(errors)
    observations=[];finished=False
    tag='EXE_1920' if offscreen else 'EXE_VISIBLE'
    def snapshot():
        data=w.data;f=data.get('formal',{});t=data.get('t0',{});s=data.get('soak',{})
        checks={};predictions={}
        for horizon,card in [('T1',w.t1),('T2',w.t2)]:
            r=f.get('predictions',{}).get(horizon,{})
            top=r.get('pmf',{}).get('top',[]);center=r.get('continuous',{}).get('continuous_prediction_c')
            checks[horizon+'_READ']=bool(r.get('snapshot') and top and r.get('model'))
            checks[horizon+'_INTEGER_PMF_TOP1']=bool(top and isinstance(top[0][0],int) and card.value.text()==fmt(top[0][0],' °C'))
            checks[horizon+'_DECIMAL_CENTER_AUX']=center is not None and card.metrics.fields['Continuous center'].text()==fmt(center,'°C')
            checks[horizon+'_PMF_SANITY']=r.get('pmf',{}).get('total') is not None and abs(r['pmf']['total']-1)<1e-6 and r['pmf']['status']!='DATA WARNING'
            predictions[horizon]=dict(model=r.get('model',{}).get('model_family'),top=top,continuous_center=center,
                main_display=card.value.text(),aux_display=card.metrics.fields['Continuous center'].text(),
                probability_state=r.get('snapshot',{}).get('probability_state_version'),pmf_status=r.get('pmf',{}).get('status'),
                issue_time=r.get('snapshot',{}).get('prediction_issue_time'))
        outputs=t.get('snapshot',{}).get('outputs',[])
        checks.update(GUI_START=w.isVisible(),FROZEN_EXECUTABLE=bool(getattr(sys,'frozen',False)),
            LIVE_PROJECT_ROOT=str(ROOT)=='C:\\ZUUU_Prediction_System',
            T0_READ=bool(outputs and any(r.get('prediction') is not None for r in outputs)),
            ZUUU_READ=f.get('observation',{}).get('temperature_c') is not None,
            ECMWF_READ=bool(f.get('ecmwf') and len(f.get('hours',[]))==72),
            T0_GROWTH_READ=isinstance(t.get('growth',{}).get('n'),int),
            SOAK_READ=bool(s.get('start') and s.get('target_hours')==72 and s.get('acceptance')),
            PHASE10_RUNNING=f.get('worker_status')=='RUNNING',T0_RUNNING=t.get('worker_status')=='RUNNING',
            ICON_LOADED=not w.windowIcon().isNull() and (ASSETS/'zuuu.ico').is_file(),
            CHINESE_FONT_AVAILABLE='Microsoft YaHei UI' in QFontDatabase.families(),
            CHINESE_NAV=w.nav.item(0).text().endswith('首页 / 总览'),READ_ERRORS_ABSENT=not data.get('errors'),
            CHARTS_HAVE_REAL_POINTS=all(any(y is not None and math.isfinite(y) for _,points,_ in chart.series for _,y in points) for chart in [w.trajectory,w.evolution,w.pcharts['T1'][1],w.pcharts['T2'][1]]),
            TABLES_FULLY_VISIBLE=not w.stats.verticalScrollBar().maximum() and not w.system_table.verticalScrollBar().maximum(),
            NO_RENDER_ERRORS=not errors.records)
        if offscreen:
            checks['HOME_1920x1080']=w.width()==1920 and w.height()==1080
            checks['HOME_NO_PAGE_SCROLL']=not w.stack.widget(0).verticalScrollBar().maximum() and not w.stack.widget(0).horizontalScrollBar().maximum()
        else:
            import ctypes
            checks['NATIVE_WINDOW_VISIBLE']=bool(ctypes.windll.user32.IsWindowVisible(int(w.winId())))
            checks['CURRENT_DESKTOP_LAYOUT']=w.width()>=1000 and w.height()>=650 and not w.stack.widget(0).horizontalScrollBar().maximum()
        checks['TEXT_NOT_CLIPPED']=all(label.height()>=label.heightForWidth(label.width()) for card in (w.t0,w.t1,w.t2,w.growth,w.obs,w.ecmwf,w.soak,w.system) for label in card.findChildren(QLabel) if label.isVisible() and label.wordWrap())
        return dict(time=now().isoformat(),refresh_count=w.refresh_count,checks=checks,predictions=predictions,
            T0_growth=t.get('growth'),T0_outputs=outputs,observation_time=f.get('observation',{}).get('observation_time'),
            soak={k:s.get(k) for k in ('status','start','completion','elapsed','progress','critical','high','acceptance','sample_time')},
            phase10=dict(pid=f.get('scheduler',{}).get('pid'),heartbeat=f.get('scheduler',{}).get('time'),status=f.get('worker_status')),
            t0_worker=dict(pid=t.get('event',{}).get('pid'),heartbeat=t.get('event',{}).get('time'),status=t.get('worker_status')))
    def save_image():
        for name,widget in [(('EXE_HOME_1920x1080' if offscreen else 'EXE_VISIBLE_WINDOW'),w),(tag+'_T0_GROWTH',w.growth),(tag+'_SOAK',w.soak),(tag+'_T1_PROBABILITY',w.pcharts['T1'][0]),(tag+'_T2_PROBABILITY',w.pcharts['T2'][0])]:
            if not widget.grab().save(str(out/(name+'.png'))):errors.records.append('PNG_SAVE_FAILED '+name)
    def finish(timeout=False):
        nonlocal finished
        if finished:return
        finished=True
        observations.append(snapshot());save_image()
        checks={k:all(r['checks'].get(k,False) for r in observations) for k in observations[-1]['checks']}
        if not offscreen:
            checks['AUTOMATIC_REFRESH']=w.refresh_count>=2 and len(observations)>=2 and utc_delta(observations[0]['time'],observations[-1]['time'])>=w.interval*.9
        checks['NO_RENDER_ERRORS']=not errors.records
        checks['NOT_TIMED_OUT']=not timeout
        result=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,observations=observations,
            sys_executable=sys.executable,frozen=bool(getattr(sys,'frozen',False)),bundle_root=getattr(sys,'_MEIPASS',None),
            app_module=w.__class__.__module__,font_family=QFontInfo(app.font()).family(),dpi=w.devicePixelRatioF(),
            refresh_interval_seconds=w.interval,render_errors=errors.records,closing_method='Window.close / Qt closeEvent',
            gui_close_time=now().isoformat(),render_mode='Qt 1920x1080 offscreen layout test' if offscreen else 'Native visible Windows window',
            client_size=[w.width(),w.height()],screen_size=[w.screen().size().width(),w.screen().size().height()])
        (out/(tag+'_SMOKE_RESULT.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        w.close();app.quit()
    def loaded():
        if finished:return
        if not observations:
            def first():
                if not finished:
                    observations.append(snapshot());save_image()
                    (out/(tag+'_SMOKE_FIRST.json')).write_text(json.dumps(observations[0],ensure_ascii=False,indent=2),encoding='utf-8')
                    if offscreen:QTimer.singleShot(200,finish)
            QTimer.singleShot(700,first)
        elif w.refresh_count>=2:QTimer.singleShot(700,finish)
    w.loaded.connect(loaded)
    QTimer.singleShot((w.interval+25)*1000,lambda:finish(True))

def utc_delta(before,after):
    from .adapters import utc
    return (utc(after)-utc(before)).total_seconds()
