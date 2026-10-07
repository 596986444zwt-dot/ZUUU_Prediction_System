"""Bounded network adapters and immutable true-receipt archives."""
import logging
import random
import time
from datetime import datetime, timedelta
import requests
from src.ecmwf_contract import HOURLY_VARIABLES, FIELD_MAP, validate_payload, archive_cycle, parse_utc
from src.parsers.zuuu_metar_parser import parse_metar, PARSER_VERSION
from .contracts import UTC, BJT, utc, iso, now, identity, require

class Fetcher:
    def __init__(self, cfg, session=None, sleeper=time.sleep):
        self.cfg=cfg; self.session=session or requests.Session(); self.sleeper=sleeper

    def get(self, endpoint, params):
        attempts=[]
        for n in range(self.cfg['max_attempts']):
            start=now(); tick=time.monotonic()
            try:
                response=self.session.get(endpoint,params=params,headers={'User-Agent':'ZUUU-Prediction-System/Phase10'},
                    timeout=(self.cfg['connect_timeout'],self.cfg['read_timeout']))
                content=response.content  # Complete receipt before timestamp.
                received=now()
                attempts.append(dict(time=iso(start),received_time=iso(received),latency=time.monotonic()-tick,http_status=response.status_code,success=response.ok))
                response.raise_for_status()
                return content, received, attempts
            except requests.RequestException as exc:
                if not attempts or attempts[-1]['time']!=iso(start): attempts.append(dict(time=iso(start),latency=time.monotonic()-tick,http_status=None,success=False))
                attempts[-1]['error_type']=type(exc).__name__
                if n+1<self.cfg['max_attempts']:
                    delay=min(self.cfg['max_backoff_seconds'],self.cfg['backoff_seconds']*2**n)
                    self.sleeper(delay+random.uniform(0,delay*.25))
        return None,now(),attempts

def archive_metar(db, payload, received, spool_id=None):
    import json
    require(isinstance(payload,list),'MALFORMED_METAR_PAYLOAD')
    added=0
    for index,v in enumerate(payload):
        try:
            require(isinstance(v,dict),'MALFORMED_METAR_RECORD')
            raw=v.get('rawOb'); rid=identity({'source':'AWC','raw':raw,'obsTime':v.get('obsTime')})
            identity(v)  # Verify the raw body can be archived before opening a transaction.
        except (ValueError,TypeError) as exc:
            raw_json=json.dumps(v,sort_keys=True,ensure_ascii=False)
            error=dict(source='ZUUU',reason=str(exc),error_type=type(exc).__name__,time=iso(received),
                       status='QUARANTINED',raw_payload_json=raw_json,spool_id=spool_id,member_index=index)
            with db.c:db.insert('errors',error)
            continue
        if any(r['_id']==rid for r in db.rows('zuuu_raw')): continue
        rawrecord=dict(raw_message=raw,raw_payload=v,raw_payload_hash=identity(v),source='AWC',station=v.get('icaoId'),
                       actual_ingest_time=iso(received),source_receipt_time=v.get('receiptTime'),issue_time=v.get('reportTime'))
        with db.c:
            db.insert('zuuu_raw',rawrecord,rid,received)
            try:
                require(isinstance(raw,str) and raw.strip(),'EMPTY_METAR')
                tokens=raw.split()
                # Transport prefix adapter; preserve original bytes and correction
                # class. Frozen parser already parses COR after station/time.
                prefix_correction=tokens[0]=='COR' or (len(tokens)>1 and tokens[0] in ('METAR','SPECI') and tokens[1]=='COR')
                parser_input=raw
                if prefix_correction:
                    if tokens[0]=='COR':tokens=['METAR']+tokens[1:]
                    else:tokens=[tokens[0]]+tokens[2:]
                    parser_input=' '.join(tokens)+' COR'
                parsed=parse_metar(parser_input)
                parsed['raw_report']=raw
                parsed['is_corrected']=parsed['is_corrected'] or prefix_correction
                require(parsed['station_id']=='ZUUU','WRONG_STATION')
                obs=datetime.fromtimestamp(v['obsTime'],UTC) if isinstance(v.get('obsTime'),(int,float)) else utc(v['obsTime'])
                require(obs<=utc(received)+timedelta(minutes=5),'FUTURE_OBSERVATION')
                require(parsed['metar_day']==obs.day and parsed['metar_hour']==obs.hour and parsed['metar_minute']==obs.minute,'OBSERVATION_IDENTITY_CONFLICT')
                temp=parsed['temperature_c']; require(isinstance(temp,int) and -80<=temp<=60,'INVALID_TEMPERATURE')
                norm=dict(parsed,raw_id=rid,observation_time=iso(obs),business_date=obs.astimezone(BJT).date().isoformat(),
                          actual_ingest_time=iso(received),parser_version=PARSER_VERSION,transport_adapter_version='PHASE10_COR_PREFIX_TRANSPORT_V1',
                          message_class='COR' if parsed['is_corrected'] else parsed['report_type'] or 'PREFIXLESS',source='AWC',qc_status='VALID',supersedes_raw_id=None)
                db.insert('zuuu_normalized',norm,rid,received); added+=1
            except (ValueError,KeyError,TypeError,OverflowError) as exc:
                db.insert('errors',dict(source='ZUUU',raw_id=rid,reason=str(exc),error_type=type(exc).__name__,
                    time=iso(received),status='QUARANTINED',spool_id=spool_id,member_index=index))
    return added

def archive_ecmwf(db, payload, run, received):
    rid=identity({'run':iso(run),'payload':payload,'model':'IFS_HRES'})
    if any(r['_id']==rid for r in db.rows('ecmwf_raw_runs')): return False
    with db.c:
        db.insert('ecmwf_raw_runs',dict(model='IFS_HRES',api_model='ecmwf_ifs025',run_time=iso(run),forecast_reference_time=iso(run),
            actual_ingest_time=iso(received),source_available_time=None,availability_basis='OBSERVED_LOCAL_RECEIPT',
            payload=payload,payload_hash=identity(payload),version_identity=rid,source='Open-Meteo Single Runs API'),rid,received)
        try:
            require(utc(run)<=utc(received),'FUTURE_RUN')
            hourly=validate_payload(payload,run)
            for i,t in enumerate(hourly['time']):
                row={FIELD_MAP[k]:hourly[k][i] for k in HOURLY_VARIABLES}
                row.update(run_id=rid,model='IFS_HRES',run_time_utc=iso(run),target_time_utc=iso(parse_utc(t)),
                    lead_hours=i,actual_ingest_time=iso(received),source_available_time=None,archive_cycle=archive_cycle(run,'ecmwf_ifs025'))
                db.insert('ecmwf_hourly',row,rid+'/'+str(i),received)
        except (ValueError,TypeError,KeyError) as exc:
            db.insert('errors',dict(source='ECMWF',run_id=rid,reason=str(exc),time=iso(received)))
            return False
    return True

def source_health(db,source,attempts,new_count,received, cfg):
    prior=next((r for r in reversed(db.rows('source_health')) if r['source']==source),{})
    successes=[a for a in attempts if a['success']]
    good=bool(successes)
    last_success=successes[-1].get('received_time',iso(received)) if good else prior.get('last_success_time')
    last_new=iso(received) if new_count else prior.get('last_new_data_time')
    age=(utc(received)-utc(last_new)).total_seconds()/3600 if last_new else None
    prefix='zuuu' if source=='ZUUU' else 'ecmwf'
    status='ERROR' if not good else 'STALE' if age is None or age>cfg[prefix+'_stale_hours'] else 'DELAYED' if age>cfg[prefix+'_delayed_hours'] else 'ONLINE'
    if good and source=='ECMWF' and not attempts[-1]['success'] and status=='ONLINE':status='DELAYED'
    record=dict(source=source,last_attempt_time=attempts[-1]['time'],last_success_time=last_success,last_new_data_time=last_new,
                consecutive_failures=0 if good else prior.get('consecutive_failures',0)+1,last_latency=attempts[-1]['latency'],
                data_age_hours=age,last_error=None if good else attempts[-1].get('error_type','HTTP_ERROR'),current_status=status,attempts=attempts)
    with db.c: db.insert('source_health',record)
    logging.getLogger('phase10.'+('zuuu_collector' if source=='ZUUU' else 'ecmwf_collector')).info('%s %s attempts=%d new=%d latency=%.3f',source,status,len(attempts),new_count,record['last_latency'])
    return record

def poll_zuuu(db,fetcher,cfg):
    import json
    from .spool import save,mark
    payload,received,attempts=fetcher.get(cfg['zuuu_endpoint'],{'ids':'ZUUU','format':'json','hours':cfg['zuuu_catchup_hours']})
    added=0
    if payload is not None:
        spool_id=save(db,'ZUUU',payload,received)
        with db.c: db.insert('engine_state',dict(kind='ZUUU_RESPONSE',actual_ingest_time=iso(received),raw_payload=payload.decode('utf-8',errors='replace')))
        terminal='PROCESSED'
        try: added=archive_metar(db,json.loads(payload),received,spool_id=spool_id)
        except (ValueError,TypeError) as exc:
            attempts[-1]['success']=False
            terminal='QUARANTINED'
            with db.c: db.insert('errors',dict(source='ZUUU',reason=str(exc),error_type=type(exc).__name__,
                time=iso(received),status=terminal,spool_id=spool_id))
        mark(db,spool_id,terminal)
    source_health(db,'ZUUU',attempts,added,received,cfg)
    return added

def poll_ecmwf(db,fetcher,cfg):
    import json
    from .spool import save,mark
    current=now(); latest=current.replace(hour=(current.hour//6)*6,minute=0,second=0,microsecond=0)
    added=0; combined=[]; received=current
    for i in reversed(range(cfg['ecmwf_catchup_runs'])):
        run=latest-timedelta(hours=6*i)
        # Recheck current/latest run content; older known runs are already durable.
        known=any(r['run_time']==iso(run) for r in db.rows('ecmwf_raw_runs'))
        if known and i>1: continue
        params={'latitude':30.576,'longitude':103.950,'hourly':','.join(HOURLY_VARIABLES),'models':'ecmwf_ifs025',
                'run':run.strftime('%Y-%m-%dT%H:%M'),'timezone':'UTC','forecast_hours':72}
        payload,received,attempts=fetcher.get(cfg['ecmwf_endpoint'],params); combined.extend(attempts)
        if payload is None: continue
        spool_id=save(db,'ECMWF',payload,received,run)
        try: added+=int(archive_ecmwf(db,json.loads(payload),run,received))
        except (ValueError,TypeError) as exc:
            with db.c: db.insert('errors',dict(source='ECMWF',reason=str(exc),time=iso(received)))
        mark(db,spool_id)
    if combined: source_health(db,'ECMWF',combined,added,received,cfg)
    return added
