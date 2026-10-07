# V4 isolated evidence and reproduction

Evidence status: FINAL; this index was added after the autonomous lock.

No pytest cache or formal database writes are required. Use PyCharm's configured interpreter, currently Python313, with bytecode disabled:

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe' -B src/audit/master_v4/reproduce_boundary_cases.py
```

This creates a NEW `temp/master_audit_v4/reproduction_*` directory automatically. It reruns the 25 original isolated cases, including Snapshot identity/time/math attacks, transaction rollback, changed-body idempotency, direct-SQL REPLACE, malformed spool, state persistence failure, settlement and evaluation. Production databases are opened only through read-only immutable connections where bootstrap needs frozen data. No Engine is started and no network request is sent. Preserve the original evidence; do not rerun fixed-name fixture scripts against existing files.

The frozen autonomous findings refer to `temp/master_audit_v4/runtime_final/adversarial_cases.json`, plus full-component copy tests in `formal_copy_adversarial.json`. Formula verification is in `verify.py`, corrected independent model inference in `resume_models.py`, and receipt/window checks in `runtime_edges.py`. See the final evidence manifest for FINAL versus failed/superseded artifacts.
