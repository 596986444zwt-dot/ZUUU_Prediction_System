"""Separate, typed append-only database; no references to formal archives."""
import json
import sqlite3
from pathlib import Path
from .contracts import ROOT, VERSION, canonical, iso, now, require

TABLES = ('t0_metadata', 't0_transport_receipt', 't0_receipt_publication', 't0_zuuu_receipt_ledger',
          't0_zuuu_sighting', 't0_ecmwf_receipt_ledger', 't0_ecmwf_sighting',
          't0_prediction_snapshot', 't0_snapshot_observation',
          't0_prediction_method_output', 't0_ground_truth_evidence',
          't0_settlement', 't0_evaluation_state', 't0_worker_event')


class Archive:
    def __init__(self, path, namespace='FORWARD_VALIDATION', create=False):
        self.path = Path(path).resolve()
        require(namespace in ('FORWARD_VALIDATION', 'TEST_FIXTURE'), 'INVALID_NAMESPACE')
        if namespace == 'FORWARD_VALIDATION':
            require(self.path == (ROOT / 'database/t0_forward_v1/t0_forward_v1.db').resolve(), 'ISOLATION_PATH_REJECTED')
        else:
            require(not self.path.is_relative_to((ROOT / 'database').resolve()), 'FIXTURE_IN_FORMAL_DATABASE_DIRECTORY')
        if not self.path.exists() and not create:
            raise FileNotFoundError(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(self.path, timeout=2)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.execute('PRAGMA recursive_triggers=ON')
        self.c.execute('PRAGMA synchronous=FULL')
        self.c.execute('PRAGMA journal_mode=WAL')
        tables = self.c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not tables:
            require(create, 'SCHEMA_MISSING')
            script = (Path(__file__).parent / 'schema.sql').read_text(encoding='utf-8')
            self.c.executescript('BEGIN IMMEDIATE;\n' + script)
            try:
                self.c.execute('INSERT INTO t0_metadata VALUES (?,?,?)', (VERSION, namespace, iso(now())))
                for table in TABLES:
                    for action in ('UPDATE', 'DELETE'):
                        self.c.execute(f"CREATE TRIGGER guard_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
                    cols = [r['name'] for r in self.c.execute(f'PRAGMA table_info({table})') if r['pk']]
                    condition = ' AND '.join(f'{k}=NEW.{k}' for k in cols)
                    self.c.execute(f"CREATE TRIGGER guard_{table}_INSERT BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {condition}) BEGIN SELECT RAISE(ABORT,'IMMUTABLE_IDENTITY_CONFLICT'); END")
                self.c.commit()
            except BaseException:
                self.c.rollback()
                raise
        row = self.c.execute('SELECT * FROM t0_metadata').fetchone()
        require(row and row['version'] == VERSION and row['namespace'] == namespace, 'SCHEMA_NAMESPACE_MISMATCH')
        # Additive readiness evidence for the initial live capture; no ledger/snapshot mutation.
        if not self.c.execute("SELECT 1 FROM sqlite_master WHERE name='t0_receipt_publication'").fetchone():
            prior_tables = [t for t in TABLES if t!='t0_receipt_publication']
            require({r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")} ==
                    {f'guard_{t}_{a}' for t in prior_tables for a in ('UPDATE','DELETE','INSERT')}, 'LEGACY_GUARDS_MISMATCH')
            with self.c:
                self.c.execute('CREATE TABLE t0_receipt_publication (receipt_id TEXT PRIMARY KEY REFERENCES t0_transport_receipt(receipt_id), published_at TEXT NOT NULL, publication_basis TEXT NOT NULL)')
                for action in ('UPDATE','DELETE'):
                    self.c.execute(f"CREATE TRIGGER guard_t0_receipt_publication_{action} BEFORE {action} ON t0_receipt_publication BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
                self.c.execute("CREATE TRIGGER guard_t0_receipt_publication_INSERT BEFORE INSERT ON t0_receipt_publication WHEN EXISTS(SELECT 1 FROM t0_receipt_publication WHERE receipt_id=NEW.receipt_id) BEGIN SELECT RAISE(ABORT,'IMMUTABLE_IDENTITY_CONFLICT'); END")
        require({r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")} ==
                {f'guard_{t}_{a}' for t in TABLES for a in ('UPDATE', 'DELETE', 'INSERT')}, 'APPEND_ONLY_GUARDS_MISSING')
        self.namespace = namespace

    def insert(self, table, record):
        require(table in TABLES, 'UNKNOWN_TABLE')
        keys = list(record)
        self.c.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", tuple(record[k] for k in keys))

    def get(self, table, key, value):
        require(table in TABLES, 'UNKNOWN_TABLE')
        require(key in {r['name'] for r in self.c.execute(f'PRAGMA table_info({table})')}, 'UNKNOWN_COLUMN')
        row = self.c.execute(f'SELECT * FROM {table} WHERE {key}=?', (value,)).fetchone()
        return dict(row) if row else None

    def event(self, kind, body, event_id=None):
        from .contracts import identity
        body = dict(body, experiment_status='T0_EXPERIMENTAL', validation='FORWARD_VALIDATION')
        stamp = iso(now())
        rid = event_id or identity({'kind': kind, 'body': body, 'created': stamp})
        with self.c:
            if not self.get('t0_worker_event', 'event_id', rid):
                self.insert('t0_worker_event', dict(event_id=rid, kind=kind, created_at=stamp, payload_json=canonical(body)))

    def integrity(self):
        return dict(integrity=[r[0] for r in self.c.execute('PRAGMA integrity_check')],
                    foreign_keys=[tuple(r) for r in self.c.execute('PRAGMA foreign_key_check')])

    def close(self):
        self.c.close()
