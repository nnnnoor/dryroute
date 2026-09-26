import re
from datetime import datetime, timezone

BRICKELL_TO_GOLD = {"from_lat": 25.7617, "from_lon": -80.1918, "parking_id": "way/112762942"}  # Gold Parking Garage
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def test_dry_day_is_not_compromised(client):
    b = client.get("/routes", params=BRICKELL_TO_GOLD).json()
    assert b["compromised"] is False
    assert b["usual"]["high_risk_m"] == 0


def test_safe_route_has_less_high_risk_road(client):
    client.post("/demo/scenario", json={"scenario": "storm"})
    b = client.get("/routes", params=BRICKELL_TO_GOLD).json()
    usual, safe = b["usual"], b["safe"]
    assert b["compromised"] is True
    assert b["same_route"] is False
    assert safe["high_risk_m"] < usual["high_risk_m"]
    assert b["comparison"]["high_risk_m_avoided"] == usual["high_risk_m"] - safe["high_risk_m"]
    assert b["comparison"]["extra_minutes"] >= 0
    assert b["coverage"] == {"origin_in_area": True, "destination_in_area": True, "note": None}
    lon, lat = usual["geometry"]["coordinates"][0]
    assert abs(lon - -80.1918) < 0.01 and abs(lat - 25.7617) < 0.01  # starts near Brickell, [lon, lat]
    assert ISO_Z.match(usual["depart_at"]) and ISO_Z.match(usual["arrive_at"])


def test_arrive_by_sets_departure(client):
    b = client.get("/routes", params={**BRICKELL_TO_GOLD, "arrive_by": "2026-09-28T13:00:00Z"}).json()
    usual = b["usual"]
    assert usual["arrive_at"] == "2026-09-28T13:00:00Z"
    depart = datetime.fromisoformat(usual["depart_at"])
    assert depart < datetime(2026, 9, 28, 13, tzinfo=timezone.utc)


def test_origin_outside_area(client):
    b = client.get("/routes", params={"from_lat": 25.6870, "from_lon": -80.3500,  # Kendall
                                      "to_lat": 25.7563, "to_lon": -80.3736}).json()
    assert b["coverage"]["origin_in_area"] is False
    assert "outside the mapped area" in b["coverage"]["note"]


def test_full_closure_is_avoided(client):
    client.post("/demo/scenario", json={"scenario": "storm"})
    usual = client.get("/routes", params=BRICKELL_TO_GOLD).json()["usual"]
    closed_edge = usual["risk_segments"][0]["segment_id"]
    store = client.app.state.store
    store._closures.append({
        "edge_id": closed_edge, "closure_id": "test_full", "name": "test", "kind": "closure", "full_closure": True,
        "start": datetime(2026, 1, 1, tzinfo=timezone.utc), "end": None})
    try:
        b = client.get("/routes", params=BRICKELL_TO_GOLD).json()
    finally:
        store._closures.pop()
    for r in (b["usual"], b["safe"]):
        assert closed_edge not in {s["segment_id"] for s in r["risk_segments"]}


def test_bad_requests(client):
    base = {"from_lat": 25.7617, "from_lon": -80.1918}
    assert client.get("/routes", params=base).status_code == 400  # no destination
    assert client.get("/routes", params={**BRICKELL_TO_GOLD, "to_lat": 25.75, "to_lon": -80.3}).status_code == 400
    assert client.get("/routes", params={**base, "parking_id": "way/0"}).status_code == 404
    assert client.get("/routes", params={**BRICKELL_TO_GOLD, "depart_at": "2026-09-28T13:00:00"}).status_code == 400
    assert client.get("/routes", params={**BRICKELL_TO_GOLD, "depart_at": "2026-09-28T12:00:00Z",
                                         "arrive_by": "2026-09-28T13:00:00Z"}).status_code == 400
