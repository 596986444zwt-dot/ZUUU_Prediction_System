"""DATA V1 builder. --dry-run writes no file; --commit publishes a new DB only."""
import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from src.data_v1.contracts import *
from src.data_v1.source_io import open_snapshot, frozen_fingerprints, sha256_file
from src.data_v1.schema import create_schema, insert_rows, PREFIX
from src.data_v1.trajectory_builder import build_rows
from src.audit.phase4_data_v1_audit import validate_data, audit_connection
from src.audit.ecmwf_issue_rule_final_audit import EXPECTED_RULE_SHA


def prepare():
    before = frozen_fingerprints()
    p = open_snapshot(PRODUCTION, PRODUCTION_SHA)
    a = open_snapshot(AUXILIARY, AUXILIARY_SHA)
    created = datetime.now(timezone.utc).isoformat()
    try:
        for conn in (p,a):
            require([r[0] for r in conn.execute("PRAGMA integrity_check")] == ["ok"], "Source integrity failed")
            require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "Source FK check failed")
        require(a.execute("SELECT count(*) FROM aux_v1_meteostat_hourly").fetchone()[0] == 227097, "Meteostat audit count mismatch")
        require(a.execute("SELECT count(*) FROM aux_v1_meteostat_hourly WHERE availability_basis!='UNKNOWN' OR source_available_time IS NOT NULL").fetchone()[0] == 0,
                "Meteostat admission metadata changed")
        data = build_rows(p, a, created)
    finally:
        p.close()
        a.close()
    report = validate_data(data)
    require(frozen_fingerprints() == before, "Frozen sources changed during preparation")
    return data, report, created, before


def populate(conn, data, report, created):
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA recursive_triggers=ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        create_schema(conn)
        for table in ("target", "solar", "sample", "ecmwf_hourly", "daily"):
            insert_rows(conn, table, data[table])
        implementation = {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted((ROOT/"src/data_v1").glob("*.py"))}
        for relative in ("src/builders/phase4_data_v1_builder.py", "src/audit/phase4_data_v1_audit.py",
                         "src/audit/phase4_leakage_audit.py", "src/audit/ecmwf_issue_rule_final_audit.py"):
            implementation[relative] = sha256_file(ROOT/relative)
        conn.execute(f"INSERT INTO {PREFIX}manifest VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            VERSION, CONTRACT_VERSION, report["dataset_semantic_sha256"], PRODUCTION_SHA, AUXILIARY_SHA,
            EXPECTED_RULE_SHA, "BLOCKED", "ACCEPTED", created,
            json.dumps(report,sort_keys=True,ensure_ascii=False), json.dumps(implementation,sort_keys=True)))
        audit_connection(conn, reconstruct=False)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def build(*, commit=False, output=GOLD):
    output = Path(output).resolve()
    require(output not in (PRODUCTION.resolve(), AUXILIARY.resolve()), "Output is a protected source database")
    # Dry run can verify an existing Gold dataset's expected result but never open it for writing.
    if commit:
        require(not output.exists(), f"Gold database already exists; refusing overwrite/rebuild: {output}")
    data, report, created, before = prepare()
    if not commit:
        conn = sqlite3.connect(":memory:")
        try:
            populate(conn, data, report, created)
        finally:
            conn.close()
        require(frozen_fingerprints() == before, "Frozen source changed in dry run")
        return dict(report, mode="DRY_RUN", output_created=False)
    # Transactional staging prevents a half-built file being mistaken for DATA V1.
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.with_name(output.name + "." + uuid.uuid4().hex + ".building")
    with stage.open("xb"):
        pass
    conn = None
    try:
        conn = sqlite3.connect(stage)
        populate(conn, data, report, created)
        conn.close()
        conn = None
        require(frozen_fingerprints() == before, "Frozen source changed before publication")
        # Atomic create-if-absent; unlike rename on some OSes this NEVER replaces.
        os.link(stage, output)
    except BaseException:
        if conn is not None:
            conn.rollback()
            conn.close()
        raise  # Own .building evidence is retained; no final Gold file on failure.
    finally:
        if output.exists() and stage.exists() and os.path.samefile(stage, output):
            stage.unlink()  # Only this invocation's successfully published staging link.
    require(frozen_fingerprints() == before, "Frozen source changed after publication")
    return dict(report, mode="COMMIT", database=str(output))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--commit", action="store_true")
    parser.add_argument("--output", type=Path, default=GOLD)
    args = parser.parse_args(argv)
    print(json.dumps(build(commit=args.commit, output=args.output), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
