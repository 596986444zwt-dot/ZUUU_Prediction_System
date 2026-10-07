"""V2 formal-asset read-only guardian, excluding only audit/cache namespaces."""
import csv,json,re
from datetime import datetime,timezone
from .common import ROOT,OUT,digest,snapshot,output

ALLOWED=('src/audit/master_v3/','tests/audit_master_v3/','docs/master_audit_v3/','temp/master_audit_v3/')
def discover():
    paths=[]
    for folder in ('database','docs','models','config','src','scripts','raw','logs','backups','reports','data','tests'):
        if (ROOT/folder).exists():paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file())
    paths.extend(p for p in ROOT.iterdir() if p.is_file())
    return sorted(p for p in set(paths) if not any(p.relative_to(ROOT).as_posix().startswith(x) for x in ALLOWED) and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.lock','.request'))
def after():
    before=json.loads((OUT/'SOURCE_GUARDIAN_V3_BEFORE.json').read_text(encoding='utf-8'))['assets']
    old={r['relative_path']:r for r in before};current={};changes=[]
    # PowerShell includes Windows pytest junction aliases; pathlib.rglob does
    # not follow those junctions. Check every protected BEFORE path directly.
    paths=set(discover()) | {ROOT/rel for rel in old if (ROOT/rel).is_file()}
    for p in sorted(paths):
        rel=p.relative_to(ROOT).as_posix();st=p.stat()
        row=dict(path=str(p),size=st.st_size,mtime_ns=st.st_mtime_ns//100+621355968000000000,sha256=digest(p));current[rel]=row
        if rel not in old or any(row[k]!=old[rel][k] for k in ('size','mtime_ns','sha256')):changes.append(rel)
    changes.extend(k for k in old if k not in current)
    result=dict(assets=current,changed_files=changes,UPSTREAM_CHANGED_FILE_COUNT=len(changes),SOURCE_GUARDIAN='PASS' if not changes else 'FAIL')
    output('SOURCE_GUARDIAN_V3_AFTER.json',result);return result
def databases(s):
    result=[]
    for p in sorted((ROOT/'database').glob('*.db')):
        c=s.get(p.name)
        integrity=[r[0] for r in c.execute('PRAGMA integrity_check')];fk=[list(r) for r in c.execute('PRAGMA foreign_key_check')]
        tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts={t:c.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for t in tables}
        result.append(dict(database=str(p),integrity=integrity,foreign_keys=fk,row_counts=counts,status='PASS' if integrity==['ok'] and not fk else 'FAIL'))
        output('SCHEMA_'+p.stem+'.json',[dict(r) for r in c.execute('SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL')])
    output('DATABASE_INTEGRITY_AUDIT_V3.json',result)
    s.counts['DATABASE_INTEGRITY_FAILURE_COUNT']=sum(r['status']=='FAIL' for r in result);s.counts['DATABASE_COUNT']=len(result)
    return result

