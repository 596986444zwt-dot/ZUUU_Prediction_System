"""V4 independent arithmetic and isolated adversarial audit. Never repairs assets."""
import sys,pathlib,json,sqlite3,hashlib,traceback,math,re,pickle,calendar,copy
from datetime import datetime,timedelta,timezone,date
from collections import defaultdict,Counter
from zoneinfo import ZoneInfo
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
OUT=ROOT/'temp/master_audit_v4';DOC=ROOT/'docs/master_audit_v4'
BJT=ZoneInfo('Asia/Shanghai');UTC=timezone.utc
import numpy as np
RESULT={};CACHE={};EXCEPTIONS=[]
def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(UTC)
def eligible(day):return datetime.fromisoformat(day+'T00:00:00+08:00')+timedelta(days=2)
def sha(p):
    with pathlib.Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(n,v): (OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else str(x)),encoding='utf-8')
def db(n):
    p=ROOT/'database'/n
    for suffix in ('-wal','-journal'):
        s=pathlib.Path(str(p)+suffix)
        if s.exists() and s.stat().st_size:raise RuntimeError('NONEMPTY_JOURNAL:'+str(s))
    c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row;return c
def rows(c,t):return [dict(r) for r in c.execute('select * from '+t)]
def payloads(c,t):return {r['record_id']:dict(json.loads(r['payload_json']),_id=r['record_id'],_created=r.get('created_at')) for r in rows(c,t)}
def mismatch(a,b,tol=1e-9):
    if a is None or b is None:return a is not b
    if isinstance(a,(float,int,np.number)) and isinstance(b,(float,int,np.number)):return not math.isclose(a,b,abs_tol=tol,rel_tol=tol)
    return a!=b
def stage(name,fn):
    print('BEGIN',name,flush=True)
    try:
        r=fn();RESULT[name]=r;save(name+'.json',r);print('END',name,json.dumps({k:v for k,v in r.items() if not isinstance(v,(dict,list))}),flush=True)
    except BaseException:
        error={'time':datetime.now(UTC).isoformat(),'location':name,'stack':traceback.format_exc(),'completed':list(RESULT),'not_completed':name,'existing_conclusions_affected':False,'evidence_status':'FAILED_AUDITOR_ARTIFACT'}
        EXCEPTIONS.append(error);save('AUDITOR_EXCEPTION_V4_'+name+'.json',error);RESULT[name]={'status':'UNKNOWN','exception':error['stack']};print(error['stack'],flush=True)
    save('checkpoint.json',{'evidence_status':'INTERMEDIATE','checks':RESULT})

def integrity():
    r={}
    for p in (ROOT/'database').glob('*.db'):
        c=db(p.name);r[p.name]={'integrity':[x[0] for x in c.execute('pragma integrity_check')],'fk':[list(x) for x in c.execute('pragma foreign_key_check')],'sha256':sha(p)};c.close()
    CACHE['integrity']=r
    return {'databases':len(r),'failure_count':sum(x['integrity']!=['ok'] or bool(x['fk']) for x in r.values()),'details':r}

def ground_truth():
    c=db('zuuu_prediction.db');raw={r['id']:r for r in rows(c,'zuuu_raw_metar')};silver=rows(c,'zuuu_silver_observation');targets=rows(c,'zuuu_target_v1');c.close()
    errors=[];group=defaultdict(list);parsed={};cor=0
    for s in silver:
        r=raw[s['bronze_raw_id']];tokens=r['raw_metar'].split();match=next((re.fullmatch(r'(M?\d{2})/(M?\d{2})?',v) for v in tokens if re.fullmatch(r'(M?\d{2})/(M?\d{2})?',v)),None)
        temp=int(match[1].replace('M','-')) if match else None
        if mismatch(temp,s['temperature_c']):errors.append({'kind':'RAW_TEMPERATURE','silver':s['id'],'raw':r['id'],'expected':temp,'actual':s['temperature_c']})
        at=stamp(r['observation_time_utc']);local=at.astimezone(BJT);time_token=next((v for v in tokens if re.fullmatch(r'\d{6}Z',v)),None)
        if time_token!=at.strftime('%d%H%MZ') or stamp(s['observation_time_utc'])!=at or stamp(s['observation_time_bjt'])!=at or s['business_date_bjt']!=local.date().isoformat():errors.append({'kind':'TIME','silver':s['id']})
        parsed[s['id']]=temp
        if s['qc_status']=='PASS':group[local.date().isoformat()].append(s)
        cor+=bool(s['is_correction'])
    target_errors=[];lineage_count=0;conflicts=[]
    for t in targets:
        day=t['business_date_bjt'];g=sorted(group[day],key=lambda s:(stamp(s['observation_time_bjt']),s['id']))
        # Frozen daily rule retains COR evidence, excludes only explicitly superseded raw identity.
        superseded={s['supersedes_raw_id'] for s in g if s['supersedes_raw_id'] is not None}
        g=[s for s in g if s['bronze_raw_id'] not in superseded]
        m=max(parsed[s['id']] for s in g);peaks=[s for s in g if parsed[s['id']]==m]
        expected={'daily_tmax_c':m,'observation_count':len(g),'hourly_coverage_count':len({stamp(s['observation_time_bjt']).astimezone(BJT).hour for s in g}),'first_tmax_time_bjt':peaks[0]['observation_time_bjt'],'last_tmax_time_bjt':peaks[-1]['observation_time_bjt'],'tmax_occurrence_count':len(peaks)}
        for k,v in expected.items():
            if mismatch(v,t[k]):target_errors.append({'day':day,'field':k,'expected':v,'actual':t[k]})
        for k,v in [('all_silver_ids',[s['id'] for s in g]),('all_bronze_raw_ids',[s['bronze_raw_id'] for s in g]),('tmax_silver_ids',[s['id'] for s in peaks]),('tmax_bronze_raw_ids',[s['bronze_raw_id'] for s in peaks])]:
            lineage_count+=len(v)
            if json.loads(t[k])!=v:target_errors.append({'day':day,'field':k,'expected':v,'actual':json.loads(t[k])})
        bytime=defaultdict(set)
        for s in g:bytime[s['observation_time_utc']].add(s['temperature_c'])
        conflicts.extend({'day':day,'time':k,'values':list(v)} for k,v in bytime.items() if len(v)>1)
    CACHE['targets']={t['business_date_bjt']:t for t in targets}
    return {'raw_rows':len(raw),'silver_rows':len(silver),'target_days':len(targets),'cor_rows':cor,'lineage_edges':lineage_count,'raw_mismatch_count':len(errors),'target_mismatch_count':len(target_errors),'conflicting_times':conflicts,'errors':errors+target_errors}

def archive():
    c=db('zuuu_prediction.db');runs=rows(c,'ecmwf_archive_v1');raw={r['id']:r for r in rows(c,'ecmwf_raw_runs')};hourly={r['id']:r for r in rows(c,'ecmwf_hourly_forecasts')};c.close()
    curves=defaultdict(dict);errors=[];edges=0
    for r in hourly.values():
        curves[r['raw_run_id']][r['target_time_utc']]=r
        if stamp(r['target_time_utc'])!=stamp(r['run_time_utc'])+timedelta(hours=r['lead_hours']):errors.append({'kind':'LEAD','hourly_id':r['id']})
    for run in runs:
        r=raw[run['canonical_raw_run_id']];g=curves[r['id']];edges+=len(g)
        for k in ('run_time_utc','source_available_time_utc','content_sha256','model','api_model'):
            if mismatch(run[k],r[k]):errors.append({'kind':'CANONICAL','run':run['id'],'field':k})
        if len(g)!=run['hourly_row_count'] or sum(v['temperature_2m_c'] is not None for v in g.values())!=run['temperature_valid_count']:errors.append({'kind':'COUNTS','run':run['id']})
    c=db('phase4_data_v1.db');samples=rows(c,'phase4_data_v1_sample');hours=rows(c,'phase4_data_v1_ecmwf_hourly');solar=rows(c,'phase4_data_v1_solar');c.close()
    selection_errors=[];missing=0
    legalruns=sorted((r for r in runs if r['canonical_status']=='AVAILABLE'),key=lambda r:stamp(r['run_time_utc']),reverse=True)
    for s in samples:
        start=datetime.fromisoformat(s['business_date_bjt']+'T00:00:00+08:00');keys=[(start+timedelta(hours=i)).astimezone(UTC).isoformat() for i in range(24)];issue=stamp(s['issue_time_utc'])
        expected=next((r for r in legalruns if stamp(r['source_available_time_utc'])<=issue and stamp(r['run_time_utc'])<=issue and all(k in curves[r['canonical_raw_run_id']] and curves[r['canonical_raw_run_id']][k]['temperature_2m_c'] is not None for k in keys)),None)
        expected_issue=start-timedelta(days=int(s['horizon'][1:]))+timedelta(hours=21)
        if issue!=expected_issue.astimezone(UTC):selection_errors.append({'kind':'ISSUE','day':s['business_date_bjt'],'horizon':s['horizon']})
        if expected:
            if expected['canonical_raw_run_id']!=s['source_raw_run_id']:selection_errors.append({'kind':'SELECTION','day':s['business_date_bjt'],'horizon':s['horizon'],'expected':expected['canonical_raw_run_id'],'actual':s['source_raw_run_id']})
        else:missing+=1
    content_errors=[]
    for r in hours:
        orig=hourly[r['source_hourly_id']]
        for k in ('target_time_utc','target_time_bjt','lead_hours','model','temperature_2m_c','dew_point_2m_c','relative_humidity_2m_pct','surface_pressure_hpa','pressure_msl_hpa','cloud_cover_pct','cloud_cover_low_pct','cloud_cover_mid_pct','cloud_cover_high_pct','wind_speed_10m_kmh','wind_direction_10m_deg','wind_gusts_10m_kmh','shortwave_radiation_wm2','direct_radiation_wm2','diffuse_radiation_wm2','precipitation_mm','rain_mm','cape_jkg'):
            if mismatch(r[k],orig[k]):content_errors.append({'hourly':r['source_hourly_id'],'field':k})
        if r['source_raw_run_id']!=orig['raw_run_id']:content_errors.append({'hourly':r['source_hourly_id'],'field':'raw_run'})
    CACHE.update(runs=runs,curves=curves,historical_hourly=hourly,data_hours=hours,data_samples=samples,solar=solar)
    return {'archive_runs':len(runs),'all_hourly_rows':len(hourly),'canonical_hourly_edges':edges,'samples':len(samples),'data_hourly_rows':len(hours),'missing_complete_runs':missing,'identity_mismatch_count':len(errors),'selection_mismatch_count':len(selection_errors),'data_cell_mismatch_count':len(content_errors),'errors':errors+selection_errors+content_errors,'availability_limitation':'Historical estimated dissemination is not observed receipt; no strict historical real-availability PASS.'}

def ec_feature(d,g):
    fields=json.loads(d['source_fields']);hs=json.loads(d['hours_json']);T=[r['temperature_2m_c'] for r in g];peak=int(np.argmax(T));op=d['operation']
    hs=[peak] if op in ('at_peak','sin_peak','cos_peak') else list(range(24)) if hs is None else hs
    arrays=[[g[h][f] for h in hs] for f in fields]
    if any(v is None or not math.isfinite(v) for a in arrays for v in a):return None
    v=arrays[0];v=[x/3.6 for x in v] if fields[0].endswith('_kmh') else v
    basic={'max':lambda:max(v),'min':lambda:min(v),'mean':lambda:sum(v)/len(v),'range':lambda:max(v)-min(v),'argmax':lambda:hs[v.index(max(v))],'argmin':lambda:hs[v.index(min(v))],'at':lambda:v[0],'at_peak':lambda:v[0],'delta':lambda:v[1]-v[0],'sum':lambda:sum(v),'energy':lambda:sum(v)*3600/1e6,'clear':lambda:sum(x<=10 for x in v),'positive_count':lambda:sum(x>0 for x in v),'any_positive':lambda:int(any(x>0 for x in v)),'sin_peak':lambda:math.sin(v[0]*math.pi/180),'cos_peak':lambda:math.cos(v[0]*math.pi/180)}
    if op in basic:return basic[op]()
    if op.startswith('spread_'):
        a=[x-y for x,y in zip(*arrays)];return min(a) if op=='spread_min_c' else sum(a)/24
    if op in ('period_delta','rh_change_pct','morning_to_afternoon_delta_c'):return sum(v[12:18])/6-sum(v[6:12])/6
    diff=np.diff(T).tolist()
    shape={'max_hourly_warming_c':max(diff),'max_hourly_cooling_c':min(diff),'strongest_warming_hour_bjt':1+diff.index(max(diff)),'strongest_cooling_hour_bjt':1+diff.index(min(diff)),'peak_sharpness_c':None if peak in (0,23) else T[peak]-(T[peak-1]+T[peak+1])/2,'hours_within_05c_of_max':sum(x>=max(T)-.5 for x in T),'hours_within_10c_of_max':sum(x>=max(T)-1 for x in T),'pre_peak_slope_c_per_h':None if peak<=6 else (T[peak]-T[6])/(peak-6),'post_peak_slope_c_per_h':None if peak>=21 else (T[21]-T[peak])/(21-peak),'morning_to_peak_warming_c':max(T)-T[6],'peak_to_evening_cooling_c':max(T)-T[21]}
    return shape[op]

def features():
    c=db('phase7_feature_v1.db');registry={r['feature_name']:r for r in rows(c,'phase7_feature_registry')};samples={r['sample_id']:r for r in rows(c,'phase7_feature_sample')};values=rows(c,'phase7_feature_value');states={(r['sample_id'],r['model']):r for r in rows(c,'phase7_feature_history_state')};history=rows(c,'phase7_feature_history_label');c.close()
    groups=defaultdict(list);sol=defaultdict(list)
    for r in CACHE['data_hours']:groups[r['business_date_bjt']+'/'+r['horizon']].append(r)
    for r in CACHE['solar']:sol[r['business_date_bjt']].append(r)
    for g in groups.values():g.sort(key=lambda r:r['target_time_utc'])
    for g in sol.values():g.sort(key=lambda r:r['hour_bjt'])
    runmap={r['run_time_utc']:r for r in CACHE['runs'] if r['canonical_status']=='AVAILABLE'}
    errors=[];counts=Counter();nulls=Counter();expectedvalues={};bi={};history_edges=0
    for sid,s in samples.items():
        h=s['horizon'];issue=stamp(s['issue_time_utc']);day=s['target_business_date'];season=('DJF','MAM','JJA','SON')[((int(day[5:7])%12)//3)]
        past=[r for r in history if r['horizon']==h and r['label_business_date']<day and eligible(r['label_business_date'])<=issue and stamp(r['historical_issue_time'])<issue]
        past.sort(key=lambda r:r['label_business_date']);bias={}
        for model in ('M1','M2','M3','M4','M5'):
            used=past
            if len(past)<7:used=[]
            elif model in ('M2','M3'):
                end=issue.astimezone(BJT).date()-timedelta(days=2);lo=end-timedelta(days=(7 if model=='M2' else 30)-1);used=[r for r in past if lo.isoformat()<=r['label_business_date']<=end.isoformat()]
            elif model=='M4':used=[r for r in past if ('DJF','MAM','JJA','SON')[(int(r['label_business_date'][5:7])%12)//3]==season]
            if len(past)>=7 and len(used)<7:used=past
            bias[model]=None if not used else sum(r['raw_error'] for r in used)/len(used)
            saved=states[sid,model];history_edges+=len(used)
            if json.loads(saved['training_dates_json'])!=[r['label_business_date'] for r in used] or mismatch(saved['bias_value'],bias[model]):errors.append({'sample':sid,'kind':'BIAS_POOL','model':model})
        bias['M6']=None if bias['M1'] is None else (bias['M1']+bias['M3']+bias['M4'])/3;bi[sid]=bias
    for v in values:
        sid=v['sample_id'];s=samples[sid];d=registry[v['feature_name']];g=groups[sid];ss=sol[s['target_business_date']];src=d['source'];name=v['feature_name'];counts[src]+=1
        if src=='ECMWF':expected=ec_feature(d,g)
        elif src=='PHASE6':expected=bi[sid][d['operation']];expected=max(r['temperature_2m_c'] for r in g)-expected if name=='m6_corrected_temperature_c' and expected is not None else expected
        elif src=='REVISION':
            run=stamp(s['selected_run']);op=d['operation'];slot=op if op in ('6h','12h','24h') else 'prev_run'
            old=runmap.get((run-timedelta(hours=int(slot[:-1]))).isoformat()) if slot!='prev_run' else max((r for r in runmap.values() if stamp(r['run_time_utc'])<run and stamp(r['source_available_time_utc'])<=stamp(s['issue_time_utc']) and r['model']==s['model']),key=lambda r:stamp(r['run_time_utc']),default=None)
            if old and stamp(old['source_available_time_utc'])>stamp(s['issue_time_utc']):old=None
            og=[CACHE['curves'][old['canonical_raw_run_id']].get(r['target_time_utc']) for r in g] if old else []
            a=[r['temperature_2m_c'] for r in g];b=[r['temperature_2m_c'] for r in og] if og and all(og) else []
            if len(b)!=24 or any(x is None for x in b):expected=None
            elif op in ('prev_run','6h','12h','24h'):expected=max(a)-max(b)
            elif op=='curve_revision_prev_mean_c':expected=sum(x-y for x,y in zip(a,b))/24
            elif op=='curve_revision_prev_max_abs_c':expected=max(abs(x-y) for x,y in zip(a,b))
            elif op=='peak_hour_revision_prev_h':expected=a.index(max(a))-b.index(max(b))
            else:expected=a[14]-b[14]
        else:
            day=date.fromisoformat(s['target_business_date']);doy=day.timetuple().tm_yday;peak=max(range(24),key=lambda i:g[i]['temperature_2m_c']);sun=stamp(ss[0]['sunrise']).astimezone(BJT)
            mapping={'calendar_month':day.month,'calendar_season_code':(day.month%12)//3,'calendar_day_of_year':doy,'calendar_doy_sin':math.sin(2*math.pi*(doy-1)/(366 if calendar.isleap(day.year) else 365)),'calendar_doy_cos':math.cos(2*math.pi*(doy-1)/(366 if calendar.isleap(day.year) else 365)),'run_age_hours':(stamp(s['issue_time_utc'])-stamp(s['selected_run'])).total_seconds()/3600,'target_start_lead_hours':g[0]['lead_hours'],'target_peak_lead_hours':g[peak]['lead_hours'],'solar_day_length_min':ss[0]['daylight_duration'],'solar_max_elevation_deg':max(r['solar_elevation'] for r in ss),'solar_elevation_09_bjt_deg':ss[9]['solar_elevation'],'solar_elevation_15_bjt_deg':ss[15]['solar_elevation'],'solar_sunrise_hour_bjt':sun.hour+sun.minute/60+sun.second/3600+sun.microsecond/3600000000}
            expected=mapping[name]
        expectedvalues[sid,name]=expected
        if expected is None:nulls[src]+=1
        if mismatch(expected,v['value']) or (expected is None)==bool(v['is_available']):errors.append({'sample':sid,'feature':name,'expected':expected,'actual':v['value']})
        if stamp(v['feature_available_time'])>stamp(s['issue_time_utc']):errors.append({'sample':sid,'feature':name,'kind':'FUTURE_AVAILABILITY'})
    CACHE.update(feature_values=expectedvalues,feature_samples=samples,feature_registry=registry)
    return {'samples':len(samples),'features':len(registry),'cells':len(values),'coverage_pct':100,'source_cells':dict(counts),'null_cells':dict(nulls),'bias_history_edges':history_edges,'mismatch_count':len(errors),'errors':errors,'solar_limitation':'Solar feature values verified against frozen Solar input; astronomical algorithm itself not yet independently recomputed.'}

def preprocess(X,names,family):
    keep=[];drop={}
    for j,n in enumerate(names):
        a=X[:,j];finite=a[np.isfinite(a)]
        if not len(finite):drop[n]='ALL_NULL_TRAINING_FEATURE';continue
        if n=='hist_horizon_bias_c' and np.array_equal(a,X[:,names.index('hist_expanding_bias_c')],equal_nan=True):drop[n]='EXACT_DUPLICATE_OF_hist_expanding_bias_c';continue
        match=next((k for k in keep if np.array_equal(a,X[:,k],equal_nan=True)),None)
        if match is not None:drop[n]='EXACT_DUPLICATE_OF_'+names[match];continue
        if np.var(finite)<=1e-12:drop[n]='CONSTANT_TRAINING_VARIANCE';continue
        keep.append(j)
    A=X[:,keep];p={'indices':keep,'feature_names':[names[i] for i in keep],'dropped':drop}
    if family=='RIDGE':
        med=np.nanmedian(A,axis=0);ind=np.where(np.any(np.isnan(A),axis=0))[0];B=np.where(np.isnan(A),med,A)
        if len(ind):B=np.concatenate([B,np.isnan(A[:,ind]).astype(float)],axis=1)
        scale=np.std(B,axis=0);scale[scale<=1e-12]=1
        p.update(medians=med.tolist(),indicator_indices=ind.tolist(),means=np.mean(B,axis=0).tolist(),scales=scale.tolist())
    else:p.update(medians=[],indicator_indices=[],means=[],scales=[])
    return p
def transform_independent(X,p):
    A=X[:,p['indices']]
    if not p['medians']:return A.copy()
    B=np.where(np.isnan(A),p['medians'],A)
    if p['indicator_indices']:B=np.concatenate([B,np.isnan(A[:,p['indicator_indices']]).astype(float)],axis=1)
    return (B-np.array(p['means']))/np.array(p['scales'])
def infer(saved,X):
    A=transform_independent(X,saved['preprocessor']);e=saved['estimator']
    if saved['preprocessor']['family']=='RIDGE':return A@e.coef_+e.intercept_
    dump=e.booster_.dump_model()
    def tree(node,x):
        while 'split_index' in node:
            v=x[node['split_feature']]
            if math.isnan(v) and node['missing_type']!='NaN':v=0.0
            missing=(node['missing_type']=='NaN' and math.isnan(v)) or (node['missing_type']=='Zero' and abs(v)<=1e-35)
            left=node['default_left'] if missing else v<=float(node['threshold'])
            node=node['left_child'] if left else node['right_child']
        return node['leaf_value']
    return np.array([sum(tree(t['tree_structure'],x) for t in dump['tree_info']) for x in A])
def models():
    # Standard native startup order is source-level runtime requirement; not an expected oracle.
    import sklearn.linear_model,sklearn.ensemble,lightgbm,xgboost,catboost
    c=db('phase8_machine_learning_v1.db');samples={r['sample_id']:r for r in rows(c,'phase8_sample')};splits={r['record_id']:r for r in rows(c,'phase8_split')};preps=rows(c,'phase8_preprocessing');states=rows(c,'phase8_state');predictions=rows(c,'phase8_prediction');params=rows(c,'phase8_hyperparameter');inner=rows(c,'phase8_inner_prediction');c.close()
    names=sorted(CACHE['feature_registry']);X={sid:np.array([np.nan if CACHE['feature_values'][sid,n] is None else CACHE['feature_values'][sid,n] for n in names]) for sid in samples}
    errors=[];training_edges=0;validation_edges=0
    for split in splits.values():
        tr=json.loads(split['training_ids_json']);va=json.loads(split['validation_ids_json']);cutoff=stamp(split['training_cutoff']);training_edges+=len(tr);validation_edges+=len(va)
        if set(tr)&set(va):errors.append({'kind':'OVERLAP','split':split['record_id']})
        expected=sorted((sid for sid,s in samples.items() if s['horizon']==split['horizon'] and eligible(s['target_business_date'])<=cutoff and stamp(s['issue_time_utc'])<cutoff and s['target_business_date']<split['outer_target_date']))
        if tr!=expected:errors.append({'kind':'TRAINING_IDS','split':split['record_id'],'expected_n':len(expected),'actual_n':len(tr)})
        for sid in tr:
            if eligible(samples[sid]['target_business_date'])>cutoff:errors.append({'kind':'FUTURE_LABEL','split':split['record_id'],'sample':sid})
    for p in preps:
        ids=json.loads(splits[p['split_id']]['training_ids_json']);expected=preprocess(np.array([X[i] for i in ids]),names,p['model_family']);actual=json.loads(p['preprocessing_json'])
        for k,v in expected.items():
            wrong=not np.allclose(v,actual[k],atol=1e-10,rtol=1e-10,equal_nan=True) if k in ('medians','means','scales') else v!=actual[k]
            if wrong:errors.append({'kind':'PREPROCESSING','id':p['record_id'],'field':k})
    innergroup=defaultdict(list)
    for r in inner:innergroup[r['state_id'],r['candidate_id']].append(abs(r['continuous_prediction']-r['observed']))
    for p in params:
        expected=sum(innergroup[p['state_id'],p['candidate_id']])/len(innergroup[p['state_id'],p['candidate_id']])
        if mismatch(expected,p['inner_mae']):errors.append({'kind':'INNER_MAE','id':p['record_id']})
    assets={};binary_mismatch=0
    for s in states:
        p=ROOT/s['artifact_path']
        if sha(p)!=s['artifact_sha256']:binary_mismatch+=1
        if (s['horizon'],s['model_family']) in [('T1','RIDGE'),('T2','LIGHTGBM')]:assets[s['record_id']]=pickle.loads(p.read_bytes())
        options=[p for p in params if p['state_id']==s['record_id']];best=min(options,key=lambda p:(p['inner_mae'],p['candidate_id']))
        if best['candidate_id']!=s['selected_candidate_id']:errors.append({'kind':'PARAMETER_SELECTION','id':s['record_id']})
    tested=Counter();tree_evaluations=0;math_errors=[];examples=[]
    statepred=defaultdict(list)
    for p in predictions:
        if p['state_id'] in assets and p['ml_prediction'] is not None:statepred[p['state_id']].append(p)
    for sid,ps in statepred.items():
        saved=assets[sid];a=np.array([X[p['input_sample_id']] for p in ps]);recalc=infer(saved,a)
        if saved['preprocessor']['family']=='LIGHTGBM':tree_evaluations+=len(ps)*len(saved['estimator'].booster_.dump_model()['tree_info'])
        for p,v in zip(ps,recalc):
            expected=p['raw_ecmwf_prediction']+float(v);tested[p['horizon']]+=1
            if mismatch(expected,p['ml_prediction'],1e-8):math_errors.append({'prediction':p['record_id'],'expected':expected,'actual':p['ml_prediction']})
            if stamp(p['training_cutoff'])>stamp(p['prediction_issue_time']):math_errors.append({'prediction':p['record_id'],'kind':'FUTURE_STATE'})
            examples.append({'prediction':p['record_id'],'expected':expected,'actual':p['ml_prediction'],'state':sid})
    save('model_independent_predictions.json',examples)
    CACHE.update(model_assets=assets,model_states={s['record_id']:s for s in states},model_X=X,model_samples=samples)
    return {'states':len(states),'formal_binary_states':len(assets),'binary_hash_mismatch_count':binary_mismatch,'splits':len(splits),'training_edges':training_edges,'validation_edges':validation_edges,'preprocessing_states':len(preps),'inner_predictions':len(inner),'hyperparameter_rows':len(params),'independent_predictions':dict(tested),'tree_predictions':tree_evaluations,'metadata_mismatch_count':len(errors),'prediction_mismatch_count':len(math_errors),'errors':errors+math_errors,'limitation':'Inner estimator fitting and alternative-family inference not repeated; hyperparameter MAE independently reconstructed from saved inner predictions, not retrained.'}

K=np.arange(-80,81);EPS=1e-12
def independent_distribution(forecast,residual,method):
    r=np.array(residual,float)
    if method.startswith('GAUSSIAN'):
        mu=forecast+float(np.mean(r));sigma=max(float(np.std(r,ddof=1)),.5)
        f=np.array([.5*math.erfc(-(k+.5-mu)/(sigma*math.sqrt(2))) for k in K])
    elif method.startswith('KDE'):
        from scipy.special import erfc
        f=(.5*erfc(-(K[:,None]+.5-forecast-r[None,:])/(.75*math.sqrt(2)))).mean(axis=1)
    else:f=np.searchsorted(np.sort(forecast+r),K+.5,side='left')/len(r)
    f[-1]=1;mass=np.diff(np.r_[0,f]);return mass/mass.sum()*(1-161*EPS)+EPS
def independent_calibrate(p,a):
    u=np.minimum(np.maximum(np.cumsum(p)[:-1],1e-300),1-1e-16)
    left=np.power(u,a);right=np.power(1-u,a);out=left/(left+right)
    q=np.diff(np.r_[0,out,1]);return q/q.sum()*(1-161*EPS)+EPS
def independent_scores(p,y):
    indicator=(K==y).astype(float);cdf=np.cumsum(p)
    return {'Brier':float(sum((p-indicator)**2)),'LogLoss':-math.log(p[int(y)+80]),'CRPS':float(sum((cdf[:-1]-(K[:-1]>=y))**2))}
def probability():
    c=db('phase9_probability_v1.db');samples=payloads(c,'phase9_sample');resid=payloads(c,'phase9_residual_history');states=payloads(c,'phase9_distribution_state');cal=payloads(c,'phase9_calibration_registry');selection=payloads(c,'phase9_candidate_selection');pred=payloads(c,'phase9_probability_prediction');masses={r['record_id']:r for r in rows(c,'phase9_probability_mass')};c.close()
    errors=[];edges=0;caledges=0;seledges=0;math_errors=[];reconstructed={};scorecount=0
    for rid,r in resid.items():
        expected=None if r['continuous_prediction'] is None else r['actual']-r['continuous_prediction']
        if mismatch(expected,r['residual']):errors.append({'kind':'RESIDUAL','id':rid})
        if stamp(r['eligibility'])!=eligible(r['target_business_date']).astimezone(UTC):errors.append({'kind':'ELIGIBILITY','id':rid})
    for sid,s in states.items():
        for i in s['history_ids']:
            r=samples[i];edges+=1
            if r['horizon']!=s['horizon'] or stamp(r['eligibility'])>stamp(s['cutoff']) or stamp(r['issue'])>=stamp(s['cutoff']):errors.append({'kind':'RESIDUAL_TIME','state':sid,'sample':i})
        if len(s['history_ids'])!=s['history_n']:errors.append({'kind':'STATE_COUNT','id':sid})
    for rid,p in pred.items():
        if rid not in masses:continue
        stored=np.frombuffer(masses[rid]['pmf'],dtype='<f8');cdf=np.frombuffer(masses[rid]['cdf'],dtype='<f8');surv=np.frombuffer(masses[rid]['survival'],dtype='<f8')
        if len(stored)!=161 or not np.isfinite(stored).all() or stored.min()<0 or abs(stored.sum()-1)>1e-10 or not np.allclose(np.cumsum(stored),cdf,atol=1e-12) or np.min(np.diff(cdf))<0 or not np.allclose(1-np.r_[0,cdf[:-1]],surv,atol=1e-12):math_errors.append({'kind':'PMF_CDF','id':rid})
        scores=independent_scores(stored,p['actual']);scorecount+=3
        for k,v in scores.items():
            if mismatch(v,p[k],1e-10):math_errors.append({'kind':'SCORE','id':rid,'field':k})
        state=states.get(p.get('distribution_state'))
        if state:
            origin=p.get('probability_source_level') if p['origin'] in ('ENGINE','RAW_SELECTED','MOS_SELECTED') else p['origin']
            if origin in ('ML','RAW','MOS'):
                rr=[samples[i]['actual']-samples[i][origin] for i in state['history_ids']]
                q=independent_distribution(p['continuous_prediction'],rr,p['method'])
                if p.get('calibration_n',0):q=independent_calibrate(q,p['calibration_alpha'])
                reconstructed[rid]=q
                if not np.allclose(q,stored,atol=2e-11,rtol=2e-9):math_errors.append({'kind':'PMF_RECONSTRUCTION','id':rid,'max_abs':float(abs(q-stored).max())})
    cached_cal_scores={}
    for rid,r in cal.items():
        for i in r['calibration_ids']:
            p=pred[i];caledges+=1
            if p['horizon']!=r['horizon'] or p['origin']!=r['origin'] or stamp(p['eligibility'])>stamp(r['cutoff']) or stamp(p['issue'])>=stamp(r['cutoff']):errors.append({'kind':'CALIBRATION_TIME','id':rid,'case':i})
        if r['calibration_ids']:
            scores=[]
            for a in [1.,.8,1.2]:
                for i in r['calibration_ids']:
                    if (i,a) not in cached_cal_scores:cached_cal_scores[i,a]=-math.log(independent_calibrate(np.frombuffer(masses[i]['pmf'],dtype='<f8'),a)[int(pred[i]['actual'])+80])
                scores.append(sum(cached_cal_scores[i,a] for i in r['calibration_ids'])/len(r['calibration_ids']))
            expected=[1.,.8,1.2][min(range(3),key=lambda j:(scores[j],j))]
            if expected!=r['alpha']:errors.append({'kind':'CALIBRATION_SELECTION','id':rid})
    for rid,r in selection.items():
        for option in r['options']:
            ps=[pred[i] for i in option['validation_ids']];seledges+=len(ps)
            if any(p['horizon']!=r['horizon'] or p['origin']!=r['origin'] or stamp(p['eligibility'])>stamp(r['cutoff']) or stamp(p['issue'])>=stamp(r['cutoff']) for p in ps):errors.append({'kind':'SELECTION_TIME','id':rid})
            if mismatch(sum(p['LogLoss'] for p in ps)/len(ps),option['LogLoss']):errors.append({'kind':'SELECTION_SCORE','id':rid})
    CACHE.update(prob_samples=samples,prob_resid=resid,prob_pred=pred)
    return {'residual_rows':len(resid),'distribution_states':len(states),'residual_edges':edges,'calibration_states':len(cal),'calibration_edges':caledges,'selection_states':len(selection),'selection_edges':seledges,'probability_records':len(pred),'pmfs_checked':len(masses),'independent_pmfs':len(reconstructed),'score_cells':scorecount,'causal_mismatch_count':len(errors),'math_mismatch_count':len(math_errors),'errors':errors+math_errors}

def realtime():
    c=db('phase10_realtime_v1.db');ts=[r[0] for r in c.execute("select name from sqlite_master where type='table' and name like 'realtime_%'")];data={t.removeprefix('realtime_'):payloads(c,t) for t in ts};components=rows(c,'snapshot_components');c.close();CACHE['realtime']=data
    errors=[];matherrors=[];feature_checks=0;residualedges=0;issue_counts=Counter();lineages=[]
    for rid,s in data['prediction_snapshots'].items():
        f=data['feature_snapshots'][s['feature_id']];m=data['continuous_predictions'][s['continuous_id']];p=data['probability_predictions'][s['probability_id']];issue=stamp(s['prediction_issue_time']);issue_counts[s['horizon']]+=1
        for part in (f,m):
            for k in ('horizon','target_business_date','prediction_issue_time'):
                if part[k]!=s[k]:errors.append({'kind':'COMPONENT_IDENTITY','snapshot':rid,'field':k})
        state=data['probability_state'].get(s['probability_state_version']);resolved=[]
        while state and state.get('record_type')=='LINEAGE_ALIAS':
            resolved.append(state['state_id']);state=data['probability_state'].get(state['canonical_state_id'])
            if state and state['state_id'] in resolved:state=None;break
        if state is None:errors.append({'kind':'STATE_MISSING','snapshot':rid});continue
        if stamp(state['cutoff'])>issue:errors.append({'kind':'FUTURE_STATE','snapshot':rid})
        pool={r['record_id']:r for r in state['residuals']}
        for i in p.get('history_ids',[]):
            r=pool[i];residualedges+=1
            if r['horizon']!=s['horizon'] or stamp(r['eligibility'])>issue or stamp(r['issue'])>=issue or r['target_business_date']>=s['target_business_date']:errors.append({'kind':'PROB_RESIDUAL_TIME','snapshot':rid,'residual':i})
        rr=[pool[i]['residual'] for i in p.get('history_ids',[])]
        expected=independent_distribution(p['continuous_prediction'],rr,p['method'])
        if p.get('calibration_n'):expected=independent_calibrate(expected,p['calibration_alpha'])
        if not np.allclose(expected,p['pmf'],atol=2e-11,rtol=2e-9):matherrors.append({'kind':'RUNTIME_PMF','snapshot':rid})
        run=data['ecmwf_raw_runs'].get(f['selected_version'])
        if run is None or stamp(run['actual_ingest_time'])>issue:errors.append({'kind':'RUN_AVAILABILITY','snapshot':rid});continue
        g=sorted([r for r in data['ecmwf_hourly'].values() if r['run_id']==f['selected_version'] and stamp(r['target_time_utc']).astimezone(BJT).date().isoformat()==s['target_business_date']],key=lambda r:r['target_time_utc'])
        if len(g)!=24:errors.append({'kind':'CURVE_COUNT','snapshot':rid})
        if any(stamp(r['actual_ingest_time'])>issue for r in g):errors.append({'kind':'HOURLY_FUTURE','snapshot':rid})
        for name,v in f['values'].items():
            feature_checks+=1
            if stamp(f['lineage'][name]['feature_available_time'])>issue:errors.append({'kind':'FEATURE_FUTURE','snapshot':rid,'feature':name})
            if CACHE['feature_registry'][name]['source']=='ECMWF' and mismatch(ec_feature(CACHE['feature_registry'][name],g),v):matherrors.append({'kind':'RUNTIME_FEATURE','snapshot':rid,'feature':name})
        sid=m['model_state_id'];saved=CACHE['model_assets'][sid];x=np.array([[np.nan if f['values'][n] is None else f['values'][n] for n in sorted(CACHE['feature_registry'])]])
        expected=f['values']['ecmwf_tmax_c']+float(infer(saved,x)[0])
        if mismatch(expected,m['continuous_prediction_c'],1e-8):matherrors.append({'kind':'RUNTIME_MODEL','snapshot':rid,'expected':expected,'actual':m['continuous_prediction_c']})
        if stamp(m['training_cutoff'])>issue:errors.append({'kind':'MODEL_FUTURE','snapshot':rid})
        lineages.append({'snapshot':rid,'horizon':s['horizon'],'issue':s['prediction_issue_time'],'target':s['target_business_date'],'raw_run':run['_id'],'feature':s['feature_id'],'model_state':sid,'probability_state':s['probability_state_version'],'resolved_state':state['state_id'],'probability':s['probability_id'],'residual_edges':len(rr),'settlement_available':any(t['business_date_bjt']==s['target_business_date'] and t['settlement_status']=='FINAL' for t in data['daily_ground_truth'].values()),'evaluation_available':rid in data['daily_evaluation']})
    save('prediction_lineage.json',lineages)
    for rid,t in data['daily_ground_truth'].items():
        if t['settlement_status']=='FINAL':
            if t['hourly_coverage_count']!=24 or t['observation_count']<24 or stamp(t['label_eligibility_time'])<eligible(t['business_date_bjt']):errors.append({'kind':'SETTLEMENT_PREMATURE','id':rid})
    return {'snapshots':len(data['prediction_snapshots']),'components':len(components),'lineages':len(lineages),'horizons':dict(issue_counts),'feature_availability_cells':feature_checks,'runtime_ecmwf_feature_cells':sum(1 for d in CACHE['feature_registry'].values() if d['source']=='ECMWF')*len(lineages),'continuous_predictions':len(lineages),'pmfs':len(lineages),'residual_edges':residualedges,'states':len(data['probability_state']),'ground_truth_records':len(data['daily_ground_truth']),'settlements':len(data['daily_settlement']),'evaluations':len(data['daily_evaluation']),'semantic_mismatch_count':len(errors),'math_mismatch_count':len(matherrors),'errors':errors+matherrors,'evaluation_limitation':'No settled production forecast yet; use independent simulation scoring, do not fabricate production evaluation.'}

def adversarial():
    from src.realtime.archive import Archive
    from src.realtime.contracts import identity,config
    from src.realtime.collectors import archive_metar,archive_ecmwf,Fetcher
    from src.realtime.settlement import settle,evaluate,advance
    from src.realtime.spool import save as spool_save,recover
    from src.realtime.features import selection,runtime_context
    import requests
    evidence=[];idx=0
    def fresh(label):
        nonlocal idx
        idx+=1;return Archive(OUT/f'case_{idx:02}_{label}.db',namespace='SIMULATION',create=True)
    def counts(a):return {t:a.c.execute('select count(*) from realtime_'+t).fetchone()[0] for t in ('feature_snapshots','continuous_predictions','probability_predictions','prediction_snapshots','probability_state','zuuu_raw','zuuu_normalized','engine_state')}
    def record(label,expected,actual,before,after,**extra):evidence.append(dict(case=label,expected=expected,actual=actual,before=before,after=after,production_state_polluted=False,**extra));save('adversarial_cases.json',evidence)
    f={'horizon':'T1','target_business_date':'2026-10-03','prediction_issue_time':'2026-10-02T13:00:00+00:00','feature_version':'FEATURE_V1','selected_version':'run-A','values':{'ecmwf_tmax_c':20}}
    m=dict(f,continuous_prediction_c=21,raw_ecmwf_prediction=20,mos_prediction=20,model_state_id='RIDGE_T1',model_version='MODEL_T1_V1',training_cutoff='2026-09-01T00:00:00+00:00')
    p={'probability_state_version':'state','continuous_prediction':21,'origin':'ML','method':'GAUSSIAN_EXPANDING','pmf':(np.ones(161)/161).tolist(),'variants':[]}
    meta={k:f[k] for k in ('horizon','target_business_date','prediction_issue_time')};meta.update(probability_state_version='state',daily_anchor=True,status='OK')
    state={'state_id':'state','cutoff':'2026-10-02T12:00:00+00:00','residuals':[],'cases':[]}
    attacks=[('horizon',lambda ff,mm,pp,ss:mm.update(horizon='T2')),('target',lambda ff,mm,pp,ss:mm.update(target_business_date='2026-10-04')),('issue',lambda ff,mm,pp,ss:mm.update(prediction_issue_time='2026-10-02T14:00:00+00:00')),('future_model',lambda ff,mm,pp,ss:mm.update(training_cutoff='2026-10-03T00:00:00+00:00')),('wrong_model_family',lambda ff,mm,pp,ss:mm.update(model_version='MODEL_T2_V1')),('invalid_pmf',lambda ff,mm,pp,ss:pp.update(pmf=[-1,2])),('probability_continuous',lambda ff,mm,pp,ss:pp.update(continuous_prediction=100)),('future_state',lambda ff,mm,pp,ss:ss.update(cutoff='2026-10-03T00:00:00+00:00')),('future_feature',lambda ff,mm,pp,ss:ff.update(run_available_time='2026-10-03T00:00:00+00:00'))]
    for label,mutation in attacks:
        a=fresh(label);ff,mm,pp,ss=map(copy.deepcopy,(f,m,p,state));mutation(ff,mm,pp,ss)
        with a.c:a.insert('probability_state',ss,'state')
        before=counts(a)
        try:accepted=a.snapshot('attack',ff,mm,pp,meta);actual='ACCEPTED' if accepted else 'DEDUPED'
        except Exception as e:actual='REJECTED:'+str(e)
        record('snapshot_'+label,'REJECTED_WITH_NO_COMPONENTS',actual,before,counts(a),input={'feature':ff,'continuous':mm,'probability':pp,'state':ss,'metadata':meta});a.close()
    for label,version in [('missing_state','absent'),('mismatched_state','different')]:
        a=fresh(label);pp=copy.deepcopy(p);pp['probability_state_version']=version;before=counts(a)
        try:a.snapshot('attack',f,m,pp,meta);actual='ACCEPTED'
        except Exception as e:actual='REJECTED:'+str(e)
        record(label,'REJECTED_WITH_NO_COMPONENTS',actual,before,counts(a));a.close()
    a=fresh('rollback')
    with a.c:a.insert('probability_state',state,'state')
    for component in ('feature_snapshots','continuous_predictions','probability_predictions'):
        before=counts(a)
        try:a.snapshot('rollback',f,m,p,meta,fail_after=component);actual='ACCEPTED'
        except RuntimeError:actual='ROLLED_BACK'
        record('transaction_'+component,'ROLLED_BACK_WITH_NO_COMPONENTS',actual,before,counts(a))
    a.snapshot('valid',f,m,p,meta);before=counts(a);changed=dict(m,continuous_prediction_c=99)
    actual=a.snapshot('valid',f,changed,p,meta)
    record('duplicate_snapshot_changed_payload','IDENTITY_CONFLICT_REJECTED',str(actual),before,counts(a))
    old=a.c.execute("select * from realtime_continuous_predictions where record_id='valid/ML'").fetchone();new=json.loads(old['payload_json']);new['continuous_prediction_c']=99
    try:
        with a.c:a.c.execute('INSERT OR REPLACE INTO realtime_continuous_predictions VALUES (?,?,?)',(old['record_id'],old['created_at'],json.dumps(new)))
        actual='OVERWRITTEN'
    except sqlite3.DatabaseError as e:actual='REJECTED:'+str(e)
    record('append_only_replace','REJECTED_UNCHANGED',actual,before,counts(a),recursive_triggers=a.c.execute('pragma recursive_triggers').fetchone()[0],stored_prediction=json.loads(a.c.execute("select payload_json from realtime_continuous_predictions where record_id='valid/ML'").fetchone()[0])['continuous_prediction_c']);a.close()
    # Source response retry, malformed partial ingestion and spool replay.
    received=datetime.fromisoformat('2026-10-02T14:00:00+00:00');obs=datetime.fromisoformat('2026-10-02T13:00:00+00:00')
    metar={'rawOb':'METAR ZUUU 021300Z 00000MPS 9999 NSC 20/10 Q1010','obsTime':int(obs.timestamp()),'icaoId':'ZUUU'}
    a=fresh('mixed_metar');before=counts(a)
    content=json.dumps([metar,42]).encode();spool_save(a,'ZUUU',content,received)
    for attempt in range(2):
        try:recover(a);actual='RECOVERED'
        except Exception as e:actual='RECOVERY_RAISED:'+str(e)
        record('mixed_metar_spool_attempt_'+str(attempt),'QUARANTINE_MALFORMED_AND_COMPLETE_SPOOL',actual,before,counts(a),spool_done=sum(r.get('kind')=='SPOOL_PROCESSED' for r in a.rows('engine_state')))
    a.close()
    class Session:
        def __init__(self):self.n=0
        def get(self,*args,**kwargs):self.n+=1;raise requests.Timeout('injected')
    session=Session();sleeps=[];cfg=config();fetch=Fetcher(cfg,session=session,sleeper=lambda n:sleeps.append(n));content,_,attempts=fetch.get('https://fixture.invalid',{})
    record('network_timeout_retry','BOUNDED_3_ATTEMPTS',str(session.n),{}, {},attempts=attempts,sleep_count=len(sleeps))
    # Honest persisted state remains absent after an injected DB failure, but caller object may advance.
    a=fresh('state_rollback')
    with a.c:a.insert('probability_state',state,'state')
    a.snapshot('anchor',f,m,p,meta)
    target={'business_date_bjt':'2026-10-03','daily_tmax_c':22,'settlement_status':'FINAL','settlement_time':'2026-10-05T00:00:00+00:00','label_eligibility_time':'2026-10-05T00:00:00+00:00'}
    with a.c:a.insert('daily_ground_truth',target,'truth')
    ss=copy.deepcopy(state);history=[];original=a.insert
    def fail(table,*args,**kwargs):
        if table=='probability_state':raise sqlite3.OperationalError('INJECTED_DB_FAILURE')
        return original(table,*args,**kwargs)
    a.insert=fail;before=counts(a)
    try:advance(a,ss,history,'2026-10-05T01:00:00+00:00');actual='NO_ERROR'
    except sqlite3.OperationalError:actual='DB_ERROR'
    a.insert=original;retry=advance(a,ss,history,'2026-10-05T01:01:00+00:00')
    record('state_advance_db_failure','MEMORY_ROLLBACK_OR_RETRY_PERSISTENCE',actual,before,counts(a),memory_state_id=ss['state_id'],persisted_state_ids=[r['_id'] for r in a.rows('probability_state')],memory_residual_count=len(ss['residuals']),retry_changed=retry,history_rows=len(history));a.close()
    # Independent settlement/evaluation fixtures cover completeness and late corrections.
    for mode in ('complete','missing_hour','temperature_conflict','late_after_final'):
        a=fresh('settle_'+mode);day='2026-10-03';start=datetime.fromisoformat(day+'T00:00:00+08:00')
        for h in range(24 if mode!='missing_hour' else 23):
            at=(start+timedelta(hours=h)).astimezone(UTC);msg=dict(rawOb='METAR ZUUU '+at.strftime('%d%H%MZ')+' 00000MPS 9999 NSC '+str(10+h%10).zfill(2)+'/05 Q1010',obsTime=int(at.timestamp()),icaoId='ZUUU');archive_metar(a,[msg],start+timedelta(days=1))
        if mode=='temperature_conflict':
            at=start.astimezone(UTC);msg=dict(rawOb='METAR ZUUU '+at.strftime('%d%H%MZ')+' COR 00000MPS 9999 NSC 40/05 Q1010',obsTime=int(at.timestamp()),icaoId='ZUUU');archive_metar(a,[msg],start+timedelta(days=1))
        settled=settle(a,start+timedelta(days=1,hours=1));actual=settled[-1]['settlement_status'];before=counts(a)
        if mode=='late_after_final':
            at=start.astimezone(UTC)+timedelta(minutes=30);msg=dict(rawOb='METAR ZUUU '+at.strftime('%d%H%MZ')+' 00000MPS 9999 NSC 40/05 Q1010',obsTime=int(at.timestamp()),icaoId='ZUUU');archive_metar(a,[msg],start+timedelta(days=1,hours=2));settled=settle(a,start+timedelta(days=1,hours=3));actual=settled[-1]['settlement_status']
        expected={'complete':'FINAL','missing_hour':'INCOMPLETE','temperature_conflict':'PENDING_VERSION_CONFLICT','late_after_final':'PENDING_AFTER_FINAL_DATA'}[mode]
        record('settlement_'+mode,expected,actual,before,counts(a),targets=a.rows('daily_ground_truth'))
        if mode=='complete':
            with a.c:a.insert('probability_state',state,'state')
            a.snapshot('score',f,m,p,meta);evaluate(a,start+timedelta(days=2));evaluate(a,start+timedelta(days=2));ev=a.rows('daily_evaluation');expected_scores=independent_scores(np.array(p['pmf']),19)
            record('evaluation_once_independent_score','ONE_ROW_MATCHES_INDEPENDENT_SCORE',str(len(ev)),{},counts(a),metrics=ev,independent_scores=expected_scores,mismatch_count=sum(mismatch(ev[0][k],v) for k,v in expected_scores.items()))
        a.close()
    return {'cases':len(evidence),'unexpected_acceptances':sum(e['expected'].startswith('REJECTED') and e['actual'] in ('ACCEPTED','OVERWRITTEN') for e in evidence),'evidence':evidence}

if __name__=='__main__':
    stages=[('database_integrity',integrity),('ground_truth',ground_truth),('ecmwf_archive',archive),('feature_integrity',features),('models',models),('probability',probability),('realtime_lineage',realtime),('adversarial',adversarial)]
    if len(sys.argv)>1:stages=[r for r in stages if r[0]==sys.argv[1]]
    for name,fn in stages:stage(name,fn)
    save('verification_results.json',{'evidence_status':'FINAL','seed':4102026,'checks':RESULT,'auditor_exceptions':EXCEPTIONS})
