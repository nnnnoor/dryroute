"""TomTom traffic times, with TomTom faked (no network). tests/test_tomtom_live.py calls the real API."""
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations.tomtom import TomTomTraffic

BRICKELL_TO_GOLD = {"from_lat": 25.7617, "from_lon": -80.1918, "parking_id": "way/112762942"}
PATH = [[-80.19, 25.76], [-80.25, 25.765], [-80.37, 25.755]]  # GeoJSON [lon, lat]


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=httpx.Request("POST", "https://x"), response=self)

    def json(self):
        return self._payload


SUMMARY = {"routes": [{"summary": {"travelTimeInSeconds": 1800, "noTrafficTravelTimeInSeconds": 1200,
                                   "trafficDelayInSeconds": 600, "lengthInMeters": 25000}}]}


def test_request_format_and_parsing(monkeypatch):
    calls = []

    def fake_post(url, params, json, timeout):
        calls.append((url, params, json))
        return FakeResponse(200, SUMMARY)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = TomTomTraffic("k").travel_times("r_1", PATH, None)
    assert result == {"traffic_s": 1800.0, "no_traffic_s": 1200.0, "delay_s": 600.0, "length_m": 25000.0,
                      "live": True}
    url, params, body = calls[0]
    assert url.endswith("/calculateRoute/25.76,-80.19:25.755,-80.37/json")  # lat,lon:lat,lon
    assert params["traffic"] == "true" and params["computeTravelTimeFor"] == "all"
    assert params["departAt"] == "now"
    assert body["supportingPoints"][1] == {"latitude": 25.765, "longitude": -80.25}


def test_cached_per_route_and_slot(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1) or FakeResponse(200, SUMMARY))
    t = TomTomTraffic("k")
    t.travel_times("r_1", PATH, None)
    t.travel_times("r_1", PATH, None)
    t.travel_times("r_2", PATH, None)
    assert len(calls) == 2


def test_future_departure_is_predicted(monkeypatch):
    params_seen = []
    monkeypatch.setattr(httpx, "post", lambda url, params, json, timeout:
                        params_seen.append(params) or FakeResponse(200, SUMMARY))
    result = TomTomTraffic("k").travel_times("r_1", PATH, datetime(2030, 1, 7, 13, tzinfo=timezone.utc))
    assert result["live"] is False
    assert params_seen[0]["departAt"].startswith("2030-01-07T13:00:00")


@pytest.mark.parametrize("response", [FakeResponse(403, {}), FakeResponse(200, {"routes": []})])
def test_failures_return_none(monkeypatch, response):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: response)
    assert TomTomTraffic("k").travel_times("r_1", PATH, None) is None


def test_timeout_returns_none(monkeypatch):
    def slow(*a, **k):
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(httpx, "post", slow)
    assert TomTomTraffic("k").travel_times("r_1", PATH, None) is None


class StubTraffic:
    """Pretends traffic doubles every trip."""
    def __init__(self, works=True):
        self.works = works

    def travel_times(self, route_id, coords, depart_at):
        return None if not self.works else {"traffic_s": 2 * 60 * 20, "no_traffic_s": 60 * 20, "delay_s": 60 * 20,
                                            "length_m": 0, "live": depart_at is None}


def test_routes_use_traffic_times(client, monkeypatch):
    planner = client.app.state.planner
    free = client.get("/routes", params=BRICKELL_TO_GOLD).json()["usual"]
    assert free["eta_source"] == "free_flow" and free["traffic_delay_minutes"] is None

    monkeypatch.setattr(planner, "traffic", StubTraffic())
    b = client.get("/routes", params={**BRICKELL_TO_GOLD, "arrive_by": "2026-09-28T13:00:00Z"}).json()
    usual = b["usual"]
    assert usual["eta_minutes"] == 40.0 and usual["traffic_delay_minutes"] == 20.0
    assert usual["eta_source"] == "predicted_traffic"
    assert usual["depart_at"] == "2026-09-28T12:20:00Z"  # 40 min before 13:00


def test_routes_fall_back_when_traffic_fails(client, monkeypatch):
    monkeypatch.setattr(client.app.state.planner, "traffic", StubTraffic(works=False))
    usual = client.get("/routes", params=BRICKELL_TO_GOLD).json()["usual"]
    assert usual["eta_source"] == "free_flow"
    assert 15 < usual["eta_minutes"] < 25


def test_congestion_counts_as_delay(monkeypatch):
    """TomTom's trafficDelayInSeconds is incidents only; plain congestion shows up as travel - noTraffic."""
    congested = {"routes": [{"summary": {"travelTimeInSeconds": 1776, "noTrafficTravelTimeInSeconds": 1638,
                                         "trafficDelayInSeconds": 0, "lengthInMeters": 25868}}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(200, congested))
    assert TomTomTraffic("k").travel_times("r_1", PATH, None)["delay_s"] == 138.0
