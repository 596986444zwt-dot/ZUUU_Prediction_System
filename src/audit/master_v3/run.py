"""Third-round fresh evidence. Never loads a prior round's verdict as proof."""
import traceback
from .common import *
from .database_audit import Source
from .feature_audit import Features
from .model_audit import Models
from .probability_audit import Probability
from .guardian import databases,after

def checkpoint(s,stage):
    hashes={p.name:digest(p) for p in OUT.iterdir() if p.is_file() and p.suffix in ('.json','.csv') and p.name!='V3_PROGRESS.json'}
    output('V3_PROGRESS.json',dict(stage=stage,counts=dict(s.counts),evidence=s.evidence,evidence_hashes=hashes,protocol_sha256=digest(OUT/'MASTER_AUDIT_V3_PROTOCOL.md'),classification='INTERMEDIATE'))
    print('V3_STAGE',stage,json.dumps(dict(s.counts),default=lambda v:int(v)),flush=True)

def run():
    s=Source();stage='LOAD_FROZEN_INPUTS'
    try:
        g=json.loads((OUT/'MA001_NEW_PREDICTION_GUARD_FINAL_V3.json').read_text(encoding='utf-8'));s.counts.update(g['counts'])
        s.load();databases(s)
        stage='PHASE1-6';s.target_audit();s.ecmwf_audit();s.phase4_5();s.phase6()
        s.counts['TARGET_RECALC_CHECK_COUNT']=len(s.truth);s.counts['ISSUE_RULE_CHECK_COUNT']=len(s.samples4);checkpoint(s,stage)
        stage='PHASE7';f=Features(s);f.solar_audit();f.audit();checkpoint(s,stage)
        stage='PHASE8';m=Models(s,f);m.audit();s.counts['LIGHTGBM_TREE_TRAVERSAL_CHECK_COUNT']=s.counts['MODEL_T2_REPRO_CHECK_COUNT'];s.counts['LIGHTGBM_TREE_TRAVERSAL_MISMATCH_COUNT']=s.counts['MODEL_T2_REPRO_MISMATCH_COUNT'];checkpoint(s,stage)
        stage='PHASE9';p=Probability(s,m);p.audit()
        from .probability_diagnostics import audit as diagnostics
        diagnostics(s,p);checkpoint(s,stage)
        from .adversarial import risk_samples,aliases,anchor_probes,forward_replay,supplemental
        stage='ALIASES_ANCHOR';risk_samples(s,f,m,p);aliases(s);anchor_probes(s);checkpoint(s,stage)
        from .realtime_audit import Realtime
        stage='REALTIME_PARITY_END_TO_END';r=Realtime(s,f,m,p);r.audit();r.parity();r.probes();checkpoint(s,stage)
        stage='FORWARD_120_DAYS';forward_replay(s,p);checkpoint(s,stage)
        stage='SUPPLEMENTAL_DEPENDENCY_HASH';supplemental(s,f,m,p,r)
        from .selection_audit import audit as selection_audit
        selection_audit(s,m)
        from .hash_audit import audit_hashes
        audit_hashes(s)
        from .static_audit import audit_static
        audit_static(s)
        from .dependency import audit_dependency
        audit_dependency(s)
        checkpoint(s,'COMPLETE_MATH')
        guardian=after();s.counts['UPSTREAM_CHANGED_FILE_COUNT']=guardian['UPSTREAM_CHANGED_FILE_COUNT'];checkpoint(s,'FINAL_GUARDIAN')
    except Exception:
        exc=traceback.format_exc();guardian=after()
        output('AUDITOR_EXCEPTION_RUN_V3.json',dict(stage=stage,exception=exc,completed_counts=dict(s.counts),pending='Stages after '+stage,guardian=guardian,classification='FAILED_AUDITOR_ARTIFACT'))
        print(exc,flush=True);raise
    finally:s.close()

if __name__=='__main__':run()
