"""Step 10: road closures / construction, matched to edges, for routing (not part of risk_score).

Sources:
  fl511       FL511 traveler info (FDOT), EventType in config.FL511_EVENT_TYPES. Live closures/roadwork
              with lanes, direction, start/end. Needs FL511_API_KEY in .env; skipped without it.
  county_ucc  Miami-Dade Utility Coordination layers (config.UCC_LAYERS): projects in the construction
              phase, not complete, end date >= now, window <= config.UCC_MAX_WINDOW_DAYS. Planned work
              zones, not actual lane closures: a weak proxy, flagged as kind "planned_construction".
              FDOT is excluded here (fiscal-year windows; its real closures come from FL511).

Matching (UTM): lines/polygons are buffered by CLOSURE_BUFFER_M and an edge matches if >= CLOSURE_MIN_OVERLAP
of its length is inside; points take the nearest edge(s) within CLOSURE_SNAP_M. Directional FL511 events
(Northbound, ...) only match edges heading within CLOSURE_BEARING_TOL degrees of that direction.

Time-varying, so this is its own Mongo collection (config.MONGO_CLOSURES), replaced on every run:
one doc per (closure, edge). Backend: {"edge_id": ..., "start": {"$lte": now},
"$or": [{"end": {"$gte": now}}, {"end": null}]}. Rerun this script to refresh (FL511: every ~15 min).

Outputs:
  data/processed/closures.parquet   (gitignored: live data)
  data/checks/closures.png
  Mongo <MONGO_DB>.<MONGO_CLOSURES>

Usage:
  python -m pipeline.10_closures              # fetch, match, load
  python -m pipeline.10_closures --selftest   # offline checks of the FL511 parsing path
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import math
import os
import time
from datetime import datetime, timezone

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import shapely
from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient
from shapely.geometry import LineString, Point

FL511_URL = "https://fl511.com/api/v2/get/event"
UCC_BASE = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/{}/FeatureServer/0/query"
UCC_FIELDS = "PRJNAME,PROJECTID,PRJSCOPE,AGCYNAME,FACTYPE,AGYPRJSTAT,GENPRJSTAT,STARTDATE,ENDDATE"
BEARING = {"northbound": 0, "eastbound": 90, "southbound": 180, "westbound": 270}
COLUMNS = ["edge_id", "closure_id", "source", "kind", "full_closure", "direction", "start", "end",
           "name", "description", "lanes_affected", "agency", "status"]


# ---------------------------------------------------------------- FL511
def decode_polyline(s):
    """Google encoded polyline -> [(lon, lat), ...]."""
    coords, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        lat += vals[0]
        lon += vals[1]
        coords.append((lon / 1e5, lat / 1e5))
    return coords


def ts(v):
    return datetime.fromtimestamp(v, timezone.utc) if v else None


def parse_fl511(events):
    """FL511 event dicts (511 platform schema) -> GeoDataFrame of closures in the bbox."""
    W, S, E, N = config.BBOX
    rows = []
    for ev in events:
        if ev.get("EventType") not in config.FL511_EVENT_TYPES:
            continue
        geom = None
        if ev.get("EncodedPolyline"):
            pts = decode_polyline(ev["EncodedPolyline"])
            geom = LineString(pts) if len(set(pts)) >= 2 else Point(pts[0])
        elif ev.get("Latitude") is not None and ev.get("Longitude") is not None:
            p1 = (ev["Longitude"], ev["Latitude"])
            p2 = (ev.get("LongitudeSecondary"), ev.get("LatitudeSecondary"))
            geom = LineString([p1, p2]) if all(p2) and p2 != p1 else Point(p1)
        if geom is None or not geom.intersects(shapely.box(W, S, E, N)):
            continue
        full = bool(ev.get("IsFullClosure"))
        rows.append(dict(
            closure_id=f"fl511:{ev.get('ID')}", source="fl511",
            kind="closure" if full or ev.get("EventType") == "closures" else "roadwork",
            full_closure=full, direction=ev.get("DirectionOfTravel"),
            start=ts(ev.get("StartDate")), end=ts(ev.get("PlannedEndDate")),
            name=ev.get("RoadwayName"), description=ev.get("Description"),
            lanes_affected=ev.get("LanesAffected"), agency=ev.get("Organization"),
            status=ev.get("Severity"), geometry=geom))
    return gpd.GeoDataFrame(rows, columns=[c for c in COLUMNS if c != "edge_id"] + ["geometry"],
                            geometry="geometry", crs=config.CRS_WGS)


def fetch_fl511(key):
    r = requests.get(FL511_URL, params={"key": key, "format": "json"}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"FL511 HTTP {r.status_code}: {r.text[:200]}")
    events = r.json()
    print(f"  fl511: {len(events)} events statewide")
    return parse_fl511(events)


# ---------------------------------------------------------------- county Utility Coordination
def fetch_ucc(now):
    W, S, E, N = config.BBOX
    parts = []
    for layer in config.UCC_LAYERS:
        feats, offset = [], 0
        while True:
            r = requests.get(UCC_BASE.format(layer), params=dict(
                where="GENPRJSTAT = 'Construction'", geometry=f"{W},{S},{E},{N}",
                geometryType="esriGeometryEnvelope", inSR=4326, spatialRel="esriSpatialRelIntersects",
                outFields=UCC_FIELDS, outSR=4326, resultOffset=offset, resultRecordCount=1000,
                f="geojson"), timeout=180).json()
            if "error" in r:
                raise RuntimeError(f"{layer}: {r['error']}")
            feats += r["features"]
            if len(r["features"]) < 1000 and not r.get("properties", {}).get("exceededTransferLimit"):
                break
            offset += 1000
        if feats:
            g = gpd.GeoDataFrame.from_features(feats, crs=config.CRS_WGS)
            g["layer"] = layer
            parts.append(g)
    ucc = pd.concat(parts, ignore_index=True)
    for c in ("STARTDATE", "ENDDATE"):
        ucc[c] = pd.to_datetime(ucc[c], unit="ms", utc=True)
    n0 = len(ucc)
    window = (ucc["ENDDATE"] - ucc["STARTDATE"]).dt.days
    fdot = ucc["AGCYNAME"].isin(config.UCC_EXCLUDE_AGENCIES)
    keep = (~fdot & ~ucc["AGYPRJSTAT"].isin(config.UCC_EXCLUDE_AGENCY_STATUS) & (ucc["ENDDATE"] >= now)
            & (window <= config.UCC_MAX_WINDOW_DAYS) & ucc.geometry.notna())
    print(f"  county_ucc: {n0} construction-phase records in bbox across {ucc['layer'].nunique()} layers; "
          f"kept {int(keep.sum())} (dropped: agency {config.UCC_EXCLUDE_AGENCIES} {int(fdot.sum())}, complete/closed {int(ucc['AGYPRJSTAT'].isin(config.UCC_EXCLUDE_AGENCY_STATUS).sum())}, "
          f"ended {int((ucc['ENDDATE'] < now).sum())}, window > {config.UCC_MAX_WINDOW_DAYS} d "
          f"{int((window > config.UCC_MAX_WINDOW_DAYS).sum())}; overlapping)")
    u = ucc[keep]
    return gpd.GeoDataFrame(dict(
        closure_id="ucc:" + u["PROJECTID"].astype(str), source="county_ucc", kind="planned_construction",
        full_closure=None, direction=None, start=u["STARTDATE"], end=u["ENDDATE"], name=u["PRJNAME"],
        description=u["PRJSCOPE"], lanes_affected=None, agency=u["AGCYNAME"], status=u["AGYPRJSTAT"],
        geometry=u.geometry), crs=config.CRS_WGS)


# ---------------------------------------------------------------- matching
def edge_bearings(edges):
    xy = shapely.get_coordinates(edges.geometry.values, return_index=True)
    first = pd.DataFrame(xy[0]).groupby(xy[1]).first().to_numpy()
    last = pd.DataFrame(xy[0]).groupby(xy[1]).last().to_numpy()
    return np.degrees(np.arctan2(last[:, 0] - first[:, 0], last[:, 1] - first[:, 1])) % 360


def match(closures, edges):
    """(closure row index, edge_id) pairs. closures and edges in UTM; edges has a 'bearing' column."""
    pairs = []
    area = ~closures.geom_type.isin(["Point", "MultiPoint"])
    if area.any():
        a = closures[area]
        buf = a.geometry.buffer(config.CLOSURE_BUFFER_M)
        hit = gpd.sjoin(edges, gpd.GeoDataFrame(geometry=buf, crs=edges.crs), predicate="intersects")
        inter = shapely.intersection(hit.geometry.values, buf.loc[hit["index_right"]].values)
        frac = shapely.length(inter) / np.maximum(hit["length_m"].to_numpy(), 1e-9)
        ok = hit[frac >= config.CLOSURE_MIN_OVERLAP]
        pairs.append(pd.DataFrame({"c": ok["index_right"].to_numpy(), "edge_id": ok["edge_id"].to_numpy()}))
    if (~area).any():
        p = closures[~area]
        near = gpd.sjoin(edges, gpd.GeoDataFrame(geometry=p.geometry.buffer(config.CLOSURE_SNAP_M), crs=edges.crs),
                         predicate="intersects")
        near["d"] = shapely.distance(near.geometry.values, p.geometry.loc[near["index_right"]].values)
        pairs.append(pd.DataFrame({"c": near["index_right"].to_numpy(), "edge_id": near["edge_id"].to_numpy(),
                                   "d": near["d"].to_numpy()}))
    m = pd.concat(pairs, ignore_index=True) if pairs else pd.DataFrame(columns=["c", "edge_id", "d"])
    # directional events: keep edges heading the right way
    target = closures["direction"].fillna("").str.lower().map(BEARING)
    m = m.merge(edges[["edge_id", "bearing"]], on="edge_id")
    t = target.loc[m["c"]].to_numpy(dtype=float)
    diff = np.abs((m["bearing"].to_numpy() - t + 180) % 360 - 180)
    m = m[np.isnan(t) | (diff <= config.CLOSURE_BEARING_TOL)]
    # points: nearest remaining edge(s), ties within 1 m (e.g. both directions of a two-way street)
    if "d" in m and m["d"].notna().any():
        pts = m["d"].notna()
        dmin = m[pts].groupby("c")["d"].transform("min")
        m = pd.concat([m[~pts], m[pts][m.loc[pts, "d"] <= dmin + 1]])
    return m[["c", "edge_id"]].drop_duplicates()


# ---------------------------------------------------------------- main
def connect():
    uri = os.environ.get("MONGO_URI")
    assert uri, "MONGO_URI missing from .env"
    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    client.admin.command("ping")
    return client


def plain(v):
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def selftest():
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [(-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]
    fake = [
        dict(ID=1, EventType="roadwork", RoadwayName="SW 8th St", DirectionOfTravel="Eastbound",
             Latitude=25.7632, Longitude=-80.3000, StartDate=1758000000, PlannedEndDate=None,
             IsFullClosure=False, LanesAffected="1 right lane blocked", Description="test", Organization="FDOT"),
        dict(ID=2, EventType="accidentsAndIncidents", Latitude=25.76, Longitude=-80.25),
        dict(ID=3, EventType="closures", Latitude=27.0, Longitude=-82.0),  # outside bbox
        dict(ID=4, EventType="closures", IsFullClosure=True, Latitude=25.7700, Longitude=-80.2000,
             LatitudeSecondary=25.7750, LongitudeSecondary=-80.2000, StartDate=1758000000,
             PlannedEndDate=1790000000, DirectionOfTravel="Both Directions"),
        # eastbound event on SW 64th Ave (north-south, 2.5 m away): no eastbound edge within 30 m -> no match
        dict(ID=5, EventType="roadwork", DirectionOfTravel="Eastbound", Latitude=25.7654, Longitude=-80.3000),
    ]
    g = parse_fl511(fake)
    assert list(g["closure_id"]) == ["fl511:1", "fl511:4", "fl511:5"], g["closure_id"].tolist()
    assert g.loc[1, "kind"] == "closure" and g.loc[0, "kind"] == "roadwork" and pd.isna(g.loc[0, "end"])
    edges = gpd.read_parquet(config.PROC / "roads.parquet")[["edge_id", "geometry"]].to_crs(config.CRS_UTM)
    edges["length_m"] = edges.geometry.length
    edges["bearing"] = edge_bearings(edges)
    m = match(g.to_crs(config.CRS_UTM).reset_index(drop=True), edges)
    got = m.groupby("c")["edge_id"].apply(list).to_dict()
    b = edges.set_index("edge_id")["bearing"]
    assert 0 in got and all(abs((b[e] - 90 + 180) % 360 - 180) <= config.CLOSURE_BEARING_TOL for e in got[0]), got.get(0)
    assert 2 not in got, got.get(2)
    print(f"selftest OK: polyline decode; FL511 parse/filter (3 of 5 kept); eastbound point -> {got[0]}; "
          f"wrong-direction point -> no match; 2-point line -> {len(got.get(1, []))} edge(s)")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    load_dotenv(config.ROOT / ".env")
    now = pd.Timestamp.now(tz="UTC")
    print("Fetching closures ...")
    parts = []
    key = os.environ.get("FL511_API_KEY", "").strip()
    if key:
        parts.append(fetch_fl511(key))
    else:
        print("  fl511: SKIPPED (no FL511_API_KEY in .env) -> no live closures; county planned construction only")
    parts.append(fetch_ucc(now))
    closures = pd.concat(parts, ignore_index=True).pipe(gpd.GeoDataFrame, geometry="geometry", crs=config.CRS_WGS)

    edges = gpd.read_parquet(config.PROC / "roads.parquet")[["edge_id", "name", "geometry"]].to_crs(config.CRS_UTM)
    edges["length_m"] = edges.geometry.length
    edges["bearing"] = edge_bearings(edges)
    c_utm = closures.to_crs(config.CRS_UTM).reset_index(drop=True)
    m = match(c_utm, edges)
    links = m.merge(pd.DataFrame(closures.drop(columns="geometry")).reset_index(drop=True),
                    left_on="c", right_index=True).drop(columns="c")
    links = links.drop_duplicates(["closure_id", "edge_id"])[COLUMNS]  # same project in several layers
    assert set(links["edge_id"]) <= set(edges["edge_id"])
    config.PROC.mkdir(parents=True, exist_ok=True)
    links.to_parquet(config.PROC / "closures.parquet", index=False)

    client = connect()
    coll = client[config.MONGO_DB][config.MONGO_CLOSURES]
    docs = [{"_id": f"{r['closure_id']}|{r['edge_id']}", **{k: plain(v) for k, v in r.items()}}
            for r in links.to_dict("records")]
    deleted = coll.delete_many({}).deleted_count
    if docs:
        coll.insert_many(docs, ordered=False)
    coll.create_index("edge_id")
    coll.create_index([("start", ASCENDING), ("end", ASCENDING)])

    # ---- verification ----
    print(f"\nclosures: {len(closures)} ({closures['source'].value_counts().to_dict()}); "
          f"matched to edges: {links['closure_id'].nunique()}; unmatched: "
          f"{len(set(closures['closure_id']) - set(links['closure_id']))}")
    km = links.merge(edges[["edge_id", "length_m"]], on="edge_id")
    print("links by source x kind:\n" + km.groupby(["source", "kind"]).agg(
        closures=("closure_id", "nunique"), edges=("edge_id", "nunique"),
        km=("length_m", lambda s: round(s.sum() / 1000, 1))).to_string())
    active = (links["start"] <= now) & (links["end"].isna() | (links["end"] >= now))
    print(f"active now ({now:%Y-%m-%d %H:%M} UTC): {links.loc[active, 'closure_id'].nunique()} closures on "
          f"{links.loc[active, 'edge_id'].nunique()} edges")
    per = km.groupby("closure_id").agg(name=("name", "first"), agency=("agency", "first"), edges=("edge_id", "size"),
                                       km=("length_m", lambda s: s.sum() / 1000), start=("start", "first"),
                                       end=("end", "first")).sort_values("km", ascending=False)
    print("largest by road length:\n" + per.head(10).assign(
        name=per["name"].str[:55], km=per["km"].round(2),
        start=per["start"].astype(str).str[:10], end=per["end"].astype(str).str[:10]).to_string())
    print(f"\nMongo {config.MONGO_DB}.{config.MONGO_CLOSURES}: deleted {deleted}, inserted {len(docs)}, "
          f"count {coll.count_documents({})}; indexes {sorted(coll.index_information())}")
    q = {"start": {"$lte": now.to_pydatetime()}, "$or": [{"end": {"$gte": now.to_pydatetime()}}, {"end": None}]}
    print(f"backend-style query (active now): {coll.count_documents(q)} docs")

    fig, ax = plt.subplots(figsize=(14, 6))
    wgs = edges.to_crs(config.CRS_WGS)
    wgs.plot(ax=ax, color="#ccc", linewidth=0.3)
    colors = {"fl511": "#d62728", "county_ucc": "#ff7f0e"}
    for src, g in links.groupby("source"):
        wgs[wgs["edge_id"].isin(g["edge_id"])].plot(ax=ax, color=colors[src], linewidth=1.5,
                                                     label=f"{src} ({g['closure_id'].nunique()})")
    ax.legend(); ax.set_aspect("equal")
    ax.set_title(f"Closures / construction matched to edges (fetched {now:%Y-%m-%d})")
    fig.savefig(config.CHECKS / "closures.png", dpi=140, bbox_inches="tight")
    print(f"Saved {config.CHECKS / 'closures.png'}")


if __name__ == "__main__":
    main()
