"""Alerts, with the calendar clock fixed and NWS/tide faked (no network)."""
import re
from datetime import datetime, timezone

import pytest

from app.integrations.live_conditions import parse_alerts, parse_tide

MONDAY_7AM = datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc)  # next class: COP 3530, 9:00 in PC
W10 = "way/435345239"  # a flood-prone lot


class FakeLive:
    def __init__(self, nws=None, tide=None, fail=False):
        self.nws, self.tide, self.fail = nws or [], tide, fail

    def check(self, points, at, now=None):
        if self.fail:
            return {"nws_alerts": {"status": "unavailable", "data": None}, "tide": {"status": "unavailable", "data": None}}
        return {"nws_alerts": {"status": "ok", "data": self.nws}, "tide": {"status": "ok", "data": self.tide}}


CALM_TIDE = {"expected_peak_ft": 12.5, "above_minor": False, "above_moderate": False}
HIGH_TIDE = {"expected_peak_ft": 14.4, "above_minor": True, "above_moderate": True}
FLOOD_WARNING = {"id": "urn:oid:test.1", "event": "Flood Warning", "severity": "Severe",
                 "headline": "Flood Warning for Miami-Dade until 6 PM", "ends": "2026-09-28T22:00:00+00:00"}


@pytest.fixture
def alerts(client):
    service = client.app.state.alerts
    client.app.state.store._alerts.clear()
    service.live = FakeLive(tide=CALM_TIDE)
    service.invalidate()
    yield service
    client.app.state.store._alerts.clear()
    service.live = None


def build(service, **kw):
    service.refresh("demo", now=MONDAY_7AM)
    return {a["type"]: a for a in service.for_user("demo", now=MONDAY_7AM, **kw)}


def test_dry_day_has_no_alerts(alerts):
    assert build(alerts) == {}


def test_storm_route_alert(client, alerts):
    client.post("/demo/scenario", json={"scenario": "storm"})
    a = build(alerts)["route_flood"]
    assert a["title"] == "Warning: Flooding expected on your route to COP 3530 Data Structures"
    assert a["severity"] == "warning" and a["event_id"] and a["segment_id"]
    assert a["message"].startswith("Estimated flood levels are high on your usual route")
    assert "Take the safer route" in a["message"] and a["message"].endswith("AM.")
    assert a["read"] is False and a["active"] is True and a["source"] == "trip"


def test_parking_alert_for_flood_prone_lot(client, alerts, monkeypatch):
    store = client.app.state.store
    user = {**store.get_user(), "preferred_parking_id": W10}
    monkeypatch.setattr(store, "get_user", lambda user_id="demo": user)
    client.post("/demo/scenario", json={"scenario": "storm"})
    built = build(alerts)
    a = built["parking_flood"]
    assert a["parking_id"] == W10 and a["title"] == "Warning: W10 Parking Lot may flood"
    assert a["message"].startswith("Estimated flood levels are high at W10 Parking Lot. Park at ")
    # the parking advice is its own alert, not repeated in the route alert
    assert "Park at" not in built["route_flood"]["message"]


def test_read_survives_rebuild_and_alert_resolves_when_clear(client, alerts):
    client.post("/demo/scenario", json={"scenario": "storm"})
    route = build(alerts)["route_flood"]
    assert client.post(f"/alerts/{route['alert_id']}/read").json() == {"alert_id": route["alert_id"], "read": True}
    again = build(alerts)["route_flood"]
    assert again["read"] is True and again["created_at"] == route["created_at"]

    client.post("/demo/scenario", json={"scenario": "live"})
    assert build(alerts) == {}
    resolved = build(alerts, include_resolved=True)["route_flood"]
    assert resolved["active"] is False and resolved["resolved_at"]


def test_weather_warning_and_tide(alerts):
    alerts.live = FakeLive(nws=[FLOOD_WARNING], tide=HIGH_TIDE)
    built = build(alerts)
    w = built["weather_warning"]
    assert w["title"] == "Critical: Flood Warning" and "severity: severe" in w["message"] and w["severity"] == "critical" and w["source"] == "nws"
    t = built["tide"]
    assert t["severity"] == "critical" and t["title"] == "Critical: High tide in Biscayne Bay"
    assert t["message"].startswith("Estimated water levels in Biscayne Bay are at the moderate flood level.")


def test_failed_source_does_not_clear_alerts(alerts):
    alerts.live = FakeLive(nws=[FLOOD_WARNING], tide=HIGH_TIDE)
    build(alerts)
    alerts.live = FakeLive(fail=True)  # couldn't check: keep what we had
    assert set(build(alerts)) == {"weather_warning", "tide"}
    alerts.live = FakeLive(tide=CALM_TIDE)  # checked and clear: resolve
    assert build(alerts) == {}


def test_leave_earlier_on_heavy_traffic(client, alerts, monkeypatch):
    class HeavyTraffic:
        def travel_times(self, route_id, coords, depart_at):
            return {"traffic_s": 35 * 60, "no_traffic_s": 20 * 60, "delay_s": 15 * 60, "length_m": 0, "live": False}
    monkeypatch.setattr(client.app.state.planner, "traffic", HeavyTraffic())
    a = build(alerts)["leave_earlier"]
    assert a["severity"] == "info" and a["message"].startswith("Traffic is heavier than usual. Leave by ")


def test_endpoints(client, alerts):
    client.post("/demo/scenario", json={"scenario": "storm"})
    alerts.live = FakeLive(nws=[FLOOD_WARNING], tide=CALM_TIDE)
    body = client.post("/alerts/refresh").json()  # real clock: whatever is next; NWS alert is always there
    assert any(a["type"] == "weather_warning" for a in body)
    listed = client.get("/alerts").json()
    assert [a["alert_id"] for a in listed] == [a["alert_id"] for a in body]
    first = listed[0]
    assert set(first) >= {"alert_id", "created_at", "updated_at", "type", "severity", "title", "message",
                          "event_id", "segment_id", "parking_id", "read", "active", "source", "expires_at"}
    assert client.post("/alerts/al_nope/read").status_code == 404


def test_polling_is_throttled(client, alerts, monkeypatch):
    calls = []
    real = alerts.refresh
    monkeypatch.setattr(alerts, "refresh", lambda *a, **k: calls.append(1) or real(*a, **k))
    client.get("/alerts")
    client.get("/alerts")
    assert len(calls) == 1
    client.post("/demo/scenario", json={"scenario": "storm"})  # scenario change: rebuild on next poll
    client.get("/alerts")
    assert len(calls) == 2


def test_parsers():
    payload = {"features": [
        {"properties": {"id": "a", "event": "Coastal Flood Statement", "severity": "Minor", "urgency": "Expected",
                        "ends": "2026-09-27T13:00:00-04:00", "headline": "h", "areaDesc": "Miami-Dade"}},
        {"properties": {"id": "b", "event": "Heat Advisory", "severity": "Moderate", "urgency": "Expected",
                        "ends": None, "expires": "2026-09-28T00:00:00+00:00", "headline": "h", "areaDesc": "x"}},
        {"properties": {"id": "c", "event": "Flood Watch", "severity": "Moderate", "urgency": "Future",
                        "ends": "2026-09-26T10:00:00+00:00", "headline": "h", "areaDesc": "x"}}]}
    at = datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc)
    assert [a["id"] for a in parse_alerts(payload, at)] == ["a"]  # not heat, not already ended

    now = datetime(2026, 9, 27, 0, 30, tzinfo=timezone.utc)
    preds = {"predictions": [{"t": "2026-09-27 00:24", "v": "13.0"}, {"t": "2026-09-27 01:00", "v": "13.5"}]}
    observed = {"data": [{"t": "2026-09-27 00:24", "v": "13.6"}]}  # running 0.6 ft above prediction
    t = parse_tide(observed, preds, {"nws_minor": 13.66, "nws_moderate": 14.06}, at, now)
    assert t["expected_peak_ft"] == pytest.approx(14.1) and t["above_minor"] and t["above_moderate"]


def test_alert_text_names_the_level_but_no_measurements(client, alerts, monkeypatch):
    """Titles start with the severity; text never has km/m/ft/min (names like "COP 3530" are fine)."""
    store = client.app.state.store
    user = {**store.get_user(), "preferred_parking_id": W10}
    monkeypatch.setattr(store, "get_user", lambda user_id="demo": user)
    client.post("/demo/scenario", json={"scenario": "storm"})
    alerts.live = FakeLive(nws=[FLOOD_WARNING], tide=HIGH_TIDE)
    built = build(alerts)
    assert {"route_flood", "parking_flood", "weather_warning", "tide"} <= set(built)
    for a in built.values():
        text = a["title"] + " " + a["message"]
        assert not re.search(r"\d\s*(km|m|ft|feet|min|minutes|%)(\W|$)", text), text
        assert a["title"].startswith(f"{a['severity'].capitalize()}: "), text
