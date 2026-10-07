import json
import sqlite3
import time
import unittest
from datetime import timedelta
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QLabel,QScrollArea
from src.gui.adapters import Adapter,ROOT,readonly,growth,probability,freshness,now,utc,bjt,process_status
from src.gui.app import Window
from src.gui.theme import STYLE
from tests.phase11.fixtures import create,change,payload

class GUIContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(STYLE)
        cls.root=create()
        cls.data=Adapter(cls.root).read()

    def window(self):
        w=Window(self.root,autorefresh=False);w.setAttribute(Qt.WA_DontShowOnScreen,True);w.show()
        deadline=time.monotonic()+10
        while w.refresh_count==0 and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertGreater(w.refresh_count,0);return w

    def test_A_start(self):
        w=self.window();self.assertEqual(w.nav.count(),12);w.close()
    def test_B_close_backend_independent(self):
        a=Adapter();before=a.read();w=self.window();w.close();after=a.read()
        for key in ('formal','t0'):
            self.assertEqual(before[key]['worker_status'],'RUNNING');self.assertEqual(after[key]['worker_status'],'RUNNING')
        self.assertEqual(before['formal']['scheduler']['pid'],after['formal']['scheduler']['pid'])
        self.assertEqual(before['t0']['event']['pid'],after['t0']['event']['pid'])
    def test_C_readonly(self):
        path=self.root/'database/phase10_realtime_v1.db'
        with readonly(path) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM schema_version').fetchone()[0],1)
            for sql in ('INSERT INTO schema_version VALUES ("bad","bad")','UPDATE schema_version SET version="bad"','DELETE FROM schema_version','CREATE TABLE bad(x)','PRAGMA query_only=OFF','ATTACH DATABASE ":memory:" AS bad'):
                with self.assertRaises(sqlite3.DatabaseError):c.execute(sql)
        self.assertFalse((self.root/'database/missing.db').exists())
        with self.assertRaises(sqlite3.OperationalError):
            with readonly(self.root/'database/missing.db'):pass
    def test_D_zero(self):
        t=self.data['t0'];self.assertEqual(t['growth']['n'],0)
        self.assertTrue(all(r['mae'] is None for r in t['stats']));self.assertEqual(growth(0)['remaining'],30)
    def test_E_under30(self):
        root=create('under30',3);t=Adapter(root).t0();self.assertEqual(t['growth']['n'],3);self.assertEqual(t['growth']['remaining'],27)
    def test_F_30_audit_pending(self):
        root=create('thirty',30);g=Adapter(root).t0()['growth'];self.assertEqual(g['stage'],'EVALUATION READY');self.assertEqual(g['probability'],'概率尚未校准')
    def test_G_experimental_probability(self):
        g=growth(30,{'experimental_probability_authorized':True});self.assertEqual(g['stage'],'EXPERIMENTAL PROBABILITY');self.assertIn('非 CALIBRATED',g['probability'])
    def test_H_calibrated_all_gates(self):
        flags=dict(calibration_pass=True,forward_validation_pass=True,acceptance_pass=True,project_authorized=True)
        self.assertEqual(growth(90,flags)['stage'],'T0 CALIBRATED PROBABILITY')
        for key in flags:
            incomplete=dict(flags);incomplete[key]=False;self.assertNotEqual(growth(180,incomplete)['stage'],'T0 CALIBRATED PROBABILITY')
        self.assertEqual(growth(180)['stage'],'EVALUATION READY')
    def test_I_T1_probability(self):
        r=self.data['formal']['predictions']['T1'];self.assertEqual(r['model']['model_family'],'RIDGE');self.assertAlmostEqual(r['pmf']['total'],1);self.assertEqual(r['pmf']['top'][0][0],23)
    def test_J_T2_probability(self):
        r=self.data['formal']['predictions']['T2'];self.assertEqual(r['model']['model_family'],'LIGHTGBM');self.assertEqual(r['pmf']['status'],'CALIBRATED')
    def test_K_no_forecast(self):
        root=create('no_forecast');change(root,'phase10_realtime_v1.db','DELETE FROM realtime_prediction_snapshots');r=Adapter(root).formal()['predictions']['T1'];self.assertEqual(r['snapshot'],{});self.assertEqual(r['pmf']['status'],'暂无正式概率')
    def test_L_fallback(self):
        root=create('fallback');payload(root,'probability_predictions','T1/P',{'status':'FALLBACK','fallback_reason':'TEST_FIXTURE'});self.assertEqual(Adapter(root).formal()['predictions']['T1']['pmf']['status'],'FALLBACK')
    def test_M_stale_ZUUU(self):
        self.assertEqual(freshness((now()-timedelta(hours=3)).isoformat(),7200,14400),'STALE');self.assertEqual(freshness((now()-timedelta(hours=5)).isoformat(),7200,14400),'DATA STALE')
    def test_N_stale_ECMWF(self):
        self.assertEqual(freshness((now()-timedelta(hours=25)).isoformat(),43200,86400),'DATA STALE')
    def test_O_unavailable_retains_cache(self):
        a=Adapter(self.root);before=a.read();original=a.formal;a.formal=lambda:(_ for _ in ()).throw(sqlite3.OperationalError('locked'));after=a.read();self.assertEqual(before['formal'],after['formal']);self.assertIn('READ TEMPORARILY UNAVAILABLE',after['errors']['formal']);self.assertTrue(after['t0']);a.formal=original
    def test_P_malformed_optional(self):
        root=create('malformed');change(root,'phase10_realtime_v1.db',"UPDATE realtime_daily_evaluation SET payload_json='bad'")
        c=sqlite3.connect(root/'database/phase10_realtime_v1.db');c.execute('INSERT INTO realtime_daily_evaluation VALUES (?,?,?)',('malformed',now().isoformat(),'[1,2]'));c.commit();c.close()
        result=Adapter(root).read();self.assertTrue(result['formal']['predictions']['T1']['snapshot']);self.assertEqual(result['formal']['evaluation'],[])
    def test_Q_worker_stopped(self):
        self.assertEqual(process_status(99999999),'STOPPED');self.assertEqual(process_status(None),'UNKNOWN')
        w=self.window();w.data['formal']['worker_status']='STOPPED';w.update_clock();self.assertIn('BACKEND NOT RUNNING',w.clock.text());w.close()
    def test_R_layout_1920(self):
        w=self.window();w.resize(1920,1080);self.app.processEvents();self.assertEqual(w.size().width(),1920)
        w.grab().save(str(ROOT/'docs/phase11/v1_1/screenshots/TEST_FIXTURE_normal.png'));w.close()
    def test_S_layout_125_logical(self):
        w=self.window();w.resize(1536,864);self.app.processEvents();self.assertGreater(w.stack.width(),900);w.close()
    def test_T_layout_150_logical(self):
        w=self.window();w.resize(1280,720);self.app.processEvents();self.assertGreaterEqual(w.stack.width(),850)
        for card in (w.t0,w.t1,w.t2,w.growth,w.obs,w.ecmwf,w.soak,w.system):
            self.assertGreaterEqual(card.height(),card.minimumSizeHint().height())
            self.assertGreaterEqual(card.body.height(),card.body.heightForWidth(card.body.width()))
        w.close()
    def test_U_continuous_refresh(self):
        w=self.window();baseline=len(w.findChildren(QLabel))
        for i in range(12):
            while w.reader:self.app.processEvents();time.sleep(.005)
            target=w.refresh_count+1;w.refresh();deadline=time.monotonic()+5
            while (w.refresh_count<target or w.reader) and time.monotonic()<deadline:self.app.processEvents();time.sleep(.005)
            self.assertGreaterEqual(w.refresh_count,target)
        self.assertEqual(len(w.findChildren(QLabel)),baseline);self.assertEqual(w.stats.rowCount(),7);w.close()
    def test_V_probability_sanity(self):
        for pmf in ([.2,.2],[float('nan'),.5],[-.1,1.1]):self.assertEqual(probability(dict(pmf=pmf,support_min=0,support_max=1))['status'],'DATA WARNING')
    def test_W_timezone(self):
        self.assertIn('08:00 BJT',bjt('2026-10-05T00:00:00+00:00'))
        self.assertIn('时间语义未知',bjt('2026-10-05T00:00:00'))
        with self.assertRaises(ValueError):utc('2026-10-05T00:00:00')
    def test_X_no_production_writers(self):
        import ast
        forbidden=['src.realtime.archive','src.realtime.engine','src.realtime.t0.archive','src.realtime.t0.worker','src.realtime.settlement','src.realtime.handoff']
        for p in (ROOT/'src/gui').glob('*.py'):
            tree=ast.parse(p.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):self.assertNotIn(node.module,forbidden)
        source=(ROOT/'src/gui/adapters.py').read_text(encoding='utf-8');self.assertIn('?mode=ro',source);self.assertNotIn('immutable=1',source)
    def test_Y_truth_revision_invalid_day(self):
        root=create('invalid_day',1);change(root,'t0_forward_v1/t0_forward_v1.db',"UPDATE t0_ground_truth_evidence SET status='PENDING_AFTER_CONFIRMED_DATA'");self.assertEqual(Adapter(root).t0()['growth']['n'],0)
    def test_Z_event_not_growth(self):
        root=create('event_only',1);change(root,'t0_forward_v1/t0_forward_v1.db',"UPDATE t0_prediction_snapshot SET trigger_type='EVENT'");self.assertEqual(Adapter(root).t0()['growth']['n'],0)
    def test_WAL_writer_not_blocked(self):
        root=create('wal');path=root/'database/phase10_realtime_v1.db';writer=sqlite3.connect(path,timeout=.2)
        writer.execute('PRAGMA journal_mode=WAL');writer.execute('BEGIN IMMEDIATE')
        writer.execute('INSERT INTO realtime_manifest VALUES (?,?,?)',('wal',now().isoformat(),'{}'))
        with readonly(path) as reader:
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM realtime_manifest').fetchone()[0],0)
            writer.commit()
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM realtime_manifest').fetchone()[0],1)
        writer.close()

if __name__=='__main__':unittest.main()
