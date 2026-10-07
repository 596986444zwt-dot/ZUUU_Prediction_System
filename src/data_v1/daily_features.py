"""Pure daily trajectory statistics; label errors are audit-only."""
import math


def summarize(sample, hourly):
    result = dict(business_date_bjt=sample["business_date_bjt"], horizon=sample["horizon"],
                  trajectory_complete=int(sample["sample_status"] == "ELIGIBLE"))
    names = ("ecmwf_daily_max_c", "ecmwf_daily_min_c", "ecmwf_daily_mean_c", "ecmwf_max_occurrence_hour_bjt",
             "ecmwf_max_occurrence_count", "ecmwf_max_first_time_bjt", "ecmwf_max_last_time_bjt",
             "error_c", "absolute_error_c")
    result.update(dict.fromkeys(names))
    if result["trajectory_complete"]:
        values = [r["temperature_2m_c"] for r in hourly]
        maximum = max(values)
        peaks = [r for r in hourly if r["temperature_2m_c"] == maximum]
        result.update(ecmwf_daily_max_c=maximum, ecmwf_daily_min_c=min(values),
            ecmwf_daily_mean_c=math.fsum(values)/len(values),
            ecmwf_max_occurrence_hour_bjt=int(peaks[0]["target_time_bjt"][11:13]),
            ecmwf_max_occurrence_count=len(peaks), ecmwf_max_first_time_bjt=peaks[0]["target_time_bjt"],
            ecmwf_max_last_time_bjt=peaks[-1]["target_time_bjt"],
            error_c=maximum-sample["target_tmax_c"], absolute_error_c=abs(maximum-sample["target_tmax_c"]))
    return result
