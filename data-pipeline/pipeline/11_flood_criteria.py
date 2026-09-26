"""Step 11: Miami-Dade County Flood Criteria 2022 vs. road ground elevation ("freeboard").

The County Flood Criteria is the minimum required elevation (ft NAVD88) of developed land and of road
crowns, for a 10-year / 24-hour storm in the 2060 sea-level-rise scenario (effective 2022-10-28).
Source: county raster (File Geodatabase, EPSG:2236, 100 ft cells) from the official download link on
the ArcGIS item "County Flood Criteria 2022 - Raster" (MDPublisher). Checked against the county's
contour layer (CountyFloodCriteria_gdb): the raster reads 7.16 / 11.00 / 16.88 ft on the 7 / 11 / 16 ft contours.

Samples the criteria raster and dem.tif (Step 3) at the same points along each edge (every
config.ELEV_SAMPLE_M, min 5), so freeboard = ground - criteria is per point.

Outputs:
  data/raw/flood_criteria/                 downloaded zip + CountyFloodCriteria.gdb (cached; delete to refresh)
  data/features/flood_criteria.parquet     edge_id, flood_criteria_m, freeboard_p10_m, below_criteria_frac
  data/checks/flood_criteria.png           roads colored by freeboard_p10_m
Not scored (overlaps elev_p10); a feature for the ML model and dashboard.
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import zipfile

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import requests
import shapely

ZIP_URL = "https://giswspro.miamidade.gov/opendata/flood/2022/CountyFloodCriteria.zip"
DIR = config.RAW / "flood_criteria"
GDB = DIR / "CountyFloodCriteria.gdb"
DEM = config.RAW / "dem.tif"
US_FT = 1200 / 3937  # EPSG:2236 uses US survey feet
MIN_SAMPLES = 5


def download():
    if GDB.exists():
        print(f"Using cached {GDB}")
        return
    DIR.mkdir(parents=True, exist_ok=True)
    print("Downloading County Flood Criteria 2022 raster ...")
    z = DIR / "CountyFloodCriteria.zip"
    with requests.get(ZIP_URL, stream=True, timeout=300) as r:
        r.raise_for_status()
        z.write_bytes(r.content)
    zipfile.ZipFile(z).extractall(DIR)


def sample_points(roads):
    """Evenly spaced points along each edge (UTM): returns (edge row index, point geometry)."""
    n = np.maximum(MIN_SAMPLES, np.ceil(roads.geometry.length / config.ELEV_SAMPLE_M).astype(int) + 1)
    idx = np.repeat(np.arange(len(roads)), n)
    frac = np.concatenate([np.linspace(0, 1, k) for k in n])
    pts = shapely.line_interpolate_point(roads.geometry.values[idx], frac, normalized=True)
    return idx, pts


def sample(path, pts):
    with rasterio.open(path) as src:
        xy = gpd.GeoSeries(pts, crs=config.CRS_UTM).to_crs(src.crs)
        v = np.ma.concatenate(list(src.sample(zip(xy.x, xy.y), masked=True))).astype(float).filled(np.nan)
        if src.nodata is not None:
            v[np.isclose(v, src.nodata)] = np.nan
    v[~np.isfinite(v)] = np.nan
    return v


def auc(score, label):
    """ROC AUC via ranks (Mann-Whitney); higher score should mean label 1."""
    r = pd.Series(score).rank().to_numpy()
    pos = label.astype(bool)
    n1, n0 = pos.sum(), (~pos).sum()
    return (r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def main():
    assert DEM.exists(), "run Step 3 first (needs data/raw/dem.tif)"
    config.FEAT.mkdir(parents=True, exist_ok=True)
    download()

    roads = gpd.read_parquet(config.PROC / "roads.parquet")[["edge_id", "is_bridge", "geometry"]]
    idx, pts = sample_points(roads.to_crs(config.CRS_UTM))
    crit = sample(GDB, pts) * US_FT
    ground = sample(DEM, pts)
    s = pd.DataFrame({"i": idx, "crit": crit, "fb": ground - crit})
    g = s.groupby("i")
    feat = pd.DataFrame(roads[["edge_id", "is_bridge"]]).reset_index(drop=True)
    feat["flood_criteria_m"] = g["crit"].mean()
    feat["freeboard_p10_m"] = g["fb"].quantile(0.1)
    feat["below_criteria_frac"] = g["fb"].apply(lambda x: (x < 0).sum() / x.notna().sum() if x.notna().any() else np.nan)
    feat[["edge_id", "flood_criteria_m", "freeboard_p10_m", "below_criteria_frac"]].to_parquet(
        config.FEAT / "flood_criteria.parquet", index=False)

    # ---- verification ----
    print(f"\nsamples: {len(s)}; criteria NaN (water/outside raster): {int(np.isnan(crit).sum())}")
    print(f"flood_criteria.parquet: {len(feat)} rows, edge_id unique: {feat['edge_id'].is_unique}")
    cols = ["flood_criteria_m", "freeboard_p10_m", "below_criteria_frac"]
    print("Null counts:\n" + feat[cols].isna().sum().to_string())
    print("\n" + feat[cols].describe(percentiles=[.05, .25, .5, .75, .95]).round(2).to_string())
    nb = feat[~feat["is_bridge"]]
    print(f"\nnon-bridge edges with freeboard_p10 < 0 (partly below the 2060 design level): "
          f"{(nb['freeboard_p10_m'] < 0).mean():.1%}; entirely below (frac = 1): {(nb['below_criteria_frac'] == 1).mean():.1%}")

    other = {}
    for name, cols_ in [("fema.parquet", ["edge_id", "fema_risk_level"]), ("elevation.parquet", ["edge_id", "elev_p10"]),
                        ("reports_311.parquet", ["edge_id", "flood_report_days", "jurisdiction"])]:
        if (config.FEAT / name).exists():
            other[name] = pd.read_parquet(config.FEAT / name, columns=cols_)
    x = nb
    for d in other.values():
        x = x.merge(d, on="edge_id")
    if "fema_risk_level" in x:
        print("median freeboard_p10_m by FEMA level:\n" + x.groupby("fema_risk_level")["freeboard_p10_m"].median().round(2).to_string())
    if {"elev_p10", "flood_report_days"} <= set(x.columns):
        x = x.dropna(subset=["freeboard_p10_m", "elev_p10"])
        print(f"corr(freeboard_p10_m, elev_p10) = {x['freeboard_p10_m'].corr(x['elev_p10']):.2f}")
        print("AUC for 'edge has >= 1 flood report day' (0.5 = no signal; higher = lower value -> more reports):")
        for j, gj in [("all", x)] + list(x.groupby("jurisdiction")):
            y = (gj["flood_report_days"] > 0).to_numpy()
            print(f"  {j:<7} n={len(gj):>6} pos={y.sum():>5}: -elev_p10 {auc(-gj['elev_p10'].to_numpy(), y):.3f} | "
                  f"-freeboard_p10_m {auc(-gj['freeboard_p10_m'].to_numpy(), y):.3f} | "
                  f"below_criteria_frac {auc(gj['below_criteria_frac'].to_numpy(), y):.3f}")

    plot = roads.merge(feat[["edge_id", "freeboard_p10_m"]], on="edge_id").sort_values("freeboard_p10_m", ascending=False)
    fig, ax = plt.subplots(figsize=(14, 6))
    plot.plot(ax=ax, column="freeboard_p10_m", cmap="RdBu", vmin=-1.5, vmax=1.5, linewidth=0.5, legend=True,
              legend_kwds={"label": "ground - 2060 flood criteria (m); red = below", "shrink": 0.6})
    ax.set_title("Freeboard vs Miami-Dade County Flood Criteria 2022 (10-yr/24-hr storm, 2060 SLR)")
    ax.set_aspect("equal")
    fig.savefig(config.CHECKS / "flood_criteria.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {config.CHECKS / 'flood_criteria.png'}")


if __name__ == "__main__":
    main()
