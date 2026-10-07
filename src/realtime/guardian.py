"""Read-only formal asset guard. Does not lock new Phase10 implementation."""
import hashlib
import json
from pathlib import Path
from src.data_v1.source_io import open_snapshot, sha256_file

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs/phase10'

def protected():
    paths = set()
    for phase in range(3, 10):
        folder = ROOT / f'docs/phase{phase}'
        paths.update(p for p in folder.rglob('*') if p.is_file())
        for p in folder.rglob('*MANIFEST*.json'):
            m = json.loads(p.read_text(encoding='utf-8'))
            if isinstance(m.get('sha256'), dict):
                for name, expected in m['sha256'].items():
                    source = ROOT / name
                    if not source.is_file() or sha256_file(source) != expected:
                        raise ValueError('UPSTREAM_MANIFEST_MISMATCH: ' + name)
                    paths.add(source)
    paths.update((ROOT / 'docs/architecture').glob('*.pdf'))
    paths.update(p for p in (ROOT / 'database').glob('*.db') if not p.name.startswith('phase10'))
    for p in (ROOT / 'src').rglob('*.py'):
        if not p.relative_to(ROOT).as_posix().startswith('src/realtime/') and 'phase10' not in p.name:
            paths.add(p)
    return sorted(paths)

def fingerprints():
    return {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in protected()}

def save_before():
    DOCS.mkdir(parents=True, exist_ok=True)
    p = DOCS / 'PHASE10_SOURCE_GUARDIAN_BEFORE.json'
    if p.exists():
        return json.loads(p.read_text(encoding='utf-8'))
    hashes = fingerprints()
    checks = {}
    for name, digest in hashes.items():
        if name.endswith('.db'):
            c = open_snapshot(ROOT / name, digest)
            try:
                integrity = [r[0] for r in c.execute('PRAGMA integrity_check')]
                fk = list(c.execute('PRAGMA foreign_key_check'))
                if integrity != ['ok'] or fk:
                    raise ValueError('UPSTREAM_INTEGRITY_FAILURE: ' + name)
                checks[name] = {'integrity': integrity, 'foreign_keys': len(fk)}
            finally:
                c.close()
    result = {'sha256': hashes, 'integrity': checks,
              'protocol_sha256': sha256_file(DOCS / 'PHASE10_PROTOCOL_V1.md')}
    with p.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result

def check_after(before):
    current = fingerprints()
    changed = sorted(k for k, v in before['sha256'].items() if current.get(k) != v)
    protocol = sha256_file(DOCS / 'PHASE10_PROTOCOL_V1.md')
    result = {'sha256': current, 'changed_files': changed,
              'UPSTREAM_CHANGED_FILE_COUNT': len(changed),
              'SOURCE_GUARDIAN': 'PASS' if not changed and protocol == before['protocol_sha256'] else 'FAIL',
              'protocol_sha256': protocol}
    (DOCS / 'PHASE10_SOURCE_GUARDIAN_AFTER.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result
