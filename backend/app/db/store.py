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


class LocalStore(Store):
    def __init__(self, settings: Settings):
        d = settings.data_dir
        self.segments = gpd.read_parquet(d / "segments.parquet").set_index("edge_id", drop=False)
        self.parking = gpd.read_file(d / "fiu_parking.geojson").set_index("osm_id", drop=False)
        self.hotspots = gpd.read_file(d / "fiu_hotspots.geojson").set_index("hotspot_id", drop=False)
        with open(settings.fixtures_dir / "closures.json", encoding="utf-8") as f:
            self._closures = [_parse_closure(c) for c in json.load(f)]
        self._storm_run = {**FAKE_STORM_RUN, "computed_at": datetime.now(timezone.utc)}

    def active_closures(self, now=None):
        now = now or datetime.now(timezone.utc)
        return [c for c in self._closures
                if c["start"] <= now and (c["end"] is None or c["end"] >= now)]

    # There is no ML job locally. "live" has no run, so the backend falls back to the static score.
    # "storm" is a fake run derived from the static score, so the demo toggle works offline.
    def latest_risk_run(self, scenario):
        return self._storm_run if scenario == "storm" else None

    def risk_scores(self, run_id):
        if run_id != FAKE_STORM_RUN["_id"]:
            return {}
        streets = self.segments.drop_duplicates("street_id")
        # Same ranking as the static score, pushed up (0.32 -> 0.50, 0.58 -> 0.72)
        return dict(zip(streets["street_id"], streets["risk_score"] ** 0.6))


FAKE_STORM_RUN = {
    "_id": "fake-storm",
    "scenario": "storm",
    "model_version": "fake-storm-local",
    "rain": {"rain_mm_next_3h": 50.0, "rain_mm_last_24h": 80.0, "max_hourly_mm": 25.0},
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
