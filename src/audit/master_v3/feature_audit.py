import calendar
import json
import math
from collections import defaultdict
from datetime import timedelta
from .common import *

def solar(day,hour):
    base=datetime.fromisoformat(day).replace(tzinfo=BJT);local=base+timedelta(hours=hour)
    def terms(dt):
        g=2*math.pi/(366 if calendar.isleap(dt.year) else 365)*(dt.timetuple().tm_yday-1+(dt.hour-12)/24)
        eq=229.18*(.000075+.001868*math.cos(g)-.032077*math.sin(g)-.014615*math.cos(2*g)-.040849*math.sin(2*g))
        decl=.006918-.399912*math.cos(g)+.070257*math.sin(g)-.006758*math.cos(2*g)+.000907*math.sin(2*g)-.002697*math.cos(3*g)+.00148*math.sin(3*g)
        return eq,decl
    eq,de=terms(local);lat=math.radians(30.576);ha=math.radians(((hour*60+eq+4*103.950-480)%1440)/4-180)
    elevation=math.degrees(math.asin(max(-1,min(1,math.sin(lat)*math.sin(de)+math.cos(lat)*math.cos(de)*math.cos(ha)))))
    eqn,den=terms(base+timedelta(hours=12));angle=math.degrees(math.acos(math.cos(math.radians(90.833))/(math.cos(lat)*math.cos(den))-math.tan(lat)*math.tan(den)))
    rise=base+timedelta(minutes=1200-4*(103.950+angle)-eqn);setting=base+timedelta(minutes=1200-4*(103.950-angle)-eqn)
    return dict(elevation=elevation,day_length=(setting-rise).total_seconds()/60,sunrise=rise)

def arithmetic(d,rows):
    """Independent finite-array implementation of the frozen registry contract."""
    t=[r['temperature_2m_c'] for r in rows];peak=max(range(24),key=lambda j:t[j]);op=d['operation'];hours=json.loads(d['hours_json'])
    fields=json.loads(d['source_fields']);idx=[peak] if op in ('at_peak','sin_peak','cos_peak') else hours if hours is not None else list(range(24))
    arrays=[[rows[i].get(f) for i in idx] for f in fields]
    if any(x is None or not math.isfinite(x) for v in arrays for x in v):return None
    a=arrays[0];a=[x/3.6 for x in a] if fields[0].endswith('_kmh') else a
    if op=='max':return max(a)
    if op=='min':return min(a)
    if op=='mean':return math.fsum(a)/len(a)
    if op=='range':return max(a)-min(a)
    if op=='argmax':return idx[a.index(max(a))]
    if op=='argmin':return idx[a.index(min(a))]
    if op in ('at','at_peak'):return a[0]
    if op=='delta':return a[-1]-a[0]
    if op=='sum':return math.fsum(a)
    if op=='energy':return math.fsum(a)*3600/1000000
    if op=='clear':return sum(x<=10 for x in a)
    if op=='positive_count':return sum(x>0 for x in a)
    if op=='any_positive':return int(any(x>0 for x in a))
    if op in ('sin_peak','cos_peak'):return (math.sin if op=='sin_peak' else math.cos)(a[0]*math.pi/180)
    if op.startswith('spread_'):
        b=[x-y for x,y in zip(*arrays)];return min(b) if op=='spread_min_c' else math.fsum(b)/len(b)
    if op in ('period_delta','rh_change_pct','morning_to_afternoon_delta_c'):return math.fsum(a[12:18])/6-math.fsum(a[6:12])/6
    delta=[t[i+1]-t[i] for i in range(23)]
    if op=='max_hourly_warming_c':return max(delta)
    if op=='max_hourly_cooling_c':return min(delta)
    if op=='strongest_warming_hour_bjt':return delta.index(max(delta))+1
    if op=='strongest_cooling_hour_bjt':return delta.index(min(delta))+1
    if op=='peak_sharpness_c':return None if peak in (0,23) else t[peak]-(t[peak-1]+t[peak+1])/2
    if op=='hours_within_05c_of_max':return sum(v>=max(t)-.5 for v in t)
    if op=='hours_within_10c_of_max':return sum(v>=max(t)-1 for v in t)
    if op=='pre_peak_slope_c_per_h':return None if peak<=6 else (t[peak]-t[6])/(peak-6)
    if op=='post_peak_slope_c_per_h':return None if peak>=21 else (t[21]-t[peak])/(21-peak)
    if op=='morning_to_peak_warming_c':return max(t)-t[6]
    if op=='peak_to_evening_cooling_c':return max(t)-t[21]
    raise ValueError('UNVERIFIABLE_OPERATOR '+op)

class Features:
    def __init__(self,source):
        self.s=source;c=source.get('phase7_feature_v1.db')
        self.registry=records(c,'phase7_feature_registry');self.names=sorted(r['feature_name'] for r in self.registry)
        self.samples={r['sample_id']:r for r in records(c,'phase7_feature_sample')};self.values=defaultdict(dict);self.cells={}
        for r in records(c,'phase7_feature_value'):self.values[r['sample_id']][r['feature_name']]=r['value'];self.cells[r['sample_id'],r['feature_name']]=r
        self.bundles={r['bundle_id']:r for r in records(c,'phase7_feature_source_lineage')};self.independent={};self.revision_evidence=[]

    def compute(self,s):
        day=s.get('business_date_bjt',s.get('target_business_date'));h=s['horizon'];s4=self.s.samples_by_key[day,h];rows=self.s.curve(s4);t=[r['temperature_2m_c'] for r in rows];peak=t.index(max(t));issue=stamp(s4['issue_time_utc']);run=stamp(s4['selected_ecmwf_run_time_utc']);available=stamp(s4['selected_ecmwf_source_available_time_utc']);result={};av={};revision={}
        bias=self.s.independent_bias.get((day,h)) or self.s.mos_bias(s4)
        for d in self.registry:
            n=d['feature_name'];op=d['operation'];src=d['source'];a=available
            if src=='ECMWF':v=arithmetic(d,rows)
            elif src=='REVISION':
                slot=op if op in ('prev_run','6h','12h','24h') else 'prev_run'
                if slot not in revision:
                    if slot=='prev_run':
                        olds=[r for r in self.s.runs_available if stamp(r['run_time_utc'])<run and stamp(r['source_available_time_utc'])<=issue];old=max(olds,key=lambda r:r['run_time_utc'],default=None)
                    else:old=self.s.runs_by_time.get((run-timedelta(hours=int(slot[:-1]))).isoformat());old=old if old and stamp(old['source_available_time_utc'])<=issue else None
                    start=datetime.fromisoformat(day).replace(tzinfo=BJT).astimezone(UTC)
                    oldrows=[self.s.curves.get(old['canonical_raw_run_id'],{}).get((start+timedelta(hours=k)).isoformat()) for k in range(24)] if old else []
                    valid=len(oldrows)==24 and all(r is not None and r['temperature_2m_c'] is not None for r in oldrows)
                    revision[slot]=(old,[r['temperature_2m_c'] for r in oldrows] if valid else None)
                old,ot=revision[slot];a=max(a,stamp(old['source_available_time_utc'])) if old else a
                if ot is None:v=None
                elif op in ('prev_run','6h','12h','24h'):v=max(t)-max(ot)
                elif op=='curve_revision_prev_mean_c':v=math.fsum(x-y for x,y in zip(t,ot))/24
                elif op=='curve_revision_prev_max_abs_c':v=max(abs(x-y) for x,y in zip(t,ot))
                elif op=='peak_hour_revision_prev_h':v=peak-ot.index(max(ot))
                elif op=='temp_14_revision_prev_c':v=t[14]-ot[14]
                else:raise ValueError(op)
                if slot=='24h':self.revision_evidence.append(dict(date=day,horizon=h,current_run=run.isoformat(),issue=issue.isoformat(),old_run=old['run_time_utc'] if old else None,old_available=old['source_available_time_utc'] if old else None,old_complete=ot is not None,reason='COMPLETE' if ot else 'RUN_ABSENT' if old is None else 'OLD_TARGET_DAY_INCOMPLETE',value=v))
            elif src=='PHASE6':
                b,dates,path=bias[op];v=None if b is None else max(t)-b if n=='m6_corrected_temperature_c' else b
                a=available if op=='M6' else datetime(1900,1,1,tzinfo=UTC)
                for date in dates:
                    hs=self.s.samples_by_key[date,h];a=max(a,eligible(date),stamp(hs['selected_ecmwf_source_available_time_utc']))
            elif src=='CALENDAR':
                date=datetime.fromisoformat(day);doy=date.timetuple().tm_yday;angle=2*math.pi*(doy-1)/(366 if calendar.isleap(date.year) else 365)
                mapping={'calendar_month':date.month,'calendar_season_code':('DJF','MAM','JJA','SON').index(season(day)),'calendar_day_of_year':doy,'calendar_doy_sin':math.sin(angle),'calendar_doy_cos':math.cos(angle),'run_age_hours':(issue-run).total_seconds()/3600,'target_start_lead_hours':(datetime.fromisoformat(day).replace(tzinfo=BJT).astimezone(UTC)-run).total_seconds()/3600,'target_peak_lead_hours':(datetime.fromisoformat(day).replace(tzinfo=BJT).astimezone(UTC)+timedelta(hours=peak)-run).total_seconds()/3600}
                v=mapping[n];a=available if n in ('run_age_hours','target_start_lead_hours','target_peak_lead_hours') else datetime(1900,1,1,tzinfo=UTC)
            elif src=='SOLAR':
                so=[solar(day,k) for k in range(24)];rise=so[0]['sunrise'];mapping={'solar_day_length_min':so[0]['day_length'],'solar_max_elevation_deg':max(r['elevation'] for r in so),'solar_elevation_09_bjt_deg':so[9]['elevation'],'solar_elevation_15_bjt_deg':so[15]['elevation'],'solar_sunrise_hour_bjt':rise.hour+rise.minute/60+rise.second/3600+rise.microsecond/3.6e9};v=mapping[n];a=datetime(1900,1,1,tzinfo=UTC)
            else:raise ValueError('UNVERIFIABLE_SOURCE '+src)
            result[n]=v;av[n]=a.isoformat()
        return result,av

    def audit(self):
        out=[];counts=defaultdict(lambda:defaultdict(int));bad=0;timebad=0;maskbad=0
        for sid,s in sorted(self.samples.items()):
            v,av=self.compute(s);self.independent[sid]=v
            for name in self.names:
                cell=self.cells[sid,name];b=not close(v[name],cell['value']);time=stamp(av[name])>stamp(s['issue_time_utc']) or stamp(cell['feature_available_time'])>stamp(s['issue_time_utc']);bad+=b;timebad+=time
                maskbad+=int((cell['value'] is None)!=(not cell['is_available']) or (cell['value'] is None)!=(cell['missing_reason'] is not None))
                counts[name][s['horizon']+'_valid']+=cell['value'] is not None;counts[name]['mismatches']+=b
                out.append(dict(sample_id=sid,horizon=s['horizon'],feature_name=name,stored=cell['value'],recalculated=v[name],difference=None if v[name] is None or cell['value'] is None else v[name]-cell['value'],available=cell['feature_available_time'],independent_available=av[name],issue=s['issue_time_utc'],status='FAIL' if b or time else 'PASS'))
        registry=[]
        for r in self.registry:
            n=r['feature_name'];reason='Frozen source/group history required; NULL when unavailable' if r['status']=='CONDITIONAL' else 'Complete deterministic/forecast contract'
            reasons={h:sorted({self.cells[sid,n]['missing_reason'] for sid,sample in self.samples.items() if sample['horizon']==h and self.cells[sid,n]['missing_reason']}) for h in ('T1','T2')}
            category='B: cold start / group history' if r['source']=='PHASE6' else 'C: missing legal complete old run' if r['source']=='REVISION' else 'D: source variable missing' if r['status']=='CONDITIONAL' else 'A: complete source'
            reason=category+'; '+json.dumps(reasons,ensure_ascii=False)
            registry.append(dict(r,dtype='REAL nullable',coverage_T1=counts[n]['T1_valid']/729,coverage_T2=counts[n]['T2_valid']/728,conditional_reason=reason,T1_valid=counts[n]['T1_valid'],T1_missing=729-counts[n]['T1_valid'],T2_valid=counts[n]['T2_valid'],T2_missing=728-counts[n]['T2_valid'],lineage_status='PASS' if all(self.cells[sid,n]['bundle_id'] in self.bundles for sid in self.samples) else 'FAIL',independent_recalculation_status='FAIL' if counts[n]['mismatches'] else 'PASS'))
        self.s.counts['FEATURE_CELL_CHECK_COUNT']=len(out);self.s.counts['FEATURE_FORMULA_MISMATCH_COUNT']=bad;self.s.counts['FEATURE_TIME_AVAILABILITY_VIOLATION_COUNT']=timebad;self.s.counts['SILENT_IMPUTATION_COUNT']+=maskbad
        output('MASTER_FEATURE_REPRODUCTION.csv',out);output('MASTER_FEATURE_REGISTRY_AUDIT.csv',registry);output('MASTER_T2_REVISION_24H_AUDIT.csv',[r for r in self.revision_evidence if r['horizon']=='T2'])
        self.s.evidence['features']=dict(count=len(self.registry),accepted=sum(r['status']=='ACCEPTED' for r in self.registry),conditional=sum(r['status']=='CONDITIONAL' for r in self.registry),T1=len([s for s in self.samples.values() if s['horizon']=='T1']),T2=len([s for s in self.samples.values() if s['horizon']=='T2']),t2_24h_null_justified=all(r['old_complete']==False and r['value'] is None for r in self.revision_evidence if r['horizon']=='T2'),mask_mismatches=maskbad)
        # Full X schema inspection, without touching target outcomes for selection.
        c=self.s.get('phase7_feature_v1.db');cols=[r['name'] for r in c.execute('PRAGMA table_info(training_feature_view)')]
        self.s.counts['TARGET_LEAKAGE_COUNT']=sum(n not in self.names+['sample_id'] for n in cols)
        X=[[self.values[sid][n] for n in self.names] for sid in sorted(self.samples)]
        import numpy as np
        a=np.array(X,dtype=float);duplicates=[];constant=[];near=[]
        for i,n in enumerate(self.names):
            finite=a[:,i][np.isfinite(a[:,i])]
            if len(finite) and np.ptp(finite)==0:constant.append(n)
            if len(finite) and max(sum(finite==v) for v in np.unique(finite))/len(finite)>=.95:near.append(n)
            for j in range(i):
                if np.array_equal(a[:,i],a[:,j],equal_nan=True):duplicates.append((self.names[j],n))
        self.s.evidence['feature_redundancy']=dict(exact_duplicate_pairs=duplicates,constant_features=constant,near_constant_features=near)

    def solar_audit(self):
        rows=records(self.s.get('phase3_auxiliary_v1.db'),'aux_v1_solar_time');bad=0
        for r in rows:
            d=stamp(r['target_time']).astimezone(BJT);v=solar(d.date().isoformat(),d.hour)
            bad+=int(r['latitude']!=30.576 or r['longitude']!=103.950 or not close(v['elevation'],r['solar_elevation']) or not close(v['day_length'],r['daylight_duration']) or stamp(v['sunrise'].isoformat())!=stamp(r['sunrise']))
        self.s.counts['SOLAR_MISMATCH_COUNT']=bad
        self.s.evidence['phase3']=dict(solar_rows=len(rows),coordinate=(30.576,103.950),solar_mismatch_count=bad,meteostat_role='TRAINING_BLOCKED',wu='NOT_FORMAL_ASSET')

