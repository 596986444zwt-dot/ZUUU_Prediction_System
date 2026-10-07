"""Durable response receipt before SQLite; replay never invents old arrival."""
import json
import os
from pathlib import Path
from .contracts import ROOT,iso,identity,canonical,require,now

def directory(db):
    return ROOT/'raw/phase10' if db.namespace=='PRODUCTION' else db.path.parent/'simulation_spool'

def save(db,source,content,received,run=None):
    record=dict(source=source,content_hex=content.hex(),actual_ingest_time=iso(received),run=iso(run) if run else None,namespace=db.namespace)
    rid=identity(record);folder=directory(db);folder.mkdir(parents=True,exist_ok=True)
    path=folder/(rid+'.json')
    if path.exists():require(path.read_text(encoding='utf-8')==canonical(record),'SPOOL_IDENTITY_CONFLICT');return rid
    tmp=folder/(rid+'.tmp')
    with tmp.open('xb') as f:
        f.write(canonical(record).encode());f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    return rid

def mark(db,rid,status='PROCESSED'):
    with db.c:db.insert('engine_state',dict(kind='SPOOL_PROCESSED',spool_id=rid,status=status),rid+'/SPOOL')

def recover(db):
    from .collectors import archive_metar,archive_ecmwf
    folder=directory(db)
    done={r['spool_id'] for r in db.rows('engine_state') if r.get('kind')=='SPOOL_PROCESSED'}
    added=0
    if not folder.exists():return 0
    for path in sorted(folder.glob('*.json')):
        if path.stem in done:continue
        try:
            r=json.loads(path.read_text(encoding='utf-8'))
            require(identity(r)==path.stem and r['namespace']==db.namespace,'SPOOL_HASH_OR_NAMESPACE_ERROR')
            content=json.loads(bytes.fromhex(r['content_hex']))
            if r['source']=='ZUUU':added+=archive_metar(db,content,r['actual_ingest_time'],spool_id=path.stem)
            elif r['source']=='ECMWF':added+=int(archive_ecmwf(db,content,r['run'],r['actual_ingest_time']))
            else:raise ValueError('UNSUPPORTED_SPOOL_SOURCE')
        except (ValueError,KeyError,TypeError) as exc:
            with db.c:db.insert('errors',dict(reason='SPOOL_QUARANTINED',spool_id=path.stem,error_type=type(exc).__name__,
                error=str(exc),time=iso(now()),status='QUARANTINED',raw_payload_reference=str(path)))
            mark(db,path.stem,'QUARANTINED');continue
        mark(db,path.stem)
    return added
