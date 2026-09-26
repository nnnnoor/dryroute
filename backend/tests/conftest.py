import os

import pytest

# Tests always run offline on the committed data, whatever backend/.env says
os.environ["DATA_BACKEND"] = "local"
os.environ["TOMTOM_API_KEY"] = ""  # no real traffic calls; test_traffic.py fakes TomTom

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # `with` runs the startup that loads the data (~5 s), once per test run
        yield c


@pytest.fixture(autouse=True)
def live_scenario(client):
    client.app.state.risk.scenario = "live"
