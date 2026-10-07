import sys,pathlib,os
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from src.realtime.archive import Archive
a=Archive(ROOT/'temp/master_audit_v4/crash_transaction.db',namespace='SIMULATION')
a.c.execute('BEGIN IMMEDIATE')
a.insert('feature_snapshots',{'fixture':'crash'},'uncommitted')
os._exit(78)
