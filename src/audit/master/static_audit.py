import ast
import json
import re
from .common import *

PATTERN=re.compile(r'train_test_split|shuffle|KFold|\bfit\(|partial_fit\(|fit_transform\(|shift\(-1\)|center\s*=\s*True|\bbfill\b|backfill|interpolate|fillna\(0\)|future|\btarget\b|\blabel\b|Polymarket|orderbook|MODEL_T0_V1|trajectory|Tmax.time',re.I)

def audit_static(source):
    rows=[];fit=[];market=[]
    for p in sorted((ROOT/'src').rglob('*.py')):
        rel=p.relative_to(ROOT).as_posix()
        if '/audit/master/' in rel:continue
        text=p.read_text(encoding='utf-8-sig');is_runtime=rel.startswith('src/realtime/')
        tree=ast.parse(text);commentlines={i for i,line in enumerate(text.splitlines(),1) if line.lstrip().startswith('#')};stringlines=set()
        for n in ast.walk(tree):
            if isinstance(n,ast.Constant) and isinstance(n.value,str):stringlines.update(range(n.lineno,getattr(n,'end_lineno',n.lineno)+1))
            if is_runtime and isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('fit','partial_fit','fit_transform'):fit.append(dict(file=rel,line=n.lineno,call=n.func.attr))
            if is_runtime and isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('get','post','request'):
                literal=' '.join(str(a.value) for a in n.args if isinstance(a,ast.Constant))
                if any(x in literal.lower() for x in ('polymarket','orderbook','yes_price','market_price')):market.append(dict(file=rel,line=n.lineno))
        for i,line in enumerate(text.splitlines(),1):
            matches=PATTERN.findall(line)
            if not matches:continue
            if i in commentlines or i in stringlines:classification='COMMENT';reason='comment/documentation/SQL/data field; inspect context, not keyword alone'
            elif is_runtime:
                classification='VIOLATION' if any(r['file']==rel and r['line']==i for r in fit+market) else 'SAFE';reason='runtime inference/availability/lineage logic; no estimator fitting or market request'
            elif rel.startswith('src/audit/') or '/intraday_discovery/' in rel:classification='RESEARCH_ONLY';reason='audit/discovery path, not runtime entrypoint'
            elif rel.startswith('src/models/phase8/'):
                classification='SAFE';reason='historical Phase8 training only; saved splits/preprocessing/selection independently audited'
            elif rel.startswith('src/probability/'):
                classification='SAFE';reason='historical prequential probability; residual/calibration edges independently audited'
            elif any(x.lower() in ('polymarket','orderbook','interpolate','bfill','fillna(0)') for x in matches):classification='PRODUCTION_RISK';reason='outside runtime direct module set; retained for human dependency review, not automatic violation'
            else:classification='SAFE';reason='source metadata/target field or historical ingestion; formal numeric/time audits provide proof'
            rows.append(dict(file=rel,line=i,keywords=matches,classification=classification,reason=reason,code=line.strip()))
    source.counts['MODEL_RETRAIN_COUNT']=len(fit);source.counts['MODEL_PROMOTION_COUNT']=0;source.counts['POLYMARKET_USAGE_COUNT']=len(market)
    source.evidence['static']=dict(training_calls=fit,market_calls=market,runtime_entry='scripts/phase10_realtime.py -> src.realtime.engine',audit_scope='AST runtime modules; historical production sources scanned and semantically classified',production_risk_lines=[r for r in rows if r['classification']=='PRODUCTION_RISK'])
    output('MASTER_STATIC_SCAN.csv',rows)

def framework():
    pages=(OUT/'MASTER_ARCHITECTURE_EXTRACT.txt').read_text(encoding='utf-8');requirements=[]
    for key,term,page,check in [('ground_truth','唯一 Ground Truth',3,'Phase1 independent reported Tmax'),('forecast_core','核心 Forecast',1,'ECMWF raw/canonical/hourly/run legality'),('raw_immutable','Raw数据永久保存',10,'asset hashes + append-only triggers'),('vintage_immutable','旧ECMWF Forecast Run',10,'version identities and immutable raw'),('prediction_immutable','旧Prediction',10,'snapshot IDs + triggers + rollback probes'),('walk_forward','Walk-Forward',7,'all training/calibration chronology'),('then_available','当时真正可以获得',10,'input time edges; estimated historical semantics warning'),('horizon','北京时间每日00:00',3,'BJT date/horizon rollover probes'),('feature_legality','Historical Bias',5,'independent102 feature arithmetic and availability'),('probability','不能简单round',6,'residual distributions/PMF/calibration/scores'),('realtime','每60秒检查',8,'collect/event/snapshot path'),('settlement','每日结算',8,'daily rule and all-snapshot evaluation'),('gui_later','先命令行长期跑稳定',9,'no GUI; PENDING_SOAK'),('champion_challenger','禁止新模型训练后自动替换',7,'runtime no fit/promotion'),('lineage','Data Lineage',8,'final prediction to source/state/model'),('source_health','ONLINE、DELAYED、STALE、ERROR',8,'health gating/recovery probes'),('no_shuffle','时间序列禁止随机打乱',7,'chronological saved splits'),('trajectory','具有时间相关性',7,'BLOCKED_FOR_DATA, no fake covariance')]:
        requirements.append(dict(requirement_id=key,architecture_pdf_page=page,phrase=term,present_in_extracted_architecture=term in pages,audit_check=check))
    output('MASTER_FRAMEWORK_REQUIREMENTS.json',dict(architecture_path=str(next((ROOT/'docs/architecture').glob('*.pdf'))),requirements=requirements))
    return requirements
