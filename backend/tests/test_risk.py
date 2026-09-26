BRICKELL = {"west": -80.2000, "south": 25.7580, "east": -80.1880, "north": 25.7700}


def test_weather_live_without_ml_run_is_stale(client):
    body = client.get("/weather").json()
    assert body["scenario"] == "live"
    assert body["stale"] is True
    assert body["model_version"] == "static-baseline"


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
    assert all(f["properties"]["risk_score"] >= 0.6 for f in dry)
    client.post("/demo/scenario", json={"scenario": "storm"})
    storm = client.get("/segments/risk", params={**BRICKELL, "min_risk": 0.6}).json()["features"]
    assert len(storm) > len(dry)


def test_segments_risk_bad_box(client):
    r = client.get("/segments/risk", params={**BRICKELL, "west": -80.1, "east": -80.2})
    assert r.status_code == 400
