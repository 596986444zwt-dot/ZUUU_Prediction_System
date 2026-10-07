"""Deterministic semantic projection: independent of IDs, clock and DB pages."""
import hashlib
import json
from .schema import KEYS, SPECS
from .contracts import VERSION, CONTRACT_VERSION

EXCLUDED = frozenset({"source_target_id", "source_archive_id", "source_raw_run_id", "source_hourly_id",
                      "source_provenance_json", "created_at_utc", "ingest_time"})


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def dataset_hash(data):
    digest = hashlib.sha256()
    digest.update(canonical_json({"dataset": VERSION, "contract": CONTRACT_VERSION,
        "meteostat_training_admission": "BLOCKED"}).encode("utf-8") + b"\n")
    for table in sorted(KEYS):
        digest.update(table.encode("ascii") + b"\n")
        columns = sorted(set(SPECS[table])-EXCLUDED)
        for row in sorted(data[table], key=lambda r: tuple(r[k] for k in KEYS[table])):
            digest.update(canonical_json({c: row[c] for c in columns}).encode("utf-8") + b"\n")
    return digest.hexdigest()
