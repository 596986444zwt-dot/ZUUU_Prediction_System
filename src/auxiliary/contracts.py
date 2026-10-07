"""Strict UTC and point-in-time eligibility contracts."""
from datetime import datetime, timezone
import math

UTC = timezone.utc
TIMEZONE = "Asia/Shanghai"
# Pinned airport coordinates from config/settings.py; NOT Meteostat station coordinates.
LATITUDE = 30.576
LONGITUDE = 103.950
COORDINATE_VERSION = "ZUUU_CONFIG_COORDINATES_V1"


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone is required")
    return value.astimezone(UTC)


def eligible_observation(row, cutoff):
    """No estimated release lag, current revision, or model fill in a past cutoff.

    V1 accepts only a snapshot actually captured by the cutoff. Historical bulk
    downloads have UNKNOWN availability and are research-only. Later designs
    may support verifiable publication/version evidence, but V1 does not guess.
    """
    cutoff = utc(cutoff)
    if row.get("value_kind") != "OBSERVATION" or row.get("quality") != "VALID":
        return False
    if row.get("provider") not in ("metar", "isd_lite") or row.get("source") != "Meteostat":
        return False
    value = row.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if row.get("availability_basis") != "CAPTURED_SNAPSHOT":
        return False
    digest = row.get("evidence_sha256", "")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or row.get("source_version") != "sha256:" + digest
            or not row.get("source_available_time")):
        return False
    observation, available, ingest = (utc(row[k]) for k in
        ("observation_time", "source_available_time", "ingest_time"))
    # A first-seen snapshot timestamp is a conservative availability bound,
    # explicitly NOT the provider's historical publication time.
    return observation <= available == ingest <= cutoff
