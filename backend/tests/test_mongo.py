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
