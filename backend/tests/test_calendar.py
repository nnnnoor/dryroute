from datetime import datetime, timedelta, timezone

import pytest

MONDAY_7AM_MIAMI = datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc)  # next: COP 3530, 9:00 in PC 213


@pytest.fixture
def calendar(client):
    return client.app.state.calendar


@pytest.mark.parametrize("location, code", [
    ("PC 213", "PC"), ("PC213", "PC"), ("GL 100", "GL"), ("Graham Center (GC) 243", "GC"), ("AHC5 110", "AHC5"),
    ("Online (Zoom)", None), ("ECS 135", None), ("", None), (None, None),
])
def test_resolve_location(calendar, location, code):
    b = calendar.resolve(location)
    assert (b["code"] if b else None) == code


def test_events_endpoint(client):
    events = client.get("/calendar/events", params={"hours": 168}).json()
    assert len(events) >= 10  # a full week of the fake schedule
    e = events[0]
    assert set(e) == {"event_id", "event_name", "start_time", "end_time", "location", "location_point"}
    online = [e for e in events if "Online" in e["location"]]
    assert online and all(e["location_point"] is None for e in online)


def test_next_event_leave_by(calendar):
    n = calendar.next_event(now=MONDAY_7AM_MIAMI)
    assert n["event_name"] == "COP 3530 Data Structures"
    assert n["start_time"] == "2026-09-28T13:00:00Z"
    trip = n["trip"]
    assert trip["parking_name"] == "Gold Parking Garage"
    depart = datetime.fromisoformat(n["recommended_departure"])
    assert depart.second == 0
    # leave early enough for the drive + walk + 10 min buffer
    budget = timedelta(minutes=trip["eta_minutes"] + trip["walk_minutes"] + 10)
    assert depart + budget <= datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)
    assert n["leave_in_minutes"] == round((depart - MONDAY_7AM_MIAMI).total_seconds() / 60)
    assert n["route_query"]["parking_id"] == trip["parking_id"]


def test_next_event_storm_takes_safe_route(client, calendar):
    client.post("/demo/scenario", json={"scenario": "storm"})
    trip = calendar.next_event(now=MONDAY_7AM_MIAMI)["trip"]
    assert trip["compromised"] is True
    assert trip["take"] == "safe"


def test_next_event_skips_online_and_nulls_unknown_building(calendar, monkeypatch):
    def fake_events(time_min, time_max):
        return [
            {"id": "a", "summary": "Online class", "location": "Zoom", "start": {"dateTime": "2026-09-28T09:00:00-04:00"},
             "end": {"dateTime": "2026-09-28T10:00:00-04:00"}},
            {"id": "b", "summary": "Lab", "location": "ECS 135", "start": {"dateTime": "2026-09-28T11:00:00-04:00"},
             "end": {"dateTime": "2026-09-28T12:00:00-04:00"}},
        ]
    monkeypatch.setattr(calendar.source, "list_events", fake_events)
    n = calendar.next_event(now=MONDAY_7AM_MIAMI)
    assert n["event_name"] == "Lab"
    assert n["location_point"] is None and n["trip"] is None and n["recommended_departure"] is None


def test_next_event_endpoint(client, calendar, monkeypatch):
    assert client.get("/calendar/next-event").status_code == 200
    assert client.get("/calendar/next-event", params={"from_lat": 25.76}).status_code == 400
    monkeypatch.setattr(calendar.source, "list_events", lambda time_min, time_max: [])
    assert client.get("/calendar/next-event").status_code == 204
