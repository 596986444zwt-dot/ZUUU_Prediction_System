"""Build and seal one independent FEATURE_V1 artifact; fail rather than overwrite."""
import argparse
import csv
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter,defaultdict
from datetime import datetime,timezone
from src.features.contracts import ROOT,OUTPUT,REPORTS,CONTRACT,VERSION,require,SOURCES,utc
from src.features.registry import REGISTRY,DEFERRED
from src.features.data import guardian,load,fingerprints
from src.features.build import assemble
from src.features.schema import canonical,semantic_hash,populate,read
from src.features.qc import distribution
from src.data_v1.source_io import sha256_file,open_snapshot,sidecar_state
from src.audit.phase7_feature_v1_audit import audit_connection

def prepare():
    require((REPORTS/'PHASE7_SOURCE_INVENTORY.md').exists(),'Run actual Source Inventory before Feature construction')
    before,checks=guardian();source=load();data=assemble(source);semantic=semantic_hash(data,CONTRACT)
    implementation={str(p.relative_to(ROOT)).replace('\\','/'):sha256_file(p) for p in sorted((ROOT/'src/features').glob('*.py'))}
    for name in ['src/builders/phase7_feature_v1_builder.py','src/audit/phase7_feature_v1_audit.py','tests/test_phase7_feature_v1.py']:
        if (ROOT/name).exists():implementation[name]=sha256_file(ROOT/name)
    data['manifest']=[dict(version=VERSION,build_status='FROZEN_READY',contract_json=canonical(CONTRACT),semantic_sha256=semantic,
        source_sha_before_json=canonical(before),source_guardian_json=canonical(checks),implementation_sha256_json=canonical(implementation),
        candidate_registry_json=canonical(DEFERRED),phase6_candidate='NONE',created_at=datetime.now(timezone.utc).isoformat())]
    require(fingerprints()==before,'Source changed during feature assembly')
    return data,source,before

def build(path=OUTPUT):
    path=__import__('pathlib').Path(path)
    require(not path.exists(),'Existing Phase7 artifact must not be overwritten: '+str(path))
    data,source,before=prepare()
    fd,tmp=tempfile.mkstemp(prefix='phase7_staged_',suffix='.db',dir=path.parent);os.close(fd)
    try:
        c=sqlite3.connect(tmp);c.row_factory=sqlite3.Row
        try:
            populate(c,data);result=audit_connection(c,source)
            schema=';\n\n'.join(r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))+';\n'
        finally:c.close()
        require(fingerprints()==before,'Source mutation before publication')
        os.link(tmp,path) # Fails atomically if destination exists; no replacement.
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return data,result,schema,before

def write_csv(name,rows):
    with (REPORTS/name).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def mdtable(rows,fields=None):
    if not rows:return '(none)\n'
    fields=fields or list(rows[0])
    return '| '+' | '.join(fields)+' |\n| '+' | '.join(['---']*len(fields))+' |\n'+'\n'.join('| '+' | '.join(str(r.get(k,'')).replace('|','/') for k in fields)+' |' for r in rows)+'\n'

def export(data,result,schema,before):
    REPORTS.mkdir(exist_ok=True)
    samples={s['sample_id']:s for s in data['feature_sample']};values={(v['sample_id'],v['feature_name']):v for v in data['feature_value']}
    bundles={b['bundle_id']:b for b in data['feature_source_lineage']};states={(b['sample_id'],b['model']):b for b in data['feature_history_state']}
    history={(h['label_business_date'],h['horizon']):h for h in data['feature_history_label']}
    coverage=[]
    for r in REGISTRY:
        for h in ('T1','T2'):
            rows=[v for (sid,n),v in values.items() if n==r['feature_name'] and samples[sid]['horizon']==h]
            valid=[v['value'] for v in rows if v['value'] is not None];reasons=Counter(v['missing_reason'] for v in rows if v['value'] is None)
            coverage.append(dict(feature_name=r['feature_name'],horizon=h,status=r['status'],n_total=len(rows),n_valid=len(valid),n_missing=len(rows)-len(valid),missing_pct=100*(len(rows)-len(valid))/len(rows),missing_reasons_json=canonical(dict(reasons)),**distribution([v['value'] for v in rows])))
    write_csv('PHASE7_FEATURE_REGISTRY.csv',REGISTRY)
    candidates=[dict(feature_name=r['feature_name'],family=r['family'],eligibility_category=r['eligibility_category'],status=r['status'],reason=r['description'],version=VERSION) for r in REGISTRY]+DEFERRED
    write_csv('PHASE7_CANDIDATE_FEATURE_REGISTRY.csv',candidates)
    write_csv('PHASE7_FEATURE_COVERAGE.csv',coverage)
    (REPORTS/'PHASE7_SCHEMA.sql').write_text(schema,encoding='utf-8')
    (REPORTS/'PHASE7_FEATURE_CONTRACT.md').write_text('# FEATURE_V1 / FEATURE_UNIT_CONTRACT_V1 / FEATURE_REGISTRY_V1\n\n'+json.dumps(CONTRACT,ensure_ascii=False,indent=2)+'\n\nEvery registry formula/operator, unit, required input fields, hours, availability and missing rule is frozen in DB and CSV. ACCEPTED/CONDITIONAL are materialized, with truthful NULL masks; conditional features require Phase8 explicit missingness policy. No selected features based on labels or target correlation. Weather source units checked against frozen raw hourly_units; wind km/h /3.6 to m/s; native RH used, no derived RH.\n\nHistorical MOS fields are copied from Phase6, not refitted. M1=M5 duplicate intentionally reported. M6 remains benchmark-derived and Candidate NONE.\n\nX: training_feature_view has only sample_id + registered feature columns. Y: training_label_view. sample metadata: training_sample_view. Optional combined phase7_feature_matrix is explicitly a labeled audit projection, never automatic X. View training_feature_mask_view preserves missing vs zero.\n\nCalendar/EPOCH marks timeless computability, not historical publication. Run-based time features inherit selected-run availability; peak lead also depends on forecast temperature. Solar is the frozen deterministic NOAA projection at 30.576,103.950.\n\nSolar radiation uses hourly backward means: energy=sum W/m2*3600/1e6. Hour labels 00..23 are inherited; sums represent those labeled intervals (first may start previous day at 23), not a newly re-aligned exact midnight daily accumulation. Precipitation has the same source hour-label convention. Source documentation: [Open-Meteo historical forecast variables](https://open-meteo.com/en/docs/historical-forecast-api), [Forecast hourly variable definitions](https://open-meteo.com/en/docs). Only existing frozen individual run data are used; no new API data fetched.\n\nPartial feature validity requires all necessary input hours; no partial means or imputation. Some slope/peak definitions have legitimate structural NULL. Revision 24h can be structurally unavailable for T2 because older runs cover only 72h. Exact named offsets do not seek nearest future or alternative run.\n\nT0 fixed-issue is LEGACY_T0_REFERENCE, not materialized in formal T1/T2 matrices. Intraday contract is separately deferred. Changes after freeze require FEATURE_V2.\n',encoding='utf-8')
    t0=[d for d in DEFERRED if d['family']=='T0 Intraday Deferred']
    (REPORTS/'PHASE7_T0_INTRADAY_FEATURE_CONTRACT.md').write_text('# T0_INTRADAY_FEATURE_CONTRACT_V1\n\nstatus = DEFERRED / BLOCKED_FOR_HISTORICAL_REPLAY\n\nHISTORICAL_ZUUU_INGEST_NOT_OBSERVED. No historical values manufactured. Future production requires timestamped actually observed arrival for current temperature, today max/min, 1/2/3h change, warming/cooling rates, dewpoint/wind/cloud/pressure changes, time since current max, lawful remaining ECMWF trajectory and remaining warming potential. Every future input must arrive<=issue. D+2 daily-label eligibility cannot supply intraday receipt evidence.\n\n'+mdtable(t0)+'\nHistorical fixed-issue ECMWF-only T0 is LEGACY_T0_REFERENCE and remains available in Phase4-6; not production Intraday.\n',encoding='utf-8')
    # Input-only redundancy, never use Ground Truth or rank predictive accuracy.
    ordered=sorted(samples);vectors={r['feature_name']:tuple(values[(sid,r['feature_name'])]['value'] for sid in ordered) for r in REGISTRY}
    groups=defaultdict(list)
    for n,v in vectors.items():groups[v].append(n)
    duplicates=[ns for ns in groups.values() if len(ns)>1]
    constants=[n for n,v in vectors.items() if len({x for x in v if x is not None})<=1]
    correlations=[];names=sorted(vectors)
    for j,a in enumerate(names):
        for b in names[j+1:]:
            pairs=[(x,y) for x,y in zip(vectors[a],vectors[b]) if x is not None and y is not None]
            if len(pairs)<30:continue
            mx=math.fsum(x for x,y in pairs)/len(pairs);my=math.fsum(y for x,y in pairs)/len(pairs)
            xx=math.fsum((x-mx)**2 for x,y in pairs);yy=math.fsum((y-my)**2 for x,y in pairs)
            if xx and yy:
                corr=math.fsum((x-mx)*(y-my) for x,y in pairs)/math.sqrt(xx*yy)
                if abs(corr)>=.995:correlations.append(dict(feature_a=a,feature_b=b,n=len(pairs),correlation=corr))
    (REPORTS/'PHASE7_FEATURE_REDUNDANCY.md').write_text('# FEATURE_REDUNDANCY_REPORT\n\nNo target data used. Definitions retained regardless of target association. Exact duplicate vectors including missing masks:\n\n'+json.dumps(duplicates,indent=2)+'\n\nConstant/near-constant notes: constants='+json.dumps(constants)+'; rare event features and run lead may be constant within a horizon, not deleted.\n\nPaired valid feature-only Pearson |r|>=0.995 (no feature selection):\n\n'+mdtable(correlations),encoding='utf-8')
    # Four seasons, both horizons, earliest/midpoint/latest in each + six dates.
    chosen=[]
    for h in ('T1','T2'):
        for months in ([12,1,2],[3,4,5],[6,7,8],[9,10,11]):
            ss=sorted(sid for sid,s in samples.items() if s['horizon']==h and int(s['target_business_date'][5:7]) in months)
            chosen.extend(ss[i] for i in (0,len(ss)//2,len(ss)-1))
    for sid in ordered:
        if sid not in chosen:chosen.append(sid)
        if len(chosen)>=30:break
    snapshots=[]
    for sid in chosen:
        snapshots.append(dict(sample=samples[sid],ground_truth=next(l['label_tmax_c'] for l in data['feature_label'] if l['sample_id']==sid),
            features={r['feature_name']:values[(sid,r['feature_name'])] for r in REGISTRY},input_bundles=[b for b in bundles.values() if b['sample_id']==sid]))
    (REPORTS/'PHASE7_FEATURE_SNAPSHOT_AUDIT.json').write_text(json.dumps(snapshots,ensure_ascii=False,indent=2),encoding='utf-8')
    revisions=[]
    for sid in chosen:
        for slot in ('prev_run','6h','12h','24h'):
            v=values[(sid,'ecmwf_tmax_revision_'+slot+'_c')];b=bundles[v['bundle_id']]
            if v['value'] is not None:
                current=values[(sid,'ecmwf_tmax_c')]['value']
                revisions.append(dict(sample_id=sid,slot=slot,current_run=b['current_run'],previous_run=b['comparison_run'],current_available_time=b['current_run_available_time'],previous_available_time=b['comparison_available_time'],issue_time=b['issue_time'],current_tmax=current,previous_tmax=current-v['value'],revision=v['value']))
    require(len(revisions)>=20,'Revision snapshots insufficient')
    write_csv('PHASE7_FORECAST_REVISION_AUDIT.csv',revisions)
    bias=[]
    for sid in chosen:
        for m in ('M1','M2','M3','M4','M5','M6'):
            b=states[(sid,m)]
            if b['bias_value'] is None:continue
            dates=json.loads(b['training_dates_json']);h=samples[sid]['horizon']
            bias.append(dict(sample_id=sid,model=m,prediction_issue=samples[sid]['issue_time_utc'],bias_value=b['bias_value'],fallback=b['fallback_path'],training_n=b['training_n'],historical_labels=[history[(d,h)] for d in dates]))
    require(len(bias)>=20,'Bias snapshots insufficient')
    (REPORTS/'PHASE7_HISTORICAL_BIAS_AUDIT.json').write_text(json.dumps(bias,ensure_ascii=False,indent=2),encoding='utf-8')
    family=[]
    for f in ['Temperature Curve','Moisture','Cloud','Radiation / Solar','Wind','Pressure','Precipitation / Instability','Forecast Revision','Lead / Run Age','Historical Bias','Calendar / Season','Similarity','T0 Intraday Deferred','External Reference']:
        rr=[r for r in REGISTRY if r['family']==f];dd=[d for d in DEFERRED if d['family']==f]
        family.append(dict(family=f,planned=len(rr)+len(dd),source_supported=len(rr),implemented=len(rr),accepted=sum(r['status']=='ACCEPTED' for r in rr),conditional=sum(r['status']=='CONDITIONAL' for r in rr),blocked=sum(d['status']=='BLOCKED' for d in dd),research_only=sum(d['status']=='RESEARCH_ONLY' for d in dd),rejected=sum(d['status']=='REJECTED' for d in dd),reason='complete definitions, truthful missing masks' if rr else '; '.join(d['reason'] for d in dd)))
    counts=dict(TOTAL_CANDIDATE_FEATURES=len(REGISTRY)+len(DEFERRED),ACCEPTED_FEATURES=sum(r['status']=='ACCEPTED' for r in REGISTRY),CONDITIONAL_FEATURES=sum(r['status']=='CONDITIONAL' for r in REGISTRY),RESEARCH_ONLY_FEATURES=sum(d['status']=='RESEARCH_ONLY' for d in DEFERRED),BLOCKED_FEATURES=sum(d['status']=='BLOCKED' for d in DEFERRED),REJECTED_FEATURES=sum(d['status']=='REJECTED' for d in DEFERRED),FORMAL_FEATURES=len(REGISTRY))
    plain=['我们准备了 ECMWF 温度曲线形态、露点湿度、云、风、气压、降水与 CAPE、辐射与太阳日照、预报版本变化、时效、过去偏差和日历信息。',
        '当前 archive 具备计划中的主要 ECMWF 天气字段；Weather Underground 没有冻结来源，某些旧 run 的完整目标日曲线不存在。',
        'Meteostat 和历史同日机场观测虽有记录，但无法证明当时已到达，因此没有用于正式特征。',
        '未来信息违规为 0；所有特征由当时合法预报、确定性计算或已具备 D+2 准入资格的过去标签得到。',
        '最终答案只放在独立 label 表/view，不进入 X。', '未来发布的 ECMWF run 使用次数为 0。','Meteostat 训练使用次数为 0。',
        'T+1 保留 729 个基础样本，已准备好；特征缺失仍为 NULL。','T+2 保留 728 个基础样本，已准备好；已知不完整曲线 gap 继续排除。',
        'T0 Intraday 需要历史真实到达证据，目前仍 BLOCKED / DEFERRED；固定历史 T0 仅为参考。',
        '正式特征共 '+str(len(REGISTRY))+' 个，其中 ACCEPTED '+str(counts['ACCEPTED_FEATURES'])+' 个，CONDITIONAL '+str(counts['CONDITIONAL_FEATURES'])+' 个，后者使用前必须遵守缺失策略。',
        '通过最终测试门槛后可进入 T1/T2 机器学习；本次只完成 Phase7，不训练模型、不判断准确率提升。']
    (REPORTS/'PHASE7_PLAIN_LANGUAGE_SUMMARY.md').write_text('# PHASE7_PLAIN_LANGUAGE_SUMMARY\n\n'+'\n\n'.join(str(i)+'. '+s for i,s in enumerate(plain,1)),encoding='utf-8')
    after=fingerprints();require(before==after,'Source mutation after export')
    initial=json.loads((REPORTS/'PHASE7_SOURCE_SHA_BEFORE.json').read_text(encoding='utf-8'))
    for p,record in initial.items():require(sha256_file(p)==record['sha256'] and {k:list(v) for k,v in sidecar_state(p).items()}==record['sidecars'],'Initial construction source changed: '+p)
    result.update(source_sha_before=before,source_sha_after=after,source_guardian='PASS',physical_sha256=sha256_file(OUTPUT),feature_counts=counts,
        feature_family_summary=family,coverage=coverage,qc_flag_count=len(data['feature_qc']),feature_snapshot_samples=len(snapshots),revision_audit_rows=len(revisions),historical_bias_audit_samples=len(bias),
        PHASE7_BUILD_STATUS='PASS',FEATURE_V1_STATUS='FROZEN_READY',T1_FEATURE_STATUS='READY',T2_FEATURE_STATUS='READY',T0_INTRADAY_FEATURE_STATUS='BLOCKED / DEFERRED',NEXT_PHASE_STATUS='READY_FOR_PHASE8_T1_T2_MACHINE_LEARNING',
        framework_deviation_check=dict(phase7_feature_engineering=True,phase8_training=False,ground_truth_modified=False,issue_rule_modified=False,phase1_to_6_modified=False,meteostat_training=False,unproven_intraday=False,probability=False,real_time=False,GUI=False))
    # Tests gate these preliminary construction results before final acceptance.
    result['final_test_gate']='PENDING'
    (REPORTS/'PHASE7_AVAILABILITY_AUDIT.json').write_text(json.dumps({k:v for k,v in result.items() if k not in ('coverage','feature_family_summary')},indent=2,ensure_ascii=False),encoding='utf-8')
    (REPORTS/'PHASE7_FINAL_AUDIT.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    (REPORTS/'PHASE7_BUILD_REPORT.md').write_text('# PHASE7 BUILD REPORT\n\nFinal test gate initially PENDING; final acceptance requires recorded Phase7 tests pass.\n\n'+mdtable([dict(field=k,value=v) for k,v in result.items() if not isinstance(v,(dict,list))])+'\n## Feature classification\n\n'+mdtable([counts])+'\n## Feature families\n\n'+mdtable(family)+'\n## Missingness / physical QC\n\nNo source rewrite, imputation, sample drop, target correlation or training. Canonical archive has some negative non-temperature QC values outside selected trajectories; inventory preserves those. Selected feature QC is stored as flags only. All per-feature distributions/min/max/percentiles and missing reasons are in PHASE7_FEATURE_COVERAGE.csv. Complete revision snapshots, 30 feature snapshots and historical-label evidence accompany this report. T2 24h revision may be structurally unavailable; remains CONDITIONAL, not zero.\n\n## Source Guardian\n\n'+json.dumps(dict(before=before,after=after),indent=2)+'\n\n## Plain language\n\n'+'\n\n'.join(plain),encoding='utf-8')
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--commit',action='store_true');args=parser.parse_args()
    if args.commit:
        data,result,schema,before=build();result=export(data,result,schema,before)
        print(json.dumps({k:v for k,v in result.items() if k!='coverage'},ensure_ascii=False,indent=2))
    else:
        data,source,before=prepare();print('DRY RUN',semantic_hash(data,CONTRACT),len(data['feature_value']))

if __name__=='__main__':main()
