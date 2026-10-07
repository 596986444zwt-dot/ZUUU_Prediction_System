import csv
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/master_audit_v1'
UTC = timezone.utc
BJT = ZoneInfo('Asia/Shanghai')
SEED = 20261002

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()

def stamp(s):
    d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    if d.tzinfo is None: raise ValueError('NAIVE_TIME: '+str(s))
    return d.astimezone(UTC)

def eligible(day):
    return (datetime.fromisoformat(day).replace(tzinfo=BJT)+timedelta(days=2)).astimezone(UTC)

def season(day):
    m=int(str(day)[5:7]);return 'DJF' if m in (12,1,2) else 'MAM' if m in (3,4,5) else 'JJA' if m in (6,7,8) else 'SON'

def snapshot(path):
    """Read bytes into RAM: no upstream SQLite file/sidecar creation or write."""
    p=Path(path)
    wal=p.with_name(p.name+'-wal')
    if wal.exists() and wal.stat().st_size>0:
        raise RuntimeError('NONEMPTY_WAL_REQUIRES_CONSISTENT_SNAPSHOT: '+str(p))
    image=bytearray(p.read_bytes())
    # WAL header flags are irrelevant in an isolated byte image with an empty WAL.
    # Change only the RAM copy; upstream bytes are never opened for writing.
    image[18:20]=b'\x01\x01'
    c=sqlite3.connect(':memory:');c.deserialize(bytes(image));c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    c.set_authorizer(lambda action,*args: sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE,sqlite3.SQLITE_ALTER_TABLE,sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_CREATE_TABLE) else sqlite3.SQLITE_OK)
    return c

def records(c, table): return [dict(r) for r in c.execute('SELECT * FROM "'+table+'"')]

def payloads(c, table):
    return [dict(json.loads(r['payload_json']), _id=r['record_id'], _created=r['created_at']) for r in c.execute('SELECT * FROM "'+table+'"')]

def output(name, data):
    OUT.mkdir(parents=True,exist_ok=True)
    p=OUT/name
    if p.resolve().parent!=OUT.resolve():raise ValueError('AUDIT_OUTPUT_ESCAPE')
    if name.endswith('.json'):p.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    elif name.endswith('.csv'):
        keys=list(dict.fromkeys(k for r in data for k in r)) or ['status']
        with p.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
            for r in data:w.writerow({k:json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list)) else v for k,v in r.items()})
    else:p.write_text(str(data)+'\n',encoding='utf-8')

def close(a,b,tol=1e-8):
    if a is None or b is None:return a is None and b is None
    return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a)-float(b))<=tol

def metrics(errors):
    import numpy as np
    x=np.array(errors,dtype=float);ae=np.abs(x)
    return dict(N=len(x),Bias=float(x.mean()),MAE=float(ae.mean()),RMSE=float(np.sqrt(np.mean(x*x))),MedianAE=float(np.median(ae)),P90AE=float(np.quantile(ae,.9)),MaxAE=float(ae.max()))

def scores(p,actual,kmin=-80):
    import numpy as np
    p=np.array(p,dtype=float);k=np.arange(kmin,kmin+len(p));i=int(actual)-kmin
    q=float(p[i]) if 0<=i<len(p) else 0.
    b=float(np.sum(p*p)+1-2*q)
    cdf=np.cumsum(p);crps=float(np.sum((cdf-(k>=actual))**2))
    # Frozen protocol uses stable descending PMF order; low integer wins a tie.
    order=np.argsort(-p,kind='stable');hits=[int(i in order[:n]) for n in (1,2,3)]
    return dict(brier=b,logloss=-math.log(max(q,1e-12)),crps=crps,top1=hits[0],top2=hits[1],top3=hits[2],actual_probability=q)

def select_stratified(rows,n,h='horizon',day='target_business_date'):
    """Fixed-seed season/time stratification, plus target extrema when available."""
    import random
    rng=random.Random(SEED);ordered=sorted(rows,key=lambda r:r[day]);groups={}
    for i,r in enumerate(ordered):groups.setdefault((r.get(h),season(r[day]),min(2,3*i//max(1,len(ordered)))),[]).append(r)
    picked=[]
    for key in sorted(groups,key=str):
        g=list(groups[key]);rng.shuffle(g);picked.extend(g[:max(1,n//max(1,len(groups)))])
    for key in ('actual_target','actual','label_tmax'):
        valid=[r for r in ordered if r.get(key) is not None]
        if valid:picked.extend([min(valid,key=lambda r:r[key]),max(valid,key=lambda r:r[key])]);break
    ident=lambda r:json.dumps(r,sort_keys=True,default=str)
    result={ident(r):r for r in picked};remaining=[r for r in ordered if ident(r) not in result];rng.shuffle(remaining)
    for r in remaining:
        if len(result)>=n:break
        result[ident(r)]=r
    return sorted(list(result.values())[:n],key=lambda r:r[day])
