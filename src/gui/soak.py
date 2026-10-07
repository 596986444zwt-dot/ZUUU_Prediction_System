"""Read existing Phase10 observer evidence; never open an evidence writer."""
import csv
import io
import json
from datetime import timedelta
from pathlib import Path
from src.realtime.contracts import utc, now

MAX_BYTES = 8 * 1024 * 1024

def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('EVIDENCE_TOO_LARGE')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('UNEXPECTED_EVIDENCE_OBJECT')
    return value

def csv_records(path, tail=False):
    with Path(path).open('rb') as stream:
        if tail:
            header = stream.readline().decode('utf-8-sig')
            stream.seek(0, 2)
            size = stream.tell()
            start = max(len(header.encode('utf-8')), size - 65536)
            stream.seek(start)
            if start > len(header.encode('utf-8')):
                stream.readline()  # discard partial first line
            raw = stream.read(65536)
            if raw and not raw.endswith(b'\n'):
                raw = raw.rsplit(b'\n', 1)[0] + b'\n'  # discard in-progress append
            text = header + raw.decode('utf-8')
        else:
            raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('ERROR_EVIDENCE_TOO_LARGE')
            text = raw.decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(text)))

class SoakReader:
    def __init__(self, root):
        self.root = Path(root)

    def read(self, current=None):
        current = current or now()
        manifest = read_json(self.root / 'SOAK_START_MANIFEST.json')
        process = read_json(self.root / 'SOAK_PROCESS_INFO.json')
        try:
            observer = read_json(self.root / 'evidence/LATEST_OBSERVER_STATUS.json')
            observer_source = 'evidence/LATEST_OBSERVER_STATUS.json'
        except (OSError, ValueError):
            rows = csv_records(self.root / 'SOAK_HEARTBEAT.csv', tail=True)
            sample = next((r for r in reversed(rows) if r.get('kind') == 'SAMPLING_COMPLETE'), None)
            if not sample:
                raise ValueError('NO_COMPLETE_OBSERVER_SAMPLE')
            observer = json.loads(sample['details_json'])
            observer_source = 'SOAK_HEARTBEAT.csv / SAMPLING_COMPLETE.details_json'
        sample_time = utc(observer['sample_time'])
        findings = observer['findings']
        if not isinstance(findings, list) or not all(isinstance(f, str) for f in findings):
            raise ValueError('UNEXPECTED_ACTIVE_FINDINGS')
        errors = csv_records(self.root / 'SOAK_ERRORS.csv')
        severities = {}
        for row in errors:
            if row.get('severity') in ('CRITICAL', 'HIGH'):
                # Observer finding(key, severity, ...) emits key as both kind and record_id.
                severities[row.get('record_id')] = row['severity']
                severities[row.get('kind')] = row['severity']
        unknown = [f for f in findings if f not in severities]
        critical = sum(severities.get(f) == 'CRITICAL' for f in findings)
        high = sum(severities.get(f) == 'HIGH' for f in findings)
        start = utc(manifest['SOAK_START_TIME_UTC'])
        hours = manifest['SOAK_TARGET_MINIMUM_HOURS']
        if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0:
            raise ValueError('INVALID_SOAK_TARGET')
        completion = start + timedelta(hours=hours)
        if utc(manifest['MINIMUM_72H_COMPLETION_TIME_BJT']) != completion:
            raise ValueError('SOAK_COMPLETION_MISMATCH')
        elapsed = max(0, (current - start).total_seconds())
        acceptance = manifest.get('PHASE10_OPERATIONAL_ACCEPTANCE', 'UNKNOWN')
        # This is the sealed START acceptance state. No final acceptance contract exists here.
        threshold = 'READY_FOR_FINAL_SOAK_ACCEPTANCE' if elapsed >= hours * 3600 else 'PENDING_SOAK'
        from .adapters import process_status
        pid = process.get('phase10', {}).get('pid')
        status = process_status(pid)
        activation = read_json(self.root / 'R4_PRODUCTION_ACTIVATION.json')
        return dict(status=status, start=start.isoformat(), elapsed=elapsed, target_hours=hours,
                    completion=completion.isoformat(), progress=min(100, elapsed/(hours*3600)*100),
                    critical=None if unknown else critical, high=None if unknown else high,
                    unknown_findings=unknown, findings=findings, acceptance=acceptance,
                    threshold=threshold, pid=pid, observer=observer, observer_source=observer_source,
                    sample_time=sample_time.isoformat(), sample_age=max(0,(current-sample_time).total_seconds()),
                    historical_critical=sum(r.get('severity')=='CRITICAL' for r in errors),
                    historical_high=sum(r.get('severity')=='HIGH' for r in errors),
                    activation=activation, process=process, evidence_root=str(self.root),
                    acceptance_provenance='SOAK_START_MANIFEST.json / sealed START state; final acceptance not connected')
