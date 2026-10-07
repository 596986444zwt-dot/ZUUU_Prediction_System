"""Phase10 implementation constants and strictly aware timestamps."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
BJT = ZoneInfo('Asia/Shanghai')
UTC = timezone.utc
VERSION = 'PHASE10_REALTIME_V1'
BLOCKERS = {'T0_FORMAL_MODEL': 'BLOCKED / DEFERRED',
            'CORRELATED_TRAJECTORY': 'BLOCKED_FOR_DATA',
            'TMAX_TIME_PROBABILITY': 'BLOCKED_FOR_DATA'}

def now(): return datetime.now(UTC)

def utc(value):
    if isinstance(value, str): value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None: raise ValueError('TIMEZONE_REQUIRED')
    return value.astimezone(UTC)

def iso(value): return utc(value).isoformat()

def canonical(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def identity(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

def eligibility(day):
    return datetime.fromisoformat(day + 'T00:00:00+08:00') + timedelta(days=2)

def targets(issue):
    day = utc(issue).astimezone(BJT).date()
    return {h: (day + timedelta(days=n)).isoformat() for n, h in enumerate(('T0', 'T1', 'T2'))}

def config(): return json.loads((ROOT / 'config/phase10_realtime_v1.json').read_text(encoding='utf-8'))

def require(test, reason):
    if not test: raise ValueError(reason)
