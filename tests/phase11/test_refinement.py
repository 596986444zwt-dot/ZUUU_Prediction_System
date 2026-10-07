"""Final visual refinements plus all V2 semantic and isolation regressions."""
import json,sys,unittest
from PySide6.QtWidgets import QLabel
from tests.phase11.test_v2 import Acceptance,visible_strings
from src.gui.adapters import ROOT

class Refinement(Acceptance):
    def test_58_native_screen_available_geometry(self):
        self.w.resize(1920,1010);self.app.processEvents()
        self.assertEqual(self.w.scroll.verticalScrollBar().maximum(),0)
        self.assertEqual(self.w.scroll.horizontalScrollBar().maximum(),0)
        self.assertLessEqual(self.w.system.mapTo(self.w,self.w.system.rect().bottomRight()).y(),self.w.height())
    def test_59_header_compact(self):self.assertLessEqual(self.w.header.height(),90)
    def test_60_icons(self):
        for card in self.w.cards.values():self.assertEqual(card.icon.width(),65);self.assertTrue(card.icon.toolTip())
    def test_61_weather_field_mapping(self):
        icon=self.w.t1.icon
        hours=self.d['formal']['hours'];day=self.w.last_dates['T1']
        for r in hours:r.update(cloud_cover_pct=90,precipitation_mm=1)
        icon.set_evidence(hours,day);self.assertFalse(icon.decorative_only);self.assertEqual(icon.kind,'rain')
        icon.set_evidence([],day);self.assertTrue(icon.decorative_only);self.assertIn('装饰',icon.toolTip())
    def test_62_top3_exact_pmf(self):
        for h in ('T1','T2'):
            for row,item in zip(self.w.cards[h].rows,self.d['formal']['predictions'][h]['pmf']['top']):
                self.assertEqual(row.probability,item[1]);self.assertEqual(row.bar.value(),round(item[1]*10000))
    def test_63_no_ordinal_labels(self):
        for s in visible_strings(self.w):
            for forbidden in ('第一可能','第二可能','第三可能'):self.assertNotIn(forbidden,s)
    def test_64_status_independence(self):
        for h in ('T1','T2'):
            self.d['formal']['predictions'][h]['pmf']['status']='UNCALIBRATED'
            self.d['formal']['predictions'][h]['snapshot']['status']='NORMAL'
        self.render();self.assertIn('概率尚未校准',self.w.t1.footer.text());self.assertNotIn('降级运行',self.w.t1.footer.text())
        self.d['formal']['predictions']['T1']['snapshot']['status']='DEGRADED';self.render();self.assertIn('降级运行',self.w.t1.footer.text())
    def test_65_900_no_horizontal(self):
        self.w.resize(1600,830);self.app.processEvents();self.assertEqual(self.w.scroll.horizontalScrollBar().maximum(),0)

if __name__=='__main__':
    out=ROOT/'docs/phase11/refinement'
    with (out/'TEST_LOG.txt').open('w',encoding='utf-8') as f:r=unittest.TextTestRunner(stream=f,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Refinement))
    result=dict(total=r.testsRun,passed=r.testsRun-len(r.failures)-len(r.errors),failed=len(r.failures)+len(r.errors),failures=[(str(t),s) for t,s in r.failures+r.errors])
    (out/'TEST_RESULTS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=True));sys.exit(not r.wasSuccessful())
