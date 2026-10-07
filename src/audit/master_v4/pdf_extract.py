"""Minimal read-only text extraction for the project's ReportLab PDF.
Records decoded streams; no external dependency installation.
"""
import pathlib,re,zlib,base64,json
R=pathlib.Path(__file__).resolve().parents[3];O=R/'temp/master_audit_v4'
b=next((R/'docs/architecture').glob('*.pdf')).read_bytes()
streams=[]
for m in re.finditer(rb'(\d+)\s+0\s+obj(.*?)endobj',b,re.S):
    body=m[2];s=re.search(rb'stream\r?\n(.*?)endstream',body,re.S)
    if not s:continue
    data=s[1].strip()
    try:
        if b'ASCII85Decode' in body:data=base64.a85decode(data,adobe=True)
        if b'FlateDecode' in body:data=zlib.decompress(data)
        streams.append((int(m[1]),data))
    except Exception as e:streams.append((int(m[1]),str(e).encode()))
(O/'pdf_streams.txt').write_text('\n\n'.join(f'OBJECT {n}\n'+v.decode('latin1') for n,v in streams),encoding='utf-8')
texts=[]
for n,data in streams:
    for m in re.finditer(rb'\(((?:\\.|[^\\)])*)\)\s*Tj',data):
        s=m[1]
        def unesc(m):
            v=m[1]
            if v[:1].isdigit():return bytes([int(v,8)])
            return {b'n':b'\n',b'r':b'\r',b't':b'\t'}.get(v,v)
        s=re.sub(rb'\\([0-7]{1,3}|.)',unesc,s)
        try:text=s.decode('utf-16-be') if b'\x00' in s or any(v>127 for v in s) else s.decode('ascii')
        except UnicodeError:text=s.decode('latin1')
        texts.append(text)
(O/'architecture.txt').write_text('\n'.join(texts),encoding='utf-8')
print('streams',len(streams),'text fragments',len(texts));print('\n'.join(texts)[:18000])
