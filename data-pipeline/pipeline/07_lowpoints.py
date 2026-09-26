"""Step 7 (optional): depression (sink) depth along each edge.

pysheds fill_pits -> fill_depressions on data/raw/dem.tif (from Step 3); depth = filled - dem is how
deep water can pond at a cell before it spills out. Sampled every config.ELEV_SAMPLE_M along each edge
(same sampler as Step 3). Bare-earth DEM: bridge decks read the ground/water below; Step 8 caps them.

Outputs:
  data/raw/sink_depth.tif              depth raster (m), same grid as dem.tif
  data/features/lowpoints.parquet      edge_id, sink_depth (max along edge), sink_p90 (90th pct)
  data/checks/lowpoints.png            depth raster + roads colored by sink_depth
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import shapely
from pysheds.grid import Grid

DEM = config.RAW / "dem.tif"
SINK = config.RAW / "sink_depth.tif"
MIN_SAMPLES = 5


def sink_raster():
    grid = Grid.from_raster(str(DEM))
    dem = grid.read_raster(str(DEM))
    filled = grid.fill_depressions(grid.fill_pits(dem))
    depth = np.asarray(filled, dtype="float32") - np.asarray(dem, dtype="float32")
    with rasterio.open(DEM) as src:
        profile = src.profile
    profile.update(dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(SINK, "w", **profile) as dst:
        dst.write(depth, 1)
    return depth


def sample_points(roads):
    """Evenly spaced points along each edge (UTM): returns (edge row index, point geometry)."""
    n = np.maximum(MIN_SAMPLES, np.ceil(roads.geometry.length / config.ELEV_SAMPLE_M).astype(int) + 1)
    idx = np.repeat(np.arange(len(roads)), n)
    frac = np.concatenate([np.linspace(0, 1, k) for k in n])
    pts = shapely.line_interpolate_point(roads.geometry.values[idx], frac, normalized=True)
    return idx, pts


def main():
    assert DEM.exists(), "run Step 3 first (needs data/raw/dem.tif)"
    config.FEAT.mkdir(parents=True, exist_ok=True)
    print("Filling pits and depressions ...")
    depth = sink_raster()

    roads = gpd.read_parquet(config.PROC / "roads.parquet")[["edge_id", "name", "length", "is_bridge", "geometry"]]
    roads_utm = roads.to_crs(config.CRS_UTM)
    idx, pts = sample_points(roads_utm)
    with rasterio.open(SINK) as src:
        xy = gpd.GeoSeries(pts, crs=config.CRS_UTM).to_crs(src.crs)
        vals = np.ma.concatenate(list(src.sample(zip(xy.x, xy.y), masked=True))).astype(float).filled(np.nan)
    g = pd.DataFrame({"i": idx, "d": vals}).groupby("i")["d"]
    feat = pd.DataFrame(roads[["edge_id", "name", "length", "is_bridge"]]).reset_index(drop=True)
    feat["sink_depth"] = g.max()
    feat["sink_p90"] = g.quantile(0.9)
    feat[["edge_id", "sink_depth", "sink_p90"]].to_parquet(config.FEAT / "lowpoints.parquet", index=False)

    # ---- verification ----
    d = depth[np.isfinite(depth)]
    print(f"\nsink raster: {depth.shape[1]}x{depth.shape[0]} px; cells with depth > 0.05 m: {(d > 0.05).mean():.1%}, "
          f"> 0.3 m: {(d > 0.3).mean():.1%}, > 1 m: {(d > 1).mean():.1%}; max {d.max():.2f} m; "
          f"negative: {(d < -1e-4).sum()}")
    print(f"\nlowpoints.parquet: {len(feat)} rows, edge_id unique: {feat['edge_id'].is_unique}")
    print("Null counts:\n" + feat[["sink_depth", "sink_p90"]].isna().sum().to_string())
    print("\n" + feat[["sink_depth", "sink_p90"]].describe(percentiles=[.25, .5, .75, .9, .95, .99])
          .round(3).to_string())
    for c in ("sink_depth", "sink_p90"):
        print(f"{c}: edges = 0: {(feat[c] <= 1e-4).mean():.1%}, > 0.1 m: {(feat[c] > 0.1).mean():.1%}, "
              f"> 0.5 m: {(feat[c] > 0.5).mean():.1%}, > 1 m: {(feat[c] > 1).mean():.1%}")
    br = feat["is_bridge"]
    print(f"median sink_depth: bridges {feat.loc[br, 'sink_depth'].median():.2f} m vs others "
          f"{feat.loc[~br, 'sink_depth'].median():.2f} m")
    print("\nDeepest 15 non-bridge edges:\n" + feat[~br].nlargest(15, "sink_depth")
          [["edge_id", "name", "length", "sink_depth", "sink_p90"]].round(2).to_string(index=False))

    fig, axes = plt.subplots(2, 1, figsize=(14, 11))
    with rasterio.open(SINK) as src:
        b = src.bounds
    axes[0].imshow(np.clip(depth, 0, 1), cmap="Blues", extent=(b.left, b.right, b.bottom, b.top))
    axes[0].set_title("Depression depth (m, clipped 0-1), EPSG:5070")
    plot = roads.merge(feat[["edge_id", "sink_depth"]], on="edge_id").sort_values("sink_depth")
    plot.plot(ax=axes[1], column=plot["sink_depth"].clip(upper=1), cmap="YlGnBu", linewidth=0.5, legend=True,
              legend_kwds={"label": "sink_depth (m, clipped at 1)", "shrink": 0.6})
    axes[1].set_title("Max depression depth along edge"); axes[1].set_aspect("equal")
    fig.tight_layout()
    fig.savefig(config.CHECKS / "lowpoints.png", dpi=130)
    print(f"\nSaved {config.CHECKS / 'lowpoints.png'}")


if __name__ == "__main__":
    main()
