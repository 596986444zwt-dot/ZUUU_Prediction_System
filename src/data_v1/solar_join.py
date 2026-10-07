"""Read accepted Solar rows exactly; never regenerate or write Phase 3."""
import math
from datetime import date
from src.audit.ecmwf_issue_rule_final_audit import target_hours
from .contracts import DATES, BJT, require, utc, SOLAR_FEATURES


def load_solar(conn):
    rows = [dict(r) for r in conn.execute("SELECT * FROM aux_v1_solar_time ORDER BY target_time")]
    require(len(rows) == 17496, "Solar count mismatch")
    expected = {t for d in DATES for t in target_hours(date.fromisoformat(d))}
    result = {}
    for row in rows:
        instant = utc(row["target_time"])
        require(instant not in result, "Duplicate Solar instant")
        require(instant in expected, "Unexpected Solar hour")
        local = instant.astimezone(BJT)
        require(row["business_date_bjt"] == local.date().isoformat() and row["hour_bjt"] == local.hour,
                "Solar date/hour/timezone misalignment")
        require(row["timezone"] == "Asia/Shanghai" and row["source"] == "NOAA_SOLAR_EQUATIONS", "Solar source mismatch")
        require(row["source_version"] == "NOAA_FRACTIONAL_YEAR_ZUUU_V1"
                and row["coordinate_version"] == "ZUUU_CONFIG_COORDINATES_V1", "Solar version mismatch")
        require((row["latitude"], row["longitude"]) == (30.576, 103.950), "Solar coordinate mismatch")
        require(row["availability_basis"] == "DETERMINISTIC_NOT_APPLICABLE"
                and row["observation_time"] is None and row["source_available_time"] is None, "Solar availability mismatch")
        for field in SOLAR_FEATURES:
            require(row[field] is not None, f"Missing Solar field {field}")
            if isinstance(row[field], (float, int)):
                require(math.isfinite(row[field]), f"Nonfinite Solar field {field}")
        result[instant] = row
    require(set(result) == expected, "Solar coverage mismatch")
    return result


if __name__ == "__main__":
    from .contracts import AUXILIARY, AUXILIARY_SHA
    from .source_io import open_snapshot
    conn = open_snapshot(AUXILIARY, AUXILIARY_SHA)
    try:
        print("SOLAR ALIGNMENT GATE PASS:", len(load_solar(conn)), "exact UTC instants")
    finally:
        conn.close()
