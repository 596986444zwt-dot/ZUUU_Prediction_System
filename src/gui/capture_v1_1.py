"""Capture a real Qt window and assert layout bounds at the requested Qt DPI."""
import argparse
import json
import time
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication,QLabel
from .app import Window
from .adapters import ROOT
from .theme import STYLE

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--scale',type=float,default=1)
    parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--name',default='PHASE11_V1_1_HOME_1920x1080')
    args=parser.parse_args()
    app=QApplication([]);app.setStyleSheet(STYLE);app.setFont(QFont('Microsoft YaHei UI',9))
    w=Window(args.root,autorefresh=False);w.setAttribute(Qt.WA_DontShowOnScreen,True)
    w.resize(round(1920/args.scale),round(1080/args.scale));w.show()
    deadline=time.monotonic()+15
    while (not w.refresh_count or w.reader) and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
    for _ in range(5):app.processEvents();time.sleep(.02)
    out=ROOT/'docs/phase11/v1_1/screenshots';out.mkdir(parents=True,exist_ok=True)
    pix=w.grab();pix.save(str(out/(args.name+'.png')))
    scroll=w.stack.widget(0)
    result=dict(name=args.name,root=str(args.root),qt_dpi_scale=w.devicePixelRatioF(),
                logical_size=[w.width(),w.height()],physical_image_size=[pix.width(),pix.height()],
                page_scroll=[scroll.horizontalScrollBar().maximum(),scroll.verticalScrollBar().maximum()],
                stats_scroll=w.stats.verticalScrollBar().maximum(),system_scroll=w.system_table.verticalScrollBar().maximum(),
                refresh_count=w.refresh_count,errors=w.data.get('errors',{}),violations=[])
    for name,card in [('T0',w.t0),('T1',w.t1),('T2',w.t2),('growth',w.growth),('METAR',w.obs),('ECMWF',w.ecmwf),('soak',w.soak),('system',w.system)]:
        if card.body.height()<card.body.heightForWidth(card.body.width()):result['violations'].append(name+' body clipping')
        for label in card.findChildren(QLabel):
            if label.isVisible() and label.wordWrap() and label.height()<label.heightForWidth(label.width()):
                result['violations'].append(name+' label clipping: '+label.text()[:25])
    if abs(w.devicePixelRatioF()-args.scale)>.01:result['violations'].append('Qt DPI mismatch')
    if [pix.width(),pix.height()]!=[1920,1080]:result['violations'].append('physical capture size mismatch')
    if args.scale==1:
        if any(result['page_scroll']):result['violations'].append('1920 home requires page scrolling')
        if result['stats_scroll'] or result['system_scroll']:result['violations'].append('summary table clipping')
        prefix='' if args.root.resolve()==ROOT.resolve() else 'TEST_FIXTURE_'
        for name,widget in [('T0_GROWTH_STATUS',w.growth),('SOAK_STATUS',w.soak),('T1_PROBABILITY',w.pcharts['T1'][0]),('T2_PROBABILITY',w.pcharts['T2'][0])]:widget.grab().save(str(out/(prefix+name+'.png')))
    result['status']='PASS' if not result['violations'] else 'FAIL'
    (out/(args.name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    w.close();app.processEvents();print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(bool(result['violations']))

if __name__=='__main__':raise SystemExit(main())
