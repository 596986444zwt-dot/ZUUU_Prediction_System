"""Immutable snapshots and explicit projections; answers never enter feature context."""
import contextlib
import io
import json
from collections import defaultdict
from src.data_v1.source_io import open_snapshot, sha256_file, sidecar_state
from src.mos.data import guardian as earlier_guardian
from src.mos_review.storage import semantic_hash as phase6_hash
from src.mos.schema import read as read6
from .contracts import SOURCES, PHASE6_SEMANTIC, require, ROOT

def fingerprints():
    result={}
    for name,(path,expected) in SOURCES.items():
        digest=sha256_file(path)
        require(digest==expected,'Frozen source SHA mismatch: '+name)
        result[name]={'sha256':digest,'sidecars':sidecar_state(path)}
    return result

def guardian():
    before=fingerprints()
    with contextlib.redirect_stdout(io.StringIO()):
        _,checks,_=earlier_guardian()
    c=open_snapshot(*SOURCES['phase6'])
    try:
        integrity=[r[0] for r in c.execute('PRAGMA integrity_check')]
        fk=len(c.execute('PRAGMA foreign_key_check').fetchall())
        d=read6(c);semantic=phase6_hash(d)
        require(integrity==['ok'] and fk==0 and semantic==PHASE6_SEMANTIC,'Phase6 integrity/semantic failure')
        m=d['manifest'][0]
        require(m['build_status']=='PASS' and m['candidate']=='NONE','Phase6 conclusion must remain PASS/NONE')
        require(all(sha256_file(ROOT/p)==h for p,h in json.loads(m['implementation_sha256_json']).items()),'Frozen Phase6 implementation differs from manifest')
        checks['phase6']=dict(integrity_check=integrity,foreign_key_check=fk,semantic_sha256=semantic,
            row_counts={t:len(v) for t,v in d.items()},contract=json.loads(m['contract_json']),
            schema=[dict(r) for r in c.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")])
    finally:c.close()
    require(fingerprints()==before,'Source mutation during guardian')
    return before,checks

def load():
    c=open_snapshot(*SOURCES['phase4'])
    try:
        base=[dict(r) for r in c.execute("SELECT * FROM phase4_data_v1_sample WHERE horizon IN ('T1','T2') ORDER BY business_date_bjt,horizon")]
        hours=defaultdict(list)
        for r in c.execute("SELECT * FROM phase4_data_v1_ecmwf_hourly WHERE horizon IN ('T1','T2') ORDER BY business_date_bjt,horizon,target_time_utc"):
            hours[r['business_date_bjt'],r['horizon']].append(dict(r))
        solar=defaultdict(list)
        for r in c.execute('SELECT * FROM phase4_data_v1_solar ORDER BY target_time'):solar[r['business_date_bjt']].append(dict(r))
    finally:c.close()
    excluded=[r for r in base if r['sample_status']!='ELIGIBLE']
    require(len(excluded)==1 and excluded[0]['business_date_bjt']=='2025-08-07' and excluded[0]['horizon']=='T2' and excluded[0]['trajectory_valid_hours']==14,'Known gap changed')
    base=[r for r in base if r['sample_status']=='ELIGIBLE']
    require([sum(r['horizon']==h for r in base) for h in ('T1','T2')]==[729,728],'Phase4 universe mismatch')
    labels={r['business_date_bjt']:r['target_tmax_c'] for r in base}
    # Remove label and audit-only target-derived fields before any feature computation.
    samples=[{k:v for k,v in r.items() if k not in ('target_tmax_c',)} for r in base]
    p=open_snapshot(*SOURCES['production'])
    try:
        runs={r['run_time_utc']:dict(r) for r in p.execute("SELECT * FROM ecmwf_archive_v1 WHERE canonical_status='AVAILABLE' ORDER BY run_time_utc")}
        trajectories=defaultdict(dict)
        for r in p.execute('SELECT h.id,h.raw_run_id,h.target_time_utc,h.temperature_2m_c,h.lead_hours FROM ecmwf_hourly_forecasts h JOIN ecmwf_archive_v1 a ON h.raw_run_id=a.canonical_raw_run_id WHERE a.canonical_status="AVAILABLE"'):
            require(r['target_time_utc'] not in trajectories[r['raw_run_id']],'Duplicate canonical hourly target')
            trajectories[r['raw_run_id']][r['target_time_utc']]=dict(r)
    finally:p.close()
    q=open_snapshot(*SOURCES['phase6'])
    try:
        states={(r['target_business_date'],r['horizon'],r['model']):dict(r) for r in q.execute('SELECT * FROM phase6_mos_bias_state')}
        predictions={(r['target_business_date'],r['horizon'],r['model']):dict(r) for r in q.execute('SELECT * FROM phase6_mos_prediction')}
        history={(r['business_date_bjt'],r['horizon']):dict(r) for r in q.execute('SELECT * FROM phase6_mos_sample')}
    finally:q.close()
    return dict(samples=samples,labels=labels,hours=hours,solar=solar,runs=runs,trajectories=trajectories,states=states,predictions=predictions,history=history,excluded=excluded)
