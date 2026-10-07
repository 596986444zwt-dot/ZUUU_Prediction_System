"""Short-lived, bounded, query-only connections. No production repository objects."""
import json
import logging
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from datetime import timedelta
from src.realtime.contracts import utc, now, BJT

from .paths import ROOT
LOG = logging.getLogger('phase11')

@contextmanager
def readonly(path):
    c = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True,
                        timeout=.15, isolation_level=None)
    try:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
        c.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        deadline = time.monotonic()+2
        c.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
        yield c
    finally:
        c.close()

def decode(row):
    if not row:
        return {}
    body = json.loads(row['payload_json'])
    if not isinstance(body, dict):
        raise ValueError('UNEXPECTED_JSON_OBJECT')
    return dict(body, _id=row['record_id'], _created=row['created_at'])

def fmt(value, suffix=''):
    if value is None:
        return '--'
    if isinstance(value, float):
        return f'{value:.2f}{suffix}' if math.isfinite(value) else '--'
    return str(value)+suffix

def bjt(value):
    try:
        return utc(value).astimezone(BJT).strftime('%m-%d %H:%M BJT')
    except (ValueError, TypeError, AttributeError):
        return '-- / 时间语义未知' if value else '--'

def age(value, current=None):
    try:
        return max(0, ((current or now())-utc(value)).total_seconds())
    except (ValueError, TypeError, AttributeError):
        return None

def freshness(value, delayed, stale, current=None):
    seconds = age(value, current)
    if seconds is None:
        return 'UNKNOWN'
    return 'DATA STALE' if seconds>stale else 'STALE' if seconds>delayed else 'FRESH'

def probability(record):
    p = record.get('pmf')
    if p is None:
        return dict(status='暂无正式概率', bars=[], top=[], total=None)
    lo, hi = record.get('support_min'), record.get('support_max')
    valid = (isinstance(p,list) and isinstance(lo,int) and isinstance(hi,int)
             and len(p)==hi-lo+1 and len(p)>0
             and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and 0<=x<=1 for x in p))
    total = sum(p) if valid else None
    if not valid or abs(total-1)>1e-6:
        LOG.warning('probability sanity warning: total=%s', total)
        return dict(status='DATA WARNING', bars=[], top=[], total=total)
    ranked = sorted(enumerate(p,lo), key=lambda x:(-x[1],x[0]))
    # Only crop negligible tails for drawing; original probabilities and sum are retained.
    visible = [i for i,x in enumerate(p,lo) if x>=.001]
    bounds = (min(visible)-1,max(visible)+1) if visible else (lo,hi)
    return dict(status=record.get('status','UNKNOWN'), top=ranked[:3], top5=ranked[:5], total=total,
                bars=[(i,x*100) for i,x in enumerate(p,lo) if bounds[0]<=i<=bounds[1]])

def growth(n, authorization=None):
    """Authorization is an explicit display input, never inferred from N.

    No such production contract exists yet. Only fixtures exercise future gates.
    """
    a = authorization or {}
    calibrated = all(a.get(k) is True for k in ('calibration_pass','forward_validation_pass','acceptance_pass','project_authorized'))
    stage = ('T0 CALIBRATED PROBABILITY' if calibrated else
             'CALIBRATION / VALIDATION CANDIDATE' if a.get('candidate_authorized') is True else
             'EXPERIMENTAL PROBABILITY' if a.get('experimental_probability_authorized') is True else
             'EVALUATION READY' if n>=30 else 'T0 EXPERIMENTAL')
    milestone = next((x for x in (30,60,90,180) if n<x),180)
    threshold = 30 if stage in ('T0 EXPERIMENTAL','EVALUATION READY') else milestone
    remaining = max(0,threshold-n)
    return dict(n=n, stage=stage, target=threshold, percent=min(100,n/threshold*100), remaining=remaining,
                probability='正式校准概率' if calibrated else '实验概率 · 非 CALIBRATED' if a.get('experimental_probability_authorized') else '概率尚未校准',
                earliest=(now().astimezone(BJT).date()+timedelta(days=remaining)).isoformat(),
                validation='正在积累真实前向验证数据' if n<30 else '已达到数据门槛 · 等待独立 Audit / 授权',
                champion='NOT AUTHORIZED / NONE' if not calibrated else '须读取正式 Champion 身份',
                milestone='180+ 有效日：长期稳定性 / 季节性里程碑' if n>=180 else f'下一研究里程碑 {milestone} 有效日（不会自动验收）')

def process_status(pid):
    if not isinstance(pid,int) or pid<=0:
        return 'UNKNOWN'
    if __import__('os').name == 'nt':
        import ctypes
        k = ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        handle = k.OpenProcess(0x1000,False,pid)  # QUERY_LIMITED_INFORMATION only
        if not handle:
            return 'STOPPED' if ctypes.get_last_error()==87 else 'UNKNOWN'
        try:
            code = ctypes.c_ulong()
            return ('RUNNING' if code.value==259 else 'STOPPED') if k.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(code)) else 'UNKNOWN'
        finally:
            k.CloseHandle(ctypes.c_void_p(handle))
    return 'UNKNOWN'

class Adapter:
    def __init__(self, root=ROOT, soak_root=None):
        self.root = Path(root)
        self.soak_root = Path(soak_root) if soak_root else (Path('C:/ZUUU_PHASE10_SOAK') if self.root.resolve()==ROOT.resolve() else self.root/'soak')
        self.cfg = json.loads((self.root/'config/phase10_realtime_v1.json').read_text(encoding='utf-8'))
        self.tcfg = json.loads((self.root/'config/t0_experimental_v1.json').read_text(encoding='utf-8-sig'))
        self.cache = {}

    def read(self):
        result = dict(read_at=now().isoformat(), errors={})
        for name, loader in (('formal',self.formal),('t0',self.t0),('soak',self.soak)):
            try:
                data = loader()
                self.cache[name] = data
            except Exception as exc:
                LOG.exception('adapter read error: %s',name)
                data = self.cache.get(name,{})
                result['errors'][name] = 'READ TEMPORARILY UNAVAILABLE · '+type(exc).__name__
            result[name] = data
        return result

    def formal(self):
        with readonly(self.root/self.cfg['database']) as c:
            version = c.execute('SELECT * FROM schema_version').fetchone()
            if not version or version['namespace'] not in ('PRODUCTION','TEST_FIXTURE') or version['version']!='PHASE10_REALTIME_V1':
                raise ValueError('UNEXPECTED_SCHEMA')
            def rows(table,limit=1,where='',params=()):
                # Table names are internal constants, not UI input.
                if where:
                    where = 'WHERE json_valid(payload_json) AND ('+where.removeprefix('WHERE ')+')'
                result = []
                for r in c.execute(f'SELECT * FROM realtime_{table} {where} ORDER BY rowid DESC LIMIT ?',(*params,limit)).fetchall():
                    try:result.append(decode(r))
                    except (ValueError,TypeError):LOG.warning('malformed optional record: %s/%s',table,r['record_id'])
                return result
            def latest(table,where='',params=()):
                values = rows(table,1,where,params)
                return values[0] if values else {}
            today = now().astimezone(BJT).date()
            predictions = {}
            models = rows('model_registry',100)
            for h,offset in (('T1',1),('T2',2)):
                day = (today+timedelta(days=offset)).isoformat()
                s = latest('prediction_snapshots',"WHERE json_extract(payload_json,'$.horizon')=? AND json_extract(payload_json,'$.target_business_date')=?",(h,day))
                cont = latest('continuous_predictions','WHERE record_id=?',(s.get('continuous_id',''),))
                p = latest('probability_predictions','WHERE record_id=?',(s.get('probability_id',''),))
                model = next((r for r in models if r.get('record_id')==cont.get('model_state_id')), {})
                # A missing forecast does not erase the actually registered formal identity.
                # Never substitute a registry state when a prediction claims an unresolved ID.
                if not cont.get('model_state_id'):
                    model = next((r for r in models if r.get('formal_horizon')==h), {})
                error = latest('errors',"WHERE json_extract(payload_json,'$.horizon')=?",(h,))
                predictions[h] = dict(snapshot=s,continuous=cont,probability=p,pmf=probability(p),model=model,error=error)
            obs = rows('zuuu_normalized',200,"WHERE json_extract(payload_json,'$.qc_status')='VALID'")
            obs.sort(key=lambda r:utc(r['observation_time']))
            observation = obs[-1] if obs else {}
            health = {s:latest('source_health',"WHERE json_extract(payload_json,'$.source')=?",(s,)) for s in ('ZUUU','ECMWF')}
            state = latest('engine_state',"WHERE json_extract(payload_json,'$.kind')='CYCLE_COMPLETE'")
            engine = latest('engine_health')
            runs = rows('ecmwf_raw_runs',20)
            legal = []
            for r in runs:
                if utc(r['run_time'])<=now() and utc(r['actual_ingest_time'])<=now():
                    count = c.execute("SELECT COUNT(*) FROM realtime_ecmwf_hourly WHERE json_extract(payload_json,'$.run_id')=?",(r['_id'],)).fetchone()[0]
                    if count==72:
                        legal.append(r)
            legal.sort(key=lambda r:(utc(r['run_time']),utc(r['actual_ingest_time'])))
            run = legal[-1] if legal else {}
            hours = rows('ecmwf_hourly',72,"WHERE json_extract(payload_json,'$.run_id')=?",(run.get('_id',''),))
            hours.sort(key=lambda r:r['target_time_utc'])
            return dict(predictions=predictions, observation=observation, observations=obs,health=health,
                        engine=engine,scheduler=state,worker_status=process_status(state.get('pid') or engine.get('pid')),
                        ecmwf=run,hours=hours,runs=[{k:r.get(k) for k in ('run_time','actual_ingest_time','_id')} for r in legal],
                        history=rows('prediction_snapshots',120),settlement=rows('daily_settlement',120),evaluation=rows('daily_evaluation',10))

    def t0(self):
        day = now().astimezone(BJT).date().isoformat()
        with readonly(self.root/self.tcfg['database']) as c:
            meta = c.execute('SELECT * FROM t0_metadata').fetchone()
            if not meta or meta['namespace'] not in ('FORWARD_VALIDATION','TEST_FIXTURE'):
                raise ValueError('UNEXPECTED_T0_NAMESPACE')
            snaps = [dict(r) for r in c.execute('SELECT * FROM t0_prediction_snapshot WHERE target_date=? ORDER BY prediction_time,rowid',(day,)).fetchall()]
            for s in snaps:
                s['outputs'] = [dict(r) for r in c.execute('SELECT * FROM t0_prediction_method_output WHERE prediction_id=?',(s['prediction_id'],)).fetchall()]
            truth = {r['target_date']:dict(r) for r in c.execute('SELECT * FROM t0_ground_truth_evidence ORDER BY rowid').fetchall()}
            scores = [dict(r) for r in c.execute('''SELECT e.*,s.target_date,s.cutoff_bjt,s.trigger_type,m.candidate_status FROM t0_settlement e
                JOIN t0_prediction_snapshot s USING(prediction_id)
                JOIN t0_prediction_method_output m ON m.prediction_id=e.prediction_id AND m.method=e.method''').fetchall()
                if truth.get(r['target_date'],{}).get('truth_id')==r['truth_id'] and truth[r['target_date']]['status']=='CONFIRMED_EXPERIMENTAL_TARGET']
            # Count unique dates with BOTH scheduled methods settled to current confirmed truth.
            dates = {}
            for r in scores:
                if r['trigger_type']=='SCHEDULED' and r['candidate_status']=='EXPERIMENTAL_ACTIVE_CANDIDATE':
                    dates.setdefault(r['target_date'],set()).add(r['method'])
            valid_days = sum(v=={'LEVEL0','L1_A'} for v in dates.values())
            stats = []
            for method in ('LEVEL0','L1_A'):
                rs = [r for r in scores if r['method']==method and r['trigger_type']=='SCHEDULED' and r['candidate_status']=='EXPERIMENTAL_ACTIVE_CANDIDATE']
                n = len(rs)
                coverage = c.execute('''SELECT COUNT(*) FROM t0_prediction_method_output m JOIN t0_prediction_snapshot s USING(prediction_id)
                     WHERE method=? AND prediction IS NOT NULL AND trigger_type='SCHEDULED'
                     AND candidate_status='EXPERIMENTAL_ACTIVE_CANDIDATE' ''',(method,)).fetchone()[0]
                avg = lambda key: sum(r[key] for r in rs)/n if n else None
                stats.append(dict(method=method,valid=coverage,settled=n,mae=avg('absolute_error'),
                             rmse=math.sqrt(sum(r['continuous_error']**2 for r in rs)/n) if n else None,
                             bias=avg('continuous_error'),exact=avg('integer_exact_hit'),within=avg('within_1c')))
            event = c.execute("SELECT * FROM t0_worker_event WHERE kind IN ('HEARTBEAT','STOPPED') ORDER BY rowid DESC LIMIT 1").fetchone()
            event = dict(event) if event else {}
            body = json.loads(event.get('payload_json','{}'))
            receipt = c.execute("SELECT * FROM t0_ecmwf_receipt_ledger WHERE availability_status='AVAILABLE' ORDER BY run_time DESC,ready_at DESC LIMIT 1").fetchone()
            obs = c.execute("SELECT * FROM t0_zuuu_receipt_ledger WHERE business_date_bjt=? AND qc_status='VALID' ORDER BY observation_time DESC,ingest_time DESC LIMIT 1",(day,)).fetchone()
            ev = c.execute('SELECT * FROM t0_evaluation_state ORDER BY rowid DESC LIMIT 1').fetchone()
            return dict(snapshot=snaps[-1] if snaps else {},snapshots=snaps,growth=growth(valid_days),stats=stats,
                        event=dict(event,**body),worker_status=process_status(body.get('pid')),
                        ecmwf=dict(receipt) if receipt else {},observation=dict(obs) if obs else {},
                        evaluation=json.loads(ev['payload_json']) if ev else {})

    def soak(self):
        from .soak import SoakReader
        return SoakReader(self.soak_root).read()
