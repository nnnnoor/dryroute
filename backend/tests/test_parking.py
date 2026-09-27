from collections import Counter

W10 = "way/435345239"   # W10 Parking Lot: low ground, one of the most flood-exposed lots
GOLD = "way/112762942"  # Gold Parking Garage: the least exposed
FROM_BRICKELL = {"from_lat": 25.7617, "from_lon": -80.1918}


def test_parking_list(client):
    lots = client.get("/parking").json()
    assert len(lots) == 46
    lot = lots[0]
    assert set(lot) >= {"parking_id", "name", "type", "hazard_score", "hazard_label", "center", "geometry"}
    assert lot["geometry"]["type"] in {"Polygon", "MultiPolygon"}


def test_dry_day_no_lot_is_hazardous(client):
    assert {l["hazard_label"] for l in client.get("/parking").json()} == {"low"}
    b = client.get("/routes", params={**FROM_BRICKELL, "parking_id": W10}).json()
    assert b["parking"]["hazardous"] is False
    assert b["parking"]["alternatives"] == []
    assert b["recommendation"]["message"] == "No flooding expected on your usual route."


def test_storm_hazardous_lot_gets_safer_alternatives(client):
    client.post("/demo/scenario", json={"scenario": "storm"})
    labels = Counter(l["hazard_label"] for l in client.get("/parking").json())
    assert labels["high"] > 0

    b = client.get("/routes", params={**FROM_BRICKELL, "parking_id": W10}).json()
    p = b["parking"]
    assert p["planned"]["name"] == "W10 Parking Lot"
    assert p["hazardous"] is True
    alts = p["alternatives"]
    assert 1 <= len(alts) <= 3
    assert all(a["hazard_label"] == "low" for a in alts)
    assert alts[0]["type"] == "garage"
    assert f"Park at {alts[0]['name']} instead" in b["recommendation"]["message"]


def test_storm_safe_lot_is_not_flagged(client):
    client.post("/demo/scenario", json={"scenario": "storm"})
    p = client.get("/routes", params={**FROM_BRICKELL, "parking_id": GOLD}).json()["parking"]
    assert p["hazardous"] is False
    assert p["alternatives"] == []


def test_unfindable_or_restricted_lots_never_suggested(client):
    client.post("/demo/scenario", json={"scenario": "storm"})
    service = client.app.state.parking
    store = client.app.state.store
    for pid in store.parking.index:
        for alt in service.assess(pid)["alternatives"]:
            assert not any(w in alt["name"] for w in ["Unnamed", "Loading Area", "Staff", "Compound"])
            assert store.parking.at[alt["parking_id"], "access"] != "private"


def test_no_ml_run_assumes_rain(client, monkeypatch):
    monkeypatch.setattr(client.app.state.store, "latest_risk_run", lambda scenario: None)
    lot = client.app.state.parking.assess(W10)["planned"]
    assert lot["hazard_score"] == 0.71  # static parking_score, unscaled
