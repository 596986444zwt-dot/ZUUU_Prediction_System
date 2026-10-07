"""Append-only domain archive. Mutable state is represented by new versions."""
import sqlite3
import hashlib
from pathlib import Path
from .contracts import canonical, identity, iso, now, VERSION, require

TABLES = ('zuuu_raw', 'zuuu_normalized', 'ecmwf_raw_runs', 'ecmwf_hourly',
          'source_health', 'engine_health', 'events', 'feature_snapshots',
          'continuous_predictions', 'probability_predictions', 'prediction_snapshots',
          'daily_ground_truth', 'daily_settlement', 'daily_evaluation',
          'probability_state', 'errors', 'engine_state', 'model_registry', 'manifest')

class Archive:
    def __init__(self, path, namespace='PRODUCTION', create=False):
        self.path = Path(path)
        require(namespace in ('PRODUCTION', 'SIMULATION'), 'INVALID_NAMESPACE')
        if not self.path.exists() and not create: raise FileNotFoundError(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(self.path, timeout=5)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.execute('PRAGMA recursive_triggers=ON')
        self.c.execute('PRAGMA busy_timeout=5000')
        self.c.execute('PRAGMA journal_mode=WAL')
        self.c.execute('PRAGMA synchronous=FULL')
        exists = self.c.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone()
        if not exists:
            require(create, 'SCHEMA_MISSING')
            with self.c:
                self.c.execute('CREATE TABLE schema_version (version TEXT PRIMARY KEY, namespace TEXT NOT NULL)')
                self.c.execute('INSERT INTO schema_version VALUES (?,?)', (VERSION, namespace))
                for t in TABLES:
                    self.c.execute(f'CREATE TABLE realtime_{t} (record_id TEXT PRIMARY KEY NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL)')
                    for action in ('UPDATE', 'DELETE'):
                        self.c.execute(f"CREATE TRIGGER immutable_{t}_{action} BEFORE {action} ON realtime_{t} BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
                self.c.execute('CREATE TABLE snapshot_components (snapshot_id TEXT PRIMARY KEY REFERENCES realtime_prediction_snapshots(record_id) DEFERRABLE INITIALLY DEFERRED, feature_id TEXT NOT NULL REFERENCES realtime_feature_snapshots(record_id), continuous_id TEXT NOT NULL REFERENCES realtime_continuous_predictions(record_id), probability_id TEXT NOT NULL REFERENCES realtime_probability_predictions(record_id))')
                for action in ('UPDATE', 'DELETE'):
                    self.c.execute(f"CREATE TRIGGER immutable_components_{action} BEFORE {action} ON snapshot_components BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END")
        row = self.c.execute('SELECT * FROM schema_version').fetchone()
        require(row['version'] == VERSION and row['namespace'] == namespace, 'NAMESPACE_OR_VERSION_MISMATCH')
        self.namespace = namespace
        # R4: bounded to immutable prediction/archive/state history.
        with self.c:
            for t in ('feature_snapshots', 'continuous_predictions', 'probability_predictions',
                      'prediction_snapshots', 'probability_state'):
                self.c.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{t}_INSERT
                    BEFORE INSERT ON realtime_{t}
                    WHEN EXISTS (SELECT 1 FROM realtime_{t} WHERE record_id=NEW.record_id)
                    BEGIN
                      SELECT CASE WHEN EXISTS (SELECT 1 FROM realtime_{t}
                        WHERE record_id=NEW.record_id AND created_at=NEW.created_at
                        AND payload_json=NEW.payload_json) THEN RAISE(IGNORE)
                        ELSE RAISE(ABORT,'IMMUTABLE_IDENTITY_CONFLICT') END;
                    END""")
            self.c.execute("""CREATE TRIGGER IF NOT EXISTS immutable_components_INSERT
                BEFORE INSERT ON snapshot_components
                WHEN EXISTS (SELECT 1 FROM snapshot_components WHERE snapshot_id=NEW.snapshot_id)
                BEGIN
                  SELECT CASE WHEN EXISTS (SELECT 1 FROM snapshot_components
                    WHERE snapshot_id=NEW.snapshot_id AND feature_id=NEW.feature_id
                    AND continuous_id=NEW.continuous_id AND probability_id=NEW.probability_id)
                    THEN RAISE(IGNORE) ELSE RAISE(ABORT,'IMMUTABLE_IDENTITY_CONFLICT') END;
                END""")

    def insert(self, table, record, rid=None, created=None):
        require(table in TABLES, 'UNKNOWN_TABLE')
        r = dict(record, namespace=self.namespace)
        rid = rid or identity(r)
        existing = self.c.execute(f'SELECT payload_json FROM realtime_{table} WHERE record_id=?', (rid,)).fetchone()
        payload = canonical(r)
        if existing:
            require(existing[0] == payload, 'IMMUTABLE_IDENTITY_CONFLICT')
            return False
        self.c.execute(f'INSERT INTO realtime_{table} VALUES (?,?,?)', (rid, iso(created or now()), payload))
        return True

    def rows(self, table):
        import json
        require(table in TABLES, 'UNKNOWN_TABLE')
        return [dict(json.loads(r['payload_json']), _id=r['record_id'], _created=r['created_at']) for r in self.c.execute(f'SELECT * FROM realtime_{table} ORDER BY created_at,record_id')]

    def latest(self, table):
        rows = self.rows(table)
        if table == 'probability_state':
            rows = [r for r in rows if r.get('record_type') != 'LINEAGE_ALIAS']
        return rows[-1] if rows else None

    def resolve_probability_state(self, version):
        """Resolve immutable legacy-reference aliases without advancing forecast state."""
        import json
        seen = set()
        while version not in seen:
            seen.add(version)
            row = self.c.execute('SELECT payload_json FROM realtime_probability_state WHERE record_id=?', (version,)).fetchone()
            require(row is not None, 'UNRESOLVED_PROBABILITY_STATE')
            state = json.loads(row[0])
            require(state.get('state_id') == version, 'PROBABILITY_STATE_ID_MISMATCH')
            if state.get('record_type') != 'LINEAGE_ALIAS':
                return state
            version = state['canonical_state_id']
        raise ValueError('PROBABILITY_STATE_ALIAS_CYCLE')

    def snapshot(self, rid, feature, continuous, probability, metadata, fail_after=None):
        import json
        from .snapshot_guard import validate_snapshot
        bodies = [('feature_snapshots', rid+'/X', feature),
                  ('continuous_predictions', rid+'/ML', continuous),
                  ('probability_predictions', rid+'/P', probability),
                  ('prediction_snapshots', rid, dict(metadata, feature_id=rid+'/X',
                   continuous_id=rid+'/ML', probability_id=rid+'/P'))]
        existing = self.c.execute('SELECT 1 FROM realtime_prediction_snapshots WHERE record_id=?', (rid,)).fetchone()
        if existing:
            for table, key, body in bodies:
                row = self.c.execute(f'SELECT payload_json FROM realtime_{table} WHERE record_id=?', (key,)).fetchone()
                require(row and canonical(json.loads(row[0])) == canonical(dict(body, namespace=self.namespace)),
                        'REJECT_CONFLICTING_DUPLICATE')
            links = self.c.execute('SELECT * FROM snapshot_components WHERE snapshot_id=?', (rid,)).fetchone()
            require(links and tuple(links) == (rid, rid+'/X', rid+'/ML', rid+'/P'), 'REJECT_CONFLICTING_DUPLICATE')
            return False  # IDEMPOTENT_DUPLICATE: complete component bodies compared.
        if self.namespace == 'PRODUCTION' or 'probability_state_version' in metadata:
            version = metadata.get('probability_state_version')
            require(version and version == probability.get('probability_state_version'), 'PROBABILITY_STATE_REFERENCE_MISMATCH')
            self.resolve_probability_state(version)
        # Unbound SIMULATION component fixtures are storage tests, never formal forecasts.
        # Every PRODUCTION snapshot and state-bound simulation uses the same guard.
        if self.namespace == 'PRODUCTION' or 'probability_state_version' in metadata:
            validate_snapshot(self, feature, continuous, probability, metadata)
        with self.c:
            for t, suffix, value in [('feature_snapshots', '/X', feature), ('continuous_predictions', '/ML', continuous), ('probability_predictions', '/P', probability)]:
                self.insert(t, value, rid + suffix)
                if fail_after == t: raise RuntimeError('INJECTED_TRANSACTION_FAILURE')
            self.insert('prediction_snapshots', dict(metadata, feature_id=rid+'/X', continuous_id=rid+'/ML', probability_id=rid+'/P'), rid)
            self.c.execute('INSERT INTO snapshot_components VALUES (?,?,?,?)', (rid, rid+'/X', rid+'/ML', rid+'/P'))
        return True

    def integrity(self):
        return [r[0] for r in self.c.execute('PRAGMA integrity_check')], list(self.c.execute('PRAGMA foreign_key_check'))

    def backup(self, destination, timeout=30):
        require(not self.c.in_transaction, 'BACKUP_DURING_TRANSACTION')
        p = Path(destination); p.parent.mkdir(parents=True, exist_ok=True)
        require(not p.exists(), 'BACKUP_ALREADY_EXISTS')
        import os,time
        tmp=p.parent/(p.name+'.partial_'+str(os.getpid())+'_'+now().strftime('%Y%m%d%H%M%S%f'))
        require(not tmp.exists(),'PARTIAL_BACKUP_ALREADY_EXISTS')
        target=sqlite3.connect(tmp);start=time.monotonic();complete=False
        def progress(status,remaining,total):
            if time.monotonic()-start>timeout:raise TimeoutError('BACKUP_TIMEOUT')
        try:
            self.c.backup(target,progress=progress,pages=256,sleep=.1)
            require(target.execute('PRAGMA integrity_check').fetchone()[0]=='ok','BACKUP_INTEGRITY_ERROR')
            target.close();require(not p.exists(),'BACKUP_ALREADY_EXISTS');os.replace(tmp,p);complete=True
        finally:
            target.close()
            if not complete and tmp.exists():tmp.unlink()
        return p

    def semantic(self):
        h = hashlib.sha256()
        for t in TABLES:
            h.update((t+'\n').encode())
            for r in self.c.execute(f'SELECT record_id,payload_json FROM realtime_{t} ORDER BY record_id'):
                h.update((r[0]+'\n'+r[1]+'\n').encode())
        return h.hexdigest()

    def close(self):
        try:
            self.c.commit()
            self.c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:self.c.close()
