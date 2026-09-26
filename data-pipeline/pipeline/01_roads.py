"""Step 1: download the drivable OSM road network for the study bbox.

Outputs:
  data/processed/graph.graphml   routing graph (routing team loads this)
  data/processed/roads.parquet   edge_id, street_id, u, v, key, name, highway, length,
                                 is_bridge, is_tunnel, geometry (EPSG:4326)
  data/checks/roads.png          network plot colored by highway class
"""
import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import osmnx as ox
import pandas as pd
from shapely.geometry import box


def to_text(val):
    """OSM tags are sometimes lists; join them so parquet gets a plain string column."""
    if isinstance(val, list):
        return ";".join(str(v) for v in val)
    if pd.isna(val):
        return None
    return str(val)


def is_tagged(val):
    """True if an OSM tag like bridge/tunnel is present and not "no" (lists: any such value)."""
    vals = val if isinstance(val, list) else [val]
    return any(isinstance(v, str) and v != "no" for v in vals)


def pair_twins(edges_utm, tol=config.TWIN_TOL_M):
    """Match each directed edge u->v to its physical twin v->u by geometry.

    The key is not reliable for this: parallel edges can get different keys in each
    direction, and two separate one-way carriageways can join the same two nodes.
    A pair needs a reverse edge within `tol` m (Hausdorff) that is each edge's
    closest candidate (mutual), so the pairing is one-to-one.
    Returns (street_id Series, {edge_id: (twin_edge_id, hausdorff_m)}).
    """
    by_uv = {}
    for eid, u, v in zip(edges_utm["edge_id"], edges_utm["u"], edges_utm["v"]):
        by_uv.setdefault((u, v), []).append(eid)
    geom = dict(zip(edges_utm["edge_id"], edges_utm.geometry))

    best = {}
    for eid, u, v in zip(edges_utm["edge_id"], edges_utm["u"], edges_utm["v"]):
        cands = [c for c in by_uv.get((v, u), []) if c != eid]  # c != eid: self-loops
        dists = [(geom[eid].hausdorff_distance(geom[c]), c) for c in cands]
        dists = [d for d in dists if d[0] <= tol]
        if dists:
            d, c = min(dists)
            best[eid] = (c, d)

    pairs = {e: (c, d) for e, (c, d) in best.items() if best.get(c, (None,))[0] == e}
    street_id = edges_utm["edge_id"].map(lambda e: min(e, pairs[e][0]) if e in pairs else e)
    return street_id, pairs


def build_graph():
    """The one graph recipe. Changing anything here changes every edge_id (breaking for routing + Mongo).

    Default simplification merges a bridge with its ground-level approaches into one edge
    (the bridge tag then covers the whole edge), so pull unsimplified and split edges
    wherever the bridge/tunnel tag changes.
    """
    G = ox.graph_from_bbox(config.BBOX, network_type="drive", simplify=False)
    return ox.simplify_graph(G, edge_attrs_differ=["bridge", "tunnel"])


def check_bridges(raw):
    """Print bridge/tunnel tag values and bridge edges worth spot-checking (mixed tags,
    long outliers), plus non-bridge edges that mostly lie on a bridge deck."""
    raw = raw.assign(is_bridge=raw["bridge"].map(is_tagged), is_tunnel=raw["tunnel"].map(is_tagged))
    for col in ("bridge", "tunnel"):
        print(f"\n{col} tag values (edges):\n"
              + raw[col].map(lambda v: str(v) if isinstance(v, list) else v)
                        .value_counts(dropna=False).to_string())
        print(f"is_{col}: {raw[f'is_{col}'].sum()}")

    b = raw[raw["is_bridge"]].copy()
    b["mixed_tags"] = b["bridge"].map(lambda v: isinstance(v, list))
    b["n_ways"] = b["osmid"].map(lambda v: len(v) if isinstance(v, list) else 1)
    q1, q3 = b["length"].quantile([0.25, 0.75])
    b["long_outlier"] = b["length"] > q3 + 3 * (q3 - q1)
    print(f"\nbridge edge length: median {b['length'].median():.0f} m, "
          f"outlier cutoff (Q3 + 3*IQR) {q3 + 3 * (q3 - q1):.0f} m")
    flag = b[b["mixed_tags"] | b["long_outlier"]]
    print(f"bridge edges to spot-check (mixed tags or long): {len(flag)} "
          f"(mixed {b['mixed_tags'].sum()}, long {b['long_outlier'].sum()}); "
          f"bridge edges merged from >1 OSM way: {(b['n_ways'] > 1).sum()}")
    print(flag.assign(bridge=flag["bridge"].astype(str))
              .sort_values("length", ascending=False)
              [["edge_id", "bridge", "n_ways", "length", "mixed_tags", "long_outlier"]]
              .round({"length": 0}).to_string(index=False))

    deck = b.geometry.buffer(1.0).union_all()
    nb = raw[~raw["is_bridge"]]
    frac = nb.geometry.intersection(deck).length / nb.geometry.length
    over = nb.assign(deck_frac=frac)[frac > 0.5]
    print(f"\nnon-bridge edges >50% on a bridge deck (1 m buffer): {len(over)}")
    if len(over):
        print(over[["edge_id", "name", "highway", "length", "deck_frac"]]
              .round({"length": 0, "deck_frac": 2}).to_string(index=False))


def main():
    config.PROC.mkdir(parents=True, exist_ok=True)
    config.CHECKS.mkdir(parents=True, exist_ok=True)

    print(f"Downloading drive network for bbox {config.BBOX} ...")
    G = build_graph()
    ox.save_graphml(G, config.PROC / "graph.graphml")
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    edges = ox.graph_to_gdfs(G, nodes=False).reset_index()
    edges["edge_id"] = (edges["u"].astype(str) + "_" + edges["v"].astype(str)
                        + "_" + edges["key"].astype(str))
    for col in ("bridge", "tunnel"):
        if col not in edges:
            edges[col] = None
        edges[f"is_{col}"] = edges[col].map(is_tagged)
    raw_tags = edges[["edge_id", "name", "highway", "bridge", "tunnel", "osmid", "length", "geometry"]].to_crs(config.CRS_UTM)
    for col in ("name", "highway"):
        edges[col] = edges[col].map(to_text)

    edges["street_id"], pairs = pair_twins(edges.to_crs(config.CRS_UTM))

    # guardrails: every street is a single edge or exactly one u->v / v->u pair
    sizes = edges.groupby("street_id").size()
    assert sizes.isin([1, 2]).all(), sizes[~sizes.isin([1, 2])]
    two = edges[edges["street_id"].map(sizes) == 2].sort_values("street_id")
    a, b = two.iloc[0::2].reset_index(drop=True), two.iloc[1::2].reset_index(drop=True)
    assert (a["street_id"] == b["street_id"]).all()
    assert ((a["u"] == b["v"]) & (a["v"] == b["u"])).all()
    assert edges["street_id"].isin(edges["edge_id"]).all()

    roads = edges[["edge_id", "street_id", "u", "v", "key", "name", "highway", "length",
                   "is_bridge", "is_tunnel", "geometry"]]
    roads = roads.to_crs(config.CRS_WGS)
    roads.to_parquet(config.PROC / "roads.parquet", index=False)

    # ---- verification ----
    print(f"\nroads.parquet: {len(roads)} rows, CRS {roads.crs}")
    print("edge_id unique:", roads["edge_id"].is_unique)

    dists = pd.Series({e: d for e, (_, d) in pairs.items()})
    print(f"Twin pairing: {len(pairs)} edges paired ({len(pairs) // 2} pairs), "
          f"{len(roads) - len(pairs)} unpaired -> {roads['street_id'].nunique()} physical streets")
    print(f"  max Hausdorff among pairs: {dists.max():.4f} m")
    near = dists[(dists > 0.1) & (dists <= config.TWIN_TOL_M)]
    print(f"  pairs at 0.1-{config.TWIN_TOL_M} m (check these): {len(near) // 2}")
    for e, d in near.items():
        if e < pairs[e][0]:
            print(f"    {e} <-> {pairs[e][0]}: {d:.3f} m")

    print("\nNull counts:\n" + roads.isna().sum().to_string())
    check_bridges(raw_tags)
    print(f"\nlength (m): min {roads['length'].min():.1f}, median {roads['length'].median():.1f}, "
          f"max {roads['length'].max():.1f}, total {roads['length'].sum() / 1000:.0f} km")
    outside = ~roads.intersects(box(*config.BBOX))
    print(f"Edges not touching bbox: {outside.sum()}")
    print(f"Total bounds: {tuple(round(float(b), 4) for b in roads.total_bounds)}")
    print("\nTop highway classes:\n" + roads["highway"].value_counts().head(10).to_string())

    fig, ax = plt.subplots(figsize=(14, 6))
    roads.plot(ax=ax, column="highway", linewidth=0.5, legend=True,
               legend_kwds={"fontsize": 6, "loc": "lower left"})
    ax.set_title(f"OSM drive network: {len(roads)} edges")
    ax.set_aspect("equal")
    fig.savefig(config.CHECKS / "roads.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {config.CHECKS / 'roads.png'}")


if __name__ == "__main__":
    main()
