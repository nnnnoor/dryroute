"""Live flood risk per road, as written by the ML job (docs/api-contracts.md, risk_scores).

The backend does not compute risk. It serves the newest ML run for the active scenario ("live", or
"storm" for the demo), mapped from streets to both directions' edges, and falls back to the static
risk_score in segments when there is no usable run.

Labels (low / medium / high) are ML's: its risk_score is a percentile ranking, so only ML knows which
scores mean "high". The backend's own cutoffs (settings.risk_medium / risk_high) are only a fallback,
for the static score and for streets a run left unlabeled.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from shapely.geometry import box

from app.config import Settings
from app.db.store import Store
from app.timeutil import iso_utc

SCENARIOS = ("live", "storm")
LABELS = ("low", "medium", "high")
LABEL_RANK = {label: i for i, label in enumerate(LABELS)}


def risk_label(score: float, settings: Settings) -> str:
    """Fallback label from the backend's cutoffs (static score, parking lots)."""
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
        self._cached_key = None
        self._cached = None  # (scores, labels), both indexed by edge_id
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
        return self._current()[0]

    def edge_labels(self) -> pd.Series:
        """edge_id -> "low" | "medium" | "high" right now."""
        return self._current()[1]

    def _current(self) -> tuple[pd.Series, pd.Series]:
        run, stale = self.current_run()
        key = "static" if stale else run["_id"]
        if key != self._cached_key:
            segs = self.store.segments
            if stale:
                scores = segs["risk_score"]
                labels = self._cutoff_labels(scores)
            else:
                by_street = self.store.risk_scores(run["_id"])
                # A street the run skipped keeps its static score
                scores = segs["street_id"].map({s: v["risk_score"] for s, v in by_street.items()})
                scores = scores.fillna(segs["risk_score"])
                labels = segs["street_id"].map({s: v.get("risk_label") for s, v in by_street.items()})
                labels = labels.where(labels.isin(LABELS), self._cutoff_labels(scores))
            self._cached_key, self._cached = key, (scores, labels)
        return self._cached

    def _cutoff_labels(self, scores: pd.Series) -> pd.Series:
        s = self.settings
        return pd.Series(np.select([scores >= s.risk_high, scores >= s.risk_medium], ["high", "medium"], "low"),
                         index=scores.index)

    def weather(self) -> dict:
        run, stale = self.current_run()
        return {
            "scenario": self.scenario,
            "computed_at": iso_utc(run["computed_at"]) if run else None,
            "stale": stale,
            "model_version": run["model_version"] if run and not stale else "static-baseline",
            "rain": run["rain"] if run else None,
            "rain_level": rain_level(run["rain"]) if run else None,
        }

    def streets_in_box(self, west, south, east, north, min_label="low") -> tuple[list[dict], bool]:
        """GeoJSON features for streets intersecting the box, riskiest first. Returns (features, truncated)."""
        idx = self._streets.sindex.query(box(west, south, east, north), predicate="intersects")
        streets = self._streets.iloc[idx]
        scores, labels = self._current()
        table = pd.DataFrame({"score": scores.reindex(streets.index), "label": labels.reindex(streets.index)})
        table["rank"] = table["label"].map(LABEL_RANK)
        table = table[table["rank"] >= LABEL_RANK[min_label]]
        table = table.sort_values(["rank", "score"], ascending=False)
        limit = self.settings.segments_risk_limit
        truncated = len(table) > limit
        table = table.iloc[:limit]

        closed = {self.store.segments.at[c["edge_id"], "street_id"]
                  for c in self.store.active_closures() if c["full_closure"]}
        features = []
        for edge_id, score, label in zip(table.index, table["score"], table["label"]):
            row = streets.loc[edge_id]
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[round(x, 6), round(y, 6)] for x, y in row.geometry.coords]},
                "properties": {
                    "segment_id": edge_id,
                    "name": row["name"] if isinstance(row["name"], str) else None,
                    "risk_score": round(float(score), 3),
                    "risk_label": label,
                    "closed": row["street_id"] in closed,
                },
            })
        return features, truncated
