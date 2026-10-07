"""MA-001 append-only identity repair. Forecast payloads are never rewritten."""
import json
from .contracts import identity, require, iso, now

def plan_aliases(connection):
    def rows(table):
        return {r[0]:json.loads(r[1]) for r in connection.execute('SELECT record_id,payload_json FROM realtime_'+table)}
    states=rows('probability_state');probabilities=rows('probability_predictions');snapshots=rows('prediction_snapshots');aliases={};fingerprints={}
    for sid,snapshot in snapshots.items():
        probability=probabilities[snapshot['probability_id']];reference=probability['probability_state_version'];canonical=snapshot['probability_state_version']
        require(canonical in states and states[canonical].get('record_type')!='LINEAGE_ALIAS','INVALID_CANONICAL_STATE')
        if reference==canonical:continue
        state=states[canonical]
        if canonical not in fingerprints:
            with_namespace={k:v for k,v in state.items() if k!='created_at'}
            without_namespace={k:v for k,v in with_namespace.items() if k!='namespace'}
            fingerprints[canonical]=(identity(with_namespace),identity(without_namespace),identity(state))
        require(reference in fingerprints[canonical][:2],'LEGACY_STATE_FINGERPRINT_MISMATCH')
        alias=dict(state_id=reference,record_type='LINEAGE_ALIAS',canonical_state_id=canonical,
                   canonical_payload_sha256=fingerprints[canonical][2],cutoff=state['cutoff'],repair_id='MA001_LINEAGE_FIX_V1',
                   semantics='identity alias only; not residual/calibration state advancement')
        require(reference not in aliases or aliases[reference]==alias,'AMBIGUOUS_LEGACY_STATE_REFERENCE')
        aliases[reference]=alias
    for rid,alias in aliases.items():
        if rid in states:
            expected=dict(alias,namespace=states[rid]['namespace'])
            require(states[rid]==expected,'EXISTING_STATE_ALIAS_CONFLICT')
    return aliases

def append_aliases(archive, aliases):
    added=0
    with archive.c:
        for rid,alias in sorted(aliases.items()):
            added+=archive.insert('probability_state',alias,rid,now())
    return added
