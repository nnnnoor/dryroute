"""Step 12: FIU (Modesto Maidique campus) parking flood exposure + campus ponding hotspots.

- Campus outline and parking polygons: OpenStreetMap (amenity=university named config.FIU_NAME;
  amenity=parking whose representative point is on campus). Garages = parking=multi-storey.
- 1 m USGS 3DEP bare-earth DEM for the campus + config.FIU_DEM_BUFFER_M (py3dep). Water (DEM <
  FIU_WATER_MAX_ELEV_M, plus OSM water polygons) and the raster edge are drain outlets; depressions are
  filled by morphological reconstruction; sink depth = filled - dem (0 on water). Bare earth: a garage's footprint reads the ground
  under it, which is what matters for its ground-level entrance.
- Per lot: ground p10/median, pond_frac (share of cells with depth >= POND_MIN_DEPTH_M), pond_depth_p90,
  FEMA zone (at a representative point), County Flood Criteria 2060 freeboard (Step 11 raster),
  access roads (non-bridge drive edges within PARKING_ACCESS_M: best/worst risk_score). parking_score =
  weighted mean of fixed-range 0-1 components (config.PARKING_WEIGHTS / PARKING_RANGE); higher = worse.
- Campus hotspots: connected areas with depth >= HOTSPOT_MIN_DEPTH_M and area >= HOTSPOT_MIN_AREA_M2.
No 311 coverage on campus (FIU runs its own grounds), so this is terrain-based exposure, not observed flooding.

Outputs:
  data/raw/fiu_dem_1m.tif, fiu_sink_1m.tif, fiu_water_1m.tif, fiu_campus.geojson, fiu_parking_osm.geojson
  data/processed/fiu_parking.geojson, fiu_hotspots.geojson
  data/checks/fiu_parking.html (folium), fiu_parking.png
  Mongo <MONGO_DB>.<MONGO_PARKING>, <MONGO_HOTSPOTS> (2dsphere on geometry; replaced each run)
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import math
import os

import folium
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import pandas as pd
import py3dep
import rasterio
import shapely
from dotenv import load_dotenv
from pymongo import GEOSPHERE, MongoClient
from pymongo.errors import BulkWriteError
from rasterio.features import rasterize, shapes
from rasterio.mask import mask as rio_mask
from rasterstats import zonal_stats
from scipy import ndimage
from skimage.morphology import reconstruction

DEM = config.RAW / "fiu_dem_1m.tif"
SINK = config.RAW / "fiu_sink_1m.tif"
WATER = config.RAW / "fiu_water_1m.tif"
CRITERIA = config.RAW / "flood_criteria" / "CountyFloodCriteria.gdb"
US_FT = 1200 / 3937


def osm_layers():
    ox.settings.cache_folder = str(config.ROOT / "cache")
    uni = ox.features_from_bbox(config.FIU_AREA, tags={"amenity": "university"})
    uni = uni[uni.geom_type.isin(["Polygon", "MultiPolygon"]) & uni["name"].eq(config.FIU_NAME)]
    assert len(uni), f"no OSM university polygon named {config.FIU_NAME!r}"
    campus = gpd.GeoDataFrame({"name": [config.FIU_NAME]}, geometry=[shapely.make_valid(uni.geometry.union_all())],
                              crs=config.CRS_WGS)
    park = ox.features_from_bbox(config.FIU_AREA, tags={"amenity": "parking"})
    park = park[park.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index()
    park = park[park.geometry.representative_point().within(campus.geometry.iloc[0])].copy()
    park["osm_id"] = park["element"] + "/" + park["id"].astype(str)
    for c in ("name", "parking", "capacity", "access", "fee"):
        if c not in park:
            park[c] = None
    park["type"] = np.where(park["parking"].eq("multi-storey"), "garage", park["parking"].fillna("surface"))
    park["name"] = park["name"].fillna("Unnamed " + park["type"] + " lot (" + park["osm_id"] + ")")
    park = gpd.GeoDataFrame(park[["osm_id", "name", "type", "capacity", "access", "fee", "geometry"]],
                            geometry="geometry", crs=config.CRS_WGS).reset_index(drop=True)  # zonal stats align by position
    park["geometry"] = park.geometry.make_valid()
    campus.to_file(config.RAW / "fiu_campus.geojson", driver="GeoJSON")
    park.to_file(config.RAW / "fiu_parking_osm.geojson", driver="GeoJSON")
    return campus, park


def dem_and_sink(campus):
    """1 m DEM -> water mask + sink depth, with the raster edge AND water cells as drain outlets."""
    if not DEM.exists():
        box = campus.to_crs(config.CRS_UTM).buffer(config.FIU_DEM_BUFFER_M).to_crs(config.CRS_WGS).total_bounds
        print(f"Downloading 1 m 3DEP DEM for {tuple(round(float(b), 4) for b in box)} ...")
        py3dep.get_dem(tuple(float(b) for b in box), resolution=1).rio.to_raster(DEM)
    with rasterio.open(DEM) as src:
        dem = src.read(1).astype("float64")
        prof, tf, crs = src.profile, src.transform, src.crs
        cell = abs(tf.a * tf.e)
    ox.settings.cache_folder = str(config.ROOT / "cache")
    osm_w = ox.features_from_bbox(config.FIU_AREA, tags={"natural": "water", "water": True, "landuse": ["basin", "reservoir"]})
    osm_w = osm_w[osm_w.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(crs)
    water = dem < config.FIU_WATER_MAX_ELEV_M
    if len(osm_w):
        water |= rasterize(osm_w.geometry, out_shape=dem.shape, transform=tf).astype(bool)
    lab, n = ndimage.label(water)
    sizes = ndimage.sum(water, lab, index=np.arange(1, n + 1)) * cell
    water = np.isin(lab, np.flatnonzero(sizes >= config.FIU_WATER_MIN_AREA_M2) + 1)
    seed = np.full_like(dem, dem.max())
    edge = np.zeros(dem.shape, bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    seed[edge | water] = dem[edge | water]
    filled = reconstruction(seed, dem, method="erosion")  # standard depression fill, outlets = seeds
    depth = (filled - dem).astype("float32")
    depth[water] = 0  # permanent water is not ponding
    prof.update(dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(SINK, "w", **prof) as dst:
        dst.write(depth, 1)
    prof.update(dtype="uint8", nodata=255)
    with rasterio.open(WATER, "w", **prof) as dst:
        dst.write(water.astype("uint8"), 1)
    print(f"water mask: {water.mean():.1%} of the 1 m DEM ({water.sum() * cell:,.0f} m2; OSM water polygons: {len(osm_w)})")
    return water, tf, crs


def zonal(geoms, path, **kw):
    with rasterio.open(path) as src:
        crs = src.crs
    return pd.DataFrame(zonal_stats(gpd.GeoSeries(geoms).to_crs(crs), str(path), **kw))


def lot_features(park, segs):
    g = park.geometry
    z = zonal(g, DEM, stats=["percentile_10", "median", "count"], nodata=np.nan)
    park["elev_p10"], park["elev_median"], park["cells_1m"] = z["percentile_10"], z["median"], z["count"]
    z = zonal(g, SINK, stats=["percentile_90", "max"], nodata=np.nan,
              add_stats={"pond_frac": lambda a: float((a.compressed() >= config.POND_MIN_DEPTH_M).mean())
                         if a.count() else np.nan})
    park["pond_frac"], park["pond_depth_p90"], park["pond_depth_max"] = z["pond_frac"], z["percentile_90"], z["max"]
    if CRITERIA.exists():
        z = zonal(g, CRITERIA, stats=["mean"], all_touched=True)
        park["flood_criteria_m"] = z["mean"] * US_FT
        park["freeboard_p10_m"] = park["elev_p10"] - park["flood_criteria_m"]
    fema = gpd.read_file(config.RAW / "fema_zones.geojson", bbox=tuple(config.FIU_AREA))
    pts = gpd.GeoDataFrame(geometry=park.geometry.representative_point(), crs=config.CRS_WGS)
    fz = gpd.sjoin(pts, fema[["FLD_ZONE", "SFHA_TF", "geometry"]], predicate="within", how="left")
    fz = fz[~fz.index.duplicated()]
    park["fema_zone"], park["in_sfha"] = fz["FLD_ZONE"], fz["SFHA_TF"].eq("T")

    roads = segs[~segs["is_bridge"]][["edge_id", "name", "risk_score", "geometry"]].to_crs(config.CRS_UTM)
    buf = gpd.GeoDataFrame({"lot": park.index}, geometry=park.to_crs(config.CRS_UTM).buffer(config.PARKING_ACCESS_M),
                           crs=config.CRS_UTM)
    acc = gpd.sjoin(roads, buf, predicate="intersects")
    a = acc.groupby("lot").agg(access_edges=("edge_id", "size"), access_risk_min=("risk_score", "min"),
                               access_risk_max=("risk_score", "max"),
                               access_roads=("name", lambda s: "; ".join(s.dropna().value_counts().index[:3])))
    a["access_method"] = "adjacent"
    miss = buf[~buf["lot"].isin(a.index)]
    if len(miss):  # interior lots: aisles/service roads aren't in the drive graph -> nearest drive edge
        nn = gpd.sjoin_nearest(miss, roads, max_distance=config.PARKING_ACCESS_FALLBACK_M, distance_col="dist")
        b = nn.groupby("lot").agg(access_edges=("edge_id", "size"), access_risk_min=("risk_score", "min"),
                                  access_risk_max=("risk_score", "max"),
                                  access_roads=("name", lambda s: "; ".join(s.dropna().value_counts().index[:3])))
        b["access_method"] = "nearest"
        a = pd.concat([a, b])
    return park.join(a)


def score(park):
    parts = {}
    for c, (lo, hi) in config.PARKING_RANGE.items():
        if c in park:
            parts[c] = ((park[c].astype(float) - lo) / (hi - lo)).clip(0, 1)
    parts = pd.DataFrame(parts)
    w = pd.Series({c: config.PARKING_WEIGHTS[c] for c in parts})
    have = parts.notna().mul(w).sum(axis=1)
    park["parking_score"] = parts.mul(w).sum(axis=1) / have.where(have > 0)
    return park


def hotspots(campus, park, segs):
    with rasterio.open(SINK) as src:
        arr, tf = rio_mask(src, campus.to_crs(src.crs).geometry, crop=True, nodata=np.nan)
        crs = src.crs
    d = arr[0]  # 0 on water, so lakes/canals never become hotspots
    wet = (d >= config.HOTSPOT_MIN_DEPTH_M).astype("uint8")
    polys = [shapely.geometry.shape(geom) for geom, v in shapes(wet, mask=wet.astype(bool), transform=tf) if v == 1]
    hs = gpd.GeoDataFrame(geometry=polys, crs=crs)
    hs["area_m2"] = hs.area
    hs = hs[hs["area_m2"] >= config.HOTSPOT_MIN_AREA_M2].reset_index(drop=True)
    st = pd.DataFrame(zonal_stats(hs.geometry, str(SINK), stats=["max", "mean"], nodata=np.nan))
    hs["depth_max_m"], hs["depth_mean_m"] = st["max"], st["mean"]
    # bare-earth DEM interpolates under buildings: drop hotspots mostly inside a building footprint
    bld = ox.features_from_bbox(config.FIU_AREA, tags={"building": True})
    bld = bld[bld.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(crs).geometry.union_all()
    hs["building_frac"] = hs.geometry.intersection(bld).area / hs.area
    n0 = len(hs)
    hs = hs[hs["building_frac"] <= 0.5].reset_index(drop=True)
    print(f"hotspots: dropped {n0 - len(hs)} of {n0} that are mostly under buildings")
    hs = hs.to_crs(config.CRS_WGS)
    hs["geometry"] = hs.geometry.simplify(0.000005).make_valid()  # ~0.5 m; keeps GeoJSON small and valid
    lots = gpd.sjoin(hs[["geometry"]], park[["name", "geometry"]], predicate="intersects")
    hs["lots"] = lots.groupby(level=0)["name"].apply(lambda s: "; ".join(sorted(set(s))))
    rd = gpd.sjoin(hs[["geometry"]], segs[["name", "geometry"]], predicate="intersects")
    hs["roads"] = rd.groupby(level=0)["name"].apply(lambda s: "; ".join(sorted(set(s.dropna()))[:4]))
    hs = hs.sort_values(["depth_max_m", "area_m2"], ascending=False).reset_index(drop=True)
    hs.insert(0, "hotspot_id", [f"fiu_hs_{i + 1:03d}" for i in range(len(hs))])
    return hs


def plain(v):
    if v is None or v is pd.NA:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def load_mongo(name, gdf, id_col):
    load_dotenv(config.ROOT / ".env")
    client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=15000)
    coll = client[config.MONGO_DB][name]
    coll.delete_many({})
    coll.create_index([("geometry", GEOSPHERE)])  # index first, so an invalid polygon fails alone
    docs = [{"_id": r[id_col], **{k: plain(v) for k, v in r.items() if k != "geometry"},
             "geometry": shapely.geometry.mapping(shapely.remove_repeated_points(r["geometry"]))}
            for r in gdf.to_dict("records")]
    failed = 0
    try:
        coll.insert_many(docs, ordered=False)
    except BulkWriteError as e:
        failed = len(e.details["writeErrors"])
        print(f"  {name}: {failed} docs rejected, e.g. {e.details['writeErrors'][0]['errmsg'][:150]}")
    print(f"  Mongo {config.MONGO_DB}.{name}: {coll.count_documents({})} docs ({failed} rejected)")
    return coll


def main():
    for d in (config.RAW, config.PROC, config.CHECKS):
        d.mkdir(parents=True, exist_ok=True)
    campus, park = osm_layers()
    print(f"FIU campus {campus.to_crs(config.CRS_UTM).area.iloc[0] / 1e6:.2f} km2; parking polygons on campus: "
          f"{len(park)} ({park['type'].value_counts().to_dict()})")
    dem_and_sink(campus)
    segs = gpd.read_parquet(config.PROC / "segments.parquet")
    park = score(lot_features(park, segs)).sort_values("parking_score", ascending=False)
    hs = hotspots(campus, park, segs)
    park.to_file(config.PROC / "fiu_parking.geojson", driver="GeoJSON")
    hs.to_file(config.PROC / "fiu_hotspots.geojson", driver="GeoJSON")
    print("Loading to Atlas ...")
    pcoll = load_mongo(config.MONGO_PARKING, park, "osm_id")
    load_mongo(config.MONGO_HOTSPOTS, hs, "hotspot_id")

    # ---- verification ----
    with rasterio.open(DEM) as src:
        a = src.read(1, masked=True)
        print(f"\n1 m DEM: {src.width}x{src.height} px, {src.crs}, res {src.res[0]:.2f} m, "
              f"range {float(a.min()):.2f}-{float(a.max()):.2f} m, nodata px {int(np.ma.getmaskarray(a).sum())}")
    cols = ["name", "type", "parking_score", "pond_frac", "pond_depth_p90", "elev_p10", "freeboard_p10_m",
            "fema_zone", "access_risk_min", "access_method", "access_roads"]
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 42)
    print("\nNull counts:\n" + park[cols].isna().sum()[lambda s: s > 0].to_string())
    print("\nparking_score:\n" + park["parking_score"].describe().round(3).to_string())
    print("\nAll lots, worst first:\n" + park[cols].round(2).to_string(index=False))
    print("\nby type (median):\n" + park.groupby("type")[["parking_score", "pond_frac", "elev_p10", "freeboard_p10_m"]]
          .median().round(2).to_string())
    print(f"\nhotspots (>= {config.HOTSPOT_MIN_DEPTH_M} m deep, >= {config.HOTSPOT_MIN_AREA_M2} m2) on campus: {len(hs)}, "
          f"total {hs['area_m2'].sum():,.0f} m2; in parking lots: {hs['lots'].notna().sum()}, on roads: {hs['roads'].notna().sum()}")
    print(hs.head(12)[["hotspot_id", "area_m2", "depth_max_m", "depth_mean_m", "lots", "roads"]].round(2).to_string(index=False))
    fiu_pt = {"type": "Point", "coordinates": [-80.3733, 25.7574]}
    near = list(pcoll.find({"geometry": {"$near": {"$geometry": fiu_pt, "$maxDistance": 300}}}, {"name": 1, "parking_score": 1}).limit(3))
    print("Mongo $near check (300 m of campus center):", [(d["name"], d.get("parking_score") and round(d["parking_score"], 2)) for d in near])

    m = folium.Map(location=[25.7565, -80.3765], zoom_start=16, tiles="OpenStreetMap")
    folium.GeoJson(campus.__geo_interface__, style_function=lambda _: {"color": "#333", "weight": 2, "fillOpacity": 0}).add_to(m)
    cmap = matplotlib.colormaps["RdYlGn_r"]
    lots_fg = folium.FeatureGroup(name="Parking (color = parking_score)").add_to(m)
    for r in park.itertuples():
        c = matplotlib.colors.to_hex(cmap(r.parking_score)) if pd.notna(r.parking_score) else "#999"
        folium.GeoJson(r.geometry.__geo_interface__, style_function=lambda _, c=c: {"color": c, "fillColor": c, "weight": 1, "fillOpacity": 0.55},
                       tooltip=f"{r.name} ({r.type}) score {r.parking_score:.2f} | ponding {r.pond_frac:.0%} of lot, "
                               f"p90 {r.pond_depth_p90:.2f} m | access: {r.access_roads}").add_to(lots_fg)
    hs_fg = folium.FeatureGroup(name="Ponding hotspots").add_to(m)
    for r in hs.itertuples():
        folium.GeoJson(r.geometry.__geo_interface__, style_function=lambda _: {"color": "#08519c", "fillColor": "#3182bd", "weight": 1, "fillOpacity": 0.7},
                       tooltip=f"{r.hotspot_id}: {r.area_m2:,.0f} m2, max {r.depth_max_m:.2f} m").add_to(hs_fg)
    folium.LayerControl().add_to(m)
    m.save(config.CHECKS / "fiu_parking.html")

    fig, ax = plt.subplots(figsize=(12, 8))
    campus.boundary.plot(ax=ax, color="#333", linewidth=1)
    segs.cx[config.FIU_AREA[0]:config.FIU_AREA[2], config.FIU_AREA[1]:config.FIU_AREA[3]].plot(ax=ax, color="#bbb", linewidth=0.5)
    park.plot(ax=ax, column="parking_score", cmap="RdYlGn_r", vmin=0, vmax=1, legend=True, alpha=0.8,
              legend_kwds={"label": "parking_score (higher = worse)", "shrink": 0.6})
    hs.plot(ax=ax, color="#3182bd")
    b = campus.total_bounds
    ax.set_xlim(b[0] - 0.002, b[2] + 0.002); ax.set_ylim(b[1] - 0.002, b[3] + 0.002); ax.set_aspect("equal")
    ax.set_title("FIU MMC: parking flood exposure + ponding hotspots (blue, 1 m DEM)")
    fig.savefig(config.CHECKS / "fiu_parking.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {config.CHECKS / 'fiu_parking.html'} and fiu_parking.png")


if __name__ == "__main__":
    main()
