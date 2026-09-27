"""Live conditions for a departure in the next few hours: NWS flood alerts, Virginia Key
water level against flood thresholds, and 15-minute rainfall. No keys or signups.

fetch_* functions call the network; parse_* functions are pure. A source that fails is
reported as unavailable, never as "no flooding".
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path

import requests

from ml.settings import LATITUDE, LONGITUDE, NWS_USER_AGENT, OUTPUT, TIDE_STATION

MAX_LEAD = timedelta(hours=3)
STALE_OBSERVATION = timedelta(minutes=30)
QUARTER = timedelta(minutes=15)
NOAA = "https://api.tidesandcurrents.noaa.gov"


def _utc(text):
    # NOAA (time_zone=gmt) and Open-Meteo (timezone=GMT) both return naive UTC times.
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def parse_alerts(payload, departs_at):
    """Flood-related NWS alerts still in effect at departure."""
    alerts = []
    for feature in payload["features"]:
        p = feature["properties"]
        ends = p.get("ends") or p.get("expires")
        if "flood" not in p["event"].lower() or (ends and datetime.fromisoformat(ends) < departs_at):
            continue
        alerts.append({"id": p["id"], "event": p["event"], "severity": p["severity"],
            "urgency": p["urgency"], "onset": p.get("onset"), "ends": ends,
            "headline": p.get("headline"), "area": p.get("areaDesc"),
            # Zone-wide alerts (e.g. coastal flood statements) have no polygon.
            "has_polygon": feature.get("geometry") is not None})
    return alerts


def parse_tide(observed, predictions, levels, departs_at, now, window=timedelta(hours=1)):
    """Highest expected water level within +/- window of departure, in ft above station datum.

    Expected = predicted tide + the current observed-minus-predicted anomaly (persistence).
    A missing or stale observation falls back to the predicted tide alone.
    """
    series = [(_utc(x["t"]), float(x["v"])) for x in predictions["predictions"]]
    in_window = [v for t, v in series if abs(t - departs_at) <= window]
    if not in_window:
        raise ValueError("Tide predictions do not cover the departure window")
    if levels.get("nws_minor") is None:
        raise ValueError("Missing NWS minor flood threshold")
    rows = [x for x in observed.get("data", []) if x.get("v") not in ("", None)]
    observed_at = observed_ft = predicted_ft = anomaly = None
    if rows:
        observed_at, observed_ft = _utc(rows[-1]["t"]), float(rows[-1]["v"])
        nearest_t, nearest_v = min(series, key=lambda s: abs(s[0] - observed_at))
        if now - observed_at <= STALE_OBSERVATION and abs(nearest_t - observed_at) <= timedelta(minutes=6):
            predicted_ft, anomaly = nearest_v, observed_ft - nearest_v
    expected = max(in_window) + (anomaly or 0.)
    return {"station": TIDE_STATION, "datum": "STND", "units": "ft",
        "observed_at": observed_at.isoformat() if observed_at else None, "observed_ft": observed_ft,
        "predicted_at_observation_ft": predicted_ft, "anomaly_ft": anomaly,
        "predicted_peak_ft": max(in_window), "expected_peak_ft": expected,
        "anomaly_applied": anomaly is not None,
        "nws_minor_ft": levels["nws_minor"], "nws_moderate_ft": levels.get("nws_moderate"),
        "margin_to_minor_ft": levels["nws_minor"] - expected,
        "above_minor": expected >= levels["nws_minor"]}


def parse_rain(payload, departs_at, now, trip=timedelta(hours=1)):
    """Per-point rainfall sums around the trip. Each value is the preceding 15 minutes' total."""
    out = []
    for point in payload if isinstance(payload, list) else [payload]:
        if point.get("minutely_15_units", {}).get("precipitation") != "mm":
            raise ValueError("Expected 15-minute precipitation in mm")
        m = point["minutely_15"]
        series = [(_utc(t), v) for t, v in zip(m["time"], m["precipitation"])]
        def total(start, end):
            values = [v for t, v in series if start < t <= end]
            if any(v is None for v in values) or (start + QUARTER <= end and not values):
                return None
            return float(sum(values))
        span = [v for t, v in series if now - timedelta(hours=2) < t <= departs_at + trip]
        out.append({"latitude": point["latitude"], "longitude": point["longitude"],
            "past_2h_mm": total(now - timedelta(hours=2), now),
            "before_departure_mm": total(now, departs_at),
            "during_trip_mm": total(departs_at, departs_at + trip),
            "max_15min_mm": None if not span or None in span else float(max(span))})
    return out


def fetch_alerts(points, departs_at):
    seen, alerts = set(), []
    for lat, lon in points:
        response = requests.get("https://api.weather.gov/alerts/active",
            params={"point": f"{lat:.4f},{lon:.4f}"}, timeout=20,
            headers={"User-Agent": NWS_USER_AGENT, "Accept": "application/geo+json"})
        response.raise_for_status()
        for alert in parse_alerts(response.json(), departs_at):
            if alert["id"] not in seen:
                seen.add(alert["id"])
                alerts.append(alert)
    return alerts


def _noaa(product, **params):
    response = requests.get(f"{NOAA}/api/prod/datagetter", timeout=30, params={
        "station": TIDE_STATION, "product": product, "datum": "STND", "units": "english",
        "time_zone": "gmt", "format": "json", "application": "dryroute", **params})
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise ValueError(payload["error"].get("message", "NOAA error"))
    return payload


def fetch_tide(departs_at, now):
    fmt = "%Y%m%d %H:%M"
    utc = departs_at.astimezone(timezone.utc)
    predictions = _noaa("predictions", begin_date=(now - timedelta(hours=1)).strftime(fmt),
                        end_date=(utc + timedelta(hours=1)).strftime(fmt))
    response = requests.get(f"{NOAA}/mdapi/prod/webapi/stations/{TIDE_STATION}/floodlevels.json", timeout=30)
    response.raise_for_status()
    return parse_tide(_noaa("water_level", date="latest"), predictions, response.json(), departs_at, now)


def fetch_rain(points, departs_at, now, trip):
    response = requests.get("https://api.open-meteo.com/v1/forecast", timeout=30, params={
        "latitude": ",".join(str(lat) for lat, _ in points),
        "longitude": ",".join(str(lon) for _, lon in points),
        "minutely_15": "precipitation", "past_minutely_15": 9,
        "forecast_minutely_15": ceil((departs_at + trip - now) / QUARTER) + 1, "timezone": "GMT"})
    response.raise_for_status()
    return parse_rain(response.json(), departs_at, now, trip)


def live_conditions(departs_at, points=None, trip=timedelta(hours=1), now=None):
    if departs_at.tzinfo is None:
        raise ValueError("departs_at requires a timezone")
    now = now or datetime.now(timezone.utc)
    if not timedelta(0) <= departs_at - now <= MAX_LEAD:
        raise ValueError(f"Live conditions need a departure between now and {MAX_LEAD} ahead")
    points = points or [(LATITUDE, LONGITUDE)]
    result = {"retrieved_at": now.isoformat(), "departs_at": departs_at.isoformat(),
              "trip_minutes": trip / timedelta(minutes=1), "points": points}
    sources = {"nws_alerts": lambda: fetch_alerts(points, departs_at),
               "tide": lambda: fetch_tide(departs_at, now),
               "rain": lambda: fetch_rain(points, departs_at, now, trip)}
    for name, fetch in sources.items():
        try:
            result[name] = {"status": "ok", "data": fetch()}
        except (requests.RequestException, ValueError, KeyError) as error:
            result[name] = {"status": "unavailable", "error": f"{type(error).__name__}: {error}"}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--departs-at", type=datetime.fromisoformat, required=True,
                        help="ISO time with offset, e.g. 2026-09-26T19:30-04:00")
    parser.add_argument("--point", action="append", help="lat,lon along the route; repeatable")
    parser.add_argument("--trip-minutes", type=int, default=60)
    parser.add_argument("--output", type=Path, default=OUTPUT / "live_conditions.json")
    args = parser.parse_args()
    points = [tuple(float(v) for v in p.split(",")) for p in args.point] if args.point else None
    result = live_conditions(args.departs_at, points, timedelta(minutes=args.trip_minutes))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
