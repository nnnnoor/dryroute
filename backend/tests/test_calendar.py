from datetime import datetime, timedelta, timezone

import pytest

MONDAY_7AM_MIAMI = datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc)  # next: COP 3530, 9:00 in PC 213


@pytest.fixture
def calendar(client):
    return client.app.state.calendar


@pytest.mark.parametrize("location, code", [
    ("PC 213", "PC"), ("PC213", "PC"), ("GL 100", "GL"), ("Graham Center (GC) 243", "GC"), ("AHC5 110", "AHC5"),
    ("Online (Zoom)", None), ("ECS 135", None), ("", None), (None, None),
    # written by name, as students type them in Google Calendar
    ("Parking Garage 6 115", "PG6"), ("Chem & Physics 197", "CP"), ("Chemistry and Physics 197", "CP"),
    ("Green Library 100", "GL"), ("Graham Center 243", "GC"), ("Academic Health Center 1 110", "AHC1"),
    ("Parking Garage 7", None), ("Library", None), ("Health Center", None), ("Zoom Online Meeting", None),
])
def test_resolve_location(calendar, location, code):
    b = calendar.resolve(location)
    assert (b["code"] if b else None) == code


def test_events_endpoint(client):
    events = client.get("/calendar/events", params={"hours": 168}).json()
    assert len(events) >= 10  # a full week of the fake schedule
    e = events[0]
    assert set(e) == {"event_id", "event_name", "start_time", "end_time", "location", "location_point", "route_query"}
    online = [e for e in events if "Online" in e["location"]]
    assert online and all(e["location_point"] is None and e["route_query"] is None for e in online)
    # in person at a known building: the trip from the saved home, arriving before class (same as next-event)
    q = next(e for e in events if e["location_point"])["route_query"]
    assert set(q) >= {"from_lat", "from_lon", "arrive_by"} and ("parking_id" in q or "to_lat" in q)


def test_next_event_leave_by(calendar):
    n = calendar.next_event(now=MONDAY_7AM_MIAMI)
    assert n["event_name"] == "COP 3530 Data Structures"
    assert n["start_time"] == "2026-09-28T13:00:00Z"
    trip = n["trip"]
    assert trip["parking_name"] == "Gold Parking Garage"
    depart = datetime.fromisoformat(n["recommended_departure"])
    assert depart.second == 0
    # leave early enough for the drive + finding a spot + walk + 10 min buffer
    assert trip["parking_search_minutes"] == 5 + 4  # a garage, on a weekday morning
    budget = timedelta(minutes=trip["eta_minutes"] + trip["parking_search_minutes"] + trip["walk_minutes"] + 10)
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


def test_preferred_lot_is_honored_even_if_far(client, calendar, monkeypatch):
    """A student who always parks at W10 (~1 km from PC) still gets W10, with the longer walk counted."""
    store = client.app.state.store
    user = {**store.get_user(), "preferred_parking_id": "way/435345239"}
    monkeypatch.setattr(store, "get_user", lambda user_id="demo": user)
    trip = calendar.next_event(now=MONDAY_7AM_MIAMI)["trip"]
    assert trip["parking_name"] == "W10 Parking Lot"
    assert trip["walk_minutes"] >= 15


def test_parking_search_time_by_lot_type_and_hour(client):
    parking = client.app.state.parking
    gold, lot32 = "way/112762942", client.app.state.store.parking.index[client.app.state.store.parking["name"] == "Lot 32"][0]
    monday_830 = datetime(2026, 9, 28, 12, 30, tzinfo=timezone.utc)   # 8:30 am Miami
    monday_15 = datetime(2026, 9, 28, 19, 0, tzinfo=timezone.utc)     # 3:00 pm Miami
    saturday_830 = datetime(2026, 9, 26, 12, 30, tzinfo=timezone.utc)
    assert parking.search_minutes(gold, monday_830) == 9      # garage 5 + peak 4
    assert parking.search_minutes(gold, monday_15) == 5
    assert parking.search_minutes(gold, saturday_830) == 5    # no weekend peak
    assert parking.search_minutes(lot32, monday_15) == 3      # surface lot


def test_already_at_the_building_is_not_an_error(client, calendar, monkeypatch):
    """Home (or current location) snaps to the destination: the class shows, with no trip, and alerts don't crash."""
    def same_place(*args, **kwargs):
        raise ValueError("Start and destination are at the same place on the map")
    monkeypatch.setattr(calendar.trips, "plan", same_place)
    n = calendar.next_event(now=MONDAY_7AM_MIAMI)
    assert n["event_name"] == "COP 3530 Data Structures"
    assert n["trip"] is None and n["recommended_departure"] is None
    assert client.post("/alerts/refresh").status_code == 200


def test_every_event_route_query_matches_next_event(client, calendar):
    n = calendar.next_event(now=MONDAY_7AM_MIAMI)
    same = [e for e in calendar.events(48, now=MONDAY_7AM_MIAMI) if e["event_id"] == n["event_id"]]
    assert same and same[0]["route_query"] == n["route_query"]
