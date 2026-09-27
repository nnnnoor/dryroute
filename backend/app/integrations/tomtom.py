"""Traffic-aware travel times from TomTom for routes the backend already chose.

We keep our own route (the flood-aware choice) and ask TomTom how long *that exact path* takes in traffic:
Calculate Route (https://docs.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route) with the
path as `supportingPoints`, `traffic=true` and `computeTravelTimeFor=all`. It never picks the roads.

Any failure (no key, timeout, HTTP error, odd response) returns None and the caller keeps its own
free-flow estimate. Results are cached per route and departure time to stay inside the free tier.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)
URL = "https://api.tomtom.com/routing/1/calculateRoute/{locations}/json"


class TomTomTraffic:
    def __init__(self, api_key: str, timeout_s: float = 4.0, cache_minutes: float = 10, max_points: int = 400):
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.cache_s = cache_minutes * 60
        self.max_points = max_points
        self._cache: dict[tuple, tuple[float, dict | None]] = {}

    def travel_times(self, route_id: str, coords: list[list[float]], depart_at: datetime | None) -> dict | None:
        """Seconds for this path: {"traffic_s", "no_traffic_s", "delay_s", "live"} or None.

        coords are GeoJSON [lon, lat]. depart_at None (or in the past) means now, which uses live traffic;
        a future time uses TomTom's traffic prediction for then (live=False).
        """
        now = datetime.now(timezone.utc)
        future = depart_at is not None and depart_at > now + timedelta(minutes=5)
        # Same route within the same 10-minute slot -> same answer
        slot = int((depart_at if future else now).timestamp() // self.cache_s)
        key = (route_id, slot)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cache_s:
            return cached[1]

        result = self._request(coords, depart_at if future else None)
        self._cache[key] = (time.monotonic(), result)
        return result

    def _request(self, coords, depart_at) -> dict | None:
        points = _thin(coords, self.max_points)
        (lon0, lat0), (lon1, lat1) = points[0], points[-1]
        params = {"key": self.api_key, "traffic": "true", "computeTravelTimeFor": "all",
                  "travelMode": "car", "departAt": depart_at.isoformat() if depart_at else "now"}
        body = {"supportingPoints": [{"latitude": lat, "longitude": lon} for lon, lat in points]}
        try:
            r = httpx.post(URL.format(locations=f"{lat0},{lon0}:{lat1},{lon1}"),
                           params=params, json=body, timeout=self.timeout_s)
            r.raise_for_status()
            summary = r.json()["routes"][0]["summary"]
            traffic = float(summary["travelTimeInSeconds"])
            no_traffic = float(summary.get("noTrafficTravelTimeInSeconds", traffic))
            # trafficDelayInSeconds only counts incidents (crashes, closures), not ordinary congestion, so
            # the delay is whichever is bigger: incidents, or time with traffic minus time without
            delay = max(float(summary.get("trafficDelayInSeconds", 0)), traffic - no_traffic, 0.0)
            return {
                "traffic_s": traffic,
                "no_traffic_s": no_traffic,
                "delay_s": delay,
                "length_m": float(summary.get("lengthInMeters", 0)),
                "live": depart_at is None,
            }
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            # Never log the URL: it contains the API key
            status = getattr(getattr(e, "response", None), "status_code", None)
            log.warning("TomTom travel time failed (%s%s); using free-flow estimate",
                        type(e).__name__, f", HTTP {status}" if status else "")
            return None


def _thin(coords: list[list[float]], max_points: int) -> list[list[float]]:
    """Evenly keep at most max_points of the path, always including both ends."""
    if len(coords) <= max_points:
        return coords
    step = (len(coords) - 1) / (max_points - 1)
    return [coords[round(i * step)] for i in range(max_points)]
