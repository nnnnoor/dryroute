"""Validate segments, deduplicate reports, and sample a weighted street-day panel."""
import hashlib

import numpy as np
import pandas as pd

from ml.settings import HISTORY_GAP_DAYS, HISTORY_MIN_EXPOSURE_DAYS, SEED, TRAIN_END, VALIDATION_END, WINDOWS
from ml.weather import RAIN_FEATURES

STATIC = ["fema_risk_level", "elev_p10", "sink_p90", "length", "drain_count", "is_tunnel"]


def street_table(segments):
    required = ["edge_id", "street_id", "is_bridge", "elev_water_only", "jurisdiction", *STATIC]
    missing = set(required) - set(segments)
    if missing:
        raise ValueError(f"Missing segment columns: {sorted(missing)}")
    if segments.empty or segments.edge_id.isna().any() or not segments.edge_id.is_unique or segments.street_id.isna().any():
        raise ValueError("Segment IDs must be non-null and edge IDs unique")
    if not segments.jurisdiction.isin(WINDOWS).all():
        raise ValueError("Unknown jurisdiction/observation window")
    if segments[["is_bridge", "is_tunnel", "elev_water_only"]].isna().any().any():
        raise ValueError("Missing bridge/tunnel/water flags")
    for c in ["is_bridge", "is_tunnel", "elev_water_only"]:
        if not segments[c].isin([True, False]).all():
            raise ValueError(f"Invalid Boolean flag: {c}")
    if segments.groupby("street_id").jurisdiction.nunique().gt(1).any():
        raise ValueError("Street twins disagree on jurisdiction")
    s = segments.copy()
    s[STATIC] = s[STATIC].astype(float).replace([np.inf, -np.inf], np.nan)
    s.loc[s.elev_water_only, "elev_p10"] = np.nan
    s.loc[~s.fema_risk_level.isin([1, 2, 3]), "fema_risk_level"] = np.nan
    for c in ["sink_p90", "length", "drain_count"]:
        s.loc[s[c] < 0, c] = np.nan
    agg = {c: "mean" for c in STATIC}
    agg.update({"is_bridge": "max", "elev_water_only": "max", "jurisdiction": "first"})
    return s.sort_values("edge_id").groupby("street_id", as_index=False).agg(agg)


def baseline(streets):
    parts = pd.DataFrame({
        "fema": streets.fema_risk_level / 3,
        "low_ground": 1 - ((streets.elev_p10 - 0.5) / 4.5).clip(0, 1),
        "ponding": streets.sink_p90.clip(0, 1),
    }, index=streets.index)
    weights = pd.Series({"fema": .35, "low_ground": .25, "ponding": .10})
    available = parts.notna().mul(weights).sum(axis=1)
    scores = parts.mul(weights).sum(axis=1).div(available).fillna(.5)
    return scores, available / weights.sum()


def label_links(segments, streets, reports):
    if not reports.edge_id.isin(segments.edge_id).all():
        raise ValueError("Report references unknown edge ID")
    links = reports.loc[reports.category.eq("flood")].merge(
        segments[["edge_id", "street_id"]], on="edge_id", validate="many_to_one")
    links["date"] = pd.to_datetime(links.date, errors="raise").dt.normalize()
    if links.date.isna().any() or links[["source", "ticket_id"]].isna().any().any():
        raise ValueError("Flood reports require dates, sources and ticket IDs")
    links = links.merge(streets[["street_id", "jurisdiction", "is_bridge"]], on="street_id", validate="many_to_one")
    valid = pd.Series(False, index=links.index)
    for jurisdiction, (start, end) in WINDOWS.items():
        valid |= links.jurisdiction.eq(jurisdiction) & links.date.between(start, end)
    audit = {"flood_links": len(links), "outside_window_links": int((~valid).sum()),
             "bridge_links": int(links.is_bridge.sum())}
    return links.loc[valid & ~links.is_bridge].copy(), audit


def report_history(frame, positives, freeze=None):
    """Flood report-days per year on each street, counting only reports before date - gap.

    frame needs street_id, jurisdiction and date. freeze caps the cutoff, mimicking serving,
    where history ends with the last 311 record. Under the minimum coverage is unknown (NaN).
    """
    cutoff = pd.to_datetime(frame.date) - pd.Timedelta(days=HISTORY_GAP_DAYS)
    if freeze is not None:
        cutoff = cutoff.clip(upper=pd.Timestamp(freeze))
    start = frame.jurisdiction.map({k: pd.Timestamp(v[0]) for k, v in WINDOWS.items()})
    end = frame.jurisdiction.map({k: pd.Timestamp(v[1]) + pd.Timedelta(days=1) for k, v in WINDOWS.items()})
    exposure = (end.clip(upper=cutoff) - start).dt.days
    counts = pd.Series(0., index=frame.index)
    for value, index in frame.groupby(cutoff).groups.items():
        before = positives.loc[positives.date < value].groupby("street_id").size()
        counts.loc[index] = frame.street_id.loc[index].map(before).fillna(0).to_numpy()
    return (counts / (exposure / 365.25)).where(exposure >= HISTORY_MIN_EXPOSURE_DAYS)


def assign_groups(streets, links):
    # A snapped ticket may tie between different physical streets: union these too.
    parent = {s: s for s in streets.street_id}
    def root(s):
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s
    for _, rows in links.groupby(["source", "ticket_id"]):
        ids = rows.street_id.unique()
        for s in ids[1:]:
            a, b = root(ids[0]), root(s)
            parent[max(a, b)] = min(a, b)
    out = streets.copy()
    out["split_group"] = out.street_id.map(root)
    out["heldout_street"] = out.split_group.map(
        lambda s: int(hashlib.sha256(f"{SEED}:{s}".encode()).hexdigest()[:8], 16) % 5 == 0)
    return out


def prepare_panel(segments, reports, weather, negative_fraction=.02):
    if not 0 < negative_fraction <= 1:
        raise ValueError("negative_fraction must be in (0,1]")
    streets = street_table(segments)
    links, audit = label_links(segments, streets, reports)
    streets = assign_groups(streets, links)
    eligible = streets.loc[~streets.is_bridge]
    positives = links[["street_id", "date"]].drop_duplicates()
    positive_map = positives.groupby("date").street_id.agg(set).to_dict()
    weather = weather.set_index("date", verify_integrity=True)
    rng = np.random.default_rng(SEED)
    pieces = []
    # Sample separately per date, jurisdiction and split so weights represent each stratum.
    for jurisdiction, (start, end) in WINDOWS.items():
        for heldout in [False, True]:
            candidates = eligible.loc[eligible.jurisdiction.eq(jurisdiction) & eligible.heldout_street.eq(heldout), "street_id"].to_numpy()
            if not len(candidates):
                continue
            for date in pd.date_range(start, end):
                if date not in weather.index or weather.loc[date, RAIN_FEATURES].isna().any():
                    raise ValueError(f"Incomplete weather for {date.date()}")
                mask = np.isin(candidates, list(positive_map.get(date, set())))
                positive, negative = candidates[mask], candidates[~mask]
                count = min(len(negative), max(1, int(np.ceil(len(negative) * negative_fraction))))
                chosen = rng.choice(negative, size=count, replace=False)
                ids = np.concatenate([positive, chosen])
                frame = pd.DataFrame({"street_id": ids, "date": date,
                    "target": np.r_[np.ones(len(positive), dtype=int), np.zeros(count, dtype=int)],
                    "sample_weight": np.r_[np.ones(len(positive)), np.full(count, len(negative) / count if count else 1)]})
                if heldout:
                    split = "unseen_streets_test" if date > pd.Timestamp(VALIDATION_END) else "unused"
                else:
                    split = "train" if date <= pd.Timestamp(TRAIN_END) else ("validation" if date <= pd.Timestamp(VALIDATION_END) else "future_test")
                frame["split"] = split
                if split != "unused":
                    pieces.append(frame)
    panel = pd.concat(pieces, ignore_index=True).merge(streets, on="street_id", validate="many_to_one")
    panel = panel.merge(weather.reset_index(), on="date", validate="many_to_one")
    panel["report_rate"] = report_history(panel, positives)
    if panel.duplicated(["street_id", "date"]).any():
        raise ValueError("Duplicate street-day")
    audit.update({"segments": len(segments), "streets": len(streets), "positive_street_days": len(positives),
                  "sampled_rows": len(panel), "negative_fraction": negative_fraction})
    return streets, positives, panel, audit
