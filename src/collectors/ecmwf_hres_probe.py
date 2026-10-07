import requests

URL = "https://single-runs-api.open-meteo.com/v1/forecast"

MODELS = [
    "ecmwf_ifs025",
    "ecmwf_ifs",
    "ecmwf_ifs_hres",
    "ecmwf_ifs_hres_9km",
]

RUNS = [
    "2024-03-14T00:00",
    "2024-03-15T00:00",
    "2024-04-01T00:00",
    "2024-06-01T00:00",
    "2025-01-01T00:00",
    "2026-09-20T00:00",
]

for model in MODELS:

    print()
    print("=" * 70)
    print("MODEL:", model)
    print("=" * 70)

    for run in RUNS:

        params = {
            "latitude": 30.576,
            "longitude": 103.950,
            "hourly": "temperature_2m",
            "forecast_hours": 72,
            "timezone": "UTC",
            "models": model,
            "run": run,
        }

        try:

            r = requests.get(
                URL,
                params=params,
                timeout=30,
            )

            print(
                run,
                "HTTP",
                r.status_code,
                end=" | "
            )

            if r.status_code == 200:

                data = r.json()

                hourly = data.get(
                    "hourly",
                    {}
                )

                times = hourly.get(
                    "time",
                    []
                )

                temps = hourly.get(
                    "temperature_2m",
                    []
                )

                print(
                    "PASS",
                    "| points:",
                    len(times),
                    "| first:",
                    times[0] if times else None,
                    "| temp:",
                    temps[0] if temps else None,
                )

            else:

                print(
                    r.text[:250]
                )

        except Exception as e:

            print(
                run,
                "ERROR:",
                repr(e)
            )