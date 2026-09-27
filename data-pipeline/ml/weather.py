"""Daily local rainfall, in mm. One coarse weather point for the study area."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from ml.settings import LATITUDE, LONGITUDE, OUTPUT, TIMEZONE, WEATHER_CACHE

RAIN_FEATURES = ["rain_mm", "rain_lag1_mm", "rain_prior3_mm"]
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def daily_features(payload):
    if payload.get("timezone") != TIMEZONE:
        raise ValueError("Weather must use America/New_York local dates")
    if payload.get("daily_units", {}).get("precipitation_sum") != "mm":
        raise ValueError("Expected precipitation in mm")
    daily = payload["daily"]
    data = pd.DataFrame({"date": pd.to_datetime(daily["time"]), "rain_mm": daily["precipitation_sum"]})
    if data.empty or data.date.duplicated().any():
        raise ValueError("Weather dates must be nonempty and unique")
    data = data.sort_values("date").reset_index(drop=True)
    if not data.date.equals(pd.Series(pd.date_range(data.date.min(), data.date.max()))):
        raise ValueError("Weather must have consecutive calendar days")
    if not np.isfinite(data.rain_mm.to_numpy(dtype=float)).all() or data.rain_mm.lt(0).any():
        raise ValueError("Missing, negative or non-finite rainfall")
    data["rain_lag1_mm"] = data.rain_mm.shift(1)
    data["rain_prior3_mm"] = data.rain_mm.shift(1).rolling(3, min_periods=3).sum()
    return data


def fetch_archive(path=WEATHER_CACHE):
    response = requests.get(ARCHIVE_URL, params={
        "latitude": LATITUDE, "longitude": LONGITUDE,
        "start_date": "2021-12-29", "end_date": "2024-08-09",
        "daily": "precipitation_sum", "timezone": TIMEZONE,
    }, timeout=90)
    response.raise_for_status()
    payload = response.json()
    daily_features(payload)
    payload["dryroute_provenance"] = {"url": response.url, "fetched_at": datetime.now(timezone.utc).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return payload


def load_archive(path=WEATHER_CACHE):
    if not path.exists():
        raise FileNotFoundError("Fetch weather first: python -m ml.weather")
    return daily_features(json.loads(path.read_text()))


def forecast_records(payload, days, now):
    """Convert forecast daily totals into consecutive local-day model inputs."""
    if not 1 <= days <= 7:
        raise ValueError("Forecast horizon must be 1–7 days")
    if now.tzinfo is None:
        raise ValueError("Forecast retrieval time requires a timezone")
    data = daily_features(payload)
    today = pd.Timestamp(now.astimezone(ZoneInfo(TIMEZONE)).date())
    records = []
    for date in pd.date_range(today, periods=days):
        rows = data.loc[data.date.eq(date)]
        if len(rows) != 1 or rows[RAIN_FEATURES].isna().any().any():
            raise ValueError(f"Forecast response lacks {date.date()} or its prior three days")
        records.append({"date": date.date().isoformat(),
            **{c: float(rows.iloc[0][c]) for c in RAIN_FEATURES},
            "source": "open_meteo_forecast_daily", "updated_at": now.isoformat(),
            "lead_days": int((date - today).days)})
    return records


def fetch_forecasts(days=3):
    if not 1 <= days <= 7:
        raise ValueError("Forecast horizon must be 1–7 days")
    response = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": LATITUDE, "longitude": LONGITUDE, "daily": "precipitation_sum",
        "past_days": 3, "forecast_days": days, "timezone": TIMEZONE,
    }, timeout=45)
    response.raise_for_status()
    now = datetime.now(ZoneInfo(TIMEZONE))
    records = forecast_records(response.json(), days, now)
    for record in records:
        record["source_url"] = response.url
    return records


def fetch_forecast():
    return fetch_forecasts(days=1)[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download daily rainfall from Open-Meteo")
    parser.add_argument("--forecast", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT / "current_weather.json")
    args = parser.parse_args()
    if args.forecast:
        forecast = fetch_forecast()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(forecast, indent=2) + "\n")
        print(json.dumps(forecast, indent=2))
    else:
        payload = fetch_archive()
        print(daily_features(payload).describe().to_string())
