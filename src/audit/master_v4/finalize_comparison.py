"""Post-lock historical comparison/publication. Never modifies autonomous files."""
import pathlib,json,hashlib,datetime,collections
ROOT=pathlib.Path(__file__).resolve().parents[3]; D=ROOT/'docs/master_audit_v4';T=ROOT/'temp/master_audit_v4'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(n,v): (D/n).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def md(n,s):(D/n).write_text(s,encoding='utf-8')
lock=read(D/'MASTER_AUDIT_V4_AUTONOMOUS_LOCK.json')
assert lock['MASTER_AUDIT_V4_AUTONOMOUS_FINDINGS_LOCKED']=='YES'
verification={n:{'expected':s,'actual':sha(D/n),'matches':sha(D/n)==s} for n,s in lock['locked_files'].items()}
assert all(x['matches'] for x in verification.values())
aut=read(D/'MASTER_AUDIT_V4_AUTONOMOUS_MANIFEST.json')
assert all(sha(ROOT/p)==s for p,s in aut['final_direct_evidence_sha256'].items())
guardian=read(D/'MASTER_AUDIT_V4_SOURCE_GUARDIAN_AFTER.json')
assert guardian['protected_count']==9692 and guardian['changed_count']==0
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
priorpaths=[
 'docs/master_audit_v1/MASTER_AUDIT_FINDINGS.json',
 'docs/master_audit_v2/MASTER_AUDIT_V2_FINDINGS.json',
 'docs/master_audit_v2/MASTER_AUDIT_V2_REPORT.md',
 'docs/master_audit_v2/MASTER_AUDIT_V2_MANIFEST.json',
 'docs/master_audit_v2/MASTER_AUDIT_V2_TERMINAL_REPORT.txt',
 'docs/master_audit_v2/MA001_NEW_PREDICTION_GUARD_AUDIT.json',
 'docs/master_audit_v3/MASTER_AUDIT_V3_FINDINGS.json',
 'docs/master_audit_v3/MASTER_AUDIT_V3_REPORT.md',
 'docs/master_audit_v3/MA001_NEW_PREDICTION_GUARD_FINAL_V3.json',
 'docs/master_audit_v3/TRIPLE_AUDIT_FINDING_REGISTRY.json',
 'docs/master_audit_v3/AUDITOR_EXCEPTION_001.json',
 'docs/master_audit_v3/AUDITOR_RESUME_RECORD_001.json',
 'docs/master_audit_v3/AUDITOR_EVIDENCE_CORRECTIONS_V3.json',
 'docs/master_audit_v3/SNAPSHOT_IMMUTABILITY_V3.json',
 'docs/phase10/ma_fixes_v1/PATCH_FILE_MANIFEST.json',
 'docs/phase10/ma_fixes_v1/MA_001_MA_002_REPAIR_REPORT.md',
 'src/audit/master_v2/post.py','src/audit/master_v2/adversarial.py','src/audit/master_v2/report_v2.py']
prior=[]
for version,path in [(1,priorpaths[0]),(2,priorpaths[1]),(3,priorpaths[6])]:
    content=read(ROOT/path);items=content['findings'] if isinstance(content,dict) else content
    for x in items:prior.append({'origin_audit':version,'finding':x})
write('CROSS_AUDIT_SOURCE_REGISTRY_V4.json',{'evidence_status':'FINAL','stage':'CROSS_AUDIT_COMPARISON','autonomous_lock_timestamp':lock['AUTONOMOUS_FINDINGS_LOCK_TIMESTAMP'],'historical_reads_authorized_after_lock':True,'sources':[{ 'path':p,'sha256':sha(ROOT/p)} for p in priorpaths],'prior_findings':prior})
matches=[{'v4':'V4-001','prior':['V3-H001'],'scope':'Snapshot business identity and future state cutoff; V4 adds six attack dimensions and two full-component copied attacks.'}, {'v4':'V4-007','prior':['MA-007','V2-W003','V3-M004'],'scope':'Historical availability is estimated, not measured; no confirmed historical future leakage.'}]
new=['V4-002','V4-003','V4-004','V4-005','V4-006','V4-008']
prior_only=[
 {'topic':'MA-001 legacy dangling state reference','ids':['MA-001'],'v4':'Current34 references resolve via formal alias; historical repair report explains prior failure. Not a current V4 first-stage finding.'},
 {'topic':'MA-002 same-hour non-anchor status','ids':['MA-002'],'v4':'Current code uses actual daily anchor. Prior repair and V3 tests report closure; V4 did not independently repeat the16-scenario anchor matrix.'},
 {'topic':'Old manifest test caches','ids':['MA-HASH-3'],'v4':'V4 excludes historical audit/caches. Not reopened as a system bug.'},
 {'topic':'Long-running and real settlement acceptance','ids':['MA-003','V2-W001','V3-M002'],'v4':'V4 explicitly retains PENDING_SOAK and zero production scoring limitation; no separate first-stage finding for this topic.'},
 {'topic':'T2 small-bin calibration/tail skill','ids':['MA-004','V2-W002','V3-M003'],'v4':'Prior bin N11 versus1 hit is a model-skill warning. V4 verifies individual scores, does not independently reproduce these aggregate bins; recorded as prior-only evidence.'},
 {'topic':'Deployment source/network stability','ids':['MA-005','V2-W003','V3-M004'],'v4':'V4 tests mocked timeouts and retains real-network limitation; no deployment Soak claim.'},
 {'topic':'Feature redundancy/near-constant/structural NULL','ids':['MA-006','V2-W004','V3-L001'],'v4':'V4 independently rebuilds all cells and preserves structural NULL; redundant features are not correctness errors.'},
 {'topic':'Off-anchor skill applicability','ids':['V2-W005','V3-L002'],'v4':'V4 retains historical fixed-issue versus runtime transfer limitation; no independently established off-anchor skill.'},
 {'topic':'V2 auditor aggregation contradiction','ids':['V3-M001'],'v4':'CROSS_AUDIT_ONLY. Actual V2 FAIL JSON hash is in final manifest; not ignored as stale evidence.'}]
disagreements=[
 {'topic':'V2 Snapshot Guard PASS/soak YES versus V4 FAIL/NO','status':'DISAGREEMENT_REQUIRES_INVESTIGATION','prior_evidence':['docs/master_audit_v2/MASTER_AUDIT_V2_REPORT.md','docs/master_audit_v2/MA001_NEW_PREDICTION_GUARD_AUDIT.json','docs/master_audit_v2/MASTER_AUDIT_V2_MANIFEST.json'],'v4_evidence':['temp/master_audit_v4/runtime_final/adversarial_cases.json','temp/master_audit_v4/formal_copy_adversarial.json'],'assessment':'V2 summary disagrees with its manifest-enrolled FAIL artifact. V3 and V4 direct attacks reject its broad acceptance claim; no majority vote.'},
 {'topic':'V3 FAILURE_RECOVERY PASS versus V4 state-write and poison-spool failures','status':'DISAGREEMENT_REQUIRES_INVESTIGATION','prior_evidence':['docs/master_audit_v3/MASTER_AUDIT_V3_REPORT.md'],'v4_evidence':['temp/master_audit_v4/runtime_final/adversarial_cases.json','temp/master_audit_v4/extra_faults.json'],'assessment':'Different fault universe: normal replay does not exercise failed state persistence or malformed JSON record recovery. Broad PASS cannot cover these counterexamples; prior cases not rerun.'},
 {'topic':'V3 SNAPSHOT_IMMUTABILITY PASS versus direct SQL REPLACE bypass','status':'DISAGREEMENT_REQUIRES_INVESTIGATION','prior_evidence':['docs/master_audit_v3/SNAPSHOT_IMMUTABILITY_V3.json'],'v4_evidence':['temp/master_audit_v4/runtime_final/adversarial_cases.json'],'assessment':'Old original-row unchanged evidence and UPDATE/DELETE tests do not establish REPLACE rejection. V4 observed referenced ML value change21 to99 only in fixture; no formal occurrence claimed.'}]
patch=read(ROOT/'docs/phase10/ma_fixes_v1/PATCH_FILE_MANIFEST.json');patchmap={r['path']:r['sha256'] for r in patch['files']}
provenance=[]
for r in read(T/'frozen_manifest.json')['errors']:
    provenance.append(dict(r,patch_manifest_sha256=patchmap.get(r['path']),matches_repair_manifest=patchmap.get(r['path'])==r['actual']))
assert len(provenance)==4 and all(r['matches_repair_manifest'] for r in provenance)
v2guard=ROOT/'docs/master_audit_v2/MA001_NEW_PREDICTION_GUARD_AUDIT.json';m=read(ROOT/'docs/master_audit_v2/MASTER_AUDIT_V2_MANIFEST.json')
entries=[x for x in m['files'] if pathlib.Path(x['path']).name==v2guard.name]
assert len(entries)==1
auditor={'v2_guard_actual_status':read(v2guard)['status'],'v2_guard_current_sha256':sha(v2guard),'v2_manifest_recorded_sha256':entries[0]['sha256'],'artifact_matches_final_manifest':entries[0]['sha256']==sha(v2guard),'v2_final_report_claim':'PASS_WITH_WARNINGS','assessment':'Confirmed contradictory enrolled evidence and report. V3 attributes reuse of same reference_guards.db (4 pre-existing components/idempotentFalse) plus report aggregation; V4 independently checks static same-path reuse and final enrolled hash, does not rerun prior auditor. post.py loads counts then supplemental recomputes; precise final execution/overwriting chronology beyond saved evidence UNKNOWN. Do not assert a stale checkpoint as sole proven root cause.'}
write('CROSS_AUDIT_COMPARISON_V4.json',{'evidence_status':'FINAL','stage':'CROSS_AUDIT_COMPARISON','comparison_unit':'unique V4 findings and deduplicated prior topics; not raw report lines','matches':matches,'new_v4':new,'prior_only':prior_only,'disagreements':disagreements,'preexisting_manifest_provenance':provenance,'prior_auditor_integrity':auditor,'counts':{'match':2,'new_v4':6,'prior_only':9,'disagreement':3},'autonomous_findings_unchanged':True})
text='''# MASTER AUDIT V4 — CROSS_AUDIT_COMPARISON

Evidence status: FINAL. All content here was considered only AFTER autonomous findings lock. No first-stage finding, severity, description or PASS/FAIL was changed. Comparison uses evidence, not votes.

Counts: MATCH=2 V4 findings; NEW_V4=6 V4 findings; PRIOR_ONLY=9 deduplicated topics (including warnings already acknowledged as V4 limits); DISAGREEMENT=3 scoped broad-status conflicts. These counts are not additions/subtractions to the autonomous8 findings. Entire V4 result remains FAIL.

## A — Independent V4 findings also found before

V4-001 matches V3-H001: Snapshot can accept wrong horizon/target and registered future cutoff. V4 independently constructed nine minimal illegal combinations and full102-feature cross-horizon/invalid-PMF attacks. Both full attacks succeeded on a separate formal DB copy after equivalent legacy alias normalization; unchanged-in-values control succeeded. Initial literal control failed, retained as auditor artifact. V4 evidence is independent and broader, not a copy of V3 testing.

V4-007 matches MA-007/V2-W003/V3-M004: historical dissemination and observation maturity are estimated rather than measured receive times. This is a DATA LIMITATION, not confirmed future leakage.

## B — V4 topics absent from prior Findings

V4-002 state persistence failure/memory divergence; V4-003 malformed source/spool recovery and valid-tail loss; V4-004 SQL REPLACE bypass; V4-005 timezone relabeling; V4-006 preexisting base manifest mismatch; V4-008 historical native9km versus runtime0.25degree transfer. “New” means absent from read V1–V3 Findings, not proof that no historical file ever mentioned them.

## C — Prior-only topics and classification differences

'''+''.join('- '+r['topic']+' ('+', '.join(r['ids'])+'): '+r['v4']+'\n' for r in prior_only)+'''

## D — Substantive disagreements

'''+''.join('### '+r['topic']+'\n\nDISAGREEMENT_REQUIRES_INVESTIGATION. '+r['assessment']+'\n\nPrior: '+', '.join(r['prior_evidence'])+'. V4: '+', '.join(r['v4_evidence'])+'.\n\n' for r in disagreements)+'''
## E — Historical auditor integrity

V2's MA001_NEW_PREDICTION_GUARD_AUDIT.json says FAIL, half_components_after_rejections=4, valid_snapshot_accepted=false. Its SHA256 matches the V2 final manifest entry, while the final report says PASS_WITH_WARNINGS and soak YES. Contradiction independently verified after lock. V3-M001 describes fixture reuse and aggregation failure. Static V2 code uses the same reference_guards.db path; the4 existing complete rows/idempotentFalse do not prove production partial writes. V4 does NOT execute historical audit code or treat those4 rows as a production bug. The exact overwriting/checkpoint execution history cannot be independently inferred solely from files; post.py actually reruns supplemental, so V3's detailed stale-checkpoint root-cause claim is not independently established here. This uncertainty is confined to comparison, with both evidence sets retained.

V3's preflight encoding/junction errors, event domain/storage ID comparison error and mistaken half-component label are explicitly disclosed in its exception/resume/corrections artifacts. V4 has its own retained exception/resume records, including initial tree NaN semantics, full-copy failed control and final guardian array-wrapper error. Failed artifacts are not the sole basis for any PASS.

## F — Manifest provenance and warnings

All4 V4 baseline Phase10 base-manifest mismatches match PATCH_FILE_MANIFEST.json exactly: archive.py, engine.py, handoff.py and runtimeDB. The historical repair report records these changes for MA-001/MA-002 and preserves the old base manifest. Thus comparison explains recorded provenance; it is not evidence of unauthorized V4 changes. V4-006's locked autonomous UNKNOWN-origin description and MEDIUM classification remain unchanged. Stage2 current-source hashes match the documented patch. Authorization is asserted by that report, not reconstructed from an external approval ledger.

T2 limited-bin skill, source/network stability, off-anchor applicability and pending real Soak remain separate from program correctness. V4 has not independently replicated every historical aggregate or120-day state replay. Absence from V4 Findings is not a declaration that prior warnings are false. No T0/trajectory/Tmax-time capability was invented; all remain deferred/data-blocked.

The machine-readable CROSS_AUDIT_COMPARISON_V4.json contains individual sources/hashes, matches, prior-only topics, disagreements and four provenance edges. SOURCE_GUARDIAN=PASS after final9692-path rescan. Autonomous hashes reverified unchanged.
'''
md('MASTER_AUDIT_V4_CROSS_AUDIT_COMPARISON.md',text)
counts=collections.Counter(x['severity'] for x in read(D/'MASTER_AUDIT_V4_FINDINGS.json')['findings']);assert counts=={'HIGH':3,'MEDIUM':5}
values={
 'MASTER_AUDIT_V4':'FAIL','AUTONOMOUS_FINDINGS_LOCKED':'YES','AUTONOMOUS_FINDINGS_SHA256':lock['AUTONOMOUS_FINDINGS_SHA256'],
 'SOURCE_GUARDIAN':'PASS (9692 paths; 0 changes)','FRAMEWORK_INTEGRITY':'PASS_WITH_WARNINGS',
 'GROUND_TRUTH':'PASS_SCOPED (729 days)','ECMWF_ARCHIVE':'PASS_WITH_WARNINGS','ISSUE_RULE':'PASS (2187 samples)',
 'TIME_CAUSALITY':'FAIL_ARCHIVE_GATE; HISTORICAL_ACTUAL_AVAILABILITY_UNKNOWN','FEATURE_INTEGRITY':'PASS_SCOPED (148614 historical cells; runtime Solar excluded)',
 'MODEL_T1':'PASS (547 independent predictions)','MODEL_T2':'PASS (545 predictions; 43600 tree evaluations)',
 'PROBABILITY':'PASS_SCOPED (24266 PMFs)','PROBABILITY_STATE':'FAIL_STATE_PERSISTENCE_RECOVERY','REALTIME_ENGINE':'FAIL',
 'DATABASE_INTEGRITY':'PHYSICAL_PASS; SEMANTIC_WRITE_GUARD_FAIL','STATE_INTEGRITY':'FAIL','SNAPSHOT_INTEGRITY':'FAIL',
 'PREDICTION_LINEAGE':'EXISTING34_PASS_SCOPED; MATURE_FORWARD_UNKNOWN','SETTLEMENT':'PASS_SCOPED',
 'EVALUATION':'PASS_ISOLATED; PRODUCTION_UNOBSERVED','FAILURE_RECOVERY':'FAIL','FROZEN_ASSET_INTEGRITY':'V4_UNCHANGED; BASE_MANIFEST_DIFFERENCES_EXPLAINED_IN_COMPARISON',
 'T0_FORMAL_MODEL':'DEFERRED / BLOCKED_FOR_DATA','CORRELATED_TRAJECTORY':'BLOCKED_FOR_DATA','TMAX_TIME_PROBABILITY':'BLOCKED_FOR_DATA',
 'CRITICAL_FINDING_COUNT':0,'HIGH_FINDING_COUNT':3,'MEDIUM_FINDING_COUNT':5,'LOW_FINDING_COUNT':0,'AUTONOMOUS_SYSTEM_BUG_COUNT':5,
 'CROSS_AUDIT_MATCH_COUNT':2,'CROSS_AUDIT_NEW_V4_COUNT':6,'CROSS_AUDIT_PRIOR_ONLY_COUNT':9,'CROSS_AUDIT_DISAGREEMENT_COUNT':3,
 'PRODUCTION_FILE_MODIFICATION_COUNT':0,'READY_FOR_FINAL_REPAIR_CONSOLIDATION':'YES','READY_FOR_OPERATIONAL_SOAK':'NO','PHASE10_OPERATIONAL_ACCEPTANCE':'PENDING_SOAK'}
terminal='ZUUU SYSTEM V1\nMASTER AUDIT V4\nFRESH INDEPENDENT AUTONOMOUS AUDIT\n\n'+'\n'.join(k+' = '+str(v) for k,v in values.items())+'\n'
md('MASTER_AUDIT_V4_TERMINAL_REPORT.txt',terminal)
md('MASTER_AUDIT_V4_PLAIN_LANGUAGE_SUMMARY.md','''# 第四轮独立审计结果

证据状态：FINAL。结论：**FAIL**。正式受保护资产9692项，审计期间修改0项。

729天真实温度、148614个历史特征单元、1092次正式模型预测和24266份概率分布独立重算，在报告注明的覆盖范围内没有发现不一致。当前34条正式实时预测也没有发现已发生的未来泄漏或数值污染。数学结果一致，仍不足以证明故障后可以安全运行。

隔离测试确认3个HIGH问题：归档接口接受不相容或未来组件；概率状态写入数据库失败后，内存已推进，普通重试无法补齐；异常气象报文可能让恢复反复阻塞，或丢掉同批有效报文。另有SQL REPLACE绕过不可变性、ECMWF时区改标签问题等MEDIUM发现。共确认5个系统Bug，另3项为数据限制、运维风险或技术债。

自主结果先于历史材料读取锁定，Findings SHA256：`'''+lock['AUTONOMOUS_FINDINGS_SHA256']+'''`。历史对照匹配2项、新V4主题6项、旧审计独有主题9项、范围冲突3项；这些计数不是新增第一阶段Findings。4处旧manifest差异与历史修复清单吻合，解释写入对照报告，冻结的自主Finding未改。

历史真正的到达时间仍无法证明；实时170个Solar特征单元未独立重算；正式评分记录为0；尚未进行真实长期运行。因此不批准进入运行Soak，运行验收保持PENDING_SOAK。T0正式模型、相关轨迹和最高温出现时间概率仍为延期或数据阻塞能力。

全部测试写入隔离目录。审计器自身错误与重跑记录保留。没有修复、训练、校准、切换模型、启动生产Engine/Soak、自启动或Phase11。现在停止，等待负责人审阅。
''')
md('MASTER_AUDIT_V4_REPORT.md','''# ZUUU SYSTEM V1 — MASTER AUDIT V4

Evidence status: FINAL. **MASTER_AUDIT_V4 = FAIL**. Three unresolved HIGH system bugs prevent Phase10 operational acceptance. Final repair consolidation is ready for review; operational soak readiness is NO; actual operational acceptance remains PENDING_SOAK.

The locked autonomous report is MASTER_AUDIT_V4_REPORT_AUTONOMOUS.md. It and MASTER_AUDIT_V4_FINDINGS.json were frozen before historical comparison at '''+lock['AUTONOMOUS_FINDINGS_LOCK_TIMESTAMP']+'''. Findings SHA256: `'''+lock['AUTONOMOUS_FINDINGS_SHA256']+'''`. Final verification confirms all locked document and selected direct-evidence hashes unchanged. No subsequent result edits.

Confirmed findings: HIGH V4-001 Snapshot component/time/math gate; HIGH V4-002 state persistence failure leaves caller memory ahead; HIGH V4-003 malformed METAR handling poisons recovery or drops a valid response tail; MEDIUM V4-004 SQL REPLACE append-only bypass; MEDIUM V4-005 aware forecast timestamp relabeling. MEDIUM V4-006 base-manifest mismatch, V4-007 estimated historical availability and V4-008 source-grid transfer are technical debt/data limitation/operational risk. CRITICAL0, HIGH3, MEDIUM5, LOW0, confirmed system bugs5. No demonstrated existing formal future leakage or numerical contamination.

Independent arithmetic covered17521 raw/Silver weather reports and729 targets,3411 canonical runs/247680 raw hourly rows,2187 issue samples/52478 DATA hourly rows,all102 features×1457 samples=148614cells,395 artifact hashes,237 splits,1185 preprocessing states,547T1 and545T2 predictions,43600 LightGBM tree evaluations,24266 PMFs/72798 individual scoring cells. Final scoped mismatch counts0. Residual2974073, calibration845640 and selection1080000 edges tested against the stated historical contract; actual historic arrival remains UNKNOWN.

Current runtime all34 snapshots independently reconstructed, including34 model/PMF outputs and3298 non-Solar feature cells.170 runtime Solar cells are excluded from independent reconstruction. There are2 incomplete production ground truths/settlements and0 production evaluation rows; complete mature runtime truth-to-score lineage is UNKNOWN. Four isolated settlement scenarios plus independent scoring/dedup do not substitute for real operation.41 recorded adversarial/boundary/fault scenarios include nine illegal minimal Snapshot acceptances, two real-component illegal acceptances, three atomic rollback points, failed state persistence/retry, malformed spool, bounded mocked network timeouts and one abrupt process crash. Counts denote recorded scenarios, not41 distinct attack classes.

Guardian before/after covers9692 protected files (including4 supplemental root entrypoints), SHA256/size/mtime changes0. The final rescan initially had an audit-only nested-array enumeration error; failed output and error retained, corrected all9692-path scan yields0changes. No protected files restored or altered. Immutable reads refused nonempty SQLite WAL/journal. All write fixtures are independent simulation DBs; no production Engine started.

Historical comparison:2 matches,6 new V4 topics,9 deduplicated prior-only topics,3 broad-status disagreements marked DISAGREEMENT_REQUIRES_INVESTIGATION. V3 Snapshot gap matches V4; V4 expands it. V2 final PASS contradicts its own enrolled FAIL Guard JSON; actual artifact hash verified. Exact historical aggregation execution root cause is not fully reconstructed. V3 broad recovery and immutability PASS do not cover V4 failed-persistence, poison-spool and REPLACE counterexamples. See MASTER_AUDIT_V4_CROSS_AUDIT_COMPARISON.md and CROSS_AUDIT_COMPARISON_V4.json for both sides' evidence.

All4 preexisting Phase10 base-manifest mismatches exactly match historical repair PATCH_FILE_MANIFEST hashes. This supplies comparison-stage recorded provenance. The autonomous V4-006 severity and UNKNOWN-origin wording remain frozen. Prior repaired state alias resolves34 current references; alias equivalence is distinct from literal ID equality. Full-copy attack control was normalized only in a simulation fixture to that equivalent alias; original failed control preserved. No production legacy record was changed.

T0 formal model, correlated trajectories and Tmax timing probability remain deferred/data-blocked; scalar T1/T2 correctness does not assert these capabilities exist. Historical availability, unverified aggregate skill/method-membership completeness, runtime astronomy coverage, real mature evaluation and long-running reliability are explicit limitations in MASTER_AUDIT_V4_KNOWN_LIMITATIONS.md. Auditor errors are preserved, with FINAL evidence selected explicitly rather than checkpoint totals. The final manifest labels every artifact FINAL, SUPERSEDED, INTERMEDIATE or FAILED_AUDITOR_ARTIFACT.

Reproduction convenience wrapper: src/audit/master_v4/reproduce_boundary_cases.py creates a fresh directory every invocation. Evidence and independent formula scripts are under temp/master_audit_v4 and src/audit/master_v4; tests/audit_master_v4/README.md gives the configured interpreter invocation. This wrapper is post-lock convenience, not a new first-stage result. No packages installed or official tests modified.

No repairs, retraining, recalibration, model switching/promotion, historical prediction edits, production Engine/Soak, startup installation or Phase11 work. Audit stops at this report for owner review.
''')
write('AUDITOR_RESUME_RECORD_V4_FORMAL_CONTROL.json',{'evidence_status':'FINAL','original_error':'AUDITOR_EXCEPTION_V4_FORMAL_CONTROL.json','modification':'New simulation copy and assert equivalent canonical aliases; normalize fixture metadata state reference only','reason':'Unchanged literal legacy ID failed control, so original full-component attack attribution invalid','production_asset_changes':0,'rerun':'same control plus same2 attacks; all3 accepted','final_evidence':'temp/master_audit_v4/formal_copy_adversarial.json','no_sample_dropped':True})
write('AUDITOR_RESUME_RECORD_V4_PATCH.json',{'evidence_status':'FINAL','original_error':'AUDITOR_EXCEPTION_V4_PATCH.json','modification':'Reapply audit-only patch with exact context before lock','reason':'Patch verifier found no matching line; no partial patch applied','production_asset_changes':0,'rerun':'full-copy fixture and autonomous publication assertions; final hashes frozen'})
write('MASTER_AUDIT_V4_FINAL_LOCK_VERIFICATION.json',{'evidence_status':'FINAL','checked_at':now,'locked_documents':verification,'final_direct_evidence_verified_count':len(aut['final_direct_evidence_sha256']),'all_locked_hashes_match':True,'guardian_checked_at':guardian['checked_at'],'source_modification_count':0})
# Enumerate ONLY V4 output trees. Hashes of old materials reside in the separate source registry.
files=[]
for folder in [D,ROOT/'src/audit/master_v4',T,ROOT/'tests/audit_master_v4']:
    for p in sorted(folder.rglob('*')):
        if not p.is_file() or '__pycache__' in p.parts or p.name=='MASTER_AUDIT_V4_MANIFEST.json':continue
        name=p.name.lower(); status='FINAL'
        if any(x in name for x in ['failed_auditor','failed_control','exception']):status='FAILED_AUDITOR_ARTIFACT'
        elif any(x in name for x in ['superseded','run_1','verification_run_1']):status='SUPERSEDED'
        elif name in ['checkpoint.json','models.json','model_independent_predictions.json','pdf_streams.txt']:status='INTERMEDIATE'
        if name=='formal_snapshot_simulation_copy.db':status='FAILED_AUDITOR_ARTIFACT'
        files.append({'path':p.relative_to(ROOT).as_posix(),'size':p.stat().st_size,'sha256':sha(p),'evidence_status':status,'use_as_sole_pass_basis':False if status!='FINAL' else 'ONLY_WITH_DOCUMENTED_SCOPE'})
write('MASTER_AUDIT_V4_MANIFEST.json',{'evidence_status':'FINAL','created_at':now,'verdict':'FAIL','autonomous_locked':True,'findings_sha256':lock['AUTONOMOUS_FINDINGS_SHA256'],'source_modification_count':0,'source_guardian_protected_count':9692,'findings_counts':dict(counts),'cross_audit_counts':{'match':2,'new_v4':6,'prior_only':9,'disagreement':3},'files':files,'self_hash_excluded':True,'artifact_classification_note':'FAILED and INTERMEDIATE files are retained, never sole PASS evidence; SUPERSEDED initial runs are replaced by named final direct evidence.'})
print(terminal)
print('FINAL_ARTIFACT_COUNT =',len(files))
