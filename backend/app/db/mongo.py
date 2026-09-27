"""Store backed by MongoDB Atlas (DATA_BACKEND=mongo). Same interface and shapes as LocalStore.

Collections (db settings.mongo_db): segments, parking, fiu_hotspots and closures from the data pipeline;
risk_runs and risk_scores from the ML job (docs/api-contracts.md). The static layers are loaded into
memory at startup (~15 s from Atlas; with STATIC_DATA=files the same layers come from the committed files in
~2 s, for serverless cold starts); closures and risk runs are queried per request. The backend only reads
those; the only collections it writes are its own `users`, `alerts`, `trips`, `app_state` (the demo
switch) and the calendar sessions (`calendar_sessions`, `oauth_pending`: integrations/google_calendar.py).

Until ML writes a "storm" run, the storm scenario falls back to the local fake storm (model_version
"fake-storm-local") so the demo toggle keeps working. "live" never falls back to fake data: with no
fresh ML run it serves the static score (stale). User profiles come from the users collection (_id =
user_id); fields a user never saved come from fixtures/user.json, so the demo user needs no seeding.
"""
from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd
from pymongo import DESCENDING, MongoClient
from shapely.geometry import shape

from app.config import Settings
from app.db.store import Store, fake_run, fake_scores, load_default_user, load_static_layers


class MongoStore(Store):
    def __init__(self, settings: Settings):
        if not settings.mongo_uri:
            raise RuntimeError("DATA_BACKEND=mongo needs MONGO_URI in backend/.env")
        self.client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=15000, tz_aware=True)
        self.client.admin.command("ping")  # fail at startup, not on the first request
        self.db = self.client[settings.mongo_db]

        if settings.static_data == "files":
            load_static_layers(self, settings.data_dir)
        else:
            self.segments = _frame(self.db.segments.find({}), "edge_id").set_index("edge_id", drop=False)
            self.parking = _frame(self.db.parking.find({}), None).set_index("osm_id", drop=False)
            self.hotspots = _frame(self.db.fiu_hotspots.find({}), None).set_index("hotspot_id", drop=False)
        self._default_user = load_default_user(settings)

    # app_state: the demo switch, one doc read per use so every server copy sees a change at once
    def demo_scenario(self):
        doc = self.db.app_state.find_one({"_id": "demo_scenario"}) or {}
        return doc.get("scenario", "live")

    def set_demo_scenario(self, scenario):
        self.db.app_state.replace_one({"_id": "demo_scenario"}, {"scenario": scenario}, upsert=True)

    # users: the backend's own collection, one doc per user (_id = user_id)
    def get_user(self, user_id="demo"):
        doc = self.db.users.find_one({"_id": user_id}, {"_id": 0}) or {}
        return {**self._default_user, **doc, "user_id": user_id}

    def save_user(self, user_id, user):
        self.db.users.replace_one({"_id": user_id}, {k: v for k, v in user.items() if k != "user_id"}, upsert=True)

    # alerts: the backend's own collection (never written by the pipeline or ML)
    def get_alerts(self, user_id):
        return [{k: v for k, v in d.items() if k not in ("_id", "user_id")}
                for d in self.db.alerts.find({"user_id": user_id})]

    def save_alert(self, user_id, alert):
        self.db.alerts.replace_one({"_id": f"{user_id}|{alert['alert_id']}"},
                                   {**alert, "user_id": user_id}, upsert=True)

    # trips: also the backend's own collection
    def get_trips(self, user_id):
        return [{k: v for k, v in d.items() if k not in ("_id", "user_id")}
                for d in self.db.trips.find({"user_id": user_id})]

    def save_trip(self, user_id, trip):
        self.db.trips.replace_one({"_id": f"{user_id}|{trip['trip_id']}"}, {**trip, "user_id": user_id}, upsert=True)

    def delete_demo_trips(self, user_id):
        self.db.trips.delete_many({"user_id": user_id, "demo": True})

    def active_closures(self, now=None):
        now = now or datetime.now(timezone.utc)
        docs = self.db.closures.find({"start": {"$lte": now}, "$or": [{"end": {"$gte": now}}, {"end": None}]})
        # Planned construction has full_closure null: passable, just slower
        return [{**d, "full_closure": bool(d.get("full_closure"))} for d in docs]

    def latest_risk_run(self, scenario):
        run = self.db.risk_runs.find_one({"scenario": scenario}, sort=[("computed_at", DESCENDING)])
        if run is None:
            return fake_run("storm") if scenario == "storm" else None
        if isinstance(run["computed_at"], str):  # tolerate ISO strings as well as BSON dates
            run["computed_at"] = pd.Timestamp(run["computed_at"]).tz_convert("UTC").to_pydatetime()
        return run

    def risk_scores(self, run_id):
        if run_id == "fake-storm":
            return fake_scores(self.segments, run_id)
        scores = {}
        for d in self.db.risk_scores.find({"run_id": run_id}, {"street_id": 1, "risk_score": 1, "risk_label": 1}):
            street = d.get("street_id") or d["_id"].split("|")[-1]  # _id may be "<scenario>|<street_id>"
            scores[street] = {"risk_score": d["risk_score"], "risk_label": d.get("risk_label")}
        return scores


def _frame(docs, id_column: str | None) -> gpd.GeoDataFrame:
    """Mongo docs with a GeoJSON `geometry` -> GeoDataFrame (EPSG:4326). `_id` is renamed to id_column."""
    rows = list(docs)
    geometry = [shape(d.pop("geometry")) for d in rows]
    frame = pd.DataFrame(rows)
    frame = frame.rename(columns={"_id": id_column}) if id_column else frame.drop(columns="_id")
    return gpd.GeoDataFrame(frame, geometry=geometry, crs="EPSG:4326")
