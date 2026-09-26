"""Real TomTom call (network + TOMTOM_API_KEY in backend/.env). Only runs when asked:
    DRYROUTE_TOMTOM_TESTS=1 .venv/Scripts/python -m pytest tests/test_tomtom_live.py -s
"""
import os

import pytest
from dotenv import dotenv_values

from app.config import BACKEND_DIR
from app.integrations.tomtom import TomTomTraffic

pytestmark = pytest.mark.skipif(os.environ.get("DRYROUTE_TOMTOM_TESTS") != "1",
                                reason="set DRYROUTE_TOMTOM_TESTS=1 to call TomTom")


def test_real_route_travel_time(client):
    key = dotenv_values(BACKEND_DIR / ".env").get("TOMTOM_API_KEY")
    assert key, "put TOMTOM_API_KEY in backend/.env"
    route = client.get("/routes", params={"from_lat": 25.7617, "from_lon": -80.1918,
                                          "parking_id": "way/112762942"}).json()["usual"]
    result = TomTomTraffic(key).travel_times(route["route_id"], route["geometry"]["coordinates"], None)
    assert result is not None, "TomTom call failed: check the key and the warning in the log"
    print(f"\nours: {route['distance_m']} m, {route['eta_minutes']} min free-flow | TomTom: "
          f"{result['length_m']:.0f} m, {result['traffic_s'] / 60:.1f} min with traffic, "
          f"{result['no_traffic_s'] / 60:.1f} min without")
    # TomTom followed our path, not its own: lengths agree within 5%
    assert abs(result["length_m"] - route["distance_m"]) / route["distance_m"] < 0.05
