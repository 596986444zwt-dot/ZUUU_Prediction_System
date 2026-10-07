"""Complete V2's current-session tail; preserve completed full-math evidence."""
from .common import *
from .database_audit import Source
from .feature_audit import Features
from .model_audit import Models
from .probability_audit import Probability
from .realtime_audit import Realtime
from .guardian import after
from .run import checkpoint

def run():
    saved=json.loads((OUT/'V2_PROGRESS.json').read_text(encoding='utf-8'))
    assert saved['stage']=='PHASE10-PARITY-PROBES'
    assert saved['protocol_sha256']==digest(OUT/'MASTER_AUDIT_V2_PROTOCOL.md')
    assert after()['UPSTREAM_CHANGED_FILE_COUNT']==0
    s=Source()
    try:
        s.load();f=Features(s);m=Models(s,f);p=Probability(s,m);r=Realtime(s,f,m,p)
        s.counts.update({k:int(v) for k,v in saved['counts'].items()});s.evidence.update(saved['evidence'])
        with (OUT/'MASTER_FAILURE_PROBES.csv').open(encoding='utf-8-sig',newline='') as file:r.probes_results=list(csv.DictReader(file))
        from .adversarial import forward_replay,supplemental
        forward_replay(s,p);print('60-day chronological replay finished',flush=True)
        supplemental(s,f,m,p,r)
        from .selection_audit import audit as selection
        selection(s,m)
        from .hash_audit import audit_hashes
        audit_hashes(s)
        from .static_audit import audit_static
        audit_static(s)
        from .dependency import audit_dependency
        audit_dependency(s)
        checkpoint(s,'COMPLETE_MATH')
        s.counts['UPSTREAM_CHANGED_FILE_COUNT']=after()['UPSTREAM_CHANGED_FILE_COUNT']
        from .report_v2 import finish
        finish(s,f,m,p,r)
    except Exception:
        import traceback
        output('V2_POST_EXCEPTION.txt',traceback.format_exc());print(traceback.format_exc(),flush=True);after();raise
    finally:s.close()
if __name__=='__main__':run()
