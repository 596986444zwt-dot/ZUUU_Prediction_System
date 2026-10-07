"""Resume this same V2 run from its exhaustive Phase9 checkpoint, never V1 results."""
from .common import *
from .database_audit import Source
from .feature_audit import Features
from .model_audit import Models
from .probability_audit import Probability
from .guardian import after
from .run import checkpoint

def run():
    saved=json.loads((OUT/'V2_PROGRESS.json').read_text(encoding='utf-8'))
    assert saved['stage']=='PHASE10-PARITY-PROBES'
    assert saved['protocol_sha256']==digest(OUT/'MASTER_AUDIT_V2_PROTOCOL.md')
    assert after()['UPSTREAM_CHANGED_FILE_COUNT']==0
    s=Source()
    try:
        s.load();s.target_audit();s.ecmwf_audit();s.phase4_5();s.phase6()
        f=Features(s);f.solar_audit();f.audit();m=Models(s,f);m.audit();p=Probability(s,m)
        # Earlier exhaustive 24266-PMF and 2974073-edge calculations were executed
        # in this V2 session against these unchanged protected inputs; retain them.
        fresh=dict(s.counts);s.counts.update({k:int(v) for k,v in saved['counts'].items()});s.counts.update(fresh)
        s.evidence.update(saved['evidence']);s.counts['LINEAGE_CHECK_COUNT']=0
        from .probability_diagnostics import audit as diagnostics
        diagnostics(s,p);print('Additional probability diagnostics finished',flush=True)
        from .adversarial import risk_samples,aliases,anchor_probes,forward_replay,supplemental
        risk_samples(s,f,m,p);aliases(s);anchor_probes(s)
        from .realtime_audit import Realtime
        r=Realtime(s,f,m,p);r.audit();r.parity();r.probes();print('Strengthened runtime model/parity finished',flush=True)
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
        output('V2_RESUME_EXCEPTION.txt',traceback.format_exc());print(traceback.format_exc(),flush=True);after();raise
    finally:s.close()
if __name__=='__main__':run()
