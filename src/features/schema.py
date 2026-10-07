"""Separate X/Y projections, value masks and shared input bundles; append-only seal."""
import hashlib
import json

PREFIX='phase7_'
KEYS={'feature_registry':('feature_name',),'feature_sample':('sample_id',),'feature_label':('sample_id',),
 'feature_source_lineage':('bundle_id',),'feature_history_label':('label_business_date','horizon'),
 'feature_history_state':('sample_id','model'),'feature_value':('sample_id','feature_name'),'feature_qc':('sample_id','feature_name','flag'),
 'manifest':('version',)}

def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)

def semantic_hash(data,contract):
    h=hashlib.sha256((canonical(contract)+'\n').encode())
    for t,k in KEYS.items():
        if t=='manifest':continue
        h.update((t+'\n').encode())
        for row in sorted(data[t],key=lambda r:tuple(r[x] for x in k)):
            h.update((canonical({x:v for x,v in row.items() if x!='created_at'})+'\n').encode())
    return h.hexdigest()

def populate(c,data):
    c.execute('PRAGMA foreign_keys=ON')
    for t,keys in KEYS.items():
        cols={k:next((r[k] for r in data[t] if r[k] is not None),None) for k in data[t][0]} if data[t] else {'sample_id':'','feature_name':'','flag':''}
        fields=[k+' '+('INTEGER' if isinstance(v,int) else 'REAL' if isinstance(v,float) or k=='value' else 'TEXT')+(' NOT NULL' if k in keys else '') for k,v in cols.items()]
        fields.append('PRIMARY KEY('+','.join(keys)+')')
        if t=='feature_sample':fields+=['CHECK(horizon IN (\'T1\',\'T2\'))']
        if t in ('feature_label','feature_value','feature_history_state','feature_qc'):fields+=['FOREIGN KEY(sample_id) REFERENCES phase7_feature_sample(sample_id)']
        if t in ('feature_value','feature_qc'):fields+=['FOREIGN KEY(feature_name) REFERENCES phase7_feature_registry(feature_name)']
        if t=='feature_value':fields+=['FOREIGN KEY(bundle_id) REFERENCES phase7_feature_source_lineage(bundle_id)','CHECK((value IS NULL AND is_available=0 AND missing_reason IS NOT NULL) OR (value IS NOT NULL AND is_available=1 AND missing_reason IS NULL))']
        c.execute('CREATE TABLE '+PREFIX+t+' ('+','.join(fields)+')')
        if data[t]:
            names=list(data[t][0]);c.executemany('INSERT INTO '+PREFIX+t+' ('+','.join(names)+') VALUES ('+','.join('?' for _ in names)+')',[[r[k] for k in names] for r in data[t]])
    for t in KEYS:
        for action in ('UPDATE','DELETE'):
            c.execute(f"CREATE TRIGGER {PREFIX}{t}_{action.lower()} BEFORE {action} ON {PREFIX}{t} BEGIN SELECT RAISE(ABORT,'FEATURE_V1 immutable'); END")
        c.execute(f"CREATE TRIGGER {PREFIX}{t}_seal BEFORE INSERT ON {PREFIX}{t} WHEN EXISTS(SELECT 1 FROM phase7_manifest) BEGIN SELECT RAISE(ABORT,'FEATURE_V1 sealed'); END")
    # No answer/label fields in X; metadata stays in a separate sample view.
    features=[r['feature_name'] for r in data['feature_registry'] if r['status'] in ('ACCEPTED','CONDITIONAL')]
    pivot=','.join("MAX(CASE WHEN feature_name='"+n+"' THEN value END) AS "+n for n in features)
    c.execute('CREATE VIEW training_feature_view AS SELECT sample_id,'+pivot+' FROM phase7_feature_value GROUP BY sample_id')
    c.execute('CREATE VIEW training_label_view AS SELECT sample_id,label_tmax_c FROM phase7_feature_label')
    c.execute('CREATE VIEW training_sample_view AS SELECT * FROM phase7_feature_sample')
    c.execute('CREATE VIEW training_feature_mask_view AS SELECT sample_id,feature_name,is_available,missing_reason FROM phase7_feature_value')
    c.execute('CREATE VIEW phase7_feature_availability AS SELECT v.sample_id,v.feature_name,v.feature_available_time,s.issue_time_utc,v.is_available FROM phase7_feature_value v JOIN phase7_feature_sample s USING(sample_id)')
    c.execute('CREATE VIEW phase7_feature_matrix AS SELECT s.*,x.*,y.label_tmax_c FROM training_sample_view s JOIN training_feature_view x USING(sample_id) JOIN training_label_view y USING(sample_id)')
    c.execute("CREATE VIEW phase7_historical_label_audit AS SELECT b.sample_id,b.model,h.*,s.issue_time_utc FROM phase7_feature_history_state b,json_each(b.training_dates_json) j JOIN phase7_feature_sample s ON s.sample_id=b.sample_id JOIN phase7_feature_history_label h ON h.label_business_date=j.value AND h.horizon=s.horizon")
    c.commit()

def read(c):return {t:[dict(r) for r in c.execute('SELECT * FROM '+PREFIX+t+' ORDER BY '+','.join(k))] for t,k in KEYS.items()}
