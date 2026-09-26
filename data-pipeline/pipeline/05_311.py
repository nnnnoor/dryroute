"""Step 5: 311 flood and drainage reports snapped to road edges.

Sources (ArcGIS REST, confirmed 2026-09-26):
  city    City of Miami 311 Service Requests (points; 2022-10 onward)
  county  Miami-Dade 311, yearly tables data_311_{YEAR} (no geometry: lat/lon fields)
Only the types in config.FLOOD_TYPES / DRAIN_TYPES since config.REPORTS_SINCE are fetched. Missing
lat/lon is recovered from the State Plane X/Y fields (EPSG:2236, US ft).

Reports snap to the nearest NON-bridge edge within config.REPORT_SNAP_M (UTM). Equidistant edges
(e.g. the two directions of a street) all get the report. Counts are distinct report DATES per
edge (America/New_York), not rows, so one storm's burst of tickets counts once.

Outputs:
  data/raw/311_reports_raw.parquet    every fetched report (pre-snap)
  data/raw/city_of_miami_boundary.geojson
  data/processed/flood_reports.parquet  one row per (report, edge): edge_id, date, source, type,
                                         category (flood|drain), ticket_id, snap_m  (for the ML model)
  data/features/reports_311.parquet   edge_id, flood_report_days, drain_issue_days, jurisdiction
  data/checks/311_reports.html        folium map of the scored (flood) reports
  data/checks/311_counts.png          histogram of flood_report_days by jurisdiction
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import time

import folium
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from pyproj import Transformer

SOURCES = {
    "city": dict(url="https://services1.arcgis.com/CvuPhqcTQpZPT9qY/arcgis/rest/services/"
                     "City_of_Miami_311_Service_Requests_Since_2015/FeatureServer/0/query",
                 x="x_coordinate", y="y_coordinate"),
    **{f"county{y}": dict(url=f"https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/"
                              f"data_311_{y}/FeatureServer/0/query",
                          x="sr_xcoordinate", y="sr_ycoordinate") for y in config.COUNTY_311_YEARS},
}
BOUNDARY = ("https://services1.arcgis.com/CvuPhqcTQpZPT9qY/arcgis/rest/services/"
            "City_Boundary/FeatureServer/0/query")
PAGE = 1000
TZ = "America/New_York"


def get(url, params, tries=3):
    for attempt in range(tries):
        try:
            r = requests.get(url, params={**params, "f": "json"}, timeout=180).json()
            if "error" in r:
                raise RuntimeError(r["error"])
            return r
        except (requests.RequestException, RuntimeError) as e:
            if attempt == tries - 1:
                raise
            print(f"  retrying after {e}")
            time.sleep(5)


def fetch(name, src):
    """Chosen types inside the bbox, by lat/lon or (lat/lon missing) by State Plane X/Y."""
    kind = "city" if name == "city" else "county"
    types = config.FLOOD_TYPES[kind] + config.DRAIN_TYPES[kind]
    W, S, E, N = config.BBOX
    xs, ys = Transformer.from_crs(4326, 2236, always_xy=True).transform([W, E, W, E], [S, S, N, N])
    no_ll = "(latitude IS NULL OR longitude IS NULL OR latitude = 0 OR longitude = 0)"
    where = (f"issue_type IN ({', '.join(repr(t) for t in types)}) AND ("
             f"(latitude BETWEEN {S} AND {N} AND longitude BETWEEN {W} AND {E}) OR "
             f"({no_ll} AND {src['x']} BETWEEN {min(xs):.0f} AND {max(xs):.0f} "
             f"AND {src['y']} BETWEEN {min(ys):.0f} AND {max(ys):.0f}))")
    fields = f"ticket_id,issue_type,ticket_created_date_time,latitude,longitude,{src['x']},{src['y']}"
    rows, offset = [], 0
    while True:
        page = get(src["url"], dict(where=where, outFields=fields, returnGeometry="false",
                                    orderByFields="ObjectId", resultOffset=offset, resultRecordCount=PAGE))
        rows += [f["attributes"] for f in page["features"]]
        if len(page["features"]) < PAGE and not page.get("exceededTransferLimit"):
            break
        offset += PAGE
    df = pd.DataFrame(rows).rename(columns={src["x"]: "x_2236", src["y"]: "y_2236"})
    df["source"] = kind
    df["dataset"] = name
    print(f"  {name}: {len(df)} reports")
    return df


def locate(df):
    """lat/lon, falling back to State Plane X/Y; keep points inside the bbox."""
    bad = df["latitude"].isna() | df["longitude"].isna() | (df["latitude"] == 0) | (df["longitude"] == 0)
    lon, lat = Transformer.from_crs(2236, 4326, always_xy=True).transform(
        df.loc[bad, "x_2236"].to_numpy(float), df.loc[bad, "y_2236"].to_numpy(float))
    df.loc[bad, "longitude"], df.loc[bad, "latitude"] = lon, lat
    df["from_xy"] = bad
    W, S, E, N = config.BBOX
    inside = df["longitude"].between(W, E) & df["latitude"].between(S, N)
    return gpd.GeoDataFrame(df[inside], geometry=gpd.points_from_xy(df.loc[inside, "longitude"],
                                                                      df.loc[inside, "latitude"]),
                            crs=config.CRS_WGS)


def city_boundary():
    gj = requests.get(BOUNDARY, params=dict(where="1=1", outFields="JURINAME", outSR=4326, f="geojson"),
                      timeout=180).json()
    b = gpd.GeoDataFrame.from_features(gj["features"], crs=config.CRS_WGS)
    b.to_file(config.RAW / "city_of_miami_boundary.geojson", driver="GeoJSON")
    return b


def main():
    for d in (config.RAW, config.FEAT, config.PROC, config.CHECKS):
        d.mkdir(parents=True, exist_ok=True)

    print("Fetching 311 reports ...")
    raw = pd.concat([fetch(n, s) for n, s in SOURCES.items()], ignore_index=True)
    raw.to_parquet(config.RAW / "311_reports_raw.parquet", index=False)
    rep = locate(raw.copy())
    n_located = len(rep)
    rep["created"] = (pd.to_datetime(rep["ticket_created_date_time"], unit="ms", utc=True)
                      .dt.tz_convert(TZ))
    rep = rep[rep["created"] >= pd.Timestamp(config.REPORTS_SINCE, tz=TZ)].copy()
    rep["date"] = rep["created"].dt.date
    flood_types = {t for v in config.FLOOD_TYPES.values() for t in v}
    rep["category"] = np.where(rep["issue_type"].isin(flood_types), "flood", "drain")
    rep = rep.rename(columns={"issue_type": "type"})

    roads = gpd.read_parquet(config.PROC / "roads.parquet")[["edge_id", "is_bridge", "geometry"]]
    roads_utm = roads.to_crs(config.CRS_UTM)
    ground = roads_utm[~roads_utm["is_bridge"]][["edge_id", "geometry"]]
    snapped = gpd.sjoin_nearest(rep.to_crs(config.CRS_UTM), ground, how="inner",
                                max_distance=config.REPORT_SNAP_M, distance_col="snap_m")
    links = pd.DataFrame(snapped[["edge_id", "date", "source", "type", "category", "ticket_id", "snap_m"]])
    links.to_parquet(config.PROC / "flood_reports.parquet", index=False)

    boundary = city_boundary()
    mids = roads.set_geometry(roads_utm.geometry.interpolate(0.5, normalized=True).to_crs(config.CRS_WGS))
    in_city = gpd.sjoin(mids, boundary[["geometry"]], how="left", predicate="within")
    in_city = in_city[~in_city.index.duplicated()]["index_right"].notna()
    days = (links.groupby(["edge_id", "category"])["date"].nunique().unstack(fill_value=0)
                 .reindex(columns=["flood", "drain"], fill_value=0))
    feat = roads[["edge_id"]].copy()
    feat["jurisdiction"] = np.where(in_city.to_numpy(), "city", "county")
    feat = feat.merge(days.rename(columns={"flood": "flood_report_days", "drain": "drain_issue_days"}),
                      left_on="edge_id", right_index=True, how="left")
    feat[["flood_report_days", "drain_issue_days"]] = feat[["flood_report_days", "drain_issue_days"]].fillna(0).astype("int32")
    feat.to_parquet(config.FEAT / "reports_311.parquet", index=False)

    # ---- verification ----
    print(f"\nfetched {len(raw)}; kept {len(rep)} (located via X/Y: {int(rep['from_xy'].sum())}); "
          f"dropped: {len(raw) - n_located} no usable location/outside bbox, "
          f"{n_located - len(rep)} before {config.REPORTS_SINCE}")
    uniq = snapped.drop_duplicates("ticket_id")
    print(f"snapped within {config.REPORT_SNAP_M} m: {len(uniq)} of {len(rep)} reports "
          f"({len(rep) - len(uniq)} dropped as > {config.REPORT_SNAP_M} m from a non-bridge edge); "
          f"{len(links)} report-edge links (ties: {len(links) / max(len(uniq), 1):.2f} edges/report); "
          f"snap distance median {uniq['snap_m'].median():.1f} m")
    counts = pd.DataFrame({
        "in_bbox": rep.groupby(["source", "category", "type"]).size(),
        "snapped": uniq.groupby(["source", "category", "type"]).size(),
        "distinct_dates": uniq.groupby(["source", "category", "type"])["date"].nunique(),
    }).fillna(0).astype(int)
    print("\nReports by source x category x type (after filtering):\n" + counts.to_string())
    print(f"date range: {rep['date'].min()} -> {rep['date'].max()}")

    print(f"\nreports_311.parquet: {len(feat)} rows, edge_id unique: {feat['edge_id'].is_unique}")
    print("edges by jurisdiction:", feat["jurisdiction"].value_counts().to_dict())
    j = feat.groupby("jurisdiction").agg(
        edges=("edge_id", "size"), edges_with_flood=("flood_report_days", lambda s: (s > 0).sum()),
        max_flood_days=("flood_report_days", "max"), edges_with_drain=("drain_issue_days", lambda s: (s > 0).sum()))
    print(j.to_string())
    x = links.merge(feat[["edge_id", "jurisdiction"]], on="edge_id").drop_duplicates("ticket_id")
    print("report source vs edge jurisdiction (unique reports):\n"
          + pd.crosstab(x["source"], x["jurisdiction"]).to_string())
    bridges = roads.loc[roads["is_bridge"], "edge_id"]
    assert not feat.loc[feat["edge_id"].isin(bridges), ["flood_report_days", "drain_issue_days"]].any().any()
    print("flood_report_days distribution (edges > 0):\n"
          + feat.loc[feat["flood_report_days"] > 0, "flood_report_days"].value_counts().sort_index().to_string())

    fl = rep[rep["category"] == "flood"]
    m = folium.Map(location=[25.76, -80.285], zoom_start=12, tiles="OpenStreetMap")
    folium.GeoJson(boundary.__geo_interface__, name="City of Miami",
                   style_function=lambda _: {"color": "#555", "weight": 2, "fillOpacity": 0.03}).add_to(m)
    colors = {"city": "#1f77b4", "county": "#d62728"}
    for src, g in fl.groupby("source"):
        layer = folium.FeatureGroup(name=f"{src} flood reports ({len(g)})").add_to(m)
        for r in g.itertuples():
            folium.CircleMarker([r.latitude, r.longitude], radius=3, color=colors[src], fill=True,
                                fill_opacity=0.7, weight=0,
                                popup=f"{r.type}<br>{r.date}<br>{r.ticket_id}").add_to(layer)
    folium.LayerControl().add_to(m)
    m.save(config.CHECKS / "311_reports.html")

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
    for ax, (jn, g) in zip(axes, feat.groupby("jurisdiction")):
        v = g.loc[g["flood_report_days"] > 0, "flood_report_days"]
        ax.hist(v, bins=range(1, int(v.max()) + 2) if len(v) else 1)
        ax.set_title(f"{jn}: {len(v)} of {len(g)} edges with >= 1 flood report day")
        ax.set_xlabel("flood_report_days"); ax.set_ylabel("edges")
    fig.tight_layout()
    fig.savefig(config.CHECKS / "311_counts.png", dpi=120)
    print(f"\nSaved {config.CHECKS / '311_reports.html'} and {config.CHECKS / '311_counts.png'}")


if __name__ == "__main__":
    main()
