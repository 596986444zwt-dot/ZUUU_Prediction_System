"""Independent Phase9 file; generic canonical records, binary float64 mass vectors."""
import sqlite3,hashlib,json,os,tempfile
from .contracts import canonical,require

TABLES=('sample','residual_history','distribution_registry','distribution_state','calibration_registry','candidate_selection','probability_prediction','probability_mass','probability_metric','calibration_bin','interval_metric','slice_metric','fallback_audit','trajectory_registry','protocol','manifest')

def semantic(data):
 h=hashlib.sha256()
 for t in TABLES:
  if t=='manifest':continue
  h.update((t+'\n').encode())
  for row in sorted(data[t],key=lambda r:r['record_id']):
   h.update((canonical({k:v.hex() if isinstance(v,bytes) else v for k,v in row.items()})+'\n').encode())
 return h.hexdigest()

def write(path,data):
 require(not path.exists(),'REFUSE existing Phase9 artifact')
 fd,name=tempfile.mkstemp(prefix='phase9_',suffix='.building',dir=path.parent);os.close(fd)
 try:
  c=sqlite3.connect(name);c.execute('PRAGMA foreign_keys=ON')
  for t in TABLES:
   if t=='probability_mass':
    c.execute('CREATE TABLE phase9_probability_mass(record_id TEXT PRIMARY KEY,pmf BLOB NOT NULL,cdf BLOB NOT NULL,survival BLOB NOT NULL,FOREIGN KEY(record_id) REFERENCES phase9_probability_prediction(record_id))')
    c.executemany('INSERT INTO phase9_probability_mass VALUES(?,?,?,?)',[(r['record_id'],r['pmf'],r['cdf'],r['survival']) for r in data[t]])
   else:
    c.execute('CREATE TABLE phase9_'+t+'(record_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)')
    c.executemany('INSERT INTO phase9_'+t+' VALUES(?,?)',[(r['record_id'],canonical(r)) for r in data[t]])
   for action in ('INSERT','UPDATE','DELETE'):c.execute('CREATE TRIGGER phase9_'+t+'_'+action+' BEFORE '+action+' ON phase9_'+t+" BEGIN SELECT RAISE(ABORT,'Phase9 sealed immutable'); END")
  require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not c.execute('PRAGMA foreign_key_check').fetchall(),'New DB integrity')
  c.commit();c.close();os.link(name,path)
 finally:
  os.unlink(name)

def read(c):
 return {t:[dict(r) if t=='probability_mass' else json.loads(r['payload_json']) for r in c.execute('SELECT * FROM phase9_'+t+' ORDER BY record_id')] for t in TABLES}
