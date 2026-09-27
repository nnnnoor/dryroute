"""Atlas integration check: MongoStore serves the same data as the committed files.

Needs network + MONGO_URI in backend/.env, so it only runs when asked:
    DRYROUTE_MONGO_TESTS=1 .venv/Scripts/python -m pytest tests/test_mongo.py
"""
import os

import pytest

from app.config import Settings

pytestmark = pytest.mark.skipif(os.environ.get("DRYROUTE_MONGO_TESTS") != "1",
                                reason="set DRYROUTE_MONGO_TESTS=1 to run against Atlas")


@pytest.fixture(scope="module")
def stores():
    from app.db.mongo import MongoStore
    from app.db.store import LocalStore
    return LocalStore(Settings(data_backend="local")), MongoStore(Settings(data_backend="mongo"))


def test_same_static_layers(stores):
    local, mongo = stores
    for name in ("segments", "parking", "hotspots"):
        a, b = getattr(local, name), getattr(mongo, name)
        assert set(a.index) == set(b.index), name
        assert set(a.columns) == set(b.columns), name


def test_closures_are_usable(stores):
    _, mongo = stores
    for c in mongo.active_closures():
        assert c["edge_id"] in mongo.segments.index
        assert isinstance(c["full_closure"], bool)
        assert c["start"].tzinfo is not None


def test_storm_works_without_ml_run(stores):
    _, mongo = stores
    run = mongo.latest_risk_run("storm")
    assert run is not None
    assert mongo.risk_scores(run["_id"])


def test_alerts_round_trip(stores):
    """Writes one alert for a throwaway user in the backend's own `alerts` collection, then deletes it."""
    _, mongo = stores
    user = "pytest-alerts"
    alert = {"alert_id": "al_test", "type": "tide", "severity": "warning", "read": False, "active": True}
    try:
        mongo.save_alert(user, alert)
        mongo.save_alert(user, {**alert, "read": True})  # upsert, not a second doc
        assert mongo.get_alerts(user) == [{**alert, "read": True}]
    finally:
        mongo.db.alerts.delete_many({"user_id": user})
    assert mongo.get_alerts(user) == []


def test_trips_round_trip(stores):
    """Writes trips for a throwaway user in the backend's own `trips` collection, then deletes them."""
    _, mongo = stores
    user = "pytest-trips"
    real = {"trip_id": "tr_real", "created_at": "2026-09-28T12:00:00Z", "demo": False}
    demo = {"trip_id": "tr_demo", "created_at": "2026-09-27T12:00:00Z", "demo": True}
    try:
        mongo.save_trip(user, real)
        mongo.save_trip(user, demo)
        mongo.delete_demo_trips(user)
        assert mongo.get_trips(user) == [real]
    finally:
        mongo.db.trips.delete_many({"user_id": user})
    assert mongo.get_trips(user) == []


def test_users_round_trip(stores):
    """Saves a profile for a throwaway user in the backend's own `users` collection, then deletes it."""
    local, mongo = stores
    user = "pytest-users"
    try:
        assert mongo.get_user(user) == local.get_user(user)  # no doc yet: fixture defaults
        profile = {**mongo.get_user(user), "name": "Alex", "arrival_buffer_minutes": 20}
        mongo.save_user(user, profile)
        mongo.save_user(user, {**profile, "name": "Sam"})  # upsert, not a second doc
        assert mongo.get_user(user) == {**profile, "name": "Sam"}
        assert mongo.db.users.count_documents({"_id": user}) == 1
    finally:
        mongo.db.users.delete_many({"_id": user})
    assert mongo.get_user(user) == local.get_user(user)
