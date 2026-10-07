"""Versioned experimental contract; deliberately independent of Phase10 config/state."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UTC = timezone.utc
BJT = timezone(timedelta(hours=8))
VERSION = 'T0_EXPERIMENTAL_V1'
METHOD_VERSION = 'T0_LEVEL0_L1_A_V1'
STATUS = 'T0_EXPERIMENTAL'
VALIDATION = 'FORWARD_VALIDATION'


def now():
    return datetime.now(UTC)


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return value.astimezone(UTC)


def iso(value):
    return utc(value).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def identity(value):
    return digest(canonical(value).encode('utf-8'))


def require(test, reason):
    if not test:
        raise ValueError(reason)


def load_config(path=None):
    return json.loads(Path(path or ROOT / 'config/t0_experimental_v1.json').read_text(encoding='utf-8-sig'))


def code_version():
    paths = sorted(Path(__file__).parent.glob('*.py')) + [Path(__file__).parent / 'schema.sql']
    # Include read-only reused parser/forecast contract in executable identity.
    paths += [ROOT / 'src/parsers/zuuu_metar_parser.py', ROOT / 'src/ecmwf_contract.py']
    return identity({p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in paths})


def integer_center(value):
    import math
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value), 'NONFINITE_PREDICTION')
    return math.floor(value + .5)
