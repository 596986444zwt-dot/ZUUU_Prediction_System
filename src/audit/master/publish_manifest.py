"""Publish final audit file hashes after the audit execution log stops changing."""
from .common import *
def publish():
    final=json.loads((OUT/'MASTER_AUDIT_FINDINGS.json').read_text(encoding='utf-8'))
    paths=sorted((ROOT/'src/audit/master').glob('*.py'))+sorted((ROOT/'tests/master_audit').glob('*.py'))+[x for x in sorted(OUT.iterdir()) if x.is_file() and x.name!='MASTER_AUDIT_MANIFEST.json']
    output('MASTER_AUDIT_MANIFEST.json',dict(created_or_modified_files=[dict(path=str(x),sha256=digest(x),size=x.stat().st_size) for x in paths],protocol_sha256=digest(OUT/'MASTER_AUDIT_PROTOCOL.md'),self_hash='excluded to avoid self-reference',MASTER_ACCEPTANCE=final['MASTER_ACCEPTANCE']))
    missing=[]
    required=['MASTER_AUDIT_PROTOCOL.md','MASTER_FRAMEWORK_REQUIREMENTS.json','MASTER_ASSET_INVENTORY.json','MASTER_ASSET_INVENTORY.csv','MASTER_SOURCE_GUARDIAN_BEFORE.json','MASTER_SOURCE_GUARDIAN_AFTER.json','MASTER_DATABASE_INTEGRITY.csv','MASTER_STATIC_SCAN.csv','MASTER_PHASE1_GROUND_TRUTH_AUDIT.csv','MASTER_PHASE2_ECMWF_AUDIT.csv','MASTER_PHASE4_SAMPLE_AUDIT.csv','MASTER_PHASE6_CAUSALITY_AUDIT.csv','MASTER_FEATURE_REGISTRY_AUDIT.csv','MASTER_FEATURE_REPRODUCTION.csv','MASTER_MODEL_REPRODUCTION.csv','MASTER_PROBABILITY_REPRODUCTION.csv','MASTER_PROBABILITY_MATH_AUDIT.csv','MASTER_PHASE10_REALTIME_AUDIT.csv','MASTER_FORWARD_STATE_AUDIT.csv','MASTER_REALTIME_APPLICABILITY_AUDIT.csv','MASTER_HISTORICAL_REALTIME_PARITY.csv','MASTER_END_TO_END_REPRODUCTION.csv','MASTER_LINEAGE_AUDIT.csv','MASTER_SETTLEMENT_EVALUATION_AUDIT.csv','MASTER_BLOCKER_AUDIT.md','MASTER_KNOWN_LIMITATIONS.md','MASTER_AUDIT_FINDINGS.json','MASTER_AUDIT_REPORT.md','MASTER_AUDIT_PLAIN_LANGUAGE_SUMMARY.md','MASTER_AUDIT_TERMINAL_REPORT.txt','MASTER_AUDIT_MANIFEST.json']
    for name in required:
        if not (OUT/name).is_file():missing.append(name)
    if missing:raise RuntimeError('MISSING_REQUIRED_REPORTS: '+str(missing))
    print((OUT/'MASTER_AUDIT_TERMINAL_REPORT.txt').read_text(encoding='utf-8'))
    print('REPORT_DIRECTORY = '+str(OUT));print('REQUIRED_REPORTS_PRESENT = '+str(len(required)));print('MANIFEST_SHA256 = '+digest(OUT/'MASTER_AUDIT_MANIFEST.json'))
if __name__=='__main__':publish()
