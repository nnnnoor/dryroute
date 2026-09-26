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
