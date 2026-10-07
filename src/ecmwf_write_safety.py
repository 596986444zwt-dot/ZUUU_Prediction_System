"""Application write boundary for new Silver runs. Never repairs history."""


def is_frozen_raw_run(connection, raw_run_id):
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ecmwf_archive_v1'"
    ).fetchone()
    return bool(exists and connection.execute(
        "SELECT 1 FROM ecmwf_archive_v1 WHERE canonical_raw_run_id=? LIMIT 1",
        (raw_run_id,),
    ).fetchone())


def assert_new_silver_run(connection, raw_run_id):
    """Call inside the writer transaction, before the first INSERT.

    Even an incomplete/empty frozen dependency must never be backfilled here.
    Existing unfrozen rows are also preserved; this is not a rebuild API.
    """
    if is_frozen_raw_run(connection, raw_run_id):
        raise RuntimeError(f"Frozen Archive dependency: Raw {raw_run_id}; write blocked")
    if connection.execute(
        "SELECT 1 FROM ecmwf_hourly_forecasts WHERE raw_run_id=? LIMIT 1",
        (raw_run_id,),
    ).fetchone():
        raise RuntimeError(f"Existing Silver history: Raw {raw_run_id}; rebuild blocked")
