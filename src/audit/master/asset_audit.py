import base64
import json
import re
import zlib
from datetime import datetime,timezone
from .common import *

def discover():
    paths=[]
    for folder in ('database','docs','models','config','src','scripts','raw','logs','backups','reports','data'):
        base=ROOT/folder
        if not base.exists():continue
        for p in base.rglob('*'):
            rel=p.relative_to(ROOT).as_posix()
            if not p.is_file() or '__pycache__' in p.parts or '/master/' in rel or 'master_audit' in rel:continue
            if p.suffix in ('.pyc','.lock','.request'):continue
            paths.append(p)
    paths.extend(ROOT.glob('*.bat'))
    return sorted(set(paths))

def inventory():
    out=[]
    for p in discover():
        s=p.stat();rel=p.relative_to(ROOT).as_posix();m=re.search(r'phase[_]?([0-9]+)',rel,re.I)
        phase=int(m[1]) if m else (1 if 'zuuu' in rel else 2 if 'ecmwf' in rel else 0)
        role='database' if p.suffix=='.db' else 'model' if p.suffix in ('.pkl','.joblib') else 'protocol' if 'PROTOCOL' in p.name else 'manifest' if 'MANIFEST' in p.name else 'architecture' if p.suffix=='.pdf' else 'source' if p.suffix=='.py' else 'runtime' if rel.startswith(('logs/','raw/','backups/')) else 'document/config'
        out.append(dict(path=str(p.resolve()),relative_path=rel,size=s.st_size,modified_time=datetime.fromtimestamp(s.st_mtime,timezone.utc).isoformat(),sha256=digest(p),phase=phase,asset_role=role,frozen_status='LIVE_STOPPED_BUILD_SNAPSHOT' if phase==10 and role in ('database','runtime') else 'PROTECTED_FOR_MASTER_AUDIT',semantic_sha=None))
    return out

def pdf_text(path):
    pages=[]
    for m in re.finditer(rb'stream\r?\n(.*?)endstream',Path(path).read_bytes(),re.S):
        try:s=zlib.decompress(base64.a85decode(m[1].strip(),adobe=True))
        except Exception:continue
        texts=[]
        for q in re.finditer(rb'\(((?:\\.|[^\\)])*)\)\s*Tj',s,re.S):
            b=re.sub(rb'\\([0-7]{1,3})',lambda x:bytes([int(x[1],8)]),q[1]).replace(b'\\(',b'(').replace(b'\\)',b')').replace(b'\\\\',b'\\')
            try:texts.append(b.decode('utf-16-be') if any(v==0 or v>127 for v in b) else b.decode())
            except UnicodeError:pass
        if texts:pages.append(' '.join(texts))
    return pages

def preflight():
    if (OUT/'MASTER_SOURCE_GUARDIAN_BEFORE.json').exists():raise RuntimeError('Existing Master Audit: do not silently overwrite historical audit')
    rows=inventory();output('MASTER_ASSET_INVENTORY.json',rows);output('MASTER_ASSET_INVENTORY.csv',rows)
    output('MASTER_SOURCE_GUARDIAN_BEFORE.json',{'created_at':datetime.now(timezone.utc).isoformat(),'assets':rows})
    pdfs=[r for r in rows if r['asset_role']=='architecture']
    pages=pdf_text(pdfs[0]['path']);output('MASTER_ARCHITECTURE_EXTRACT.txt','\n\n'.join(f'PAGE {i+1}\n{p}' for i,p in enumerate(pages)))
    return rows,pages

def database_discovery(rows):
    result=[];schemas={}
    for r in rows:
        if r['asset_role']!='database' or '/backups/' in r['relative_path']:continue
        try:
            c=snapshot(r['path']);tables=[x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            counts={t:c.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for t in tables}
            integrity=[x[0] for x in c.execute('PRAGMA integrity_check')];fk=[tuple(x) for x in c.execute('PRAGMA foreign_key_check')]
            schemas[r['relative_path']]={'sql':[dict(x) for x in c.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL")],'counts':counts,'example':{t:dict(x) if (x:=c.execute('SELECT * FROM "'+t+'" LIMIT 1').fetchone()) and not any(isinstance(v,bytes) for v in x) else 'BLOB_OR_EMPTY' for t in tables}}
            result.append(dict(database=r['relative_path'],integrity=integrity,foreign_key_count=len(fk),table_counts=counts,status='PASS' if integrity==['ok'] and not fk else 'FAIL'));c.close()
        except Exception as e:result.append(dict(database=r['relative_path'],status='NOT_INDEPENDENTLY_VERIFIED',error=str(e)))
    output('MASTER_DATABASE_INTEGRITY.csv',result);output('MASTER_SCHEMA_DISCOVERY.json',schemas)
    return schemas

def after(before):
    nowrows=inventory();nowmap={r['relative_path']:r for r in nowrows};changes=[]
    for r in before:
        new=nowmap.get(r['relative_path'])
        if new is None or (new['sha256'],new['size'],new['modified_time'])!=(r['sha256'],r['size'],r['modified_time']):changes.append({'path':r['relative_path'],'before':r,'after':new})
    additions=[r for r in nowrows if r['relative_path'] not in {b['relative_path'] for b in before}]
    output('MASTER_SOURCE_GUARDIAN_AFTER.json',dict(assets=nowrows,changes=changes,unauthorized_new_assets=additions,UPSTREAM_CHANGED_FILE_COUNT=len(changes)+len(additions),SOURCE_GUARDIAN='PASS' if not changes and not additions else 'FAIL'))
    return changes,additions
