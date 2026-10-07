"""Real Qt screenshot evidence. Fixtures labelled; production access read-only."""
import copy,json,time
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QLabel
from .v2 import Window
from .adapters import ROOT,Adapter,now
from tests.phase11.test_v2 import audit,wait
from tests.phase11.fixtures import create

def main():
    app=QApplication.instance() or QApplication([]);out=ROOT/'docs/phase11/v2'
    w=Window(autorefresh=True);w.setAttribute(Qt.WA_DontShowOnScreen,True);w.resize(1920,1080);w.show();wait(w)
    data=copy.deepcopy(w.data)
    (out/'REAL_DISPLAY_DATA.json').write_text(json.dumps(dict(captured_at=now().isoformat(),predictions={h:{k:r[k] for k in ('snapshot','continuous','pmf','model')} for h,r in data['formal']['predictions'].items()},t0=data['t0']['snapshot'],observation=data['formal']['observation'],ecmwf=data['formal']['ecmwf'].get('run_time'),worker_statuses=[data['formal']['worker_status'],data['t0']['worker_status']],soak=data['soak']),ensure_ascii=False,indent=2),encoding='utf-8')
    app.processEvents();w.grab().save(str(out/'PHASE11_GUI_V2_HOME_1920x1080.png'));w.core.grab().save(str(out/'PHASE11_GUI_V2_DATE_DISPLAY.png'))
    w.select_probability('T0');app.processEvents();w.grab().save(str(out/'PHASE11_GUI_V2_T0_UNCALIBRATED.png'));w.select_probability('T1')
    language=audit(w);(out/'CHINESE_STRINGS.json').write_text(json.dumps(language,ensure_ascii=False,indent=2),encoding='utf-8')
    overflow=[]
    for label in w.findChildren(QLabel):
        if label.isVisible() and not label.wordWrap() and any('\u4e00'<=c<='\u9fff' for c in label.text()) and label.fontMetrics().horizontalAdvance(label.text().split('\n')[0])>label.width()+3:overflow.append(label.text())
    sizes={'1920':{'horizontal_scroll':w.scroll.horizontalScrollBar().maximum(),'vertical_scroll':w.scroll.verticalScrollBar().maximum(),'chinese_overflow':overflow}}
    w.resize(1600,900);app.processEvents();w.grab().save(str(out/'PHASE11_GUI_V2_HOME_1600x900.png'));sizes['1600']={'horizontal_scroll':w.scroll.horizontalScrollBar().maximum(),'vertical_scroll':w.scroll.verticalScrollBar().maximum()};w.scroll.verticalScrollBar().setValue(w.scroll.verticalScrollBar().maximum());app.processEvents();w.grab().save(str(out/'PHASE11_GUI_V2_1600_BOTTOM.png'));w.close();app.processEvents()
    root=create('v2_no_forecast');test=Window(root,False);test.setAttribute(Qt.WA_DontShowOnScreen,True);test.resize(1920,1080);test.show();wait(test)
    fixture=copy.deepcopy(test.data);fixture['formal']['predictions']['T2'].update(snapshot={},continuous={},probability={},pmf={},error={'reason':'NO_LEGAL_ECMWF_RUN','time':now().isoformat()})
    test.render(fixture);test.select_probability('T2');test.header.state.setText('测试夹具 · 非正式预测数据');app.processEvents();test.grab().save(str(out/'PHASE11_GUI_V2_T2_NO_FORECAST_FIXTURE.png'));test.close();app.processEvents()
    (out/'LAYOUT_EVIDENCE.json').write_text(json.dumps(sizes,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(language_total=language['total'],unapproved=language['unapproved'],sizes=sizes),ensure_ascii=True))
if __name__=='__main__':main()
