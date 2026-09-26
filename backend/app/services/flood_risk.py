"""Live flood risk per road, as written by the ML job (docs/api-contracts.md, risk_scores).

The backend does not compute risk. It serves the newest ML run for the active scenario ("live", or
"storm" for the demo), mapped from streets to both directions' edges, and falls back to the static
risk_score in segments when there is no usable run.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
from shapely.geometry import box

from app.config import Settings
from app.db.store import Store

SCENARIOS = ("live", "storm")


def risk_label(score: float, settings: Settings) -> str:
    if score >= settings.risk_high:
        return "high"
    if score >= settings.risk_medium:
        return "medium"
    return "low"


def rain_level(rain: dict | None) -> str | None:
    """Coarse label from the run's forecast rain in the next 3 h (mm)."""
    if not rain or rain.get("rain_mm_next_3h") is None:
        return None
    mm = rain["rain_mm_next_3h"]
    if mm < 1:
        return "none"
    if mm < 7.5:
        return "light"
    if mm < 20:
        return "moderate"
    return "heavy"


class RiskService:
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings
        self.scenario = "live"
        self._cached_run_id = None
        self._cached_risk = None
        segs = store.segments
        # One row per physical street (street_id is always one of its edge_ids), for map display
        self._streets = segs[segs["edge_id"] == segs["street_id"]]

    def current_run(self) -> tuple[dict | None, bool]:
        """(run, stale). Stale means the static score is served instead of the run's scores."""
        run = self.store.latest_risk_run(self.scenario)
        if run is None:
            return None, True
        age = datetime.now(timezone.utc) - run["computed_at"]
        stale = self.scenario == "live" and age > timedelta(hours=self.settings.risk_stale_hours)
        return run, stale

    def edge_risk(self) -> pd.Series:
        """edge_id -> risk score (0-1) right now."""
        run, stale = self.current_run()
        segs = self.store.segments
        if stale:
            return segs["risk_score"]
        if run["_id"] != self._cached_run_id:
            by_street = self.store.risk_scores(run["_id"])
            # A street the run skipped keeps its static score
            self._cached_risk = segs["street_id"].map(by_street).fillna(segs["risk_score"])
            self._cached_run_id = run["_id"]
        return self._cached_risk

    def weather(self) -> dict:
        run, stale = self.current_run()
        return {
            "scenario": self.scenario,
            "computed_at": run["computed_at"] if run else None,
            "stale": stale,
            "model_version": run["model_version"] if run and not stale else "static-baseline",
            "rain": run["rain"] if run else None,
            "rain_level": rain_level(run["rain"]) if run else None,
        }

    def streets_in_box(self, west, south, east, north, min_risk=0.0) -> tuple[list[dict], bool]:
        """GeoJSON features for streets intersecting the box, riskiest first. Returns (features, truncated)."""
        idx = self._streets.sindex.query(box(west, south, east, north), predicate="intersects")
        streets = self._streets.iloc[idx]
        risk = self.edge_risk().reindex(streets.index)
        keep = risk >= min_risk
        streets, risk = streets[keep], risk[keep].sort_values(ascending=False)
        limit = self.settings.segments_risk_limit
        truncated = len(risk) > limit
        risk = risk.iloc[:limit]

        closed = {self.store.segments.at[c["edge_id"], "street_id"]
                  for c in self.store.active_closures() if c["full_closure"]}
        features = []
        for edge_id, score in risk.items():
            row = streets.loc[edge_id]
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[round(x, 6), round(y, 6)] for x, y in row.geometry.coords]},
                "properties": {
                    "segment_id": edge_id,
                    "name": row["name"] if isinstance(row["name"], str) else None,
                    "risk_score": round(float(score), 3),
                    "risk_label": risk_label(score, self.settings),
                    "closed": row["street_id"] in closed,
                },
            })
        return features, truncated
