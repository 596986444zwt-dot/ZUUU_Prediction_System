"""Export final audit evidence; never modify production assets."""
from pathlib import Path
import hashlib,json,zipfile,re,html
from datetime import datetime,timezone
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from .guardian import after

ROOT=Path(r'C:\ZUUU_Prediction_System')
OUT=ROOT/'docs/master_audit_v3'
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def main():
    rich=json.loads((OUT/'MA001_RICH_VALID_SNAPSHOT_FINAL_V3.json').read_text(encoding='utf-8'))
    assert rich['status']=='PASS' and rich['original_pmf_sha256']==rich['saved_pmf_sha256']
    readme='''# 给下一位 AI 的阅读说明\n\n本包为 ZUUU SYSTEM V1 第三轮只读独立审计的原始报告与证据。\n\n最终结论：MASTER_AUDIT_V3 = FAIL。CRITICAL=0，HIGH=1。可以进入问题合并，不能开始 Soak。\n\n首先阅读 MASTER_AUDIT_V3_REPORT.md、MASTER_AUDIT_V3_PLAIN_LANGUAGE_SUMMARY.md、MASTER_AUDIT_V3_FINDINGS.json。\n重点核查 MA001_NEW_PREDICTION_GUARD_FINAL_V3.json：9个非法输入测试，6个拒绝、3个错误接受；12条是完整非法组件，不是半写。结构性半写为0。\n第二轮证据矛盾原因是 REPORT_AGGREGATION_BUG，贡献原因 AUDITOR_BUG；不得用旧零计数覆盖第三轮直接证据。\n另有真实102个Feature和161项PMF的合法写入阳性测试，见 MA001_RICH_VALID_SNAPSHOT_FINAL_V3.json；它不抵消非法输入问题。\n\n包内包含完整第三轮 JSON/CSV/Markdown、审计代码、测试代码、原始架构及上游正式文档、生产源代码参考。所有文件保持原始字节。\n本包不是完整数据库备份：不包含生产数据库、模型二进制、原始大容量气象档案、隔离测试数据库、日志或秘密配置。相关实际路径与SHA256见资产清单。要从原始数据库重新跑审计，需要项目负责人另行提供这些冻结输入。\n\nFINAL / SUPERSEDED / INTERMEDIATE / FAILED_AUDITOR_ARTIFACT 分类见 MASTER_AUDIT_V3_MANIFEST.json。失败/中间审计器产物作为溯源证据保留，不得当作最终通过证据。\n不得多数表决三轮结果；不得据本包自动修复、训练、重新校准、启动实时引擎或Soak。\n'''
    (OUT/'README_FOR_AI_REVIEW.md').write_text(readme,encoding='utf-8')
    assert after()['UPSTREAM_CHANGED_FILE_COUNT']==0
    def classification(p):
        n=p.name
        if 'FAILED_AUDITOR_ARTIFACT' in n or n.startswith('AUDITOR_EXCEPTION'):return 'FAILED_AUDITOR_ARTIFACT'
        if 'SUPERSEDED' in n or 'PROTOCOL_SNAPSHOT' in n:return 'SUPERSEDED'
        if n in ('V3_PROGRESS.json','AUDITOR_RESUME_RECORD_001.json'):return 'INTERMEDIATE'
        return 'FINAL'
    files=[]
    for folder in ('docs/master_audit_v3','src/audit/master_v3','tests/audit_master_v3'):
        for p in sorted((ROOT/folder).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pdf','.zip') and 'MANIFEST' not in p.name:
                files.append(dict(path=p.relative_to(ROOT).as_posix(),size=p.stat().st_size,sha256=sha(p),classification=classification(p)))
    (OUT/'MASTER_AUDIT_V3_MANIFEST.json').write_text(json.dumps(dict(audit_run=3,mode='FINAL_CONFIRMATION_MASTER_AUDIT',seed=20260415,verdict='FAIL',source_guardian='PASS',rich_valid_snapshot_success_count=1,files=files),ensure_ascii=False,indent=2),encoding='utf-8')
    pdf=OUT/'ZUUU_SYSTEM_V1_MASTER_AUDIT_V3_20261003.pdf'
    pdfmetrics.registerFont(TTFont('Chinese',r'C:\Windows\Fonts\simsun.ttc',subfontIndex=0))
    body=ParagraphStyle('body',fontName='Chinese',fontSize=9.5,leading=15,wordWrap='CJK',spaceAfter=5)
    head=ParagraphStyle('head',parent=body,fontSize=15,leading=22,textColor=colors.HexColor('#17365d'),spaceBefore=10,spaceAfter=10)
    story=[]
    def add(t,style=body):story.append(Paragraph(html.escape(t),style))
    add('ZUUU SYSTEM V1 第三轮独立总审计',head)
    add('详细报告与证据阅读版 · AUDIT_RUN=3 · SEED=20260415')
    add('最终结论：FAIL；CRITICAL=0；HIGH=1；Soak准入：NO。',head)
    add('本PDF附带原始材料压缩包。大量逐单元CSV及JSON留在压缩包中，以保持机器可读性。本PDF不能替代完整证据文件。')
    sources=['README_FOR_AI_REVIEW.md','MASTER_AUDIT_V3_REPORT.md','MASTER_AUDIT_V3_PLAIN_LANGUAGE_SUMMARY.md','MASTER_AUDIT_V3_KNOWN_LIMITATIONS.md','MASTER_AUDIT_V3_PROTOCOL.md','MASTER_AUDIT_V3_TERMINAL_REPORT.txt']
    for name in sources:
        story.append(PageBreak());add(name,head)
        for line in (OUT/name).read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():story.append(Spacer(1,5));continue
            if re.match(r'^\|\s*[-: ]+\|',line):continue
            add(line.lstrip('# ').replace('**',''),head if line.startswith('#') else body)
    for name in ['MA001_RICH_VALID_SNAPSHOT_FINAL_V3.json','MASTER_AUDIT_V3_FINDINGS.json']:
        story.append(PageBreak());add(name,head)
        for line in (OUT/name).read_text(encoding='utf-8-sig').splitlines():add(line)
    def footer(c,d):
        c.setFont('Chinese',8);c.drawString(35,23,'ZUUU V1 · 第三轮只读审计 · 完整证据见压缩包');c.drawRightString(560,23,str(d.page))
    SimpleDocTemplate(str(pdf),pagesize=(595.28,841.89),leftMargin=35,rightMargin=35,topMargin=35,bottomMargin=40,title='ZUUU V1 第三轮独立总审计',author='Independent Audit').build(story,onFirstPage=footer,onLaterPages=footer)
    paths=set()
    for folder in ['docs','src/audit/master_v3','tests/audit_master_v3','src/realtime','src/prediction','src/collectors','src/settlement']:
        base=ROOT/folder
        if base.exists():
            for p in base.rglob('*'):
                if p.is_file() and '__pycache__' not in p.parts and p.suffix.lower() in ('.md','.json','.csv','.txt','.pdf','.py','.xml') and p.stat().st_size<100*1024*1024:paths.add(p)
    # Include all production Python source as reference, without settings/secrets.
    paths.update(p for p in (ROOT/'src').rglob('*.py') if '__pycache__' not in p.parts)
    archive=OUT/'ZUUU_SYSTEM_V1_MASTER_AUDIT_V3_20261003_ORIGINAL_MATERIALS.zip'
    members=[dict(path=p.relative_to(ROOT).as_posix(),size=p.stat().st_size,sha256=sha(p)) for p in sorted(paths)]
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(paths):z.write(p,p.relative_to(ROOT).as_posix())
        z.writestr('PACKAGE_MEMBER_SHA256.json',json.dumps(members,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    assert after()['UPSTREAM_CHANGED_FILE_COUNT']==0
    result=dict(pdf=str(pdf),pdf_sha256=sha(pdf),zip=str(archive),zip_sha256=sha(archive),zip_size=archive.stat().st_size,member_count=len(members),source_guardian='PASS')
    (OUT/'MASTER_AUDIT_V3_EXPORT_MANIFEST.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
