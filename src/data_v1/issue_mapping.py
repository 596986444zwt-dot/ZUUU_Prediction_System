"""Adapter to accepted Phase 2 logic; no new selection rule."""
from src.audit import ecmwf_issue_rule_final_audit as phase2
from .contracts import DATES, HORIZONS, require, utc


def build_mapping(conn):
    phase2.validate_frozen_objects(conn)
    targets = phase2.load_targets(conn)
    require(tuple(d.isoformat() for d in targets) == DATES, "Unexpected Ground Truth calendar")
    archive = phase2.load_archive(conn)
    require(len(archive) == 3411, "Archive count mismatch")
    temperatures = phase2.load_hourly(conn)
    results = phase2.audit_backtest(targets, archive, temperatures)
    # Mandatory gate before Solar joining or Gold file creation.
    phase2.validate_backtest_results(results)
    mapping = []
    for day in targets:
        for h, horizon in enumerate(HORIZONS):
            issue = phase2.issue_time_backtest(day, h)
            selected = phase2.choose_latest_complete(archive, temperatures, day, issue)
            require(selected is not None, f"Missing legal run: {day}/{horizon}")
            run, present, valid = selected
            require(utc(run["source_available_time_utc"]) <= issue, "Leakage in Phase 2 mapping")
            require(utc(run["run_time_utc"]) <= utc(run["source_available_time_utc"]), "Run after availability")
            mapping.append(dict(day=day.isoformat(), horizon=horizon, issue=issue, run=run,
                                present=present, valid=valid))
    return mapping


if __name__ == "__main__":
    from .source_io import open_snapshot, frozen_fingerprints
    from .contracts import PRODUCTION, PRODUCTION_SHA
    before = frozen_fingerprints()
    conn = open_snapshot(PRODUCTION, PRODUCTION_SHA)
    try:
        rows = build_mapping(conn)
        print("PHASE 4 MAPPING GATE PASS:", len(rows), "samples")
    finally:
        conn.close()
    require(frozen_fingerprints() == before, "Frozen files changed")
