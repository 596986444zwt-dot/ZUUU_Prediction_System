"""Second adversarial audit: all production inputs read-only, no formal build."""
import traceback
from .common import *
from .database_audit import Source
from .feature_audit import Features
from .model_audit import Models
from .probability_audit import Probability
from .guardian import databases,after

def checkpoint(s,stage):
    output('V2_PROGRESS.json',dict(stage=stage,counts=dict(s.counts),evidence=s.evidence,protocol_sha256=digest(OUT/'MASTER_AUDIT_V2_PROTOCOL.md')))
    print(stage,json.dumps(dict(s.counts),default=lambda v:int(v)),flush=True)

def run():
    s=Source()
    try:
        s.load();print('Sources loaded',flush=True);databases(s)
        s.target_audit();s.ecmwf_audit();s.phase4_5();s.phase6()
        s.counts['TARGET_RECALC_CHECK_COUNT']=len(s.truth);s.counts['ISSUE_RULE_CHECK_COUNT']=len(s.samples4)
        checkpoint(s,'PHASE1-6')
        f=Features(s);f.solar_audit();f.audit();checkpoint(s,'PHASE7')
        m=Models(s,f);m.audit();checkpoint(s,'PHASE8')
        p=Probability(s,m);p.audit();checkpoint(s,'PHASE9')
        from .probability_diagnostics import audit as probability_diagnostics
        probability_diagnostics(s,p)
        from .adversarial import risk_samples,aliases,anchor_probes,forward_replay,supplemental
        risk_samples(s,f,m,p)
        aliases(s)
        anchor_probes(s)
        from .realtime_audit import Realtime
        r=Realtime(s,f,m,p);r.audit();r.parity();r.probes();checkpoint(s,'PHASE10-PARITY-PROBES')
        forward_replay(s,p)
        supplemental(s,f,m,p,r)
        from .selection_audit import audit as selection_audit
        selection_audit(s,m)
        from .hash_audit import audit_hashes
        audit_hashes(s)
        from .static_audit import audit_static
        audit_static(s)
        from .dependency import audit_dependency
        audit_dependency(s)
        checkpoint(s,'COMPLETE_MATH')
        guardian=after();s.counts['UPSTREAM_CHANGED_FILE_COUNT']=guardian['UPSTREAM_CHANGED_FILE_COUNT']
        from .report_v2 import finish
        finish(s,f,m,p,r)
    except Exception:
        output('V2_AUDITOR_EXCEPTION.txt',traceback.format_exc());print(traceback.format_exc(),flush=True)
        after();raise
    finally:s.close()

if __name__=='__main__':run()
