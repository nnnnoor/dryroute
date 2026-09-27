"""Build fixtures/fiu_buildings.json: FIU building codes (as used in class locations, e.g. "PC 213")
-> building name + a point inside the building, from OpenStreetMap (`short_name` tag).

Run from backend/ (needs internet; the output is committed, so the backend itself never fetches):
    .venv/Scripts/python scripts/build_fiu_buildings.py
"""
import json
from pathlib import Path

import osmnx as ox

FIU_AREA = (-80.395, 25.745, -80.360, 25.771)  # (west, south, east, north): MMC + Engineering Center
OUT = Path(__file__).resolve().parents[1] / "fixtures" / "fiu_buildings.json"

# FIU's own code -> the (misspelled or nonstandard) short_name OSM uses for that building
ALIASES = {
    "AHC2": "AHCS2", "AHC3": "AHCS3", "AHC4": "AHCD4", "AHC5": "ACH5",
    "PG1": "PGG",  # Gold Parking Garage
    "PG2": "PGB",  # Blue Parking Garage
    "PG3": "PGP",  # Panther Parking Garage
}


def main():
    b = ox.features_from_bbox(FIU_AREA, tags={"building": True})
    b = b[b["short_name"].notna() & b["name"].notna()]
    points = b.geometry.representative_point()
    buildings = {}
    for code, name, p in sorted(zip(b["short_name"], b["name"], points), key=lambda t: t[0]):
        code = code.strip().upper()
        if code not in buildings:  # a few buildings are mapped as several parts; keep the first
            buildings[code] = {"name": name, "lat": round(p.y, 6), "lon": round(p.x, 6)}
    for fiu_code, osm_code in ALIASES.items():
        if osm_code in buildings:
            buildings[fiu_code] = buildings[osm_code]
    OUT.write_text(json.dumps(buildings, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(buildings)} codes -> {OUT}")


if __name__ == "__main__":
    main()
