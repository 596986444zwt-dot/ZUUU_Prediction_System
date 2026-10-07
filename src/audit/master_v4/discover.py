import pathlib, sqlite3, json, importlib.util, datetime
ROOT=pathlib.Path(__file__).resolve().parents[3]
OUT=ROOT/'temp/master_audit_v4'
def dump(name,obj): (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
details={}
for p in (ROOT/'database').glob('*.db'):
    c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    c.row_factory=sqlite3.Row
    tables={}
    for t, in c.execute("select name from sqlite_master where type='table' and name!='sqlite_sequence'"):
        row=c.execute('select * from '+t+' limit 1').fetchone()
        tables[t]={'columns':[dict(r) for r in c.execute('pragma table_info('+t+')')], 'count':c.execute('select count(*) from '+t).fetchone()[0], 'example':dict(row) if row else None}
    details[p.name]=tables
    c.close()
dump('table_details.json',details)
modules={m:bool(importlib.util.find_spec(m)) for m in ['fitz','pdfplumber','PyPDF2']}
print(modules)
pdf=next((ROOT/'docs/architecture').glob('*.pdf'))
if modules['fitz']:
    import fitz
    document=fitz.open(pdf)
    (OUT/'architecture.txt').write_text('\n'.join(p.get_text() for p in document),encoding='utf-8')
elif modules['pdfplumber']:
    import pdfplumber
    with pdfplumber.open(pdf) as document: (OUT/'architecture.txt').write_text('\n'.join(p.extract_text() or '' for p in document.pages),encoding='utf-8')
elif modules['PyPDF2']:
    from PyPDF2 import PdfReader
    (OUT/'architecture.txt').write_text('\n'.join(p.extract_text() or '' for p in PdfReader(pdf).pages),encoding='utf-8')
else: print('PDF_READER_UNAVAILABLE')
print(json.dumps({k:{t:v['count'] for t,v in ts.items()} for k,ts in details.items()},indent=2))
