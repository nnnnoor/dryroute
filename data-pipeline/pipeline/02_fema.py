"""Step 2: FEMA NFHL flood hazard zone at each edge's midpoint.

Outputs:
  data/raw/fema_zones.geojson      cached NFHL polygons (layer 28, Flood Hazard Zones)
  data/features/fema.parquet       edge_id, fema_zone, zone_subtype, in_sfha, fema_risk_level
  data/checks/fema.png             roads colored by fema_risk_level
"""
import sys; from pathlib import Path
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
from shapely.geometry import box

URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
PAGE = 100  # larger pages (1000) return HTTP 500: the zone polygons are big

# ZONE_SUBTY strings as they appear in the NFHL for this bbox (checked 2026-09-26).
# Unlisted subtypes fall to risk level 0 and are flagged in the printout.
SUBTYPE_02PCT = "0.2 PCT ANNUAL CHANCE FLOOD HAZARD"
SUBTYPE_MINIMAL = "AREA OF MINIMAL FLOOD HAZARD"


def risk_level(zone, subtype, sfha):
    """3 = SFHA, 2 = X 0.2% annual chance, 1 = X minimal hazard, 0 = anything else / unmapped."""
    if sfha:
        return 3
    if zone == "X" and subtype == SUBTYPE_02PCT:
        return 2
    if zone == "X" and subtype == SUBTYPE_MINIMAL:
        return 1
    return 0


def get_page(params, tries=3):
    for attempt in range(tries):
        try:
            r = requests.get(URL, params=params, timeout=180)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == tries - 1:
                raise
            print(f"  retrying after {e}")
            time.sleep(5)


def download_zones():
    params = dict(geometry=",".join(map(str, config.BBOX)), geometryType="esriGeometryEnvelope",
                  inSR=4326, outSR=4326, spatialRel="esriSpatialRelIntersects",
                  outFields="FLD_ZONE,ZONE_SUBTY,SFHA_TF", orderByFields="OBJECTID", f="geojson")
    features, offset = [], 0
    while True:
        page = get_page({**params, "resultOffset": offset, "resultRecordCount": PAGE})
        if "error" in page:
            raise RuntimeError(page["error"])
        features += page["features"]
        print(f"  fetched {len(features)} zones")
        if len(page["features"]) < PAGE and not page.get("exceededTransferLimit"):
            break
        offset += PAGE
    zones = gpd.GeoDataFrame.from_features(features, crs=config.CRS_WGS)
    zones["geometry"] = zones.geometry.make_valid()
    zones["ZONE_SUBTY"] = zones["ZONE_SUBTY"].str.strip().replace("", None)
    return zones


def main():
    config.RAW.mkdir(parents=True, exist_ok=True)
    config.FEAT.mkdir(parents=True, exist_ok=True)

    print("Downloading FEMA flood hazard zones ...")
    zones = download_zones()
    zones.to_file(config.RAW / "fema_zones.geojson", driver="GeoJSON")

    roads = gpd.read_parquet(config.PROC / "roads.parquet")[["edge_id", "geometry"]].to_crs(config.CRS_UTM)
    mids = roads.set_geometry(roads.geometry.interpolate(0.5, normalized=True))

    hits = gpd.sjoin(mids, zones.to_crs(config.CRS_UTM), how="left", predicate="within")
    # a midpoint on a shared boundary can land in several polygons: keep the SFHA one if any
    hits = hits.sort_values("SFHA_TF", ascending=False).drop_duplicates("edge_id")
    fema = roads[["edge_id"]].merge(hits[["edge_id", "FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"]],
                                    on="edge_id", how="left")
    fema = fema.rename(columns={"FLD_ZONE": "fema_zone", "ZONE_SUBTY": "zone_subtype"})
    fema["in_sfha"] = fema.pop("SFHA_TF").map({"T": True, "F": False}).astype("boolean")
    sfha = fema["in_sfha"].fillna(False).astype(bool)  # plain bools; NA (no zone) -> not SFHA
    fema["fema_risk_level"] = [risk_level(z, st, sf) for z, st, sf in
                               zip(fema["fema_zone"], fema["zone_subtype"], sfha)]
    fema["fema_risk_level"] = fema["fema_risk_level"].astype("int8")
    fema.to_parquet(config.FEAT / "fema.parquet", index=False)

    # ---- verification ----
    print(f"\nzones: {len(zones)} polygons, bounds {tuple(round(float(b), 3) for b in zones.total_bounds)}")
    print("zone polygons by FLD_ZONE x ZONE_SUBTY x SFHA_TF:\n"
          + zones.value_counts(["FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"], dropna=False).to_string())
    print(f"\nfema.parquet: {len(fema)} rows (roads: {len(roads)}), edge_id unique: {fema['edge_id'].is_unique}")
    print("Null counts:\n" + fema.isna().sum().to_string())
    print("\nEdges by in_sfha:\n" + fema["in_sfha"].value_counts(dropna=False).to_string())
    bad = fema.dropna(subset=["fema_zone"]).query(
        "(fema_zone.str.startswith('A') or fema_zone.str.startswith('V')) != in_sfha")
    print(f"Zone/SFHA mismatches (A*/V* should be SFHA): {len(bad)}")
    print("\nEdges by fema_risk_level x fema_zone x zone_subtype:\n"
          + fema.value_counts(["fema_risk_level", "fema_zone", "zone_subtype"], dropna=False)
                .sort_index().to_string())
    unmapped = fema[fema["fema_risk_level"] == 0]
    if len(unmapped):
        print(f"FLAG: {len(unmapped)} edges at risk level 0 (unmapped zone/subtype or no zone):\n"
              + unmapped.value_counts(["fema_zone", "zone_subtype"], dropna=False).to_string())
    else:
        print("Risk level 0 (unmapped): none")

    plot = roads.to_crs(config.CRS_WGS).merge(fema, on="edge_id")
    fig, ax = plt.subplots(figsize=(14, 6))
    zones.clip(box(*config.BBOX)).plot(ax=ax, column="FLD_ZONE", alpha=0.25, legend=False)
    plot.plot(ax=ax, column=plot["fema_risk_level"].astype(str), cmap="YlOrRd", linewidth=0.5,
              legend=True, legend_kwds={"fontsize": 7, "loc": "lower left", "title": "fema_risk_level"})
    ax.set_title(f"FEMA risk level at edge midpoint ({fema['in_sfha'].mean():.0%} of edges in SFHA)")
    ax.set_aspect("equal")
    fig.savefig(config.CHECKS / "fema.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {config.CHECKS / 'fema.png'}")


if __name__ == "__main__":
    main()
