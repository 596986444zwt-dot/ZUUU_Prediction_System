import bisect
import json
import math
import re
from collections import defaultdict
from datetime import timedelta
from .common import *

WEATHER={'temperature_2m_c':'temperature_2m','dew_point_2m_c':'dew_point_2m','relative_humidity_2m_pct':'relative_humidity_2m','surface_pressure_hpa':'surface_pressure','pressure_msl_hpa':'pressure_msl','cloud_cover_pct':'cloud_cover','cloud_cover_low_pct':'cloud_cover_low','cloud_cover_mid_pct':'cloud_cover_mid','cloud_cover_high_pct':'cloud_cover_high','wind_speed_10m_kmh':'wind_speed_10m','wind_direction_10m_deg':'wind_direction_10m','wind_gusts_10m_kmh':'wind_gusts_10m','shortwave_radiation_wm2':'shortwave_radiation','direct_radiation_wm2':'direct_radiation','diffuse_radiation_wm2':'diffuse_radiation','precipitation_mm':'precipitation','rain_mm':'rain','cape_jkg':'cape'}

def report_temperature(raw):
    m=re.search(r'(?:^|\s)(M?\d{2})/(?:M?\d{2}|//)(?:\s|$)',raw)
    if not m:return None
    return -int(m[1][1:]) if m[1].startswith('M') else int(m[1])

def daily(rows):
    ordered=sorted(rows,key=lambda r:(stamp(r['observation_time_utc']),r['id']))
    if not ordered:return None
    temps=[float(r['temperature_c']) for r in ordered];mx=max(temps);peak=[r for r in ordered if r['temperature_c']==mx]
    times=[stamp(r['observation_time_utc']).astimezone(BJT) for r in ordered]
    day=times[0].date()
    hours={d.hour for d in times if d.date()==day and d.minute==d.second==0}
    return dict(daily_tmax_c=mx,first_tmax_time_bjt=stamp(peak[0]['observation_time_utc']).astimezone(BJT).isoformat(),last_tmax_time_bjt=stamp(peak[-1]['observation_time_utc']).astimezone(BJT).isoformat(),tmax_occurrence_count=len(peak),observation_count=len(ordered),hourly_coverage_count=len(hours),all_silver_ids=[r['id'] for r in ordered],all_bronze_raw_ids=[r['bronze_raw_id'] for r in ordered],tmax_silver_ids=[r['id'] for r in peak],tmax_bronze_raw_ids=[r['bronze_raw_id'] for r in peak],has_correction=int(any(r.get('is_correction') or r.get('message_class')=='COR' for r in ordered)),tmax_has_correction=int(any(r.get('is_correction') or r.get('message_class')=='COR' for r in peak)),has_recovery=int(any(r.get('recovery_reason') for r in ordered)),tmax_has_recovery=int(any(r.get('recovery_reason') for r in peak)))

class Source:
    def __init__(self):
        self.db={};self.counts=defaultdict(int);self.evidence={}
    def get(self,name):
        if name not in self.db:self.db[name]=snapshot(ROOT/'database'/name)
        return self.db[name]
    def load(self):
        c=self.get('zuuu_prediction.db')
        self.truth={r['business_date_bjt']:r for r in records(c,'zuuu_target_v1')}
        self.silver=records(c,'zuuu_silver_observation');self.bronze={r['id']:r for r in records(c,'zuuu_raw_metar')}
        self.rawruns={r['id']:r for r in records(c,'ecmwf_raw_runs')}
        self.runs=records(c,'ecmwf_archive_v1');self.curves={};self.rawunits={}
        for rawid,r in self.rawruns.items():
            p=json.loads(r['raw_json']);hourly=p.get('hourly',{});curve={}
            for i,t in enumerate(hourly.get('time',[])):
                target=(t+'+00:00') if len(t)==16 or len(t)==19 else t
                curve[stamp(target).isoformat()]={field:(hourly.get(api,[None]*len(hourly['time']))[i]) for field,api in WEATHER.items()}
            self.curves[rawid]=curve;self.rawunits[rawid]=p.get('hourly_units',{})
        c4=self.get('phase4_data_v1.db');self.samples4=records(c4,'phase4_data_v1_sample');self.hours4=defaultdict(list)
        for r in records(c4,'phase4_data_v1_ecmwf_hourly'):self.hours4[r['business_date_bjt'],r['horizon']].append(r)
        for v in self.hours4.values():v.sort(key=lambda r:r['target_time_utc'])
        self.solar4=defaultdict(list)
        for r in records(c4,'phase4_data_v1_solar'):self.solar4[r['business_date_bjt']].append(r)
        for v in self.solar4.values():v.sort(key=lambda r:r['target_time'])
        self.samples_by_key={(s['business_date_bjt'],s['horizon']):s for s in self.samples4}
        self.runs_available=sorted([r for r in self.runs if r['canonical_status']=='AVAILABLE'],key=lambda r:r['run_time_utc'])
        self.runs_by_time={r['run_time_utc']:r for r in self.runs_available}
        c6=self.get('phase6_statistical_mos_v1_review.db');self.samples6=records(c6,'phase6_mos_sample');self.bias6={(r['target_business_date'],r['horizon'],r['model']):r for r in records(c6,'phase6_mos_bias_state')};self.pred6={(r['target_business_date'],r['horizon'],r['model']):r for r in records(c6,'phase6_mos_prediction')}
        return self

    def target_audit(self):
        groups=defaultdict(list);out=[];special=[];invalid=0
        for r in self.silver:
            groups[r['business_date_bjt']].append(r);raw=self.bronze.get(r['bronze_raw_id'])
            invalid+=int(raw is None or report_temperature(raw['raw_metar'])!=r['temperature_c'] or stamp(r['observation_time_utc']).astimezone(BJT).date().isoformat()!=r['business_date_bjt'] or stamp(r['observation_time_bjt'])!=stamp(r['observation_time_utc']) or r['station']!='ZUUU')
            if r['is_correction'] or (r['business_date_bjt']=='2025-09-24' and r['recovery_reason']):special.append(dict(r,raw_message=raw['raw_metar'] if raw else None))
        for day,t in sorted(self.truth.items()):
            rec=daily(groups[day]);bad=[]
            for k,v in rec.items():
                stored=json.loads(t[k]) if k.endswith('_ids') else t[k]
                if stored!=v:bad.append(k)
            if rec['hourly_coverage_count']!=24 or rec['observation_count']<24:bad.append('completeness')
            out.append(dict(date=day,stored_tmax=t['daily_tmax_c'],recalculated_tmax=rec['daily_tmax_c'],count=rec['observation_count'],hour_count=rec['hourly_coverage_count'],mismatches=bad,status='PASS' if not bad else 'FAIL'))
        self.counts['TARGET_RECALC_MISMATCH_COUNT']=sum(r['status']=='FAIL' for r in out)+invalid
        output('MASTER_PHASE1_GROUND_TRUTH_AUDIT.csv',out);output('MASTER_PHASE1_SPECIAL_REPORTS.csv',special)
        self.evidence['phase1']=dict(days=len(out),first=min(self.truth),last=max(self.truth),raw_count=len(self.bronze),silver_count=len(self.silver),raw_silver_mismatches=invalid,special_reports=special)

    def ecmwf_audit(self):
        c=self.get('zuuu_prediction.db');hourly=records(c,'ecmwf_hourly_forecasts');bad=0;null=0;unitbad=0
        for r in hourly:
            src=self.curves[r['raw_run_id']].get(r['target_time_utc']);null+=r['temperature_2m_c'] is None
            bad+=int(src is None or any(not close(r.get(k),src[k]) for k in WEATHER) or (stamp(r['target_time_utc'])-stamp(r['run_time_utc'])).total_seconds()/3600!=r['lead_hours'] or stamp(r['target_time_bjt'])!=stamp(r['target_time_utc']))
        out=[]
        for r in self.runs:
            rid=r['canonical_raw_run_id'];valid=r['canonical_status']=='AVAILABLE';same=[x for x in self.rawruns.values() if x['run_time_utc']==r['run_time_utc']]
            curve=self.curves.get(rid,{})
            mismatch=valid and (rid not in self.rawruns or len(curve)!=r['hourly_row_count'] or sum(x['temperature_2m_c'] is None for x in curve.values())!=r['temperature_null_count'])
            out.append(dict(run=r['run_time_utc'],canonical_raw=rid,status=r['canonical_status'],raw_versions=len(same),distinct_contents=len(set(x['content_sha256'] for x in same)),hours=len(curve),null_temperature=sum(x['temperature_2m_c'] is None for x in curve.values()),availability=r['source_available_time_utc'],availability_semantics=r['availability_semantics'],mismatch=int(mismatch)))
        # Preserve both source NULL and all original raw content hashes.
        hashbad=sum(digest(Path(r['bronze_file_path']))!=r['content_sha256'] for r in self.rawruns.values() if Path(r['bronze_file_path']).exists())
        self.counts['PHASE2_VALUE_MISMATCH_COUNT']=bad+sum(r['mismatch'] for r in out)+hashbad
        self.evidence['phase2']=dict(run_count=len(self.runs),hourly_count=len(hourly),raw_count=len(self.rawruns),null_temperature_count=null,unavailable_count=sum(r['canonical_status']!='AVAILABLE' for r in self.runs),version_20260928=[r for r in out if r['run']=='2026-09-28T00:00:00+00:00'],raw_hash_mismatches=hashbad)
        output('MASTER_PHASE2_ECMWF_AUDIT.csv',out)
        self.hourly_source_by_id={r['id']:r for r in hourly}

    def curve(self,s):
        day=s.get('business_date_bjt',s.get('target_business_date'));start=datetime.fromisoformat(day).replace(tzinfo=BJT).astimezone(UTC)
        return [self.curves.get(s['source_raw_run_id'],{}).get((start+timedelta(hours=h)).isoformat()) for h in range(24)]

    def choose(self,day,issue):
        start=datetime.fromisoformat(day).replace(tzinfo=BJT).astimezone(UTC);targets=[(start+timedelta(hours=h)).isoformat() for h in range(24)]
        for r in reversed(self.runs_available):
            if stamp(r['run_time_utc'])>stamp(issue) or stamp(r['source_available_time_utc'])>stamp(issue):continue
            curve=self.curves.get(r['canonical_raw_run_id'],{})
            if all(t in curve and curve[t]['temperature_2m_c'] is not None for t in targets):return r
        return None

    def phase4_5(self):
        issue_rule=records(self.get('zuuu_prediction.db'),'ecmwf_issue_rule_v1')[0];out=[];valuesbad=0
        for s in self.samples4:
            day=s['business_date_bjt'];h=s['horizon'];chosen=self.choose(day,s['issue_time_utc']);known=day=='2025-08-07' and h=='T2'
            expected_issue=(datetime.fromisoformat(day).replace(tzinfo=BJT)-timedelta(days=int(h[1:]))).replace(hour=issue_rule['backtest_issue_hour_bjt']).astimezone(UTC)
            runbad=chosen is None and not known or chosen is not None and chosen['canonical_raw_run_id']!=s['source_raw_run_id']
            if known:runbad=chosen is not None or s['sample_status']!='EXCLUDED_INCOMPLETE_ECMWF' or s['trajectory_valid_hours']!=14
            future=stamp(s['selected_ecmwf_source_available_time_utc'])>stamp(s['issue_time_utc']) or stamp(s['selected_ecmwf_run_time_utc'])>stamp(s['issue_time_utc'])
            for r in self.hours4.get((day,h),[]):
                source=self.hourly_source_by_id[r['source_hourly_id']]
                valuesbad+=any(not close(r[k],source[k]) for k in WEATHER)
                self.counts['CROSS_RUN_SPLICE_COUNT']+=int(r['source_raw_run_id']!=s['source_raw_run_id'])
            out.append(dict(day=day,horizon=h,issue=s['issue_time_utc'],selected=s['selected_ecmwf_run_time_utc'],independent_selected=chosen['run_time_utc'] if chosen else None,status=s['sample_status'],known_gap=known,selection_mismatch=int(runbad),future=int(future),issue_mismatch=int(expected_issue!=stamp(s['issue_time_utc']))))
        self.counts['ISSUE_RULE_SELECTION_MISMATCH_COUNT']=sum(r['selection_mismatch'] for r in out);self.counts['FUTURE_ECMWF_RUN_COUNT']=sum(r['future'] for r in out)
        self.counts['PHASE4_SAMPLE_MISMATCH_COUNT']=valuesbad+sum(r['issue_mismatch'] for r in out)
        output('MASTER_PHASE4_SAMPLE_AUDIT.csv',out)
        c5=self.get('phase5_ecmwf_raw_baseline_v1.db');pred=records(c5,'phase5_baseline_v1_prediction');metrics_out=[];bad=0
        for p in pred:
            s=self.samples_by_key[p['business_date_bjt'],p['horizon']];v=max(r['temperature_2m_c'] for r in self.curve(s));y=self.truth[p['business_date_bjt']]['daily_tmax_c']
            bad+=int(not close(v,p['ecmwf_raw_tmax_c']) or not close(y,p['target_tmax_c']) or not close(v-y,p['error_c']))
        for saved in records(c5,'phase5_baseline_v1_metrics'):
            h=saved['horizon'];m=metrics([p['ecmwf_raw_tmax_c']-p['target_tmax_c'] for p in pred if p['horizon']==h]);mapping={'N':'n','Bias':'bias_c','MAE':'mae_c','RMSE':'rmse_c','MedianAE':'median_ae_c','P90AE':'p90_ae_c','MaxAE':'max_ae_c'}
            b=sum(not close(v,saved[mapping[k]]) for k,v in m.items());bad+=b;metrics_out.append(dict(horizon=h,**m,mismatches=b))
        self.counts['PHASE5_METRIC_MISMATCH_COUNT']=bad;self.evidence['phase5']=metrics_out;output('MASTER_PHASE5_METRIC_AUDIT.csv',metrics_out)

    def mos_bias(self,s):
        day=s.get('business_date_bjt',s.get('target_business_date'));h=s['horizon'];issue=s.get('issue_time_utc',s.get('issue'))
        allowed=sorted((r for r in self.samples6 if r['horizon']==h and r['business_date_bjt']<day and eligible(r['business_date_bjt'])<=stamp(issue) and stamp(r['issue_time_utc'])<stamp(issue)),key=lambda r:r['business_date_bjt'])
        result={};anchor=stamp(issue).astimezone(BJT).date()-timedelta(days=2)
        for model in ('M1','M2','M3','M4','M5'):
            used=allowed;path=model
            if len(allowed)<7:result[model]=(None,[],'INSUFFICIENT_HISTORY');continue
            if model in ('M2','M3'):used=[r for r in allowed if (anchor-timedelta(days=(7 if model=='M2' else 30)-1)).isoformat()<=r['business_date_bjt']<=anchor.isoformat()]
            if model=='M4':used=[r for r in allowed if season(r['business_date_bjt'])==season(day)]
            if model=='M5':path='M5>HORIZON_EXPANDING_IDENTICAL_TO_M1'
            if len(used)<7:used=allowed;path=model+'>M1'
            result[model]=(math.fsum(r['raw_error'] for r in used)/len(used),[r['business_date_bjt'] for r in used],path)
        if result['M1'][0] is None:result['M6']=(None,[],'INSUFFICIENT_HISTORY')
        else:result['M6']=(math.fsum(result[m][0] for m in ('M1','M3','M4'))/3,sorted(set(d for m in ('M1','M3','M4') for d in result[m][1])),'M6_FIXED_EQUAL_WEIGHTS')
        return result

    def phase6(self):
        out=[];bad=0;edges=0;leak=0
        self.independent_bias={}
        for s in self.samples6:
            choices=self.mos_bias(s);self.independent_bias[s['business_date_bjt'],s['horizon']]=choices
            for m,(b,dates,path) in choices.items():
                saved=self.bias6[s['business_date_bjt'],s['horizon'],m];p=self.pred6[s['business_date_bjt'],s['horizon'],m]
                sd=json.loads(saved['training_dates_json']);violation=sum(eligible(d)>stamp(s['issue_time_utc']) or d>=s['business_date_bjt'] for d in sd);edges+=len(sd);leak+=violation
                mismatch=not close(b,saved['bias_value']) or dates!=sd or saved['fallback_path']!=path or not close(None if b is None else s['raw_ecmwf_tmax']-b,p['mos_continuous_tmax'])
                bad+=mismatch;out.append(dict(date=s['business_date_bjt'],horizon=s['horizon'],method=m,history_n=len(sd),bias_saved=saved['bias_value'],bias_recalculated=b,mismatch=int(mismatch),eligibility_violations=violation))
        self.counts['PHASE6_MOS_MISMATCH_COUNT']=bad;self.counts['PHASE6_CAUSALITY_VIOLATION_COUNT']=leak;self.evidence['phase6_lineage_edges']=edges;output('MASTER_PHASE6_CAUSALITY_AUDIT.csv',out)

    def close(self):
        for c in self.db.values():c.close()
