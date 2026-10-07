"""Publish from completed full-audit checkpoint after a reporting-only interruption."""
import csv
from .common import *
from .database_audit import Source
from .feature_audit import Features
from .model_audit import Models
from .probability_audit import Probability
from .realtime_audit import Realtime
from .report import finish

def run():
    completed=json.loads((OUT/'MASTER_PROGRESS.json').read_text(encoding='utf-8'))
    if completed['counts'].get('END_TO_END_CHECK_COUNT',0)<40:raise RuntimeError('INCOMPLETE_NUMERICAL_CHECKPOINT')
    s=Source().load();s.counts.update({k:int(v) for k,v in completed['counts'].items()});s.evidence=completed['evidence']
    f=Features(s);m=Models(s,f);p=Probability(s,m);r=Realtime(s,f,m,p)
    r.notes=s.evidence['phase10']['state_reference_notes'];r.probes_results=list(csv.DictReader((OUT/'MASTER_FAILURE_PROBES.csv').open(encoding='utf-8-sig')))
    for probe in r.probes_results:
        if probe['actual'].startswith(('{','[')):
            probe['actual']=json.loads(probe['actual'])
    from .parity_status_audit import audit
    audit(s,f,m,p)
    finish(s,f,m,p,r);s.close()
if __name__=='__main__':run()
