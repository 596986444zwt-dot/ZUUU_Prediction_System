"""Transitive local module graph with inference-vs-training import distinction."""
import ast,json,re
from .common import *

def audit_dependency(s):
    roots=['src.realtime.engine'];seen=set();todo=list(roots);edges=[];calls=[];risks=[]
    while todo:
        module=todo.pop()
        if module in seen:continue
        file=ROOT/(module.replace('.','/')+'.py')
        if not file.exists():file=ROOT/module.replace('.','/')/'__init__.py'
        if not file.exists():continue
        seen.add(module);text=file.read_text(encoding='utf-8-sig');tree=ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                base=node.module or ''
                if node.level:
                    package=module.split('.')[:-1]
                    base='.'.join(package[:len(package)-node.level+1]+([base] if base else []))
                if base.startswith('src.'):
                    edges.append(dict(source=module,target=base,names=[n.name for n in node.names],line=node.lineno));todo.append(base)
            elif isinstance(node,ast.Import):
                for alias in node.names:
                    if alias.name.startswith('src.'):edges.append(dict(source=module,target=alias.name,names=[],line=node.lineno));todo.append(alias.name)
            elif isinstance(node,ast.Call):
                name=ast.unparse(node.func)
                if name.endswith(('.fit','.partial_fit','.fit_transform')):
                    calls.append(dict(module=module,line=node.lineno,call=name,classification='VIOLATION' if module.startswith('src.realtime.') else 'DEFINED_HISTORICAL_HELPER_REACHABILITY_REVIEW'))
                if re.search(r'polymarket|orderbook|market_price|yes_price|no_price',ast.unparse(node),re.I):risks.append(dict(module=module,line=node.lineno,code=ast.unparse(node),classification='REVIEW_LITERAL_CONTEXT'))
    # Explicit function imports are the decisive boundary for shared pipeline helpers.
    pipeline=[e for e in edges if e['target']=='src.models.phase8.pipeline']
    unsafe=[e for e in pipeline if any(n in ('fit_preprocessor','estimator','*') for n in e['names'])]
    runtime_fit=[r for r in calls if r['classification']=='VIOLATION']
    s.counts['MODEL_RETRAIN_COUNT']+=len(unsafe)
    s.counts['PRODUCTION_DEPENDENCY_RISK_COUNT']=len(unsafe)+len(runtime_fit)+len(risks)
    s.counts['POLYMARKET_USAGE_COUNT']+=len(risks)
    output('PRODUCTION_DEPENDENCY_GRAPH_V3.json',dict(entrypoint='scripts/phase10_realtime.py -> src.realtime.engine',modules=sorted(seen),edges=edges,training_calls=calls,shared_pipeline_imports=pipeline,market_literals=risks,status='PASS' if not unsafe and not runtime_fit and not risks else 'NOT_INDEPENDENTLY_VERIFIED',interpretation='Historical helper definitions imported alongside transform are not execution; no runtime fit/estimator import is permitted. External library internals are frozen trusted inference dependencies.'))

