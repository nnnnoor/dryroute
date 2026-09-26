"""Step 6: stormwater inlet density along each edge.

Source: Miami-Dade "Stormwater Point" (MDPublisher, StormWaterPoint_gdb/FeatureServer/0), which covers
the City of Miami as well as the county. Only TYPE in config.INLET_TYPES is fetched. Exact duplicate
points (same TYPE and location) are dropped.

Counts inlets within config.DRAIN_BUFFER_M of each edge (UTM). Bridge edges are excluded (inlets
under a deck don't drain it) and get 0. drains_per_100m = count / length * 100.

Outputs:
  data/raw/stormwater_inlets.geojson
  data/features/drains.parquet   edge_id, drain_count, drains_per_100m
  data/checks/drains.png         inlets over roads (full bbox + Brickell zoom)
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import time

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests

URL = ("https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/"
       "StormWaterPoint_gdb/FeatureServer/0/query")
PAGE = 2000


def get(params, tries=3):
    for attempt in range(tries):
        try:
            r = requests.get(URL, params=params, timeout=180).json()
            if "error" in r:
                raise RuntimeError(r["error"])
            return r
        except (requests.RequestException, RuntimeError) as e:
            if attempt == tries - 1:
                raise
            print(f"  retrying after {e}")
            time.sleep(5)


def download_inlets():
    W, S, E, N = config.BBOX
    params = dict(where=f"TYPE IN ({', '.join(repr(t) for t in config.INLET_TYPES)})",
                  geometry=f"{W},{S},{E},{N}", geometryType="esriGeometryEnvelope", inSR=4326,
                  spatialRel="esriSpatialRelIntersects", outFields="OBJECTID,ID,TYPE,MAINT_BY,MUNIC_NAME",
                  outSR=4326, orderByFields="OBJECTID", f="geojson")
    feats, offset = [], 0
    while True:
        page = get({**params, "resultOffset": offset, "resultRecordCount": PAGE})
        feats += page["features"]
        if len(page["features"]) < PAGE and not page.get("properties", {}).get("exceededTransferLimit"):
            break
        offset += PAGE
    inlets = gpd.GeoDataFrame.from_features(feats, crs=config.CRS_WGS)
    print(f"  fetched {len(inlets)} inlets")
    return inlets


def main():
    for d in (config.RAW, config.FEAT, config.CHECKS):
        d.mkdir(parents=True, exist_ok=True)
    print("Downloading stormwater inlets ...")
    inlets = download_inlets()
    n_raw = len(inlets)
    inlets = inlets[~inlets.geometry.is_empty & inlets.geometry.notna()]
    inlets = inlets.loc[~inlets.assign(wkb=inlets.geometry.to_wkb()).duplicated(["TYPE", "wkb"])]
    inlets.to_file(config.RAW / "stormwater_inlets.geojson", driver="GeoJSON")

    roads = gpd.read_parquet(config.PROC / "roads.parquet")[["edge_id", "is_bridge", "length", "geometry"]]
    roads = roads.to_crs(config.CRS_UTM)
    ground = roads[~roads["is_bridge"]]
    buf = ground[["edge_id", "geometry"]].set_geometry(ground.geometry.buffer(config.DRAIN_BUFFER_M))
    hits = gpd.sjoin(inlets[["geometry"]].to_crs(config.CRS_UTM), buf, predicate="within")
    counts = hits.groupby("edge_id").size().rename("drain_count")

    feat = roads[["edge_id", "length"]].merge(counts, left_on="edge_id", right_index=True, how="left")
    feat["drain_count"] = feat["drain_count"].fillna(0).astype("int32")
    feat["drains_per_100m"] = feat["drain_count"] / feat["length"] * 100
    feat[["edge_id", "drain_count", "drains_per_100m"]].to_parquet(config.FEAT / "drains.parquet", index=False)

    # ---- verification ----
    print(f"\ninlets: {n_raw} fetched, {len(inlets)} after dropping empty/duplicate points")
    print("by TYPE:\n" + inlets["TYPE"].value_counts().to_string())
    print("by MAINT_BY:\n" + inlets["MAINT_BY"].value_counts(dropna=False).to_string())
    used = hits.index.nunique()
    print(f"inlets within {config.DRAIN_BUFFER_M} m of a non-bridge edge: {used} of {len(inlets)} "
          f"({len(inlets) - used} farther away, e.g. parking lots/canals)")
    print(f"\ndrains.parquet: {len(feat)} rows, edge_id unique: {feat['edge_id'].is_unique}; "
          f"bridge edges with drains: {int(feat.loc[roads['is_bridge'].to_numpy(), 'drain_count'].sum())}")
    print(f"edges with 0 inlets: {(feat['drain_count'] == 0).sum()} of {len(feat)}")
    print("\n" + feat[["drain_count", "drains_per_100m"]].describe(percentiles=[.25, .5, .75, .95, .99])
          .round(2).to_string())
    short = feat["length"] < 20
    print(f"\nshort edges (< 20 m, {short.sum()}): median drains_per_100m "
          f"{feat.loc[short, 'drains_per_100m'].median():.1f} vs {feat.loc[~short, 'drains_per_100m'].median():.1f} "
          f"for the rest (the {config.DRAIN_BUFFER_M} m buffer's round ends dominate short edges)")
    jur_path = config.FEAT / "reports_311.parquet"
    if jur_path.exists():
        j = feat.merge(pd.read_parquet(jur_path, columns=["edge_id", "jurisdiction"]), on="edge_id")
        print("by jurisdiction (edges >= 20 m):\n" + j[~short.to_numpy()].groupby("jurisdiction")
              [["drain_count", "drains_per_100m"]].median().round(2).to_string())

    fig, axes = plt.subplots(1, 2, figsize=(18, 6), gridspec_kw={"width_ratios": [2.4, 1]})
    r_wgs, i_wgs = roads.to_crs(config.CRS_WGS), inlets
    for ax, (x0, x1, y0, y1), title in [
            (axes[0], (config.BBOX[0], config.BBOX[2], config.BBOX[1], config.BBOX[3]), "Full bbox"),
            (axes[1], (-80.205, -80.185, 25.755, 25.775), "Brickell zoom")]:
        r_wgs.cx[x0:x1, y0:y1].plot(ax=ax, color="#999", linewidth=0.4)
        i_wgs.cx[x0:x1, y0:y1].plot(ax=ax, color="#1f77b4", markersize=0.3 if ax is axes[0] else 3)
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal"); ax.set_title(title)
        ax.locator_params(axis="x", nbins=4)
    fig.suptitle(f"Stormwater inlets ({', '.join(config.INLET_TYPES)}): {len(inlets)}")
    fig.savefig(config.CHECKS / "drains.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {config.CHECKS / 'drains.png'}")


if __name__ == "__main__":
    main()
