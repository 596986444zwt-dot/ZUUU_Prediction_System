"""Produce discovery reports only; source DBs never opened for writing.

Run from project root: python -B -m src.audit.intraday_discovery_v1
Exit 0 = discovery completed (may conclude BLOCKED); exit 1 = audit/tool failure.
"""
import ast
import json
from datetime import datetime, timezone
from src.data_v1.source_io import open_snapshot, sha256_file
from src.intraday_discovery.contracts import ROOT, REPORT_DIR, SOURCE_HASHES, fingerprints, require, validate_feature_candidates, FEATURE_ALLOWED
from src.intraday_discovery.observations import analyze as observations
from src.intraday_discovery.ecmwf import analyze as ecmwf
from src.intraday_discovery.reporting import write_reports

def scan_project():
    inventory=[]
    for folder in ('src','database','tests','docs','config'):
        for p in sorted((ROOT/folder).rglob('*')):
            if p.is_file() and p.suffix in {'.py','.md','.sql','.json','.toml','.ini','.yaml','.yml'} and '__pycache__' not in p.parts and 'intraday' not in p.as_posix().lower():
                if p.suffix=='.py': ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p))
                inventory.append({'path':p.relative_to(ROOT).as_posix(),'sha256':sha256_file(p)})
    evidence=[]
    needles={
        'src/database/zuuu_raw_archive.py':['now_utc = datetime.now','ingest_time_utc TEXT','created_at_utc TEXT'],
        'src/collectors/zuuu_historical_raw_importer.py':['class SourceRecord','observation_time = datetime.strptime','source="IEM"'],
        'src/collectors/zuuu_ogimet_recovery_commit.py':['source="OGIMET"'],
        'src/builders/zuuu_silver_builder.py':['is_correction =','supersedes_raw_id = None','created_at_utc ='],
        'src/collectors/zuuu_raw_storage.py':['"receiptTime"','ingest_time_utc = datetime.now','"reportTime"'],
        'src/collectors/zuuu_metar_collector.py':['now_utc = datetime.now'],
        'src/parsers/zuuu_metar_temperature_parser.py':['TEMP_DEW_RE =','temperature_c: int'],
        'src/audit/ecmwf_issue_rule_final_audit.py':['def choose_latest_complete','if run["_available"] > cutoff:','if present == 24 and valid == 24:'],
        'src/auxiliary/solar/features.py':['ALGORITHM_VERSION ='],
        'docs/phase4/PHASE4_DATA_V1.md':['official_schedule_estimate'],
    }
    for path, tokens in needles.items():
        for line,text in enumerate((ROOT/path).read_text(encoding='utf-8-sig').splitlines(),1):
            if any(t in text for t in tokens): evidence.append({'path':path,'line':line,'text':text.strip()})
    return inventory,evidence

def run():
    before=fingerprints()  # Hard gate before report creation or discovery calculations.
    inventory,evidence=scan_project()
    checks={}
    solar={}
    for name,sha in SOURCE_HASHES.items():
        conn=open_snapshot(ROOT/'database'/name,sha)
        try:
            integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
            fk=conn.execute('PRAGMA foreign_key_check').fetchall()
            require(integrity=='ok' and not fk, f'{name}: integrity/FK failure')
            checks[name]={'integrity_check':integrity,'foreign_key_check':len(fk),
                'tables':[dict(r) for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")]}
            if name=='zuuu_prediction.db':
                obs=observations(conn)
                ec=ecmwf(conn,[r['business_date_bjt'] for r in obs['targets']])
            if name=='phase3_auxiliary_v1.db':
                solar_rows=[dict(r) for r in conn.execute('SELECT target_time,business_date_bjt,hour_bjt,availability_basis,source_version,coordinate_version,latitude,longitude FROM aux_v1_solar_time')]
                require(len(solar_rows)==17496, 'Solar count mismatch')
                from src.intraday_discovery.contracts import utc, BJT
                require(len({r['target_time'] for r in solar_rows})==17496, 'Solar duplicates')
                require(all(utc(r['target_time']).astimezone(BJT).date().isoformat()==r['business_date_bjt']
                    and utc(r['target_time']).astimezone(BJT).hour==r['hour_bjt']
                    and r['availability_basis']=='DETERMINISTIC_NOT_APPLICABLE'
                    and r['latitude']==30.576 and r['longitude']==103.950 for r in solar_rows), 'Solar contract mismatch')
                solar={'rows':17496,'candidate_snapshot_rows':sum(6<=r['hour_bjt']<=17 for r in solar_rows),
                    'algorithm_versions':sorted({r['source_version'] for r in solar_rows}),
                    'coordinate_versions':sorted({r['coordinate_version'] for r in solar_rows}),
                    'availability_basis':'DETERMINISTIC_NOT_APPLICABLE'}
                meteostat=[dict(r) for r in conn.execute('SELECT availability_basis,count(*) AS parameter_rows FROM aux_v1_meteostat_hourly GROUP BY availability_basis')]
        finally: conn.close()
    validate_feature_candidates(FEATURE_ALLOWED)
    for row in obs['snapshots']:
        require(row['PRE_PEAK']+row['EQUAL_PEAK']+row['POST_PEAK']==729, 'Snapshot partition error')
    require(sum(r['days'] for r in obs['peak_distribution'])==729, 'Peak histogram mismatch')
    require(all(sha256_file(ROOT/r['path'])==r['sha256'] for r in inventory), 'Existing project file changed during discovery')
    after=fingerprints()
    require(before==after,'Source DB changed during discovery')
    result={'task':'INTRADAY_DISCOVERY_V1','generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'execution_status':'DISCOVERY_COMPLETED','admission':'BLOCKED_FOR_INTRADAY_DATA_V1',
        'observation_availability':'HISTORICAL_ZUUU_INGEST_NOT_OBSERVED',
        'strict_observation_replay':'STRICT HISTORICAL OBSERVATION AS-OF REPLAY NOT YET PROVEN',
        'sources_before':before,'sources_after':after,'source_db_modified':'NO','source_checks':checks,
        'code_inventory':inventory,'code_evidence':evidence,'git_metadata_present':(ROOT/'.git').exists(),
        'observations':obs,'ecmwf':ec,'solar':solar,'meteostat_metadata':meteostat,
        'meteostat_training':'BLOCKED','legacy_T0_alias':'LEGACY_T0_21_BENCHMARK (conceptual only; frozen objects unchanged)',
        'feature_values_built':False,'formal_intraday_database_created':False}
    write_reports(REPORT_DIR,result)
    require(fingerprints()==before,'Source changed during report publication')
    return result

def main():
    try:
        result=run()
        print('INTRADAY DISCOVERY V1: DISCOVERY_COMPLETED')
        print('ZUUU availability:',result['observation_availability'])
        print('Peak minutes BJT:',result['observations']['first_peak_minutes_bjt'])
        for s in result['observations']['snapshots']:
            print(s['snapshot_bjt'],'PRE/EQUAL/POST:',s['PRE_PEAK'],s['EQUAL_PEAK'],s['POST_PEAK'])
        for r in result['ecmwf']['coverage']:
            print(r['snapshot_bjt'],r['policy'],'complete/partial/no temperature:',r['complete_24h_days'],r['partial_days'],r['completely_unavailable_days'],r['run_cycle_day_offset_distribution'])
        print('Source DB modified: NO')
        print(result['admission'])
        return 0
    except Exception as exc:
        print(f'INTRADAY DISCOVERY FAILED: {type(exc).__name__}: {exc}')
        return 1

if __name__=='__main__':
    raise SystemExit(main())
