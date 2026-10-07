import pathlib,sys,json,math,calendar,copy
from datetime import datetime,timedelta,timezone
from collections import Counter
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from src.audit.master_v4.verify import db,rows,payloads,save,sha,BJT,stamp,independent_distribution,independent_calibrate,independent_scores,stage
import numpy as np

def solar():
    c=db('phase3_auxiliary_v1.db');rs=rows(c,'aux_v1_solar_time');c.close();errors=[]
    def terms(d,h):
        y=366 if calendar.isleap(d.year) else 365;g=2*math.pi/y*(d.timetuple().tm_yday-1+(h-12)/24)
        eq=229.18*(.000075+.001868*math.cos(g)-.032077*math.sin(g)-.014615*math.cos(2*g)-.040849*math.sin(2*g))
        de=.006918-.399912*math.cos(g)+.070257*math.sin(g)-.006758*math.cos(2*g)+.000907*math.sin(2*g)-.002697*math.cos(3*g)+.00148*math.sin(3*g)
        return eq,de
    lat=math.radians(30.576);cellcount=0
    for r in rs:
        d=stamp(r['target_time']).astimezone(BJT);eq,de=terms(d,d.hour);t=d.hour*60+eq+4*103.95-480;ha=math.radians((t%1440)/4-180)
        z=math.acos(max(-1,min(1,math.sin(lat)*math.sin(de)+math.cos(lat)*math.cos(de)*math.cos(ha))));elev=90-math.degrees(z)
        midnight=d.replace(hour=0);eqn,den=terms(d,12);a=math.degrees(math.acos(math.cos(math.radians(90.833))/(math.cos(lat)*math.cos(den))-math.tan(lat)*math.tan(den)))
        rise=midnight+timedelta(minutes=1200-4*(103.95+a)-eqn);sets=midnight+timedelta(minutes=1200-4*(103.95-a)-eqn)
        expected={'solar_elevation':elev,'daylight_duration':8*a,'sin_doy':math.sin(2*math.pi*(d.timetuple().tm_yday-1)/(366 if calendar.isleap(d.year) else 365)),'cos_doy':math.cos(2*math.pi*(d.timetuple().tm_yday-1)/(366 if calendar.isleap(d.year) else 365)),'minutes_since_sunrise':(d-rise).total_seconds()/60,'minutes_to_sunset':(sets-d).total_seconds()/60}
        for k,v in expected.items():
            cellcount+=1
            if abs(v-r[k])>1e-6:errors.append({'time':r['target_time'],'field':k,'expected':v,'actual':r[k]})
        for k,v in [('sunrise',rise),('sunset',sets)]:
            cellcount+=1
            if abs((v-stamp(r[k])).total_seconds())>1e-5:errors.append({'time':r['target_time'],'field':k})
    return {'rows':len(rs),'cells':cellcount,'mismatch_count':len(errors),'errors':errors,'primary_source':'https://gml.noaa.gov/grad/solcalc/solareqns.PDF','scope':'Elevation, sunrise/sunset, duration, seasonal sin/cos and since/until times; azimuth not independently recomputed.'}

def frozen():
    records=json.loads((ROOT/'docs/master_audit_v4/MASTER_AUDIT_V4_SOURCE_GUARDIAN_BEFORE.json').read_text(encoding='utf-8-sig'));protected={pathlib.Path(r['path']).relative_to(ROOT).as_posix() for r in records};checked=[];errors=[];excluded=[]
    for phase in range(3,11):
        for p in (ROOT/f'docs/phase{phase}').glob('*MANIFEST*.json'):
            m=json.loads(p.read_text(encoding='utf-8-sig'))
            hashes=m.get('sha256',{})
            for path,v in hashes.items():
                if path not in protected:excluded.append({'manifest':str(p),'path':path,'reason':'Outside preclassified formal assets; historical/ambiguous audit contents not read'});continue
                actual=sha(ROOT/path);checked.append({'manifest':p.relative_to(ROOT).as_posix(),'path':path,'expected':v,'actual':actual})
                if v!=actual:errors.append(checked[-1])
    return {'manifest_hashes_checked':len(checked),'mismatch_count':len(errors),'errors':errors,'checks':checked,'unread_excluded_references':excluded}

def extra_faults():
    from src.realtime.archive import Archive
    from src.realtime.collectors import archive_ecmwf,archive_metar,poll_zuuu
    from src.realtime.spool import save as spool_save,recover
    from src.realtime.contracts import config
    from src.realtime.features import selection
    from src.realtime.engine import InstanceLock
    from src.probability.distribution import distribution,calibrate
    c=db('phase10_realtime_v1.db');run=next(iter(payloads(c,'realtime_ecmwf_raw_runs').values()));c.close();cases=[]
    def case(name,expected,actual,**evidence):cases.append(dict(case=name,expected=expected,actual=actual,production_state_polluted=False,**evidence));save('supplement_adversarial_cases.json',cases)
    p=ROOT/'temp/master_audit_v4/extra_offset.db';a=Archive(p,namespace='SIMULATION',create=True);payload=copy.deepcopy(run['payload']);payload['hourly']['time']=[stamp(t+'+00:00').astimezone(BJT).isoformat() for t in payload['hourly']['time']]
    accepted=archive_ecmwf(a,payload,run['run_time'],run['actual_ingest_time']);hs=a.rows('ecmwf_hourly');bad=sum(stamp(r['target_time_utc'])!=stamp(r['run_time_utc'])+timedelta(hours=r['lead_hours']) for r in hs)
    case('ecmwf_aware_time_offset','REJECT_OR_PRESERVE_INSTANT','ACCEPTED_SHIFTED' if bad else 'NORMALIZED',accepted=accepted,hourly_rows=len(hs),lead_violation_count=bad,example=hs[0] if hs else None);a.close()
    # Poller deliberately gets a mixed response: valid record after malformed element.
    obs=datetime.fromisoformat('2026-10-02T13:00:00+00:00');received=obs+timedelta(hours=1);metar={'rawOb':'METAR ZUUU 021300Z 00000MPS 9999 NSC 20/10 Q1010','obsTime':int(obs.timestamp()),'icaoId':'ZUUU'}
    class Fetch:
        def get(self,*args):return json.dumps([42,metar]).encode(),received,[{'time':received.isoformat(),'success':True,'latency':0}]
    a=Archive(ROOT/'temp/master_audit_v4/extra_mixed_poll.db',namespace='SIMULATION',create=True);n=poll_zuuu(a,Fetch(),config());case('mixed_response_valid_tail','VALID_TAIL_ARCHIVED_OR_RETRYABLE','TAIL_LOST' if not a.rows('zuuu_raw') else 'ARCHIVED',added=n,raw_rows=len(a.rows('zuuu_raw')),processed_spools=[r for r in a.rows('engine_state') if r.get('kind')=='SPOOL_PROCESSED']);a.close()
    a=Archive(ROOT/'temp/master_audit_v4/extra_poison.db',namespace='SIMULATION',create=True);spool_save(a,'ZUUU',json.dumps([42]).encode(),received);success=[]
    for i in range(3):
        try:recover(a);success.append('OK')
        except ValueError as e:success.append(str(e))
    case('poison_spool_restart','QUARANTINED_AND_RECOVERY_CONTINUES','REPEATED_FAILURE' if all(v!='OK' for v in success) else 'OK',attempts=success,processed_count=sum(r.get('kind')=='SPOOL_PROCESSED' for r in a.rows('engine_state')));a.close()
    lock=ROOT/'temp/master_audit_v4/instance.lock'
    with InstanceLock(lock):
        try:
            with InstanceLock(lock):actual='DUPLICATE_ACCEPTED'
        except RuntimeError:actual='DUPLICATE_REJECTED'
    with InstanceLock(lock):restarted=True
    case('instance_lock','DUPLICATE_REJECTED','DUPLICATE_REJECTED' if actual=='DUPLICATE_REJECTED' and restarted else actual,reacquired_after_release=restarted)
    # Full probability edge lattice, fixed before results; old/future residual injection uses forecast-only math.
    pmfchecks=0;mismatches=[]
    for forecast in (-100.,-80.5,-10.5,0.,.5,35.,80.5,100.):
        for residual in ([-2.,-1.,0.,1.,2.],[0.]*60,[-100.,0.,100.]*20):
            for method in ('GAUSSIAN_EXPANDING','EMPIRICAL_EXPANDING','KDE_EXPANDING_BW075'):
                p,_=distribution(forecast,residual,method);q=independent_distribution(forecast,residual,method);pmfchecks+=1
                if not np.allclose(p,q,atol=1e-12):mismatches.append({'forecast':forecast,'method':method})
                for alpha in (1.,.8,1.2):
                    z,_=calibrate(p,alpha);expected=independent_calibrate(q,alpha);pmfchecks+=1
                    if not np.allclose(z,expected,atol=1e-12) or abs(z.sum()-1)>1e-12 or z.min()<0:mismatches.append({'forecast':forecast,'method':method,'alpha':alpha})
    case('probability_extreme_lattice','ZERO_MISMATCHES',str(len(mismatches)),pmf_calibration_checks=pmfchecks,mismatches=mismatches)
    from src.realtime.contracts import targets
    boundary=[]
    for issue in ['2024-02-28T15:59:59+00:00','2024-02-28T16:00:00+00:00','2024-02-29T16:00:00+00:00','2025-12-31T15:59:59+00:00','2025-12-31T16:00:00+00:00','2026-09-30T16:00:00+00:00','2026-10-02T23:59:59+00:00','2026-10-03T00:00:00+00:00']:
        d=stamp(issue).astimezone(BJT).date();expected={'T0':d.isoformat(),'T1':(d+timedelta(days=1)).isoformat(),'T2':(d+timedelta(days=2)).isoformat()};actual=targets(issue);boundary.append({'issue':issue,'expected':expected,'actual':actual})
    case('calendar_boundary','ZERO_MISMATCHES',str(sum(v['expected']!=v['actual'] for v in boundary)),cases=boundary)
    return {'cases':len(cases),'evidence':cases}

if __name__=='__main__':
    stage('solar_independent',solar);stage('frozen_manifest',frozen);stage('extra_faults',extra_faults)
