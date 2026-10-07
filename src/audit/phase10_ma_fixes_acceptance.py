"""MA-001 controlled append-only repair and independent before/after verification."""
import hashlib
import json
import sqlite3
from pathlib import Path
from src.audit.master.common import snapshot, digest
from src.realtime.contracts import ROOT
from src.realtime.guardian import fingerprints

OUT=ROOT/'docs/phase10/ma_fixes_v1'
DB=ROOT/'database/phase10_realtime_v1.db'

def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def inventory(c):
    result={}
    for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        table=row[0]
        print('Checking table:',table,flush=True)
        result[table]={}
        for r in c.execute('SELECT * FROM "'+table+'"'):
            values=list(r)
            result[table][str(values[0])]=hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()
    return result

def verify(c,before):
    after=inventory(c)
    changed=[t+'/'+k for t,rows in before.items() for k,v in rows.items() if after[t].get(k)!=v]
    added=[t+'/'+k for t,rows in after.items() for k in rows if k not in before[t]]
    assert not changed,changed
    assert all(x.startswith('realtime_probability_state/') for x in added),added
    states={r[0]:json.loads(r[1]) for r in c.execute('SELECT record_id,payload_json FROM realtime_probability_state')}
    probs={r[0]:json.loads(r[1]) for r in c.execute('SELECT record_id,payload_json FROM realtime_probability_predictions')}
    evidence=[];canonical_hashes={}
    for r in c.execute('SELECT record_id,payload_json FROM realtime_prediction_snapshots'):
        s=json.loads(r[1]);ref=probs[s['probability_id']]['probability_state_version']
        assert ref in states
        resolved=states[ref].get('canonical_state_id',ref)
        assert resolved==s['probability_state_version']
        if resolved!=ref:
            if resolved not in canonical_hashes:
                payload=json.dumps(states[resolved],sort_keys=True,separators=(',',':'),ensure_ascii=False)
                canonical_hashes[resolved]=hashlib.sha256(payload.encode()).hexdigest()
            assert canonical_hashes[resolved]==states[ref]['canonical_payload_sha256']
        evidence.append(dict(snapshot_id=r[0],probability_state_version=ref,canonical_state_id=resolved,status='PASS'))
    integrity=[r[0] for r in c.execute('PRAGMA integrity_check')]
    fk=list(c.execute('PRAGMA foreign_key_check'))
    assert integrity==['ok'] and not fk
    save('LINEAGE_VERIFICATION.json',evidence)
    return dict(checked_snapshots=len(evidence),unresolved_reference_count=0,original_record_changed_count=len(changed),
                added_state_alias_count=len(added),probability_and_continuous_payloads_byte_identical=True,
                integrity=integrity,foreign_key_violation_count=len(fk))

def main():
    from src.realtime.archive import Archive
    from src.realtime.engine import InstanceLock
    from src.realtime.lineage_repair import plan_aliases,append_aliases
    OUT.mkdir(parents=True,exist_ok=True)
    assert not (OUT/'REPAIR_RESULT.json').exists(),'REPAIR_ALREADY_RECORDED'
    protected=fingerprints()
    protected.update({p.relative_to(ROOT).as_posix():digest(p) for p in (ROOT/'docs/master_audit_v1').rglob('*') if p.is_file()})
    save('SOURCE_GUARDIAN_BEFORE.json',protected)
    with InstanceLock(ROOT/'logs/phase10/engine.lock'):
        print('Lock acquired; reading isolated database image',flush=True)
        before_hash=digest(DB)
        c=snapshot(DB);original=inventory(c);print('Planning aliases',flush=True);aliases=plan_aliases(c)
        assert len(c.execute('SELECT * FROM realtime_probability_predictions').fetchall())==34
        missing=c.execute("SELECT COUNT(*) FROM realtime_probability_predictions p LEFT JOIN realtime_probability_state s ON s.record_id=json_extract(p.payload_json,'$.probability_state_version') WHERE s.record_id IS NULL").fetchone()[0]
        assert missing==34,missing
        save('REPAIR_PLAN.json',dict(before_physical_sha256=before_hash,unresolved_before=missing,aliases=aliases))
        backup=OUT/'phase10_before_repair.db'
        assert not backup.exists()
        target=sqlite3.connect(backup)
        try:c.backup(target)
        finally:target.close();c.close()
        save('ORIGINAL_ROW_HASHES.json',original)
        assert digest(DB)==before_hash
        a=Archive(DB)
        latest=a.latest('probability_state')['_id']
        try:
            added=append_aliases(a,aliases)
            assert a.latest('probability_state')['_id']==latest
            for ref,alias in aliases.items():assert a.resolve_probability_state(ref)['state_id']==alias['canonical_state_id']
            semantic=a.semantic()
        finally:a.close()
        c=snapshot(DB)
        try:result=verify(c,original)
        finally:c.close()
    after={name:digest(ROOT/name) for name in protected}
    changes=[name for name in protected if protected[name]!=after[name]]
    save('SOURCE_GUARDIAN_AFTER.json',dict(sha256=after,changed_files=changes))
    assert not changes,changes
    result.update(acceptance='PASS',source_guardian='PASS',upstream_changed_file_count=0,
                  unresolved_reference_count_before=missing,inserted_aliases=added,
                  before_physical_sha256=before_hash,after_physical_sha256=digest(DB),semantic_sha256=semantic,
                  backup_sha256=digest(backup),historical_master_reports_preserved=True)
    save('REPAIR_RESULT.json',result)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
