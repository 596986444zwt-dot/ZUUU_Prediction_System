"""Independent serialization of declared semantic projections. No production hash call."""
import ast
import hashlib
import json
from .common import *

def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def literal(path,name):
    for n in ast.parse((ROOT/path).read_text(encoding='utf-8')).body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets):return ast.literal_eval(n.value)
    raise ValueError((path,name))
def stream(c,prefix,keys,contract=None,exclude=()):
    h=hashlib.sha256()
    if contract is not None:h.update((canonical(contract)+'\n').encode())
    for table,key in keys.items():
        if table=='manifest':continue
        h.update((table+'\n').encode())
        for row in c.execute('SELECT * FROM '+prefix+table+' ORDER BY '+','.join(key)):
            h.update((canonical({k:v for k,v in dict(row).items() if k not in exclude})+'\n').encode())
    return h.hexdigest()

def audit_hashes(s):
    before=json.loads((OUT/'MASTER_SOURCE_GUARDIAN_BEFORE.json').read_text(encoding='utf-8'))['assets'];by={r['relative_path']:r for r in before};rows=[];sem={}
    c=s.get('zuuu_prediction.db')
    keys=('business_date_bjt','daily_tmax_c','first_tmax_time_bjt','last_tmax_time_bjt','tmax_occurrence_count','observation_count','hourly_coverage_count','has_correction','tmax_has_correction','has_recovery','tmax_has_recovery','tmax_silver_ids','tmax_bronze_raw_ids','all_silver_ids','all_bronze_raw_ids','rule_version','target_version')
    target=[{k:json.loads(r[k]) if k.endswith('_ids') else r[k] for k in keys} for r in sorted(s.truth.values(),key=lambda r:r['business_date_bjt'])]
    sem['TARGET_V1']=hashlib.sha256(canonical(target).encode()).hexdigest()
    archive_fields=('run_time_utc','canonical_raw_run_id','model','api_model','model_cycle','archive_type','data_spec_version','collector_version','source_available_time_utc','availability_type','availability_rule_version','requested_latitude','requested_longitude','content_sha256','raw_snapshot_count','distinct_content_count','hourly_row_count','temperature_valid_count','temperature_null_count','canonical_status','availability_semantics')
    sem['ECMWF_ARCHIVE']=hashlib.sha256(canonical([{k:r[k] for k in archive_fields} for r in sorted(s.runs,key=lambda r:r['run_time_utc'])]).encode()).hexdigest()
    issue=records(c,'ecmwf_issue_rule_v1')[0]
    payload=next(v for v in issue.values() if isinstance(v,str) and v.startswith('{'))
    sem['ISSUE_RULE']=hashlib.sha256(payload.encode()).hexdigest()
    c4=s.get('phase4_data_v1.db');keys4=literal('src/data_v1/schema.py','KEYS')
    sem['phase4_data_v1.db']=stream(c4,'phase4_data_v1_',dict(sorted(keys4.items())),{'dataset':'DATA_V1','contract':'PHASE4_DATA_CONTRACT_V1','meteostat_training_admission':'BLOCKED'},('source_target_id','source_archive_id','source_raw_run_id','source_hourly_id','source_provenance_json','created_at_utc','ingest_time'))
    for name,prefix,schema in [('phase5_ecmwf_raw_baseline_v1.db','phase5_baseline_v1_','src/baseline/storage.py'),('phase6_statistical_mos_v1_review.db','phase6_mos_','src/mos/schema.py')]:
        db=s.get(name);keys=literal(schema,'KEYS');manifest=records(db,prefix+'manifest')[0];contract=json.loads(manifest['contract_json']);projection={'contract':contract}
        for table,key in keys.items():
            if table=='manifest':continue
            projection[table]=[{k:v for k,v in dict(r).items() if k not in ('created_at','created_at_utc')} for r in db.execute('SELECT * FROM '+prefix+table+' ORDER BY '+','.join(key))]
        sem[name]=stream(db,prefix,keys,contract,('created_at',)) if name.startswith('phase6') else hashlib.sha256(canonical(projection).encode()).hexdigest()
    db=s.get('phase7_feature_v1.db');manifest=records(db,'phase7_manifest')[0]
    sem['phase7_feature_v1.db']=stream(db,'phase7_',literal('src/features/schema.py','KEYS'),json.loads(manifest['contract_json']),('created_at',))
    for name,prefix,path in [('phase8_machine_learning_v1.db','phase8_','src/models/phase8/storage.py'),('phase9_probability_v1.db','phase9_','src/probability/storage.py')]:
        db=s.get(name);h=hashlib.sha256()
        for t in literal(path,'TABLES'):
            if t=='manifest':continue
            h.update((t+'\n').encode())
            for r in db.execute('SELECT * FROM '+prefix+t+' ORDER BY record_id'):
                d=dict(r)
                if 'payload_json' in d:d=json.loads(d['payload_json'])
                if prefix=='phase8_' and t=='candidate':d['qualified']=bool(d['qualified'])
                d={k:v.hex() if isinstance(v,bytes) else v for k,v in d.items() if k not in ('fit_seconds','artifact_sha256')}
                h.update((canonical(d)+'\n').encode())
        sem[name]=h.hexdigest()
    db=s.get('phase10_realtime_v1.db');h=hashlib.sha256()
    for t in literal('src/realtime/archive.py','TABLES'):
        h.update((t+'\n').encode())
        for r in db.execute('SELECT record_id,payload_json FROM realtime_'+t+' ORDER BY record_id'):h.update((r[0]+'\n'+r[1]+'\n').encode())
    sem['phase10_realtime_v1.db']=h.hexdigest()
    expected={'TARGET_V1':'b8548609e64d10b787fc09d9fddb28e20acd1808b165f47e62b574580cc67f3e','ECMWF_ARCHIVE':'d98fe65fc17948b8418c69247c2d714b255f815e36958645076d61403a282ac7','ISSUE_RULE':'7fdbf14f0f105870f06e9e4d29753718d8cfebfe98d7608c9f916ea441455504','phase4_data_v1.db':'4eea080168f102f30314b0b8a64123af533923fd7f4b7e7c6e9312b5582835dd','phase5_ecmwf_raw_baseline_v1.db':'4f31fa80c704eed5100b1add4d37cebf120b1e56fe1c4e2ba65a140d9d8fd353','phase6_statistical_mos_v1_review.db':'43ea07f9579a617c3117efbf8d93ab7ee5fe57d5e2ebf443ea12068171834415','phase7_feature_v1.db':'5de92e79d6c073b16024c46114ec9027cc1c0597239f57ee0364baedbff20c6e','phase8_machine_learning_v1.db':'b7d10f09efb029c01552f4a5dceef954df49d712bb0e6cb1aaed159a6230cf16','phase9_probability_v1.db':'8a8dd519750716536f5a66e97684d83604929afc7c3658135886099ead089a66','phase10_realtime_v1.db':'0bafb5464b1f5652a4ae7cdd8cd809a31b3fb0396bb69dddc7d3e05efefe7bc5'}
    for name,value in sem.items():rows.append(dict(asset=name,kind='SEMANTIC',actual=value,expected=expected[name],status='PASS' if value==expected[name] else 'FAIL'))
    for p in (ROOT/'docs').rglob('*MANIFEST*.json'):
        if 'master_audit' in str(p):continue
        try:document=json.loads(p.read_text(encoding='utf-8'))
        except (ValueError,UnicodeError):continue
        def walk(obj):
            if not isinstance(obj,dict):return
            for k,v in obj.items():
                if isinstance(v,dict):walk(v)
                elif isinstance(v,str) and len(v)==64 and all(x in '0123456789abcdef' for x in v) and ('/' in k or '\\' in k):
                    asset=ROOT/k;actual=digest(asset) if asset.is_file() else None
                    rows.append(dict(asset=k,manifest=str(p.relative_to(ROOT)),kind='PHYSICAL_FILE',actual=actual,expected=v,status='PASS' if actual==v else 'FAIL'))
        walk(document)
    for row in rows:
        if row['asset'].startswith('.pytest_cache/') or '__pycache__' in row['asset']:
            row['asset_role']='EPHEMERAL_TEST_CACHE_NOT_FROZEN_PREDICTION_ASSET'
        else:row['asset_role']='FORMAL_SEMANTIC_OR_MANIFEST_ASSET'
    s.counts['HASH_MISMATCH_COUNT']=sum(r['status']=='FAIL' and r['asset_role']=='FORMAL_SEMANTIC_OR_MANIFEST_ASSET' for r in rows);s.evidence['hashes']=dict(semantic=sem,manifest_comparisons=len(rows),mismatches=[r for r in rows if r['status']=='FAIL'])
    output('MASTER_HASH_COMPARISON.csv',rows)
    for r in before:
        if Path(r['path']).name in sem:r['semantic_sha']=sem[Path(r['path']).name]
        elif Path(r['path']).name=='zuuu_prediction.db':r['semantic_sha']={k:sem[k] for k in ('TARGET_V1','ECMWF_ARCHIVE','ISSUE_RULE')}
    output('MASTER_ASSET_INVENTORY.json',before);output('MASTER_ASSET_INVENTORY.csv',before)
    return rows
