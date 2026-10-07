"""Read-only inventory and guardian. Run only from the Phase11 launcher/tests."""
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/phase11'

def connect(path):
    c = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=.15, isolation_level=None)
    c.execute('PRAGMA query_only=ON')
    return c

def inventory():
    result = {}
    for name in ('phase10_realtime_v1.db', 't0_forward_v1/t0_forward_v1.db', 'phase9_probability_v1.db'):
        with connect(ROOT/'database'/name) as c:
            tables = c.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()
            result[name] = {}
            for table, sql in tables:
                rows = c.execute('SELECT * FROM "'+table+'" ORDER BY rowid DESC LIMIT 1').fetchall()
                result[name][table] = dict(schema=sql, columns=[r[1] for r in c.execute('PRAGMA table_info("'+table+'")')], latest=[list(r) for r in rows])
                # Large probability state and binary arrays are not copied to the audit.
                result[name][table]['latest'] = [[str(v)[:14000] for v in r] for r in rows]
        c.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'SOURCE_INVENTORY.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

def guardian():
    files = {}
    for folder in ('src','config','scripts','tests','docs'):
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and not p.is_relative_to(ROOT/'src/gui') and not p.is_relative_to(ROOT/'tests/phase11') and not p.is_relative_to(ROOT/'docs/phase11'):
                files[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    for p in list(ROOT.glob('*.bat'))+[ROOT/'main.py']:
        if p.name!='start_gui.bat':files[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
    for p in (ROOT/'database').glob('*.db'):
        if p.name != 'phase10_realtime_v1.db':
            files[p.relative_to(ROOT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    schemas = {}
    for p in (ROOT/'database/phase10_realtime_v1.db', ROOT/'database/t0_forward_v1/t0_forward_v1.db'):
        c = connect(p)
        schemas[p.name] = c.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        c.close()
    return dict(files=files, schemas=schemas)

if __name__ == '__main__':
    inventory()
    (OUT/'GUARDIAN_BEFORE.json').write_text(json.dumps(guardian(),indent=2), encoding='utf-8')
