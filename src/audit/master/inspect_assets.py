"""Discovery output only; never executes a formal builder."""
import json
from .common import *

def run():
    rows=json.loads((OUT/'MASTER_ASSET_INVENTORY.json').read_text(encoding='utf-8'))
    lines=[]
    for a in rows:
        if a['asset_role']=='database' and '/backups/' not in a['relative_path']:
            c=snapshot(a['path']);lines.append('\nDATABASE '+a['relative_path'])
            for t, in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                cols=[r['name'] for r in c.execute('PRAGMA table_info("'+t+'")')];lines.append(t+' '+str(c.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0])+' '+str(cols))
                r=c.execute('SELECT * FROM "'+t+'" LIMIT 1').fetchone()
                if r:
                    d=dict(r)
                    if 'payload_json' in d:d=json.loads(d['payload_json'])
                    lines.append(json.dumps({k:(v if not isinstance(v,(bytes,str,list,dict)) or isinstance(v,str) and len(v)<220 else str(v)[:220]) for k,v in d.items()},ensure_ascii=False,default=str))
            c.close()
    output('INSPECTION.txt','\n'.join(lines))

if __name__=='__main__':run()
