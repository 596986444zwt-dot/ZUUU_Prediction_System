"""New reference preview regressions; all synthetic states use TEST_FIXTURE."""
import json,sys,unittest,copy
from PySide6.QtCore import QRect,QSize
from PySide6.QtWidgets import QLabel
from tests.phase11.test_one_screen import OneScreen
from src.gui.windowing import initial_geometry
from src.gui.adapters import ROOT

class Preview(OneScreen):
    def test_59_header_compact(self):self.assertEqual(self.w.header.height(),140)
    def test_67_default_geometry_small(self):
        size,p=initial_geometry(QRect(0,0,1600,860));self.assertEqual(size,QSize(1472,799));self.assertEqual(p.x(),64);self.assertGreaterEqual(p.y(),0)
    def test_88_summary_only(self):
        content=' '.join(label.text() for label in self.w.summary.findChildren(QLabel))
        for word in ('PID','心跳','有效结算日'):self.assertNotIn(word,content)
        self.assertIn('ZUUU',content);self.assertIn('ECMWF',content)
        self.assertNotIn('请求',self.w.summary_labels['ECMWF'].text())
        self.assertTrue(self.w.summary_request.text().startswith('请求状态：'))
    def test_93_default_probability(self):
        child=self.normal_child();self.assertEqual(child.selected,'T1');child.render(self.w.data);self.assertTrue(child.prob_chart.series[0][1])
    def test_94_tabs_do_not_create_t0_pmf(self):
        self.w.select_probability('T0');self.assertFalse(self.w.prob_chart.series);self.assertIn('暂不提供',self.w.prob_chart.message)
    def test_95_current_line_no_synthetic_points(self):
        points=copy.deepcopy(self.w.t0chart.series);self.w.update_clock();self.assertTrue(self.w.t0chart.intraday);self.assertEqual(self.w.t0chart.x_bounds,(8,18));self.assertEqual(self.w.t0chart.series,points)
    def test_96_full_probability_dates_compact(self):
        self.w.resize(1472,799);self.app.processEvents()
        for h,b in self.w.prob_buttons.items():self.assertIn({'T0':'T0','T1':'T+1','T2':'T+2'}[h],b.text());self.assertIn('月',b.text())
    def test_97_preview_launcher_only_gui(self):
        content=(ROOT/'start_gui_preview.bat').read_text(encoding='utf-8');self.assertIn('-m src.gui.app --preview',content)
        for word in ('worker','realtime','scheduler','taskkill','pyinstaller'):self.assertNotIn(word,content.lower())
    def test_98_normal_current_screen_fit(self):
        self.w.resize(1472,799);self.app.processEvents()
        self.assertEqual(self.w.scroll.verticalScrollBar().maximum(),0);self.assertEqual(self.w.scroll.horizontalScrollBar().maximum(),0)
        self.assertLessEqual(self.w.dashboard.height(),self.w.scroll.viewport().height());self.assertGreaterEqual(self.w.centralWidget().layout().contentsMargins().bottom(),8)
    def test_99_uncalibrated_independent_snapshot_status(self):
        data=copy.deepcopy(self.w.data);data['formal']['predictions']['T1']['pmf']['status']='UNCALIBRATED';data['formal']['predictions']['T1']['snapshot']['status']='READY'
        self.w.render(data);self.assertIn('概率尚未校准',self.w.t1.footer.text());self.assertNotIn('降级运行',self.w.t1.footer.text())
    def test_100_1600x900_complete(self):
        self.w.resize(1600,900)
        # Qt posts a second layout request after minimum-size changes.
        for _ in range(3):self.app.processEvents()
        self.assertEqual(self.w.scroll.verticalScrollBar().maximum(),0);self.assertEqual(self.w.scroll.horizontalScrollBar().maximum(),0)

if __name__=='__main__':
    out=ROOT/'docs/phase11/new_preview'
    with (out/'TEST_LOG.txt').open('w',encoding='utf-8') as f:r=unittest.TextTestRunner(stream=f,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Preview))
    result=dict(total=r.testsRun,passed=r.testsRun-len(r.failures)-len(r.errors),failed=len(r.failures)+len(r.errors),failures=[(str(t),s) for t,s in r.failures+r.errors])
    (out/'TEST_RESULTS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=True));sys.exit(not r.wasSuccessful())
