import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # `with` runs the startup that loads the data (~5 s), once per test run
        yield c


@pytest.fixture(autouse=True)
def live_scenario(client):
    client.app.state.risk.scenario = "live"
