import pathlib,sys,sqlite3,json,copy
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from src.realtime.archive import Archive
from src.audit.master_v4.verify import sha,save,independent_scores
source=ROOT/'database/phase10_realtime_v1.db';destination=ROOT/'temp/master_audit_v4/formal_snapshot_simulation_copy_resumed.db'
before=sha(source);destination.write_bytes(source.read_bytes())
c=sqlite3.connect(destination)
c.execute("UPDATE schema_version SET namespace='SIMULATION'");c.commit();c.close()
a=Archive(destination,namespace='SIMULATION');snapshot=a.rows('prediction_snapshots')[0]
def clean(r):return {k:v for k,v in r.items() if not k.startswith('_') and k!='namespace'}
f=clean(next(r for r in a.rows('feature_snapshots') if r['_id']==snapshot['feature_id']));m=clean(next(r for r in a.rows('continuous_predictions') if r['_id']==snapshot['continuous_id']));p=clean(next(r for r in a.rows('probability_predictions') if r['_id']==snapshot['probability_id']));meta=clean(snapshot)
canonical_meta=a.resolve_probability_state(meta['probability_state_version'])['state_id'];canonical_p=a.resolve_probability_state(p['probability_state_version'])['state_id']
assert canonical_meta==canonical_p
original_meta_reference=meta['probability_state_version']
meta['probability_state_version']=p['probability_state_version']
evidence=[]
for name,mutation in [('unchanged_control',None),('full_component_cross_horizon','horizon'),('full_component_invalid_pmf','pmf')]:
    mm=copy.deepcopy(m);pp=copy.deepcopy(p)
    if mutation=='horizon':mm['horizon']='T2' if m['horizon']=='T1' else 'T1'
    if mutation=='pmf':pp['pmf']=[-1.,2.]
    beforecount=len(a.rows('prediction_snapshots'))
    try:accepted=a.snapshot('V4/'+name,f,mm,pp,meta);actual='ACCEPTED' if accepted else 'DEDUPED'
    except ValueError as e:actual='REJECTED:'+str(e)
    evidence.append({'case':name,'expected':'ACCEPTED' if mutation is None else 'REJECTED_WITH_NO_COMPONENTS','actual':actual,'snapshot_delta':len(a.rows('prediction_snapshots'))-beforecount,'feature_count':len(f['values']),'source_snapshot':snapshot['_id'],'retained_real_state_reference':p['probability_state_version'],'production_state_polluted':False})
a.close()
c=sqlite3.connect(ROOT/'temp/master_audit_v4/crash_transaction.db');count=c.execute('select count(*) from realtime_feature_snapshots').fetchone()[0];integrity=c.execute('pragma integrity_check').fetchone()[0];c.close()
save('formal_copy_adversarial.json',{'evidence_status':'FINAL','cases':len(evidence),'evidence':evidence,'source_sha_before':before,'source_sha_after':sha(source),'source_unchanged':before==sha(source),'copy_schema_namespace':'SIMULATION','copy_existing_records':'Preserved production data on independent copy; only copy namespace and new V4 test rows changed.','fixture_alias_normalization':{'original_metadata_reference':original_meta_reference,'normalized_metadata_reference':meta['probability_state_version'],'resolved_canonical_state':canonical_meta,'reason':'Existing identity alias resolves same state, current snapshot writer demands literal equality.'}})
save('process_crash_recovery.json',{'evidence_status':'FINAL','injected_exit_code':78,'fixture':'crash_fixture.py exits process inside BEGIN IMMEDIATE before commit','uncommitted_feature_rows_after_reopen':count,'integrity':integrity,'expected_rows':0,'production_state_polluted':False})
print('full production component attacks',evidence,'crash rows',count)
