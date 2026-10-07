"""V2 acceptance cases. Fixture writers are restricted to docs/phase11/fixtures."""
import copy,json,re,sqlite3,time,sys,unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QLabel,QPushButton
from src.gui.v2 import Window,Header,PAGES
from src.gui.chinese import zh,targets,date_title
from src.gui.paths import resource_path
from src.gui.adapters import Adapter,ROOT,readonly,now,BJT
from tests.phase11.fixtures import create

OUT=ROOT/'docs/phase11/v2'
APPROVED={'ZUUU','ECMWF','METAR','T0','T','RIDGE','LIGHTGBM','Level0','L1_A','TOP','BJT','UTC','V1','QNH','hPa'}
def visible_strings(w):
    result=[x.text() for x in w.findChildren(QLabel)]+[x.text() for x in w.findChildren(QPushButton)]+[w.nav.itemText(i) for i in range(w.nav.count())]
    for x in w.findChildren(QLabel):
        tip=x.toolTip()
        if tip.startswith('原始机场气象报文：'):tip=tip.split('\n收到时间：')[-1]  # Raw source evidence is not a UI label.
        if tip:result.append(tip)
    table=w.model_table
    result += [table.item(i,j).text() for i in range(4) for j in range(4) if table.item(i,j)]
    result += [table.horizontalHeaderItem(i).text() for i in range(4)]
    result += [name for graph in (w.trajectory,w.t0chart,w.prob_chart) for name,pts,mode,col in graph.series]
    result += ['温度（°C）','概率（%）','北京时间（BJT）','图表暂不可用','暂无数据']
    return [s for s in result if s]
def audit(w):
    strings=visible_strings(w);bad=[]
    for s in strings:
        tokens=re.findall(r'[A-Za-z][A-Za-z0-9_+]*',s.replace('°C',''))
        unknown=[t for t in tokens if t not in APPROVED and t not in ('T+1','T+2')]
        if unknown:bad.append(dict(text=s,tokens=unknown))
    return dict(total=len(strings),approved=len(strings)-len(bad),unapproved=bad,strings=strings)
def wait(w):
    deadline=time.monotonic()+20
    while w.refresh_count==0 and time.monotonic()<deadline:QApplication.processEvents();time.sleep(.02)
    assert w.refresh_count>0
    QApplication.processEvents()
class Acceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.root=create('v2_validation');cls.base=Adapter(cls.root).read()
        cls.w=Window(cls.root,autorefresh=False);cls.w.setAttribute(Qt.WA_DontShowOnScreen,True);cls.w.resize(1920,1080);cls.w.show();wait(cls.w)
    @classmethod
    def tearDownClass(cls):cls.w.close();cls.app.processEvents()
    def setUp(self):self.d=copy.deepcopy(self.base);self.w.resize(1920,1080);self.w.render(self.d);self.app.processEvents()
    def render(self):self.w.render(self.d);self.app.processEvents()
    def test_01_start(self):self.assertGreater(self.w.refresh_count,0)
    def test_02_chinese(self):self.assertFalse(audit(self.w)['unapproved'])
    def test_03_font(self):self.assertIn('Microsoft YaHei UI',QFontDatabase.families())
    def test_04_encoding(self):self.assertFalse(any('\ufffd' in s or '姒傜巼' in s for s in visible_strings(self.w)))
    def test_05_overflow(self):
        bad=[w.text() for w in self.w.findChildren(QLabel) if w.isVisible() and not w.wordWrap() and any('\u4e00'<=c<='\u9fff' for c in w.text()) and w.fontMetrics().horizontalAdvance(w.text().split('\n')[0])>w.width()+3]
        self.assertFalse(bad,bad)
    def test_06_1920(self):self.assertEqual(self.w.scroll.horizontalScrollBar().maximum(),0);self.assertEqual(self.w.scroll.verticalScrollBar().maximum(),0)
    def test_07_1600(self):self.w.resize(1600,900);self.app.processEvents();self.assertEqual(self.w.scroll.horizontalScrollBar().maximum(),0)
    def test_08_header(self):self.assertFalse(self.w.header.image.isNull())
    def test_09_aspect(self):
        im=self.w.header.image;scaled=im.scaled(1920,110,Qt.KeepAspectRatioByExpanding);self.assertAlmostEqual(im.width()/im.height(),scaled.width()/scaled.height(),places=2)
    def test_10_missing_header(self):h=Header(OUT/'DOES_NOT_EXIST.png');h.resize(1920,110);self.assertTrue(h.image.isNull());self.assertFalse(h.grab().isNull());h.close()
    def test_11_t0(self):self.assertEqual(self.w.t0.value.text(),'23.0°C')
    def test_12_t0_midnight(self):self.d['t0']['snapshot']={};self.d['t0']['snapshots']=[];self.render();self.assertEqual(self.w.t0.value.text(),'--')
    def test_13_no_t0_probability(self):self.w.select_probability('T0');self.assertFalse(self.w.prob_chart.series);self.assertIn('不提供正式概率',self.w.prob_chart.message)
    def test_14_t1_integer(self):self.d['formal']['predictions']['T1']['continuous']['continuous_prediction_c']=29.8;self.render();self.assertEqual(self.w.t1.value.text(),'23°C')
    def test_15_t2_integer(self):self.assertEqual(self.w.t2.value.text(),'23°C')
    def no_forecast(self):
        r=self.d['formal']['predictions']['T2'];r.update(snapshot={},continuous={},probability={},pmf={},error={'reason':'NO_LEGAL_ECMWF_RUN','time':now().isoformat()});self.render()
    def test_16_no_forecast(self):self.no_forecast();self.assertEqual(self.w.t2.value.text(),'暂无预测');self.assertIn('等待完整ECMWF',self.w.t2.footer.text())
    def test_17_t1_uncalibrated(self):self.d['formal']['predictions']['T1']['pmf']['status']='UNCALIBRATED';self.render();self.assertIn('概率尚未校准',self.w.t1.footer.text())
    def test_18_t2_uncalibrated(self):self.d['formal']['predictions']['T2']['pmf']['status']='UNCALIBRATED';self.render();self.assertIn('概率尚未校准',self.w.t2.footer.text())
    def test_19_fresh(self):self.assertIn('数据最新',self.w.ecmwf.text())
    def test_20_stale(self):self.d['formal']['ecmwf']['run_time']=(now()-timedelta(hours=13)).isoformat();self.render();self.assertIn('数据陈旧',self.w.ecmwf.text())
    def test_21_error(self):self.d['formal']['health']['ECMWF']['current_status']='ERROR';self.render();self.assertIn('数据异常',self.w.ecmwf.text())
    def test_22_separate(self):self.d['formal']['health']['ECMWF']['current_status']='ERROR';self.render();self.assertIn('数据最新',self.w.ecmwf.text());self.assertIn('请求：数据异常',self.w.ecmwf.text())
    def test_23_pmf(self):self.w.select_probability('T1');self.assertEqual(self.w.prob_chart.series[0][1],self.d['formal']['predictions']['T1']['pmf']['bars'])
    def test_24_sum(self):self.w.select_probability('T1');self.assertIn('概率总和：1.000',self.w.prob_note.text())
    def test_25_evolution(self):self.assertEqual(len({s[0] for s in self.w.t0chart.series}),3)
    def test_26_event_time(self):
        s=copy.deepcopy(self.d['t0']['snapshot']);s.update(trigger_type='EVENT',prediction_time=now().astimezone(BJT).replace(hour=8,minute=37,second=12).isoformat());self.d['t0']['snapshots'].append(s);self.render()
        self.assertTrue(any(abs(x-(8+37/60+12/3600))<1e-9 for _,pts,mode,_ in self.w.t0chart.series if mode=='event' for x,v in pts))
    def test_27_scheduled(self):self.assertEqual(zh('SCHEDULED'),'定时预测')
    def test_28_models(self):self.assertEqual(self.w.model_table.item(0,2).text(),'RIDGE模型')
    def test_29_observation(self):self.assertIn('21°C',self.w.obs.text())
    def test_30_ecmwf(self):self.assertIn('覆盖72小时',self.w.ecmwf.text())
    def test_31_worker(self):self.assertIn('正常运行',self.w.workers.text())
    def test_32_soak(self):self.assertIn('连续运行测试',self.w.soak.text())
    def test_33_no_auto_pass(self):self.d['soak'].update(elapsed=72*3600,threshold='READY_FOR_FINAL_SOAK_ACCEPTANCE',progress=100);self.render();self.assertIn('等待最终验收',self.w.soak.text());self.assertNotIn('验收通过',self.w.soak.text())
    def test_34_growth(self):self.assertIn('0 / 30',self.w.growth.text())
    def test_35_readonly(self):
        with readonly(self.root/'database/phase10_realtime_v1.db') as c:
            with self.assertRaises(sqlite3.DatabaseError):c.execute('UPDATE schema_version SET version=version')
            with self.assertRaises(sqlite3.DatabaseError):c.execute('PRAGMA query_only=OFF')
    def check_close(self,key):
        before=Adapter().read();child=Window(self.root,False);child.show();wait(child);child.close();self.app.processEvents();after=Adapter().read()
        self.assertEqual(after[key]['worker_status'],'RUNNING');k='scheduler' if key=='formal' else 'event';self.assertEqual(before[key][k]['pid'],after[key][k]['pid'])
    def test_36_close_formal(self):self.check_close('formal')
    def test_37_close_t0(self):self.check_close('t0')
    def test_38_resource(self):self.assertTrue(resource_path('resources/images/header_airport.png').is_file())
    def test_39_bundle_resource(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(OUT/'_internal'),create=True):self.assertEqual(resource_path('resources/images/header_airport.png'),OUT/'_internal/resources/images/header_airport.png')
    def test_40_today(self):self.assertEqual(self.w.t0.heading.text(),date_title('T0')[0])
    def test_41_tomorrow(self):self.assertEqual(self.w.t1.heading.text(),date_title('T1')[0])
    def test_42_after(self):self.assertEqual(self.w.t2.heading.text(),date_title('T2')[0])
    def test_43_rollover(self):
        before=now().astimezone(BJT).replace(hour=23,minute=59,second=0,microsecond=0);after=before+timedelta(minutes=1)
        self.w.render(self.d,current=before)
        with patch('src.gui.v2.now',return_value=after):self.w.update_clock()
        self.assertEqual(self.w.last_dates,targets(after));self.assertEqual(self.w.t1.value.text(),'暂无预测')
        if self.w.reader:self.w.reader.wait(10000)
        self.app.processEvents()
    def mismatch(self,h):self.d['formal']['predictions'][h]['snapshot']['target_business_date']=(now().astimezone(BJT).date()+timedelta(days=8)).isoformat();self.render()
    def test_44_t1_date_guard(self):self.mismatch('T1');self.assertIn('日期不一致',self.w.t1.heading.text());self.assertEqual(self.w.t1.value.text(),'暂无预测')
    def test_45_t2_date_guard(self):self.mismatch('T2');self.assertIn('日期不一致',self.w.t2.heading.text());self.w.select_probability('T2');self.assertFalse(self.w.prob_chart.series[0][1])
    def test_46_empty_date(self):self.no_forecast();self.assertEqual(self.w.t2.heading.text(),date_title('T2')[0])
    def test_47_trend_dates(self):self.assertIn(date_title('T2')[0].replace(' T+2',''),self.w.trend_dates.text())
    def test_48_pmf_dates(self):self.assertIn(date_title('T2')[0].split('｜')[1],self.w.prob_buttons['T2'].text())
    def test_49_tooltips(self):self.assertIn('更新时间',self.w.t1.toolTip());self.assertIn('独立',self.w.ecmwf.toolTip())
    def test_50_empty(self):self.w.render({});self.assertEqual(self.w.t2.value.text(),'暂无预测')
    def test_51_read_error(self):self.d['errors']={'formal':'TEST_FIXTURE'};self.render();self.assertIn('读取暂不可用',self.w.refresh_text.text())
    def test_52_warning(self):self.d['formal']['predictions']['T1']['pmf'].update(status='DATA WARNING',top=[],bars=[]);self.render();self.assertIn('数据警告',self.w.t1.footer.text())
    def test_53_navigation(self):self.assertEqual(self.w.nav.count(),12);self.assertEqual(self.w.nav.itemText(11),'系统设置')
    def test_54_no_english(self):self.assertFalse(audit(self.w)['unapproved'])
    def test_55_malformed_module_isolation(self):
        self.d['formal']['hours'][0]['target_time_utc']='malformed TEST_FIXTURE'
        self.render();self.assertIn('暂不可用',self.w.trend.heading.text());self.assertIn('正常运行',self.w.workers.text())
    def test_56_repeated_refresh(self):
        for i in range(3):
            count=self.w.refresh_count;self.w.refresh();deadline=time.monotonic()+10
            while self.w.refresh_count==count and time.monotonic()<deadline:self.app.processEvents();time.sleep(.02)
            self.assertGreater(self.w.refresh_count,count)
            self.w.reader.wait(3000);self.app.processEvents()
    def test_57_registry_identity_without_forecast(self):
        root=create('v2_identity_without_forecast');path=root/'database/phase10_realtime_v1.db'
        self.assertTrue(path.resolve().is_relative_to((ROOT/'docs/phase11/fixtures').resolve()))
        with sqlite3.connect(path) as c:c.execute("DELETE FROM realtime_prediction_snapshots WHERE record_id='T2'")
        r=Adapter(root).formal()['predictions']['T2'];self.assertFalse(r['snapshot']);self.assertEqual(r['model']['model_family'],'LIGHTGBM')

if __name__=='__main__':
    OUT.mkdir(exist_ok=True)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance)
    with (OUT/'TEST_LOG.txt').open('w',encoding='utf-8') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    result=dict(total=r.testsRun,passed=r.testsRun-len(r.failures)-len(r.errors),failed=len(r.failures)+len(r.errors),failures=[(str(t),s) for t,s in r.failures+r.errors])
    (OUT/'TEST_RESULTS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=True));sys.exit(0 if r.wasSuccessful() else 1)
