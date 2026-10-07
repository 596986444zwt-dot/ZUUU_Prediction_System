from .common import *
from .asset_audit import pdf_text
from .guardian import after

def run():
    pdf=next((ROOT/'docs/architecture').glob('*.pdf'));pages=pdf_text(pdf)
    output('ARCHITECTURE_V1_ORIGINAL_EXTRACT_V3.txt','\n\n'.join('PAGE '+str(i+1)+'\n'+p for i,p in enumerate(pages)))
    entries=[('TARGET',3),('T0/T1/T2',3),('72h',3),('Ground Truth',3),('ECMWF',4),('Meteostat',4),('WU',4),('Solar/Time',4),('Bronze/Silver/Gold',3),('Observation Time',3),('Issue Time',3),('Available Time',3),('Ingest Time',3),('Target Time',3),('Lead Time',3),('immutability',10),('Walk-forward',7),('Phase5',9),('Phase6',9),('Phase7',9),('Phase8',9),('Phase9',9),('Phase10',9),('Probability',6),('T0 Intraday',5),('Prediction Snapshot',8),('Settlement',8),('Evaluation',8),('Champion/Challenger',7),('GUI boundary',9),('Source Health',8),('Lineage',8)]
    output('MASTER_FRAMEWORK_REQUIREMENTS_V3.json',dict(original_pdf=str(pdf),sha256=digest(pdf),requirements=[dict(requirement_id='FW3-'+str(i+1).zfill(3),requirement=k,architecture_page=page,original_page_text=pages[page-1],status='NOT_INDEPENDENTLY_VERIFIED',reason='Frozen interpretation recorded before fresh V3 checks; status updated only with V3 evidence') for i,(k,page) in enumerate(entries)]))
    v1=json.loads((ROOT/'docs/master_audit_v1/MASTER_AUDIT_FINDINGS.json').read_text(encoding='utf-8-sig'));v2=json.loads((ROOT/'docs/master_audit_v2/MASTER_AUDIT_V2_FINDINGS.json').read_text(encoding='utf-8-sig'))
    guard=ROOT/'docs/master_audit_v2/MA001_NEW_PREDICTION_GUARD_AUDIT.json';summary=ROOT/'docs/master_audit_v2/MASTER_AUDIT_V2_FINAL_STATUS.json';mf=json.loads((ROOT/'docs/master_audit_v2/MASTER_AUDIT_V2_MANIFEST.json').read_text(encoding='utf-8-sig'))
    expected=next(x['sha256'] for x in mf['files'] if Path(x['path']).name==guard.name)
    output('PRIOR_AUDIT_FINDING_REGISTRY_V3.json',dict(prior_reports_are_leads_not_proof=True,v1=v1,v2=v2,guard_conflict=dict(artifact=json.loads(guard.read_text(encoding='utf-8-sig')),artifact_sha=digest(guard),manifest_expected=expected,manifest_matches=digest(guard)==expected,final_counts=json.loads(summary.read_text(encoding='utf-8-sig'))['counts']),blockers=['T0','trajectory','Tmax-time','operational Soak pending']))
    output('V3_INPUT_VERSION_LOCK.json',dict(protocol_sha256=digest(OUT/'MASTER_AUDIT_V3_PROTOCOL.md'),before_sha256=digest(OUT/'SOURCE_GUARDIAN_V3_BEFORE.json'),architecture_sha256=digest(pdf),AUDIT_RUN=3,MODE='FINAL_CONFIRMATION_MASTER_AUDIT',SEED=SEED))
    print('Original PDF pages',len(pages),'prior guard artifact',json.loads(guard.read_text(encoding='utf-8-sig'))['status'],'matches V2 final manifest',digest(guard)==expected,flush=True)
    result=after();print('INITIAL_GUARDIAN_CHANGES',result['UPSTREAM_CHANGED_FILE_COUNT'],flush=True)
if __name__=='__main__':run()

