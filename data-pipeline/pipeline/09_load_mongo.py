"""Step 9: load segments.parquet into MongoDB Atlas, one doc per edge (_id = edge_id).

Replaces the whole collection (delete_many + insert_many), then builds a 2dsphere index on
geometry and an index on street_id. Refuses to load unless segments.parquet has exactly the
edge_ids of data/processed/graph.graphml, and asserts the same for the loaded _ids.

Usage:
  python -m pipeline.09_load_mongo           # load + verify
  python -m pipeline.09_load_mongo --ping    # only check MONGO_URI / IP allowlist
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import math
import os

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import shapely
from dotenv import load_dotenv
from pymongo import GEOSPHERE, MongoClient

FIU = (-80.3733, 25.7574)  # Modesto Maidique campus (lon, lat)
BRICKELL = {"type": "Polygon", "coordinates": [[  # roughly SE 8th St -> SE 15th Rd, I-95 -> the bay
    [-80.2000, 25.7580], [-80.1880, 25.7580], [-80.1880, 25.7700],
    [-80.2000, 25.7700], [-80.2000, 25.7580]]]}


def connect():
    load_dotenv(config.ROOT / ".env")
    uri = os.environ.get("MONGO_URI")
    assert uri, "MONGO_URI missing from .env"
    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    client.admin.command("ping")  # fails fast on a bad password or a non-allowlisted IP
    return client


def graph_edge_ids():
    """edge_ids of the routing graph, built the same way as Step 1 (f"{u}_{v}_{key}")."""
    G = ox.load_graphml(config.PROC / "graph.graphml")
    return {f"{u}_{v}_{k}" for u, v, k in G.edges(keys=True)}


def report_previous_load(coll, graph_ids, bridge_ids):
    """Compare what's in Atlas now (before it's replaced) with the current graph.

    Before the bridge/tunnel split (Step 1), bridges were merged with their approaches, so a
    pre-split load has different ids and lacks most of today's bridge edge_ids.
    """
    prev = {d["_id"] for d in coll.find({}, {"_id": 1})}
    if not prev:
        print("previous Atlas load: collection empty")
        return
    same = prev == graph_ids
    print(f"previous Atlas load: {len(prev)} docs; {len(prev & graph_ids)} ids in current graph, "
          f"{len(prev - graph_ids)} only in previous, {len(graph_ids - prev)} new; "
          f"current bridge edge_ids present: {len(prev & bridge_ids)}/{len(bridge_ids)}")
    if same:
        print("  -> same edge_id set as the current (post-bridge-split) graph: did NOT predate the split")
    else:
        print("  -> different graph: likely predates the bridge split (or an OSM refresh); "
              "anything keyed on the old ids is stale")


def plain(v):
    """numpy scalars -> Python; NaN/NA -> None (BSON can't encode numpy types)."""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def to_docs(segs):
    geoms = shapely.remove_repeated_points(segs.geometry.values)  # duplicate vertices break 2dsphere
    npts = shapely.get_num_coordinates(geoms)
    assert (npts >= 2).all(), f"{(npts < 2).sum()} edges collapse to < 2 points"
    fixed = (shapely.get_num_coordinates(segs.geometry.values) != npts).sum()
    print(f"remove_repeated_points changed {fixed} geometries")

    attrs = pd.DataFrame(segs.drop(columns=["edge_id", "geometry"]))
    docs = []
    for edge_id, geom, row in zip(segs["edge_id"], geoms, attrs.to_dict("records")):
        doc = {"_id": edge_id, **{k: plain(v) for k, v in row.items()}}
        doc["geometry"] = geom.__geo_interface__
        docs.append(doc)
    return docs


def main():
    client = connect()
    print(f"Connected to Atlas (server {client.server_info()['version']})")
    if "--ping" in sys.argv:
        return

    segs = gpd.read_parquet(config.PROC / "segments.parquet").to_crs(config.CRS_WGS)
    assert segs["edge_id"].is_unique
    print(f"segments.parquet: {len(segs)} rows, {segs.shape[1]} cols")
    docs = to_docs(segs)

    graph_ids = graph_edge_ids()
    assert set(segs["edge_id"]) == graph_ids, (
        f"segments.parquet vs graph.graphml edge_ids differ: {len(set(segs['edge_id']) - graph_ids)} only in parquet, "
        f"{len(graph_ids - set(segs['edge_id']))} only in graph. Rerun 01 -> 08 from one graph before loading.")
    print(f"graph.graphml: {len(graph_ids)} edges, same edge_id set as segments.parquet")

    coll = client[config.MONGO_DB][config.MONGO_COLLECTION]
    report_previous_load(coll, graph_ids, set(segs.loc[segs["is_bridge"], "edge_id"]))
    deleted = coll.delete_many({}).deleted_count
    coll.insert_many(docs, ordered=False)
    coll.create_index([("geometry", GEOSPHERE)])
    coll.create_index("street_id")
    print(f"{config.MONGO_DB}.{config.MONGO_COLLECTION}: deleted {deleted}, inserted {len(docs)}")

    # ---- verification ----
    n = coll.count_documents({})
    print(f"\ndoc count {n} vs parquet {len(segs)}: {'OK' if n == len(segs) else 'MISMATCH'}")
    mongo_ids = {d["_id"] for d in coll.find({}, {"_id": 1})}
    assert mongo_ids == graph_ids, (f"Mongo _ids vs graph edge_ids differ: {len(mongo_ids - graph_ids)} only in "
                                    f"Mongo, {len(graph_ids - mongo_ids)} only in graph")
    print("Mongo _id set == graph.graphml edge_id set: OK")
    print("indexes:", sorted(coll.index_information()))
    sample = coll.find_one({}, {"geometry": 0})
    print("sample doc (no geometry):", sample)
    types = coll.distinct("geometry.type")
    print("geometry types:", types)

    near = list(coll.find(
        {"geometry": {"$near": {"$geometry": {"type": "Point", "coordinates": list(FIU)},
                                "$maxDistance": 300}}},
        {"name": 1, "highway": 1, "risk_score": 1}).limit(10))
    print(f"\n$near FIU {FIU}, 300 m, first {len(near)}:")
    for d in near:
        print(f"  {d['_id']:<32} {str(d.get('name')):<28} {d.get('highway'):<14} risk={d.get('risk_score')}")

    within = {"geometry": {"$geoWithin": {"$geometry": BRICKELL}}}
    n_within = coll.count_documents(within)
    stats = list(coll.aggregate([{"$match": within}, {"$group": {
        "_id": None, "streets": {"$addToSet": "$street_id"},
        "min": {"$min": "$risk_score"}, "avg": {"$avg": "$risk_score"}, "max": {"$max": "$risk_score"}}}]))
    print(f"\n$geoWithin Brickell box: {n_within} edges", end="")
    if stats:
        s = stats[0]
        print(f", {len(s['streets'])} streets, risk_score min/avg/max "
              f"{s['min']:.3f}/{s['avg']:.3f}/{s['max']:.3f}")
    else:
        print(" (none: check the polygon)")


if __name__ == "__main__":
    main()
