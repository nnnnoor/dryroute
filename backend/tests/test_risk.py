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


def test_segments_risk_min_label_and_storm(client):
    dry = client.get("/segments/risk", params={**BRICKELL, "min_label": "high"}).json()["features"]
    assert dry == []  # a dry day has no high-risk roads
    client.post("/demo/scenario", json={"scenario": "storm"})
    storm = client.get("/segments/risk", params={**BRICKELL, "min_label": "high"}).json()["features"]
    assert len(storm) > 0
    assert all(f["properties"]["risk_label"] == "high" for f in storm)
    medium_up = client.get("/segments/risk", params={**BRICKELL, "min_label": "medium"}).json()["features"]
    assert len(medium_up) > len(storm)
    assert {f["properties"]["risk_label"] for f in medium_up} == {"medium", "high"}


def fake_ml_run(monkeypatch, store, run_id, score, label):
    """Pretend ML wrote a run giving every street the same score and label."""
    from datetime import datetime, timezone
    run = {"_id": run_id, "scenario": "live", "computed_at": datetime.now(timezone.utc),
           "model_version": "test", "rain": None}
    streets = store.segments["street_id"].unique()
    monkeypatch.setattr(store, "latest_risk_run", lambda scenario: run)
    monkeypatch.setattr(store, "risk_scores", lambda rid: {s: {"risk_score": score, "risk_label": label}
                                                           for s in streets})


def test_ml_labels_override_backend_cutoffs(client, monkeypatch):
    store = client.app.state.store
    trip = {"from_lat": 25.7617, "from_lon": -80.1918, "to_lat": 25.7563, "to_lon": -80.3736}

    # High score, but ML says low: nothing is high risk
    fake_ml_run(monkeypatch, store, "ml-all-low", 0.9, "low")
    feats = client.get("/segments/risk", params=BRICKELL).json()["features"]
    assert {f["properties"]["risk_label"] for f in feats} == {"low"}
    assert client.get("/routes", params=trip).json()["compromised"] is False

    # Low score, but ML says high: every road is high risk
    fake_ml_run(monkeypatch, store, "ml-all-high", 0.1, "high")
    b = client.get("/routes", params=trip).json()
    assert b["compromised"] is True
    assert b["usual"]["risk_label"] == "high"


def test_segments_risk_bad_box(client):
    r = client.get("/segments/risk", params={**BRICKELL, "west": -80.1, "east": -80.2})
    assert r.status_code == 400


def test_rain_level_reads_ml_daily_and_fake_3h_fields():
    from app.services.flood_risk import rain_level
    # ML's runs: the day's total, in mm
    assert rain_level({"rain_mm": 0.2, "rain_lag1_mm": 0, "rain_prior3_mm": 0}) == "none"
    assert rain_level({"rain_mm": 6, "rain_lag1_mm": 12, "rain_prior3_mm": 24}) == "light"
    assert rain_level({"rain_mm": 18, "rain_lag1_mm": 0, "rain_prior3_mm": 0}) == "moderate"
    assert rain_level({"rain_mm": 35, "rain_lag1_mm": 12, "rain_prior3_mm": 24}) == "heavy"
    # local fake runs: the next 3 h
    assert rain_level({"rain_mm_next_3h": 50}) == "heavy"
    assert rain_level({"rain_mm_next_3h": 0}) == "none"
    assert rain_level({"something_else": 3}) is None and rain_level(None) is None


def test_ml_daily_rain_drives_parking(client, monkeypatch):
    """With an ML run on a dry day (rain_mm 0), lots are scaled down like the fake dry day."""
    from datetime import datetime, timezone
    store = client.app.state.store
    run = {"_id": "ml-dry", "scenario": "live", "computed_at": datetime.now(timezone.utc),
           "model_version": "daily_report_v2", "rain": {"rain_mm": 0.0, "rain_lag1_mm": 0.0, "rain_prior3_mm": 0.0}}
    monkeypatch.setattr(store, "latest_risk_run", lambda scenario: run)
    monkeypatch.setattr(store, "risk_scores", lambda rid: {})
    assert client.get("/weather").json()["rain_level"] == "none"
    assert {l["hazard_label"] for l in client.get("/parking").json()} == {"low"}
