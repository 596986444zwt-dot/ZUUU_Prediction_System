"""Normal native window behaviour and reference matching GUI-only regressions."""
import json,sys,unittest
from pathlib import Path
from PySide6.QtCore import Qt,QRect,QSize
from PySide6.QtWidgets import QApplication
from tests.phase11.test_refinement import Refinement
from src.gui.windowing import initial_geometry,show_normal
from src.gui.v2 import Window
from src.gui.adapters import ROOT

class ReferenceMatch(Refinement):
    def test_59_header_compact(self):self.assertEqual(self.w.header.height(),110)
    def test_66_default_geometry_large(self):
        size,p=initial_geometry(QRect(0,0,1920,1040));self.assertEqual(size,QSize(1600,900));self.assertGreater(p.x(),0);self.assertGreater(p.y(),0)
    def test_67_default_geometry_small(self):
        size,p=initial_geometry(QRect(0,0,1600,860));self.assertEqual(size,QSize(1408,756));self.assertEqual(p.x(),96);self.assertGreaterEqual(p.y(),0);self.assertLess(p.y()+size.height()+40,860)
    def normal_child(self):
        child=Window(self.root,False);show_normal(child);self.app.processEvents();self.addCleanup(child.close);return child
    def test_68_not_fullscreen(self):self.assertFalse(self.normal_child().isFullScreen())
    def test_69_not_maximized(self):self.assertFalse(self.normal_child().isMaximized())
    def test_70_native_buttons(self):
        flags=self.normal_child().windowFlags()
        for flag in (Qt.WindowMinimizeButtonHint,Qt.WindowMaximizeButtonHint,Qt.WindowCloseButtonHint):self.assertTrue(flags & flag)
        self.assertFalse(flags & Qt.FramelessWindowHint)
    def test_71_minimize(self):
        child=self.normal_child();child.showMinimized();self.app.processEvents();self.assertTrue(child.isMinimized());child.showNormal()
    def test_72_maximize_restore(self):
        child=self.normal_child();before=child.size();child.showMaximized();self.app.processEvents();self.assertTrue(child.isMaximized());child.showNormal();self.app.processEvents();self.assertFalse(child.isMaximized());self.assertEqual(child.size(),before)
    def test_73_drag_resize(self):
        child=self.normal_child();child.move(80,40);child.resize(1280,760);self.app.processEvents();self.assertEqual(child.width(),1280);self.assertEqual(child.pos().x(),80)
    def test_74_normal_no_horizontal_scroll(self):
        child=self.normal_child();self.assertEqual(child.scroll.horizontalScrollBar().maximum(),0)
    def test_75_launch_code(self):
        app=(ROOT/'src/gui/app.py').read_text(encoding='utf-8');helper=(ROOT/'src/gui/windowing.py').read_text(encoding='utf-8')
        self.assertNotIn('showMaximized(',app+helper);self.assertNotIn('showFullScreen(',app+helper);self.assertIn('show_normal(w)',app)
    def test_76_status_panel(self):self.assertEqual(self.w.header.status_panel.objectName(),'headerStatus')
    def test_77_card_weight(self):
        for card in self.w.cards.values():self.assertGreaterEqual(card.height(),252)
    def test_78_card_identity(self):self.assertEqual([c.objectName() for c in self.w.cards.values()],['t0panel','t1panel','t2panel'])
    def test_79_divider(self):self.assertEqual(self.w.t0.divider.width(),1)
    def test_80_component_sources(self):self.assertIn('ZUUU',self.w.obs_primary.text());self.assertIn('数据源请求',self.w.ecmwf_secondary.text())
    def test_81_component_workers(self):self.assertIn('正常运行',self.w.worker_lines['formal'][0].text());self.assertIn('心跳',self.w.worker_lines['t0'][1].text())
    def test_82_day_night_no_fake_data(self):
        before=list(self.w.trajectory.series);self.w.trajectory.grab();self.assertEqual(before,self.w.trajectory.series)
    def test_83_taskbar_bounds(self):
        child=self.normal_child();available=child.screen().availableGeometry();frame=child.frameGeometry()
        self.assertLessEqual(frame.bottom(),available.bottom()+3);self.assertGreaterEqual(frame.top(),available.top())

if __name__=='__main__':
    out=ROOT/'docs/phase11/reference_match'
    with (out/'TEST_LOG.txt').open('w',encoding='utf-8') as f:r=unittest.TextTestRunner(stream=f,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReferenceMatch))
    result=dict(total=r.testsRun,passed=r.testsRun-len(r.failures)-len(r.errors),failed=len(r.failures)+len(r.errors),failures=[(str(t),s) for t,s in r.failures+r.errors])
    (out/'TEST_RESULTS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=True));sys.exit(not r.wasSuccessful())
