import hashlib
import json
from .contracts import CONTRACT
from .schema import KEYS

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def semantic_hash(data):
    projected = {'contract': CONTRACT}
    for table, keys in KEYS.items():
        if table == 'manifest':
            continue
        projected[table] = [{k: (int(v) if isinstance(v, bool) else v) for k,v in sorted(r.items()) if k != 'created_at'}
                            for r in sorted(data[table], key=lambda r: tuple(r[k] for k in keys))]
    return hashlib.sha256(canonical(projected).encode()).hexdigest()
