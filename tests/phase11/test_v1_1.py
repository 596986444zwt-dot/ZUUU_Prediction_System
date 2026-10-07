import json
import time
import unittest
from datetime import timedelta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QScrollArea
from src.gui.adapters import ROOT,Adapter,now,utc,growth
from src.gui.soak import SoakReader
from src.gui.app import Window
from tests.phase11.fixtures import create
from tests.phase11.soak_fixture import seed

class SoakContracts(unittest.TestCase):
    def test_live_audited_evidence(self):
        s=SoakReader('C:/ZUUU_PHASE10_SOAK').read()
        self.assertEqual(s['target_hours'],72)
        self.assertEqual(s['completion'],'2026-10-08T06:32:56.335357+00:00')
        self.assertGreater(s['historical_critical'],0)
        self.assertEqual(s['critical'],0)
        self.assertEqual(s['acceptance'],'PENDING_SOAK')
    def test_progress_and_no_automatic_acceptance(self):
        root=create('v1_1_soak_threshold');folder=seed(root)
        reader=SoakReader(folder);s=reader.read();start=utc(s['start'])
        for hours,expected in ((0,0),(36,50),(72,100),(90,100)):
            current=reader.read(start+timedelta(hours=hours))
            self.assertAlmostEqual(current['progress'],expected)
            self.assertEqual(current['acceptance'],'PENDING_SOAK')
            self.assertEqual(current['threshold'],'READY_FOR_FINAL_SOAK_ACCEPTANCE' if hours>=72 else 'PENDING_SOAK')
    def test_active_vs_resolved_severity(self):
        root=create('v1_1_soak_findings');folder=seed(root,findings=['ACTIVE_HIGH'])
        s=SoakReader(folder).read();self.assertEqual(s['critical'],0);self.assertEqual(s['high'],1)
        seed(root,findings=['UNKNOWN_FINDING']);s=SoakReader(folder).read()
        self.assertIsNone(s['critical']);self.assertIsNone(s['high']);self.assertEqual(s['unknown_findings'],['UNKNOWN_FINDING'])
    def test_unavailable_retains_cached_evidence(self):
        root=create('v1_1_soak_cache');seed(root);a=Adapter(root);before=a.read()
        (root/'soak/SOAK_START_MANIFEST.json').replace(root/'soak/TEST_FIXTURE_manifest_offline.json')
        after=a.read();self.assertEqual(before['soak'],after['soak']);self.assertIn('soak',after['errors'])
        app=QApplication.instance() or QApplication([]);w=Window(root,autorefresh=False)
        w.data=after;w.render_soak(after['soak']);self.assertIn('CACHED',w.soak.body.text());w.close()
    def test_csv_fallback_and_partial_append(self):
        root=create('v1_1_soak_csv');folder=seed(root)
        (folder/'evidence/LATEST_OBSERVER_STATUS.json').write_text('bad',encoding='utf-8')
        with (folder/'SOAK_HEARTBEAT.csv').open('ab') as f:f.write(b'partial_in_progress')
        s=SoakReader(folder).read();self.assertIn('SOAK_HEARTBEAT.csv',s['observer_source']);self.assertEqual(s['findings'],[])
    def test_naive_timestamp_rejected(self):
        root=create('v1_1_soak_naive');folder=seed(root);p=folder/'SOAK_START_MANIFEST.json'
        body=json.loads(p.read_text());body['SOAK_START_TIME_UTC']='2026-10-05T14:32:56';p.write_text(json.dumps(body))
        with self.assertRaises(ValueError):SoakReader(folder).read()
    def test_missing_source_is_not_created(self):
        root=create('v1_1_soak_missing');missing=root/'MISSING_SOAK'
        with self.assertRaises(FileNotFoundError):SoakReader(missing).read()
        self.assertFalse(missing.exists())
    def test_route_does_not_accept_models(self):
        for n in (0,12,30,60,90,180,250):
            g=growth(n);self.assertNotIn('CALIBRATED',g['stage']);self.assertEqual(g['champion'],'NOT AUTHORIZED / NONE')
    def test_home_1920_all_regions_visible(self):
        app=QApplication.instance() or QApplication([]);root=create('v1_1_layout');seed(root)
        w=Window(root,autorefresh=False);w.setAttribute(Qt.WA_DontShowOnScreen,True);w.resize(1920,1080);w.show()
        self.addCleanup(w.close)
        deadline=time.monotonic()+12
        while (not w.refresh_count or w.reader) and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        app.processEvents()
        scroll=w.stack.widget(0)
        self.assertEqual(scroll.verticalScrollBar().maximum(),0,'1920 dashboard must fit without page scrolling')
        self.assertEqual(w.stats.verticalScrollBar().maximum(),0,'all seven metrics visible')
        self.assertEqual(w.system_table.verticalScrollBar().maximum(),0,'all five services visible')
        self.assertEqual(w.stats.rowCount(),7)
        w.close()
    def test_soak_files_read_only_by_adapter(self):
        import hashlib
        root=create('v1_1_soak_readonly');folder=seed(root)
        before={p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
        a=Adapter(root)
        for _ in range(5):a.read()
        after={p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
        self.assertEqual(before,after)

if __name__=='__main__':unittest.main()
