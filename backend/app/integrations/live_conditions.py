"""Live flood conditions around a departure: NWS flood alerts and Biscayne Bay water level. No keys.

Adapted from the ML branch's data-pipeline/ml/live.py (same sources, same parsing): NWS active alerts
for a point, and NOAA CO-OPS station 8723214 (Virginia Key) water level against its NWS flood
thresholds. Expected level = predicted tide + the current observed-minus-predicted anomaly.

fetch_* call the network; parse_* are pure. A source that fails is "unavailable", never "no flooding".
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

NWS_ALERTS = "https://api.weather.gov/alerts/active"
NOAA = "https://api.tidesandcurrents.noaa.gov"
TIDE_STATION = "8723214"  # Virginia Key, Biscayne Bay
USER_AGENT = "dryroute-shellhacks (flood-aware routing prototype)"  # NWS asks every client to identify itself
STALE_OBSERVATION = timedelta(minutes=30)
TIDE_WINDOW = timedelta(hours=1)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)  # NOAA (time_zone=gmt) returns naive UTC


def parse_alerts(payload: dict, at: datetime) -> list[dict]:
    """Flood-related NWS alerts still in effect at `at`."""
    alerts = []
    for feature in payload["features"]:
        p = feature["properties"]
        ends = p.get("ends") or p.get("expires")
        if "flood" not in p["event"].lower() or (ends and datetime.fromisoformat(ends) < at):
            continue
        alerts.append({"id": p["id"], "event": p["event"], "severity": p["severity"], "urgency": p["urgency"],
                       "onset": p.get("onset"), "ends": ends, "headline": p.get("headline"),
                       "area": p.get("areaDesc")})
    return alerts


def parse_tide(observed: dict, predictions: dict, levels: dict, at: datetime, now: datetime) -> dict:
    """Highest expected water level within +/- 1 h of `at`, in ft above station datum."""
    series = [(_utc(x["t"]), float(x["v"])) for x in predictions["predictions"]]
    in_window = [v for t, v in series if abs(t - at) <= TIDE_WINDOW]
    if not in_window:
        raise ValueError("Tide predictions do not cover the time asked for")
    if levels.get("nws_minor") is None:
        raise ValueError("Missing NWS minor flood threshold")
    rows = [x for x in observed.get("data", []) if x.get("v") not in ("", None)]
    anomaly = observed_ft = None
    if rows:
        observed_at, observed_ft = _utc(rows[-1]["t"]), float(rows[-1]["v"])
        nearest_t, nearest_v = min(series, key=lambda s: abs(s[0] - observed_at))
        if now - observed_at <= STALE_OBSERVATION and abs(nearest_t - observed_at) <= timedelta(minutes=6):
            anomaly = observed_ft - nearest_v
    expected = max(in_window) + (anomaly or 0.0)
    return {"station": TIDE_STATION, "observed_ft": observed_ft, "anomaly_ft": anomaly,
            "expected_peak_ft": expected, "nws_minor_ft": levels["nws_minor"],
            "nws_moderate_ft": levels.get("nws_moderate"),
            "above_minor": expected >= levels["nws_minor"],
            "above_moderate": levels.get("nws_moderate") is not None and expected >= levels["nws_moderate"]}


class LiveConditions:
    def __init__(self, timeout_s: float = 8.0, cache_minutes: float = 5):
        self.timeout_s = timeout_s
        self.cache_s = cache_minutes * 60
        self._cache: dict[tuple, tuple[float, dict]] = {}

    def check(self, points: list[tuple[float, float]], at: datetime, now: datetime | None = None) -> dict:
        """{"nws_alerts": {"status", "data"}, "tide": {"status", "data"}} for (lat, lon) points at time `at`.
        `at` must be within the next 3 hours (tide predictions and alert validity are short-range)."""
        now = now or datetime.now(timezone.utc)
        key = (tuple(points), int(at.timestamp() // self.cache_s))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cache_s:
            return cached[1]
        result = {}
        for name, fetch in (("nws_alerts", lambda: self.fetch_alerts(points, at)),
                            ("tide", lambda: self.fetch_tide(at, now))):
            try:
                result[name] = {"status": "ok", "data": fetch()}
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("live condition %s unavailable: %s", name, type(e).__name__)
                result[name] = {"status": "unavailable", "data": None}
        self._cache[key] = (time.monotonic(), result)
        return result

    def fetch_alerts(self, points, at) -> list[dict]:
        seen, alerts = set(), []
        for lat, lon in points:
            r = httpx.get(NWS_ALERTS, params={"point": f"{lat:.4f},{lon:.4f}"}, timeout=self.timeout_s,
                          headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
            r.raise_for_status()
            for alert in parse_alerts(r.json(), at):
                if alert["id"] not in seen:
                    seen.add(alert["id"])
                    alerts.append(alert)
        return alerts

    def fetch_tide(self, at, now) -> dict:
        fmt = "%Y%m%d %H:%M"
        begin, end = min(now, at) - timedelta(hours=1), max(now, at) + timedelta(hours=1)
        predictions = self._noaa("predictions", begin_date=begin.astimezone(timezone.utc).strftime(fmt),
                                 end_date=end.astimezone(timezone.utc).strftime(fmt))
        r = httpx.get(f"{NOAA}/mdapi/prod/webapi/stations/{TIDE_STATION}/floodlevels.json", timeout=self.timeout_s)
        r.raise_for_status()
        return parse_tide(self._noaa("water_level", date="latest"), predictions, r.json(), at, now)

    def _noaa(self, product, **params) -> dict:
        r = httpx.get(f"{NOAA}/api/prod/datagetter", timeout=self.timeout_s, params={
            "station": TIDE_STATION, "product": product, "datum": "STND", "units": "english",
            "time_zone": "gmt", "format": "json", "application": "dryroute", **params})
        r.raise_for_status()
        payload = r.json()
        if "error" in payload:
            raise ValueError(payload["error"].get("message", "NOAA error"))
        return payload
