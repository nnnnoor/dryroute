"""Demo controls: a test class soon + the storm switch -> a route alert on the dashboard."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.test_ics_calendar import SCHEDULE

SHENANDOAH = {"label": "Home", "lat": 25.75322, "lon": -80.24144}


@pytest.fixture
def browser(client):
    b = TestClient(client.app)
    b.post("/calendar/ics/upload", content=SCHEDULE)
    b.put("/me", json={"home": SHENANDOAH})
    yield b
    client.post("/demo/scenario", json={"scenario": "live"})
    b.close()


def test_test_event_joins_the_real_calendar(browser):
    r = browser.post("/demo/test-event")
    assert r.status_code == 200, r.text
    start = datetime.fromisoformat(r.json()["start_time"].replace("Z", "+00:00"))
    assert timedelta(minutes=110) <= start - datetime.now(timezone.utc) <= timedelta(minutes=121)
    names = {e["event_name"] for e in browser.get("/calendar/events", params={"hours": 24}).json()}
    assert "DryRoute test class" in names and "COP 3530 Data Structures" in {
        e["event_name"] for e in browser.get("/calendar/events", params={"hours": 168}).json()}
    nxt = browser.get("/calendar/next-event").json()
    assert nxt["event_name"] == "DryRoute test class" and nxt["trip"] is not None

    assert browser.delete("/demo/test-event").json() == {"removed": True}
    names = {e["event_name"] for e in browser.get("/calendar/events", params={"hours": 24}).json()}
    assert "DryRoute test class" not in names


def test_storm_plus_test_event_shows_route_alert_right_away(browser):
    browser.get("/alerts")  # the alert timer is now running: the buttons must not wait for it
    browser.post("/demo/test-event")
    browser.post("/demo/scenario", json={"scenario": "storm"})
    alerts = browser.get("/alerts").json()
    route = [a for a in alerts if a["title"].endswith("on your route to DryRoute test class")]
    assert route and route[0]["severity"] in ("warning", "critical")

    browser.post("/demo/scenario", json={"scenario": "live"})
    assert not [a for a in browser.get("/alerts").json() if "DryRoute test class" in a["title"]]


def test_needs_a_connected_calendar_and_a_real_building(client, browser):
    fresh = TestClient(client.app)
    assert fresh.post("/demo/test-event").status_code == 400
    fresh.close()
    assert browser.post("/demo/test-event", json={"building": "XYZ"}).status_code == 404
    assert browser.post("/demo/test-event", json={"minutes_from_now": 5}).status_code == 422
