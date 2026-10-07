"""Bounded discovery: two station metadata records, four station-year CSVs.

No import-time network, database connection, cache or historical ingest.
CLI prints evidence summaries only; compressed responses stay in memory.
"""
import csv
import gzip
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from src.auxiliary.contracts import LATITUDE, LONGITUDE, utc
from src.auxiliary.meteostat.parser import PARAMETERS, parse_hourly

BASE = "https://data.meteostat.net"
MAX_BYTES = 512_000
MAX_EXPANDED_BYTES = 4_000_000
REQUESTS = (("56294", 2024), ("56294", 2025), ("56294", 2026), ("56187", 2025))


def distance_km(lat, lon):
    a, b = math.radians(LATITUDE), math.radians(lat)
    dlat, dlon = b-a, math.radians(lon-LONGITUDE)
    h = math.sin(dlat/2)**2 + math.cos(a)*math.cos(b)*math.sin(dlon/2)**2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


def fetch(url):
    req = Request(url, headers={"User-Agent": "ZUUU-Auxiliary-V1-Bounded-Probe"})
    with urlopen(req, timeout=20) as response:
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Probe byte cap exceeded")
        return payload, dict(response.headers), datetime.now(timezone.utc).isoformat()


def summarize(rows, start, end):
    """Missing denominator includes absent hours; model data are not observations."""
    start, end = utc(start), utc(end)
    selected = [r for r in rows if start <= utc(r["observation_time"]) < end]
    expected = int((end-start).total_seconds()/3600)
    result = dict(window_start_utc=start.isoformat(), window_end_exclusive_utc=end.isoformat(),
        expected_hours=expected, rows=len(selected), absent_hours=expected-len(selected), parameters={})
    for parameter in PARAMETERS:
        values = [r["values"][parameter] for r in selected]
        kinds = Counter(v["value_kind"] for v in values if v["quality"] == "VALID")
        observed = kinds["OBSERVATION"]
        result["parameters"][parameter] = dict(
            valid_observed_hours=observed, model_hours=kinds["MODEL"], unknown_source_hours=kinds["UNKNOWN"],
            quality_counts=dict(Counter(v["quality"] for v in values)),
            observation_missing_pct=round(100*(expected-observed)/expected, 3) if expected else None,
            source_counts=dict(Counter(v["provider"] or "UNSPECIFIED" for v in values)))
    return result


def probe():
    report = dict(probe_version="PHASE3_METEOSTAT_PROBE_V1", stations=[], hourly=[], errors=[],
        warning="Bulk includes model substitutes; historical publication/version availability unknown",
        download_limit="2 station metadata + 4 station-year CSV; 512000 compressed bytes per request")
    for station in ("56294", "56187"):
        url = f"{BASE}/stations/{station}.json"
        try:
            payload, headers, now = fetch(url)
            data = json.loads(payload)
            location = data["location"]
            report["stations"].append(dict(metadata=data, url=url, ingest_time=now,
                content_sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                distance_to_airport_km=round(distance_km(location["latitude"], location["longitude"]), 3)))
        except Exception as exc:
            report["errors"].append(dict(url=url, error=str(exc)))
    for station, year in REQUESTS:
        url = f"{BASE}/hourly/{year}/{station}.csv.gz"
        try:
            payload, headers, now = fetch(url)
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                expanded = compressed.read(MAX_EXPANDED_BYTES + 1)
            if len(expanded) > MAX_EXPANDED_BYTES:
                raise ValueError("Expanded probe byte cap exceeded")
            digest = hashlib.sha256(payload).hexdigest()
            text = expanded.decode("utf-8")
            rows = parse_hourly(text, station, ingest_time=now, snapshot_sha256=digest)
            start = max(datetime(year, 1, 1, tzinfo=timezone.utc), utc("2024-09-02T16:00:00Z"))
            end = min(datetime(year+1, 1, 1, tzinfo=timezone.utc), utc("2026-09-01T16:00:00Z"))
            times = sorted(utc(r["observation_time"]) for r in rows)
            report["hourly"].append(dict(station_id=station, year=year, url=url, bytes=len(payload),
                content_sha256=digest, ingest_time=now, source_available_time=None,
                http_last_modified=headers.get("Last-Modified") or headers.get("last-modified"),
                header=next(csv.reader(io.StringIO(text))), rows_in_file=len(rows),
                first_hour=times[0].isoformat() if times else None,
                last_hour=times[-1].isoformat() if times else None,
                hour_step_counts=dict(Counter(str(int((b-a).total_seconds()/3600)) for a,b in zip(times,times[1:]))),
                coverage=summarize(rows, start, end)))
        except Exception as exc:
            report["errors"].append(dict(url=url, error=str(exc)))
    return report


if __name__ == "__main__":
    result = probe()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(1 if result["errors"] else 0)
