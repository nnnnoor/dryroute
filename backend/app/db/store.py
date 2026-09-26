"""Data access. Services use a Store and never care where the data came from.

The static layers (segments, parking, hotspots) are loaded into memory at startup in both modes:
routing needs every segment anyway, and they only change when the pipeline is rerun. Time-varying
data (closures; later users, trips, alerts) is read per request.

- LocalStore: committed pipeline files (data-pipeline/data/processed) + backend/fixtures. No network.
- Mongo mode: same interface backed by Atlas (added in db/mongo.py once we have credentials).
"""
import json
from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd

from app.config import Settings


class Store:
    segments: gpd.GeoDataFrame   # one row per directed edge, indexed by edge_id, EPSG:4326
    parking: gpd.GeoDataFrame    # FIU lots/garages, indexed by osm_id
    hotspots: gpd.GeoDataFrame   # FIU campus ponding areas, indexed by hotspot_id

    def active_closures(self, now: datetime | None = None) -> list[dict]:
        """Closure docs (Mongo flood.closures shape) active at `now` (UTC; default: current time)."""
        raise NotImplementedError

    def latest_risk_run(self, scenario: str) -> dict | None:
        """Newest risk_runs doc (written by the ML job) for "live" or "storm", or None."""
        raise NotImplementedError

    def risk_scores(self, run_id: str) -> dict[str, float]:
        """street_id -> risk_score for one ML run (risk_scores collection)."""
        raise NotImplementedError

    def get_user(self, user_id: str = "demo") -> dict:
        """User profile: home, preferred_parking_id, arrival_buffer_minutes (users collection)."""
        raise NotImplementedError


class LocalStore(Store):
    def __init__(self, settings: Settings):
        d = settings.data_dir
        self.segments = gpd.read_parquet(d / "segments.parquet").set_index("edge_id", drop=False)
        self.parking = gpd.read_file(d / "fiu_parking.geojson").set_index("osm_id", drop=False)
        self.hotspots = gpd.read_file(d / "fiu_hotspots.geojson").set_index("hotspot_id", drop=False)
        with open(settings.fixtures_dir / "closures.json", encoding="utf-8") as f:
            self._closures = [_parse_closure(c) for c in json.load(f)]
        with open(settings.fixtures_dir / "user.json", encoding="utf-8") as f:
            self._user = json.load(f)

    def get_user(self, user_id="demo"):
        return self._user

    def active_closures(self, now=None):
        now = now or datetime.now(timezone.utc)
        return [c for c in self._closures
                if c["start"] <= now and (c["end"] is None or c["end"] >= now)]

    # There is no ML job locally, so both scenarios get a fake run derived from the static score:
    # "live" is a dry day, "storm" is heavy rain. Always fresh, so they never go stale.
    def latest_risk_run(self, scenario):
        run = FAKE_RUNS.get(scenario)
        return {**run, "computed_at": datetime.now(timezone.utc)} if run else None

    def risk_scores(self, run_id):
        run = next((r for r in FAKE_RUNS.values() if r["_id"] == run_id), None)
        if run is None:
            return {}
        streets = self.segments.drop_duplicates("street_id")
        return dict(zip(streets["street_id"], run["transform"](streets["risk_score"])))


FAKE_RUNS = {
    "live": {
        "_id": "fake-dry",
        "scenario": "live",
        "model_version": "fake-dry-local",
        "rain": {"rain_mm_next_3h": 0.0, "rain_mm_last_24h": 0.0, "max_hourly_mm": 0.0},
        # Scaled down: max 0.96 -> 0.38, so nothing is high risk
        "transform": lambda s: s * 0.4,
    },
    "storm": {
        "_id": "fake-storm",
        "scenario": "storm",
        "model_version": "fake-storm-local",
        "rain": {"rain_mm_next_3h": 50.0, "rain_mm_last_24h": 80.0, "max_hourly_mm": 25.0},
        # Same ranking as the static score, pushed up (0.32 -> 0.50, 0.58 -> 0.72)
        "transform": lambda s: s ** 0.6,
    },
}


def _parse_closure(doc: dict) -> dict:
    """ISO strings -> aware UTC datetimes, matching what pymongo returns for Mongo docs."""
    out = dict(doc)
    for k in ("start", "end"):
        if out.get(k) is not None:
            out[k] = pd.Timestamp(out[k]).tz_convert("UTC").to_pydatetime()
    return out


def make_store(settings: Settings) -> Store:
    if settings.data_backend == "local":
        return LocalStore(settings)
    raise NotImplementedError("DATA_BACKEND=mongo is not wired up yet; use DATA_BACKEND=local")
