"""Standalone read-only audit runner. No formal builder is invoked."""
import json
from .common import *
from .database_audit import Source
from .feature_audit import Features
from .model_audit import Models
from .probability_audit import Probability

def run():
    s=Source().load();print('SOURCE LOADED',flush=True)
    s.target_audit();s.ecmwf_audit();s.phase4_5();s.phase6();print('PHASE1-6',dict(s.counts),flush=True)
    f=Features(s);f.solar_audit();f.audit();print('FEATURES',dict(s.counts),flush=True)
    m=Models(s,f);m.audit();print('MODELS',dict(s.counts),flush=True)
    p=Probability(s,m);p.audit();print('PROBABILITY',dict(s.counts),flush=True)
    output('MASTER_PROGRESS.json',dict(counts=dict(s.counts),evidence=s.evidence,protocol_sha256=digest(OUT/'MASTER_AUDIT_PROTOCOL.md')))
    from .realtime_audit import Realtime
    realtime=Realtime(s,f,m,p);realtime.audit();realtime.parity();realtime.probes()
    from .parity_status_audit import audit
    audit(s,f,m,p)
    output('MASTER_PROGRESS.json',dict(counts=dict(s.counts),evidence=s.evidence,protocol_sha256=digest(OUT/'MASTER_AUDIT_PROTOCOL.md')))
    from .report import finish
    finish(s,f,m,p,realtime);s.close()

if __name__=='__main__':run()
