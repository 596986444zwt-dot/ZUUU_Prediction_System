"""Fresh raw-header proof, component context and direct network evidence."""
from collections import Counter,defaultdict
import re
from .common import *
from .database_audit import daily,report_temperature

def run():
    c=snapshot(ROOT/'database/zuuu_prediction.db')
    truth={r['business_date_bjt']:r for r in records(c,'zuuu_target_v1')};silver=records(c,'zuuu_silver_observation');bronze={r['id']:r for r in records(c,'zuuu_raw_metar')}
    groups=defaultdict(list);classes=Counter();bad=[]
    for row in silver:
        raw=bronze[row['bronze_raw_id']];text=raw['raw_metar'];head=text.strip().upper().split()[0]
        kind=head if head in ('METAR','SPECI','COR','AMD') else 'PREFIXLESS' if head=='ZUUU' else 'OTHER';classes[kind]+=1
        t=stamp(raw['observation_time_utc']);day=t.astimezone(BJT).date().isoformat();token=re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b',text)
        identity=hashlib.sha256((raw['source'].upper()+'\x1f'+t.isoformat()+'\x1f'+text).encode()).hexdigest()
        failures=[]
        if identity!=raw['raw_identity']:failures.append('RAW_IDENTITY_HASH')
        if token is None or tuple(map(int,token.groups()))!=(t.day,t.hour,t.minute):failures.append('RAW_TIME_GROUP')
        if t!=stamp(row['observation_time_utc']) or day!=row['business_date_bjt']:failures.append('FULL_TIME_DAY_MAPPING')
        if kind!=raw['message_class'] or kind!=row['message_class'] or int(kind=='COR')!=row['is_correction']:failures.append('PREFIX_COR_CLASS')
        parsed=dict(row,temperature_c=report_temperature(text),observation_time_utc=t.isoformat(),message_class=kind,is_correction=int(kind=='COR'))
        groups[day].append(parsed)
        if failures:bad.append(dict(bronze_id=raw['id'],silver_id=row['id'],failures=failures))
    for day in sorted(truth):
        computed=daily(groups[day]);stored=truth[day]
        failures=[k for k,v in computed.items() if v!=(json.loads(stored[k]) if k.endswith('_ids') else stored[k])]
        if failures:bad.append(dict(date=day,failures=failures))
    output('RAW_HEADER_TIME_IDENTITY_V3.json',dict(status='PASS' if not bad else 'FAIL',observations=len(silver),target_days=len(truth),classification=dict(classes),mismatch_count=len(bad),mismatches=bad,non_target_boundary_days=sorted(set(groups)-set(truth)),independent_day_source='Bronze full observation UTC and raw integer temperature; Silver IDs used only for lineage. All valid correction versions retained per frozen DAILY_TMAX_RULE_V1; no guessed supersession.'))
    c.close()
    c=snapshot(ROOT/'database/phase10_realtime_v1.db')
    tables={name:payloads(c,'realtime_'+name) for name in ('feature_snapshots','continuous_predictions','probability_predictions','prediction_snapshots','probability_state','source_health','events','ecmwf_raw_runs','zuuu_raw','model_registry')}
    stores={name:{r['_id']:r for r in rows} for name,rows in tables.items()};checks=[];missing=[]
    for snap in tables['prediction_snapshots']:
        identity_errors=[];lineage_errors=[];component_keys={}
        for fk,table in [('feature_id','feature_snapshots'),('continuous_id','continuous_predictions'),('probability_id','probability_predictions')]:
            component=stores[table][snap[fk]];component_keys[table]=list(component)
            for k in ('horizon','target_business_date','prediction_issue_time'):
                if k in component and component[k]!=snap[k]:identity_errors.append(table+'/'+k)
        # event_id is the domain identity; append-only event records add
        # /PENDING and /DONE to their storage keys. Compare domain to domain.
        if snap.get('event_id') and not any(e.get('event_id')==snap['event_id'] for e in tables['events']):lineage_errors.append('event')
        state=stores['probability_state'][snap['probability_state_version']]
        if stamp(state['cutoff'])>stamp(snap['prediction_issue_time']):identity_errors.append('STATE_CUTOFF')
        checks.append(dict(prediction_id=snap['_id'],identity_errors=identity_errors,lineage_errors=lineage_errors,component_fields=component_keys,metadata_fields=list(snap),source_context='ZUUU is source-health/context only; not a formal intraday model feature',status='FAIL' if identity_errors or lineage_errors else 'PASS'))
    output('PRODUCTION_COMPONENT_CONTEXT_V3.json',dict(status='PASS' if not any(r['status']=='FAIL' for r in checks) else 'FAIL',checks=checks,current_formal_mismatch_count=sum(r['status']=='FAIL' for r in checks),scope='All actual production snapshots; isolated guard acceptance vulnerabilities separately reported'))
    health=[]
    for row in tables['source_health']:
        failed=[a for a in row.get('attempts',[]) if a.get('http_status')==400]
        if row.get('source')=='ECMWF' and failed:
            subsequent=[s for s in tables['prediction_snapshots'] if stamp(s['prediction_issue_time'])>=stamp(row['last_attempt_time'])]
            health.append(dict(health_id=row['_id'],source_status=row['current_status'],attempt=row['last_attempt_time'],http400_attempts=failed,subsequent_snapshots=[dict(prediction_id=s['_id'],issue=s['prediction_issue_time'],status=s['status'],reason=s.get('reason_code'),run=s['selected_run']) for s in subsequent[:4]],remote_root_cause='NOT_YET_PROVEN: no reliable per-attempt requested run parameters'))
    output('DEPLOYMENT_NETWORK_EVIDENCE_V3.json',dict(deployment_network_validation='PENDING_SOAK',mainland_network_is_hard_gate=False,http400_evidence=health,no_live_requests=True,interpretation='HTTP400 is an observed unsuccessful request, not proof of a future run or a remote outage. Prior legal complete runs and status are independently checked in runtime reproduction.'))
    c.close()
    docs=[]
    for directory in sorted((ROOT/'docs').glob('phase*')):
        if not directory.is_dir():continue
        for path in sorted(directory.glob('*')):
            if path.suffix in ('.md','.json') and any(k in path.name.upper() for k in ('PROTOCOL','CONTRACT','MANIFEST','ACCEPTANCE','BUILD_REPORT','SUMMARY','REGISTRY')):
                content=path.read_text(encoding='utf-8-sig');docs.append(dict(path=str(path),sha256=digest(path),characters_read=len(content),role='frozen definition or prior evidence reference; verdict not substituted'))
    output('FORMAL_DOCUMENT_READ_REGISTRY_V3.json',docs)
    print('ADDITIONAL_RAW_CONTEXT',len(silver),len(truth),'raw mismatches',len(bad),'production context mismatches',sum(r['status']=='FAIL' for r in checks),flush=True)

if __name__=='__main__':run()
