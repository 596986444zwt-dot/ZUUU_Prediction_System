"""Final protected-asset check, raw header proof, completeness and output hashes."""
from .common import *
from .guardian import after
from .database_audit import Source,daily,report_temperature
from collections import defaultdict,Counter

def run():
    final=json.loads((OUT/'MASTER_AUDIT_V2_FINAL_STATUS.json').read_text(encoding='utf-8'));c=final['counts'];s=Source();bad=[];groups=defaultdict(list);classes=Counter()
    try:
        s.load()
        for silver in s.silver:
            raw=s.bronze[silver['bronze_raw_id']];text=raw['raw_metar'];head=text.strip().upper().split()[0]
            classification=head if head in ('METAR','SPECI','COR','AMD') else 'PREFIXLESS' if head=='ZUUU' else 'OTHER'
            classes[classification]+=1
            identity=hashlib.sha256((raw['source'].upper()+'\x1f'+stamp(raw['observation_time_utc']).isoformat()+'\x1f'+text).encode()).hexdigest()
            mismatches=[]
            if stamp(raw['observation_time_utc'])!=stamp(silver['observation_time_utc']):mismatches.append('FULL_BRONZE_SILVER_UTC_TIME')
            if identity!=raw['raw_identity']:mismatches.append('RAW_IDENTITY_HASH')
            if classification!=raw['message_class'] or classification!=silver['message_class']:mismatches.append('LEADING_MESSAGE_CLASS')
            if int(classification=='COR')!=silver['is_correction']:mismatches.append('COR_FLAG')
            row=dict(silver,temperature_c=report_temperature(text),is_correction=int(classification=='COR'),message_class=classification)
            groups[silver['business_date_bjt']].append(row)
            if mismatches:bad.append(dict(raw_id=raw['id'],silver_id=silver['id'],date=silver['business_date_bjt'],raw_message=text,mismatches=mismatches))
        for day,rows in sorted(groups.items()):
            if day not in s.truth:continue  # Declared partial source boundary days are not TARGET_V1.
            rec=daily(rows);saved=s.truth[day]
            mismatch=[k for k,v in rec.items() if v!=(json.loads(saved[k]) if k.endswith('_ids') else saved[k])]
            if mismatch:bad.append(dict(date=day,mismatches=mismatch))
        output('RAW_HEADER_TIME_IDENTITY_AUDIT_V2.json',dict(status='PASS' if not bad else 'FAIL',observations=len(s.silver),days=len(s.truth),non_target_source_boundary_dates=sorted(set(groups)-set(s.truth)),classes=dict(classes),mismatch_count=len(bad),mismatches=bad,cor_precedence='Frozen DAILY_TMAX_RULE_V1 retains all valid versions; no invented supersession. Raw leading classes and COR flags independently reparsed.'))
    finally:s.close()
    c['RAW_HEADER_TIME_IDENTITY_MISMATCH_COUNT']=len(bad)
    assert not bad,'NEW_RAW_HEADER_FINDING_REQUIRES_REPORT_ONLY'
    assert read_json('PARITY_RUN_IDENTITY_V2.json')['mismatch_count']==0
    guardian=after();c['UPSTREAM_CHANGED_FILE_COUNT']=guardian['UPSTREAM_CHANGED_FILE_COUNT'];assert not guardian['UPSTREAM_CHANGED_FILE_COUNT'],'UPSTREAM_MUTATION_REQUIRES_CRITICAL_REPORT'
    final['counts']=c
    for name in ('MASTER_AUDIT_V2_REPORT.md','MASTER_AUDIT_V2_PLAIN_LANGUAGE_SUMMARY.md'):
        body=(OUT/name).read_text(encoding='utf-8')
        if name.endswith('REPORT.md'):body+='\n\n补充复验：17521条Bronze/Silver完整UTC时间、原始identity和报文前缀分类逐条一致；按原始温度和COR前缀再次重建729天。155例历史/实时Run identity也逐条一致。直接实时DB的HTTP400请求记录与后续快照保存于 DEPLOYMENT_NETWORK_EVIDENCE_V2.json；远端具体原因未被证明，不作猜测。\n\nT2的30–40% Top1分箱仅11例，平均宣称32.31%，实际命中1/11（9.09%），作为样本有限的过度自信警告，不能被说成概率已经精确可信。\n\n审计重放遇到既定T2缺口后显式增加NO_FORECAST记录，未补预测或样本。原审计器异常日志保留。\n'
        else:body+='\n\n补充：T2在30–40%最高单项概率区间只有11个例子，平均32.31%但命中1次。这个警告需要后续真实测试关注；不能把“计算正确”理解成“概率一定准确”。\n'
        output(name,body)
    findings=read_json('MASTER_AUDIT_V2_FINDINGS.json')
    for finding in findings['findings']:
        if finding['finding_id']=='V2-W002':
            finding['description']+=' 独立复算T2/ENGINE/TOP1/3：N=11，mean_predicted=0.3231126732853351，actual_frequency=0.09090909090909091。'
        if finding['finding_id']=='V2-W003':finding['evidence'].append('DEPLOYMENT_NETWORK_EVIDENCE_V2.json')
    final['findings']=findings['findings'];output('MASTER_AUDIT_V2_FINDINGS.json',findings);output('MASTER_AUDIT_V2_FINAL_STATUS.json',final)
    term='ZUUU SYSTEM V1 — SECOND ADVERSARIAL MASTER AUDIT\n'+'='*65+'\n'+'\n'.join(k+' = '+str(v) for k,v in final['statuses'].items())+'\n'+'='*65+'\n'+'\n'.join(k+' = '+str(v) for k,v in c.items())
    output('MASTER_AUDIT_V2_TERMINAL_REPORT.txt',term)
    required='MASTER_AUDIT_V2_PROTOCOL.md MASTER_FRAMEWORK_REQUIREMENTS_V2.json MASTER_ASSET_INVENTORY_V2.json MASTER_ASSET_INVENTORY_V2.csv SOURCE_GUARDIAN_V2_BEFORE.json SOURCE_GUARDIAN_V2_AFTER.json DATABASE_INTEGRITY_AUDIT_V2.json PHASE1_GROUND_TRUTH_AUDIT_V2.json PHASE2_ECMWF_ARCHIVE_AUDIT_V2.json ISSUE_RULE_REPRODUCTION_V2.json PHASE3_AUXILIARY_AUDIT_V2.json PHASE4_DATA_V1_AUDIT_V2.json PHASE5_BASELINE_REPRODUCTION_V2.json PHASE6_MOS_AUDIT_V2.json PHASE7_FEATURE_AUDIT_V2.json PHASE8_MODEL_REPRODUCTION_V2.json PHASE9_PROBABILITY_AUDIT_V2.json MA001_ALIAS_INDEPENDENT_AUDIT.json MA001_STATE_CONTAMINATION_AUDIT.json MA002_ANCHOR_STATE_MACHINE_AUDIT.json PHASE10_REALTIME_AUDIT_V2.json FORWARD_STATE_AUDIT_V2.json HISTORICAL_REALTIME_PARITY_V2.json END_TO_END_REPRODUCTION_V2.json PREDICTION_LINEAGE_V2.json SNAPSHOT_IMMUTABILITY_V2.json SETTLEMENT_EVALUATION_V2.json FAILURE_RECOVERY_V2.json ADVERSARIAL_SAMPLE_REGISTRY.json AUDIT_V1_V2_COMPARISON.json MASTER_AUDIT_V2_FINDINGS.json MASTER_AUDIT_V2_KNOWN_LIMITATIONS.md MASTER_AUDIT_V2_REPORT.md MASTER_AUDIT_V2_PLAIN_LANGUAGE_SUMMARY.md MASTER_AUDIT_V2_TERMINAL_REPORT.txt MASTER_AUDIT_V2_MANIFEST.json'.split()
    assert all((OUT/name).is_file() for name in required)
    rows=[]
    for base in (OUT,ROOT/'src/audit/master_v2',ROOT/'tests/audit_master_v2'):
        for path in sorted(base.rglob('*')):
            if path.is_file() and path.name!='MASTER_AUDIT_V2_MANIFEST.json':rows.append(dict(path=str(path),size=path.stat().st_size,sha256=digest(path)))
    output('MASTER_AUDIT_V2_MANIFEST.json',dict(audit_run=2,seed=SEED,master_status=final['statuses']['MASTER_AUDIT_V2'],protocol_sha256=digest(OUT/'MASTER_AUDIT_V2_PROTOCOL.md'),files=rows,excluded='manifest itself; isolated temporary DBs; stdout of this finalization is not redirected to an inventoried active log'))
    print(term,flush=True)
    print('REQUIRED_OUTPUT_FILES =',len(required),'AUDIT_MANIFEST_FILES =',len(rows),flush=True)

def read_json(name):return json.loads((OUT/name).read_text(encoding='utf-8-sig'))
if __name__=='__main__':run()
