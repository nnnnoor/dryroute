BRICKELL = {"west": -80.2000, "south": 25.7580, "east": -80.1880, "north": 25.7700}


def test_weather_live_is_fake_dry_day(client):
    body = client.get("/weather").json()
    assert body["scenario"] == "live"
    assert body["stale"] is False
    assert body["rain_level"] == "none"


def test_no_ml_run_falls_back_to_static(client, monkeypatch):
    store = client.app.state.store
    monkeypatch.setattr(store, "latest_risk_run", lambda scenario: None)
    body = client.get("/weather").json()
    assert body["stale"] is True
    assert body["model_version"] == "static-baseline"
    risk = client.app.state.risk.edge_risk()
    assert risk.equals(store.segments["risk_score"])


def test_storm_scenario(client):
    body = client.post("/demo/scenario", json={"scenario": "storm"}).json()
    assert body["stale"] is False
    assert body["rain_level"] == "heavy"
    assert client.post("/demo/scenario", json={"scenario": "flood"}).status_code == 422


def test_segments_risk_box(client):
    body = client.get("/segments/risk", params=BRICKELL).json()
    feats = body["features"]
    assert body["truncated"] is False
    assert 200 < len(feats) < 341  # 341 directed edges in this box; twins are merged into one street
    scores = [f["properties"]["risk_score"] for f in feats]
    assert scores == sorted(scores, reverse=True)
    for f in feats:
        assert f["properties"]["risk_label"] in {"low", "medium", "high"}
        lon, lat = f["geometry"]["coordinates"][0]
        assert -80.3 < lon < -80.1 and 25.7 < lat < 25.8  # GeoJSON order is [lon, lat]


def test_segments_risk_min_risk_and_storm(client):
    dry = client.get("/segments/risk", params={**BRICKELL, "min_risk": 0.6}).json()["features"]
    assert dry == []  # a dry day has no high-risk roads
    client.post("/demo/scenario", json={"scenario": "storm"})
    storm = client.get("/segments/risk", params={**BRICKELL, "min_risk": 0.6}).json()["features"]
    assert len(storm) > 0
    assert all(f["properties"]["risk_score"] >= 0.6 for f in storm)


def test_segments_risk_bad_box(client):
    r = client.get("/segments/risk", params={**BRICKELL, "west": -80.1, "east": -80.2})
    assert r.status_code == 400
