"""Replay V4 isolated boundary tests into a fresh directory on every invocation.

This is a post-lock convenience wrapper, not new autonomous evidence.
"""
import pathlib, sys, tempfile, json
ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import src.audit.master_v4.verify as v
v.OUT=pathlib.Path(tempfile.mkdtemp(prefix='reproduction_',dir=ROOT/'temp/master_audit_v4'))
result=v.adversarial()
v.save('adversarial.json',result)
print(json.dumps({'output':str(v.OUT),'cases':result['cases'],'production_modified':False}))
