"""Homepage architecture and compact height mode with no hidden overflow."""
import json,sys,unittest
from PySide6.QtWidgets import QLabel,QPushButton
from tests.phase11.test_reference_match import ReferenceMatch
from src.gui.adapters import ROOT

class OneScreen(ReferenceMatch):
    def setUp(self):
        super().setUp();self.w.nav.setCurrentIndex(0);self.app.processEvents()
    def test_58_native_screen_available_geometry(self):
        self.w.resize(1920,1010);self.app.processEvents()
        self.assertEqual(self.w.scroll.verticalScrollBar().maximum(),0)
        self.assertLessEqual(self.w.summary.mapTo(self.w,self.w.summary.rect().bottomRight()).y(),self.w.height())
    def test_84_actual_normal_no_overflow(self):
        self.w.resize(1408,756);self.app.processEvents()
        for bar in (self.w.scroll.verticalScrollBar(),self.w.scroll.horizontalScrollBar()):self.assertEqual(bar.maximum(),0)
        self.assertLessEqual(self.w.dashboard.height(),self.w.scroll.viewport().height())
        for panel in (self.w.core,self.w.trend,self.w.prob,self.w.evolution,self.w.summary):
            self.assertLessEqual(panel.mapTo(self.w,panel.rect().bottomRight()).y(),self.w.height())
    def test_85_height_modes(self):
        self.w.resize(1600,899);self.app.processEvents();self.assertEqual(self.w.layout_mode,'COMPACT_LAYOUT')
        self.w.resize(1600,900);self.app.processEvents();self.assertEqual(self.w.layout_mode,'NORMAL_LAYOUT')
    def test_86_core_size_preserved(self):
        self.w.resize(1408,900);self.app.processEvents();height=self.w.t0.height();font=self.w.t0.value.font().pixelSize();bar_height=self.w.t1.rows[0].bar.height()
        self.w.resize(1408,756);self.app.processEvents();self.assertEqual(self.w.t0.height(),height);self.assertEqual(self.w.t0.value.font().pixelSize(),font);self.assertEqual(self.w.t1.rows[0].bar.height(),bar_height)
    def test_87_details_relocated(self):
        self.assertTrue(self.w.pages.widget(8).isAncestorOf(self.w.models));self.assertTrue(self.w.pages.widget(10).isAncestorOf(self.w.system))
        self.assertFalse(self.w.dashboard.isAncestorOf(self.w.model_table));self.assertFalse(self.w.dashboard.isAncestorOf(self.w.realtime))
    def test_88_summary_only(self):
        content=' '.join(label.text() for label in self.w.summary.findChildren(QLabel))
        for word in ('QNH','合法轮次','PID','心跳','有效结算日'):self.assertNotIn(word,content)
        self.assertIn('ZUUU',content);self.assertIn('ECMWF',content);self.assertIn('连续运行测试',content)
    def test_89_native_default_no_scroll(self):
        child=self.normal_child();self.app.processEvents();self.assertEqual(child.scroll.verticalScrollBar().maximum(),0);self.assertEqual(child.scroll.horizontalScrollBar().maximum(),0)
    def test_90_height_does_not_change_data(self):
        series=self.w.trajectory.series;self.w.resize(1408,756);self.app.processEvents();self.assertEqual(series,self.w.trajectory.series)
    def test_91_compact_chinese_overflow(self):
        self.w.resize(1408,756);self.app.processEvents()
        bad=[label.text() for label in self.w.findChildren(QLabel) if label.isVisible() and not label.wordWrap() and any('\u4e00'<=c<='\u9fff' for c in label.text()) and label.fontMetrics().horizontalAdvance(label.text().split('\n')[0])>label.width()+3]
        self.assertFalse(bad,bad)
    def test_92_details_button(self):
        next(button for button in self.w.summary.findChildren(QPushButton) if button.text()=='运行详情').click();self.assertEqual(self.w.pages.currentIndex(),10)

if __name__=='__main__':
    out=ROOT/'docs/phase11/one_screen'
    with (out/'TEST_LOG.txt').open('w',encoding='utf-8') as f:r=unittest.TextTestRunner(stream=f,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OneScreen))
    result=dict(total=r.testsRun,passed=r.testsRun-len(r.failures)-len(r.errors),failed=len(r.failures)+len(r.errors),failures=[(str(t),s) for t,s in r.failures+r.errors])
    (out/'TEST_RESULTS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=True));sys.exit(not r.wasSuccessful())
