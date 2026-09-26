"""Step 8: merge roads with every feature layer present and compute a placeholder risk_score.

Outputs:
  data/processed/segments.parquet   roads columns + all feature columns + risk_score, score_note (EPSG:4326)
  data/checks/segments_risk.png     roads colored by risk_score
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def load_features(segs):
    for path in sorted(config.FEAT.glob("*.parquet")):
        feat = pd.read_parquet(path)
        assert feat["edge_id"].is_unique, f"{path.name}: duplicate edge_id"
        clash = set(feat.columns) & set(segs.columns) - {"edge_id"}
        assert not clash, f"{path.name}: columns already present {clash}"
        unmatched = (~feat["edge_id"].isin(segs["edge_id"])).sum()
        print(f"  {path.name}: {len(feat)} rows, cols {list(feat.columns[1:])}, "
              f"edge_ids not in roads: {unmatched}")
        segs = segs.merge(feat, on="edge_id", how="left")
    return segs


def scale(col):
    if col.name in config.RISK_FIXED_RANGE:
        lo, hi = config.RISK_FIXED_RANGE[col.name]
    else:
        lo, hi = col.min(), col.max()
    s = ((col.astype(float) - lo) / (hi - lo)).clip(0, 1) if hi > lo else col * 0.0
    return 1 - s if col.name in config.RISK_INVERT else s


def risk_score(segs):
    used = {c: w for c, w in config.RISK_WEIGHTS.items() if c in segs.columns}
    parts = pd.DataFrame({c: scale(segs[c]) for c in used})
    w = pd.Series(used)
    weight_present = parts.notna().mul(w).sum(axis=1)  # renormalize over features each row has
    score = parts.mul(w).sum(axis=1) / weight_present
    return score.where(weight_present > 0), used


def main():
    segs = gpd.read_parquet(config.PROC / "roads.parquet")
    print(f"roads: {len(segs)} edges")
    segs = load_features(segs)

    fill = [c for c in config.FILL_ZERO if c in segs.columns]
    segs[fill] = segs[fill].fillna(0)
    segs["risk_score"], used = risk_score(segs)
    # final override: FEMA/DEM describe the ground under a bridge deck, not the deck
    segs["score_note"] = pd.Series(None, index=segs.index, dtype="string")
    bridge = segs["is_bridge"]
    segs.loc[bridge, "risk_score"] = segs.loc[bridge, "risk_score"].clip(upper=config.BRIDGE_RISK_CAP)
    segs.loc[bridge, "score_note"] = "bridge_capped"
    segs = segs.to_crs(config.CRS_WGS)
    segs.to_parquet(config.PROC / "segments.parquet", index=False)

    # ---- verification ----
    print(f"\nsegments.parquet: {len(segs)} rows, {segs.shape[1]} cols, edge_id unique: {segs['edge_id'].is_unique}")
    print("columns:", list(segs.columns))
    print("filled with 0:", fill or "none")
    missing = [c for c in config.RISK_WEIGHTS if c not in used]
    print(f"risk_score uses {used}; not yet available: {missing}")
    print("\nNull counts:\n" + segs.isna().sum().to_string())
    print("\nrisk_score:\n" + segs["risk_score"].describe().round(3).to_string())
    print("distinct risk_score values:", segs["risk_score"].nunique())
    print(f"score_note: {segs['score_note'].value_counts(dropna=False).to_dict()} "
          f"(cap {config.BRIDGE_RISK_CAP}); tunnels flagged only: {int(segs['is_tunnel'].sum())}")

    streets = (segs.groupby("street_id")
                   .agg(name=("name", "first"), risk_score=("risk_score", "max"),
                        edges=("edge_id", "size"), length_m=("length", "max"))
                   .sort_values(["risk_score", "length_m"], ascending=False))
    top = streets["risk_score"].max()
    print(f"\nstreets (by street_id): {len(streets)}; tied at max risk_score {top:.3f}: "
          f"{(streets['risk_score'] == top).sum()}")
    print("Top 20 streets (ties broken by length):\n"
          + streets.head(20).round({"risk_score": 3, "length_m": 0}).to_string())

    fig, ax = plt.subplots(figsize=(14, 6))
    segs.sort_values("risk_score").plot(ax=ax, column="risk_score", cmap="YlOrRd", linewidth=0.5,
                                        legend=True, legend_kwds={"label": "risk_score", "shrink": 0.6})
    ax.set_title(f"Placeholder risk_score ({', '.join(used)})")
    ax.set_aspect("equal")
    fig.savefig(config.CHECKS / "segments_risk.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {config.CHECKS / 'segments_risk.png'}")


if __name__ == "__main__":
    main()
