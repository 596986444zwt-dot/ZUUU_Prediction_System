from datetime import timedelta, timezone
import pytest
from src.realtime.archive import Archive
from src.realtime.collectors import archive_ecmwf
from src.realtime.contracts import utc
from src.ecmwf_contract import validate_payload, HOURLY_VARIABLES, EXPECTED_UNITS


@pytest.mark.parametrize('minutes', [0,480,-300,345,-240,None])
def test_timezone_target_run_lead(tmp_path, minutes):
    a=Archive(tmp_path/'timezone.db','SIMULATION',create=True)
    run=utc('2026-10-02T06:00:00+00:00')
    times=[]
    for i in range(72):
        instant=run+timedelta(hours=i)
        times.append(instant.replace(tzinfo=None).isoformat() if minutes is None else
                     instant.astimezone(timezone(timedelta(minutes=minutes))).isoformat())
    p=dict(utc_offset_seconds=0,hourly_units=EXPECTED_UNITS.copy(),hourly={
        'time':times,**{k:[20. if k=='temperature_2m' else None]*72 for k in HOURLY_VARIABLES}})
    assert validate_payload(p,run)
    represented_run=run.astimezone(timezone(timedelta(minutes=minutes or 0)))
    try:
        assert archive_ecmwf(a,p,represented_run,run+timedelta(hours=7))
        rows=a.rows('ecmwf_hourly');assert len(rows)==72
        for r in rows:
            assert utc(r['target_time_utc']) == utc(r['run_time_utc'])+timedelta(hours=r['lead_hours'])
            assert utc(r['run_time_utc']) == run
            assert r['target_time_utc'].endswith('+00:00')
    finally:a.close()


def test_naive_run_rejected_by_existing_phase10_contract(tmp_path):
    a=Archive(tmp_path/'naive.db','SIMULATION',create=True)
    try:
        with pytest.raises(ValueError,match='TIMEZONE_REQUIRED'):
            archive_ecmwf(a,{},utc('2026-10-02T06:00:00+00:00').replace(tzinfo=None),utc('2026-10-02T13:00:00+00:00'))
        assert not a.rows('ecmwf_raw_runs')
    finally:a.close()
