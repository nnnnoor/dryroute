from datetime import datetime, timezone


def test_health_loads_all_data(client):
    body = client.get("/health").json()
    assert body["segments"] == body["graph_edges"] == 36371
    assert body["parking_lots"] == 46
    assert body["hotspots"] == 166


def test_closure_time_window(client):
    store = client.app.state.store
    assert len(store.active_closures(datetime(2026, 10, 1, tzinfo=timezone.utc))) == 1
    assert store.active_closures(datetime(2026, 8, 1, tzinfo=timezone.utc)) == []


def test_cors_allows_local_frontend(client):
    for origin in ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://localhost:3000"]:
        r = client.options("/routes", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        assert r.headers.get("access-control-allow-origin") == origin
    r = client.options("/routes", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers
