"""Independent process: network threads spool even when T0 SQLite is unavailable."""
import json
import logging
import os
import signal
import threading
import time
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from src.ecmwf_contract import HOURLY_VARIABLES, validate_payload
from .contracts import ROOT, BJT, now, utc, iso, require, code_version
from .archive import Archive
from .source_adapter import Fetcher
from .receipt_ledger import persist, recover
from .snapshot import save
from .scheduler import due
from .settlement import settle
from .evaluation import evaluate


def paths(config, fixture_root=None):
    if config['namespace'] == 'TEST_FIXTURE':
        require(fixture_root is not None, 'FIXTURE_ROOT_REQUIRED')
        base = Path(fixture_root).resolve()
        require(not base.is_relative_to((ROOT/'database').resolve()), 'FIXTURE_PATH_REJECTED')
        return dict(database=base/'fixture.db', runtime=base/'runtime', spool=base/'spool', exports=base/'exports')
    require(config['namespace']=='FORWARD_VALIDATION', 'NAMESPACE_REJECTED')
    allowed = dict(database='database/t0_forward_v1/t0_forward_v1.db', runtime='logs/t0_forward_v1',
                   spool='raw/t0_forward_v1', exports='data/t0_forward_v1')
    result = {}
    for key, name in allowed.items():
        require(config[key] == name, 'ISOLATION_CONFIG_PATH_REJECTED_'+key)
        result[key] = (ROOT/name).resolve()
        require(result[key].is_relative_to(ROOT.resolve()), 'ISOLATION_PATH_ESCAPE')
    require(config['zuuu_endpoint']=='https://aviationweather.gov/api/data/metar' and
            config['ecmwf_endpoint']=='https://single-runs-api.open-meteo.com/v1/forecast', 'SOURCE_IDENTITY_REJECTED')
    return result


class InstanceLock:
    """Own advisory lock; never touches Phase10 lock or PID."""
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        if self.path.stat().st_size == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError('T0_WORKER_ALREADY_RUNNING')
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
            self.handle = None


class Worker:
    def __init__(self, config, fixture_root=None, fetcher=None):
        self.config = dict(config)
        self.paths = paths(config, fixture_root)
        self.db = None
        self.stop = threading.Event()
        self.fetcher = fetcher or Fetcher(config)
        self.threads = []
        self.last_evaluation = None
        self.last_clock = None
        self.code_version = code_version()
        folder = self.paths['runtime']
        folder.mkdir(parents=True, exist_ok=True)
        self.log = logging.getLogger('t0_forward.'+str(folder))
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        if not self.log.handlers:
            handler = RotatingFileHandler(folder/'worker.log', maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            self.log.addHandler(handler)

    def open_database(self):
        if self.db is None:
            self.db = Archive(self.paths['database'], self.config['namespace'], create=True)
        return self.db

    def capture(self, source, known_runs=None):
        if source == 'ZUUU':
            packet = self.fetcher.get('ZUUU', self.config['zuuu_endpoint'],
                                      {'ids':'ZUUU', 'format':'json', 'hours':self.config['zuuu_catchup_hours']})
            rid = persist(self.paths['spool'], packet, self.config['namespace'])
            self.log.info('CAPTURE ZUUU %s complete=%s', rid, packet['body_complete'])
            return [rid]
        require(source=='ECMWF', 'UNSUPPORTED_SOURCE')
        known_runs = known_runs if known_runs is not None else set()
        current = now()
        latest = current.replace(hour=(current.hour//6)*6, minute=0, second=0, microsecond=0)
        receipts = []
        for index in reversed(range(self.config['ecmwf_catchup_runs'])):
            if self.stop.is_set():
                break
            run = latest-timedelta(hours=6*index)
            if iso(run) in known_runs and index>1:
                continue
            params = dict(latitude=30.576, longitude=103.950, hourly=','.join(HOURLY_VARIABLES),
                          models='ecmwf_ifs025', run=run.strftime('%Y-%m-%dT%H:%M'), timezone='UTC', forecast_hours=72)
            packet = self.fetcher.get('ECMWF', self.config['ecmwf_endpoint'], params)
            rid = persist(self.paths['spool'], packet, self.config['namespace'])
            receipts.append(rid)
            self.log.info('CAPTURE ECMWF %s run=%s complete=%s', rid, iso(run), packet['body_complete'])
            if packet['body_complete']:
                try:
                    validate_payload(json.loads(bytes.fromhex(packet['content_hex'])), run)
                    known_runs.add(iso(run))
                except (ValueError, TypeError, KeyError):
                    pass
        return receipts

    def collector(self, source):
        known = set()
        interval = self.config['zuuu_poll_seconds'] if source=='ZUUU' else self.config['ecmwf_poll_seconds']
        while not self.stop.is_set():
            try:
                self.capture(source, known)
            except Exception:
                self.log.exception('T0_COLLECTOR_FAILED source=%s; other worker domains continue', source)
            self.stop.wait(interval)

    def cycle(self, issue=None, startup=False):
        issue = utc(issue or now())
        require(code_version()==self.code_version, 'EXECUTABLE_FILES_CHANGED_RESTART_REQUIRED')
        if self.last_clock is not None and issue < self.last_clock:
            raise ValueError('LOCAL_CLOCK_REVERSED_PREDICTIONS_SUSPENDED')
        self.last_clock = issue
        db = self.open_database()
        # Mark due exact-cutoff snapshots BEFORE ingesting newly recovered packets.
        due(db, issue, self.config)
        added = recover(db, self.paths['spool'])
        current = now() if db.namespace=='FORWARD_VALIDATION' else issue
        if startup or (self.config['event_snapshots'] and any(added.values())):
            if 8 <= current.astimezone(BJT).hour <= 18:
                save(db, current, 'STARTUP' if startup else 'EVENT', self.config)
        if self.last_evaluation is None or (current-self.last_evaluation).total_seconds() >= 60:
            settle(db, current, self.config)
            evaluate(db, self.paths['exports'])
            self.last_evaluation = current
        db.event('HEARTBEAT', {'pid':os.getpid(), 'time':iso(current), 'new_records':added})
        return added

    def run(self, seconds=None):
        folder = self.paths['runtime']
        stopfile = folder/'stop.request'
        with InstanceLock(folder/'worker.lock'):
            if stopfile.exists():
                stopfile.unlink()  # Mutable control marker, not evidence or prediction.
            (folder/'worker.pid').write_text(str(os.getpid()), encoding='ascii')
            for source in ('ZUUU', 'ECMWF'):
                thread = threading.Thread(target=self.collector, args=(source,), name='t0_'+source, daemon=True)
                thread.start()
                self.threads.append(thread)
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGINT, lambda *_: self.stop.set())
                signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
            end = time.monotonic()+seconds if seconds is not None else None
            first = True
            try:
                while not self.stop.is_set() and not stopfile.exists() and (end is None or time.monotonic()<end):
                    try:
                        self.cycle(startup=first)
                        first = False
                    except Exception:
                        self.log.exception('T0_CYCLE_FAILED; transport spool continues; no formal engine calls')
                        if self.db is not None:
                            self.db.close()
                            self.db = None
                    self.stop.wait(min(self.config['cycle_seconds'], max(0, end-time.monotonic())) if end is not None else self.config['cycle_seconds'])
            finally:
                self.stop.set()
                for thread in self.threads:
                    thread.join(timeout=35)
                if self.db is not None:
                    try:
                        recover(self.db, self.paths['spool'])
                        self.db.event('STOPPED', {'pid':os.getpid(), 'time':iso(now())})
                    except Exception:
                        self.log.exception('T0_STOP_DB_UNAVAILABLE; immutable spool retained')
                    finally:
                        self.db.close()
                        self.db = None
                self.log.info('T0_STOPPED')
