import pytest

PG6 = {"from_lat": 25.7617, "from_lon": -80.1918, "parking_id": "way/112781054"}  # Brickell -> Parking Garage 6


@pytest.fixture(autouse=True)
def empty_history(client):
    store = client.app.state.store
    store._trips.clear()
    store._alerts.clear()
    yield
    store._trips.clear()
    store._alerts.clear()


def test_storm_trip_on_safe_route_saves_time(client):
    client.post("/demo/scenario", json={"scenario": "storm"})
    route = client.get("/routes", params=PG6).json()
    t = client.post("/trips", json=PG6).json()
    assert t["take"] == "safe"  # default: what the recommendation says
    assert t["compromised"] is True and t["scenario"] == "storm"
    assert t["high_risk_m_avoided"] == route["comparison"]["high_risk_m_avoided"] > 0
    assert t["high_risk_segments_avoided"] > 0
    share = t["high_risk_m_avoided"] / route["usual"]["high_risk_m"]
    assert t["time_saved_minutes"] == pytest.approx(round(15 * share - t["extra_minutes"], 1), abs=0.11)
    assert t["destination"] == {"parking_id": "way/112781054", "name": "Parking Garage 6"}


def test_usual_route_or_dry_day_saves_nothing(client):
    client.post("/demo/scenario", json={"scenario": "storm"})
    t = client.post("/trips", json={**PG6, "take": "usual"}).json()
    assert t["compromised"] is True and t["high_risk_m_avoided"] == 0 and t["time_saved_minutes"] == 0
    client.post("/demo/scenario", json={"scenario": "live"})
    t = client.post("/trips", json=PG6).json()
    assert t["compromised"] is False and t["take"] == "usual" and t["time_saved_minutes"] == 0


def test_dashboard_adds_up(client):
    client.post("/demo/scenario", json={"scenario": "storm"})
    a = client.post("/trips", json=PG6).json()
    b = client.post("/trips", json={**PG6, "take": "usual"}).json()
    client.app.state.store.save_alert("demo", {"alert_id": "al_x", "active": False})
    d = client.get("/dashboard").json()
    assert d["trips_planned"] == 2
    assert d["time_saved_minutes"] == round(a["time_saved_minutes"] + b["time_saved_minutes"])
    assert d["risky_segments_avoided"] == a["high_risk_segments_avoided"]
    assert d["alerts_received"] == 1  # resolved alerts still count as received
    assert d["compromised_trips"] == 2 and d["safer_routes_taken"] == 1
    assert [t["trip_id"] for t in client.get("/trips").json()] == [t["trip_id"] for t in d["recent_trips"]]


def test_empty_dashboard(client):
    assert client.get("/dashboard").json() == {
        "trips_planned": 0, "time_saved_minutes": 0, "risky_segments_avoided": 0, "alerts_received": 0,
        "high_risk_km_avoided": 0.0, "compromised_trips": 0, "safer_routes_taken": 0, "recent_trips": []}


def test_seed_is_idempotent_keeps_real_trips_and_scenario(client):
    real = client.post("/trips", json=PG6).json()
    first = client.post("/demo/seed-trips").json()
    again = client.post("/demo/seed-trips").json()
    assert first["trips_planned"] == again["trips_planned"] == 7  # 6 demo + 1 real
    assert again["compromised_trips"] >= 2 and again["time_saved_minutes"] > 0
    assert real["trip_id"] in {t["trip_id"] for t in client.get("/trips").json()}
    assert client.get("/weather").json()["scenario"] == "live"  # seeding restored the scenario


def test_trip_validation(client):
    assert client.post("/trips", json={"from_lat": 25.76, "from_lon": -80.19}).status_code == 400
    assert client.post("/trips", json={**PG6, "to_lat": 25.75, "to_lon": -80.3}).status_code == 400
    assert client.post("/trips", json={**PG6, "parking_id": "way/0"}).status_code == 404
    assert client.post("/trips", json={**PG6, "take": "fastest"}).status_code == 422
    assert client.post("/trips", json={**PG6, "depart_at": "2026-09-28T09:00:00"}).status_code == 400
