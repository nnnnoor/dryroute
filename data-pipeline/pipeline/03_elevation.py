"""Step 3: USGS 3DEP ground elevation (m, NAVD88) sampled along each edge.

Samples every config.ELEV_SAMPLE_M along the edge (at least 5 points, both ends included), so
elev_min catches dips on long edges. The DEM is bare earth: bridge decks get the ground/water
below them. Raw values are left as-is; Step 8 caps bridge scores.

Water-only rule: a non-bridge edge whose samples are ALL < 0 m sits on water in the DEM (e.g. an
untagged bridge or a ramp off a deck), so its elev_p10 is set to null (not scored) and
elev_water_only = True; Step 8 sets score_note = "elev_water_only".

Outputs:
  data/raw/dem.tif                   3DEP DEM for the bbox (downloaded once; delete to refresh)
  data/features/elevation.parquet    edge_id, elev_mean, elev_min, elev_p10 (10th pct of samples; used for
                                     scoring), elev_water_only (bool)
  data/checks/elevation.png          roads colored by elev_min
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
import py3dep
import rasterio
import shapely

DEM = config.RAW / "dem.tif"
MIN_SAMPLES = 5


def download_dem():
    if DEM.exists():
        print(f"Using cached {DEM}")
        return
    print("Downloading 3DEP DEM (10 m) ...")
    dem = py3dep.get_dem(config.BBOX, resolution=10)
    dem.rio.to_raster(DEM)


def sample_points(roads):
    """Evenly spaced points along each edge (UTM): returns (edge row index, point geometry)."""
    n = np.maximum(MIN_SAMPLES, np.ceil(roads.geometry.length / config.ELEV_SAMPLE_M).astype(int) + 1)
    idx = np.repeat(np.arange(len(roads)), n)
    frac = np.concatenate([np.linspace(0, 1, k) for k in n])
    pts = shapely.line_interpolate_point(roads.geometry.values[idx], frac, normalized=True)
    return idx, pts


def main():
    config.RAW.mkdir(parents=True, exist_ok=True)
    config.FEAT.mkdir(parents=True, exist_ok=True)
    download_dem()

    roads = gpd.read_parquet(config.PROC / "roads.parquet")[["edge_id", "name", "length", "is_bridge", "geometry"]]
    roads = roads.to_crs(config.CRS_UTM)
    idx, pts = sample_points(roads)

    with rasterio.open(DEM) as src:
        dem_crs, dem_res, dem_bounds = src.crs, src.res, src.bounds
        grid = src.read(1, masked=True)
        xy = gpd.GeoSeries(pts, crs=config.CRS_UTM).to_crs(src.crs)
        # masked (nodata or outside the raster) -> NaN
        vals = np.ma.concatenate(list(src.sample(zip(xy.x, xy.y), masked=True))).astype(float).filled(np.nan)
    vals[~np.isfinite(vals)] = np.nan

    samples = pd.DataFrame({"i": idx, "z": vals})
    g = samples.groupby("i")["z"]
    agg = g.agg(elev_mean="mean", elev_min="min", n="size", n_nan=lambda z: z.isna().sum())
    agg["elev_p10"] = g.quantile(0.1)  # linear interpolation between samples
    agg["z_max"] = g.max()
    elev = pd.DataFrame(roads[["edge_id", "name", "length", "is_bridge"]]).reset_index(drop=True).join(agg)
    elev["elev_water_only"] = (elev["z_max"] < 0) & ~elev["is_bridge"]
    elev.loc[elev["elev_water_only"], "elev_p10"] = np.nan
    elev[["edge_id", "elev_mean", "elev_min", "elev_p10", "elev_water_only"]].to_parquet(config.FEAT / "elevation.parquet", index=False)

    # ---- verification ----
    print(f"\nDEM: {grid.shape[1]}x{grid.shape[0]} px, CRS {dem_crs}, res {dem_res[0]:.1f} m, "
          f"nodata px {int(grid.mask.sum()) if np.ma.is_masked(grid) else 0}, "
          f"range {float(grid.min()):.2f} to {float(grid.max()):.2f} m")
    print(f"samples: {len(samples)} ({agg['n'].min()}-{agg['n'].max()} per edge, median {agg['n'].median():.0f}), "
          f"NaN samples: {int(samples['z'].isna().sum())}")
    print(f"\nelevation.parquet: {len(elev)} rows (roads: {len(roads)}), edge_id unique: {elev['edge_id'].is_unique}")
    print("Null counts:\n" + elev[["elev_mean", "elev_min", "elev_p10"]].isna().sum().to_string())
    water = elev[elev["elev_water_only"]]
    print(f"\nWater-only non-bridge edges (all samples < 0 m; elev_p10 -> null): {len(water)}")
    if len(water):
        print(water[["edge_id", "name", "length", "n", "elev_min", "elev_mean"]].round(2).to_string(index=False))
    print(f"(bridge edges with all samples < 0 m, not affected: {int(((elev['z_max'] < 0) & elev['is_bridge']).sum())})")
    print("\n" + elev[["elev_mean", "elev_min", "elev_p10"]].describe(percentiles=[.01, .05, .25, .5, .75, .95, .99])
          .round(2).to_string())
    neg, high = (elev["elev_min"] < 0).sum(), (elev["elev_mean"] > 10).sum()
    print(f"\nelev_min < 0 m: {neg} edges; elev_mean > 10 m: {high} edges")
    partial = elev[(elev["n_nan"] > 0) & elev["elev_min"].notna()]
    if len(partial) or elev["elev_min"].isna().any():
        print(f"FLAG: {len(partial)} edges with some NaN samples, {elev['elev_min'].isna().sum()} all-NaN")

    bridge = roads["is_bridge"].to_numpy()
    print("\nelev_min median: bridge edges "
          f"{elev.loc[bridge, 'elev_min'].median():.2f} m vs others {elev.loc[~bridge, 'elev_min'].median():.2f} m "
          "(bare-earth DEM: bridges read the ground/water below)")
    print(f"Lowest 10 non-bridge edges:\n"
          + elev[~bridge].nsmallest(10, "elev_min")[["edge_id", "elev_min", "elev_p10", "elev_mean"]].round(2).to_string(index=False))

    fema_path = config.FEAT / "fema.parquet"
    if fema_path.exists():  # sanity: higher FEMA risk should sit lower
        fema = pd.read_parquet(fema_path, columns=["edge_id", "fema_zone", "fema_risk_level"])
        by = elev.merge(fema, on="edge_id").groupby(["fema_risk_level", "fema_zone"])["elev_min"]
        print("\nelev_min by FEMA level/zone (median, n):\n"
              + by.agg(["median", "size"]).round(2).to_string())

    plot = roads.to_crs(config.CRS_WGS).merge(elev[["edge_id", "elev_min"]], on="edge_id")
    lo, hi = plot["elev_min"].quantile([0.02, 0.98])
    fig, ax = plt.subplots(figsize=(14, 6))
    plot.sort_values("elev_min", ascending=False).plot(
        ax=ax, column="elev_min", cmap="viridis", vmin=lo, vmax=hi, linewidth=0.5, legend=True,
        legend_kwds={"label": "elev_min (m, NAVD88)", "shrink": 0.6})
    ax.set_title(f"Minimum ground elevation along edge (3DEP 10 m; color range 2nd-98th pct {lo:.1f}-{hi:.1f} m)")
    ax.set_aspect("equal")
    fig.savefig(config.CHECKS / "elevation.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {config.CHECKS / 'elevation.png'}")


if __name__ == "__main__":
    main()
