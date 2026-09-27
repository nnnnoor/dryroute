from datetime import datetime

import pytest

from tests.test_calendar import MONDAY_7AM_MIAMI

W10 = "way/435345239"


@pytest.fixture(autouse=True)
def default_profile(client):
    users = client.app.state.store._users
    users.clear()
    yield
    users.clear()


def test_default_profile_comes_from_the_fixture(client):
    assert client.get("/me").json() == {
        "user_id": "demo", "name": None, "home": {"label": "Brickell", "lat": 25.7617, "lon": -80.1918},
        "preferred_parking_id": None, "preferred_parking_name": None, "arrival_buffer_minutes": 10}


def test_put_changes_only_the_fields_sent(client):
    me = client.put("/me", json={"name": "  Alex ", "preferred_parking_id": W10}).json()
    assert me["name"] == "Alex" and me["preferred_parking_name"] == "W10 Parking Lot"
    assert me["arrival_buffer_minutes"] == 10 and me["home"]["label"] == "Brickell"

    home = {"label": "Coral Gables", "lat": 25.7215, "lon": -80.2684}
    me = client.put("/me", json={"home": home, "arrival_buffer_minutes": 20}).json()
    assert me["home"] == home and me["name"] == "Alex" and me["preferred_parking_id"] == W10
    assert client.get("/me").json() == me

    me = client.put("/me", json={"name": "", "preferred_parking_id": None}).json()
    assert me["name"] is None and me["preferred_parking_id"] is None and me["preferred_parking_name"] is None


def test_profile_drives_the_leave_by(client):
    calendar = client.app.state.calendar
    before = calendar.next_event(now=MONDAY_7AM_MIAMI)
    client.put("/me", json={"arrival_buffer_minutes": 25, "preferred_parking_id": W10})
    after = calendar.next_event(now=MONDAY_7AM_MIAMI)
    assert after["trip"]["parking_name"] == "W10 Parking Lot"
    assert datetime.fromisoformat(after["recommended_departure"]) < datetime.fromisoformat(before["recommended_departure"])


def test_validation(client):
    assert client.put("/me", json={"preferred_parking_id": "way/0"}).status_code == 404
    assert client.put("/me", json={"home": None}).status_code == 400
    assert client.put("/me", json={"arrival_buffer_minutes": None}).status_code == 400
    assert client.put("/me", json={"arrival_buffer_minutes": -5}).status_code == 422
    assert client.put("/me", json={"home": {"lat": 25.7}}).status_code == 422
    assert client.put("/me", json={"name": "x" * 61}).status_code == 422
    assert client.get("/me").json()["arrival_buffer_minutes"] == 10  # nothing saved
