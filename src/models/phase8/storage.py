"""New Phase8 database only; append-only sealing and semantic identity."""
import sqlite3
import hashlib
from .contracts import canonical
TABLES=('registry','sample','split','preprocessing','hyperparameter','inner_prediction','state','prediction','metric','slice_metric','comparison','candidate','manifest')
def semantic(data):
 h=hashlib.sha256()
 for t in TABLES:
  if t=='manifest':continue
  h.update((t+'\n').encode())
  for r in sorted(data[t],key=lambda r:r['record_id']):h.update((canonical({k:v for k,v in r.items() if k not in ('fit_seconds','artifact_sha256')})+'\n').encode())
 return h.hexdigest()
def write(path,data):
 c=sqlite3.connect(path)
 try:
  for t in TABLES:
   rs=data[t];names=list(rs[0]);defs=[]
   for n in names:
    v=next((r[n] for r in rs if r[n] is not None),None)
    typ='INTEGER' if isinstance(v,(int,bool)) else 'REAL' if isinstance(v,float) else 'TEXT'
    defs.append('"'+n+'" '+typ+(' PRIMARY KEY NOT NULL' if n=='record_id' else ''))
   c.execute('CREATE TABLE phase8_'+t+' ('+','.join(defs)+')')
   c.executemany('INSERT INTO phase8_'+t+' VALUES ('+','.join('?' for _ in names)+')',[[r[n] for n in names] for r in rs])
   for action in ('UPDATE','DELETE','INSERT'):
    c.execute('CREATE TRIGGER phase8_'+t+'_'+action+' BEFORE '+action+' ON phase8_'+t+" BEGIN SELECT RAISE(ABORT,'Phase8 sealed immutable'); END")
  c.commit()
 finally:c.close()
