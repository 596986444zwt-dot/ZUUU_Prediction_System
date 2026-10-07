"""Read source bytes only. SQLite never opens a frozen on-disk database."""
import hashlib
import sqlite3
from pathlib import Path

from .contracts import PRODUCTION, AUXILIARY, PRODUCTION_SHA, AUXILIARY_SHA, require


def sha256_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def sidecar_state(path):
    result = {}
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            data = sidecar.read_bytes()
            if suffix != "-shm":
                require(len(data) == 0, f"Nonempty source journal; cannot use main file alone: {sidecar}")
            result[suffix] = (len(data), hashlib.sha256(data).hexdigest())
    return result


def open_snapshot(path, expected_sha=None):
    path = Path(path).resolve()
    before = sidecar_state(path)
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    require(expected_sha is None or digest == expected_sha, f"Source SHA mismatch: {path}")
    require(payload[:16] == b"SQLite format 3\x00", "Not a SQLite database")
    require(sha256_file(path) == digest and sidecar_state(path) == before, "Source changed during read")
    data = bytearray(payload)
    del payload
    data[18:20] = b"\x01\x01"  # In-memory WAL-header normalization only.
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(bytes(data))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn
    except BaseException:
        conn.close()
        raise


def frozen_fingerprints():
    result = {}
    for name, path, expected in (("production", PRODUCTION, PRODUCTION_SHA), ("auxiliary", AUXILIARY, AUXILIARY_SHA)):
        digest = sha256_file(path)
        require(digest == expected, f"{name} frozen database SHA mismatch")
        result[name] = {"sha256": digest, "sidecars": sidecar_state(path)}
    return result
