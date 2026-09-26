# CLAUDE.md — Flood-Aware Routing: Data Pipeline

## Project context

We're building a **flood-aware routing app for South Florida** at ShellHacks (team project, hackathon timeline).
App features: saved commutes, flood-risk alerts, alternate routes around flood-prone roads, a predictive flood model, and a dashboard.
Target challenges: Waymo Mobility, Auto Insurance, MongoDB Atlas, Microsoft.

**This repo's job:** build the `segments` dataset — every drivable road segment in our study area, enriched with static flood-risk features — and load it into MongoDB Atlas. The rest of the team (backend, routing, ML, frontend) builds on this collection.

- Study area: FIU (Modesto Maidique) → Downtown/Brickell, Miami
- Routing happens on the OSMnx/NetworkX graph in memory, NOT in Mongo. Mongo is for storage, geo queries, and the dashboard. Edge IDs must match between the two.
- Live weather (Open-Meteo / NWS) is NOT part of this pipeline. The backend combines it with `risk_score` at request time.

## How to work in this repo (read first, Claude)

1. **Work one layer at a time.** Do not build the whole pipeline in one pass. Finish a step, run it, show the verification output, then stop and wait for me before the next step.
2. **Before writing code for a step, briefly explain the approach** (data source, join method, output columns). Keep it short.
3. **Never invent dataset URLs or API endpoints.** If you can't confirm an endpoint works (e.g., the Miami-Dade 311 or stormwater layers), say so and ask me for the URL. Test every endpoint with a small request before building on it.
4. **Verify every layer:** print row counts, null counts, and value ranges, and save a quick plot (`matplotlib` or `folium` HTML) to `data/checks/`. Flag anything suspicious (all nulls, wrong units, points outside bbox).
5. Update the **Progress** checklist at the bottom of this file when a step is done.
6. Keep scripts small, readable, and rerunnable. Each script is idempotent: rerunning overwrites its own output only.

## Conventions

- Python 3.13 (3.13.3 is what's installed; all deps have Windows wheels, no conda needed), virtualenv in `.venv/`
- `config.py` holds the bbox, CRS values, and paths. Every script imports from it; no hardcoded paths.
  Paths are absolute `pathlib.Path` objects anchored at the repo root, so scripts work from any cwd.
- **Running scripts:** from the repo root, `python -m pipeline.01_roads` (digit-prefixed names work with `-m`; they only can't be `import`ed, and scripts never import each other). `python pipeline/01_roads.py` also works because every script starts with this bootstrap so `import config` resolves:
  ```python
  import sys; from pathlib import Path
  sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
  import config
  ```
- **CRS rule:** do distance/buffer math in `EPSG:32617` (UTM 17N, meters). Store and upload in `EPSG:4326`.
- **Key:** `edge_id = f"{u}_{v}_{key}"` from OSMnx. Every feature file is keyed on `edge_id`.
- **Graph recipe is frozen:** the graph comes only from `build_graph()` in `pipeline/01_roads.py`. **Any change to it (bbox, network_type, simplification, OSM data refresh) changes every `edge_id`.** That breaks routing (the team's `graph.graphml`) and Mongo (`_id`), and means rerunning every step from 01 onward. Coordinate with the team before touching it. OSMnx caches Overpass responses in `cache/` (gitignored), so reruns reproduce the same graph; deleting `cache/` fetches fresh OSM data and can change IDs.
- **Street key:** `street_id` groups the two directions of a two-way street (built in Step 1, see there). Features stay per `edge_id`; any dashboard or summary stat aggregates by `street_id`.
- Each feature layer writes `data/features/<layer>.parquet` with columns `edge_id` + its new columns only. Step 8 merges them.
- Raw downloads go in `data/raw/` and are never committed.
- Secrets live in `.env` (`MONGO_URI=...`), loaded with `python-dotenv`.

## Repo structure

```
data-pipeline/
├── CLAUDE.md
├── .env                  # MONGO_URI=...  (gitignored)
├── .env.example          # template for .env
├── .gitignore            # data/, .env, __pycache__/, .venv/, .ipynb_checkpoints/, cache/
├── cache/                # OSMnx Overpass response cache (gitignored)
├── requirements.txt
├── config.py
├── data/
│   ├── raw/              # downloads (DEM, NLCD, etc.)
│   ├── features/         # one parquet per layer
│   ├── processed/        # graph.graphml, roads.parquet, segments.parquet
│   └── checks/           # verification plots
├── pipeline/
│   ├── 01_roads.py
│   ├── 02_fema.py
│   ├── 03_elevation.py
│   ├── 04_impervious.py
│   ├── 05_311.py
│   ├── 06_drains.py
│   ├── 07_lowpoints.py   # optional
│   ├── 08_build_segments.py
│   └── 09_load_mongo.py
└── notebooks/checks.ipynb
```

## requirements.txt

```
osmnx>=2.0
geopandas
shapely>=2.0
pyproj
pyarrow
rasterio
rioxarray
rasterstats
py3dep
pysheds
requests
pandas
pymongo
python-dotenv
matplotlib
folium
ipykernel
```

Setup:
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
Or without activating: `.venv/Scripts/python -m pip install -r requirements.txt` (Windows).
If `rasterio`/`geopandas` fail to install on Windows, fall back to `conda install -c conda-forge geopandas rasterio` (not needed on 3.13.3 so far).

## config.py

```python
BBOX = (-80.39, 25.72, -80.18, 25.80)   # (west, south, east, north): FIU -> Brickell
CRS_WGS = "EPSG:4326"
CRS_UTM = "EPSG:32617"
from pathlib import Path
ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
FEAT = ROOT / "data" / "features"
PROC = ROOT / "data" / "processed"
CHECKS = ROOT / "data" / "checks"
```

---

## Build plan

### Step 0 — Scaffold
Create the folders, `.gitignore`, `requirements.txt`, `config.py`, `.env.example`. Set up the venv and install. Confirm imports work.

### Step 1 — Roads (base layer; everything depends on this)
- **Graph recipe, `build_graph()`, the only place the graph is built:**
  ```python
  G = ox.graph_from_bbox(BBOX, network_type="drive", simplify=False)   # osmnx 2.x: (west, south, east, north)
  G = ox.simplify_graph(G, edge_attrs_differ=["bridge", "tunnel"])
  ```
  - **Why not the default `simplify=True`:** simplification merged bridges with their ground-level approaches into one edge. The `bridge` tag then covered the whole edge: 340 of 361 "bridge" edges were merged from several OSM ways, their median length was 329 m, and only 56 of the 170 km flagged was actually bridge deck. Splitting wherever the bridge/tunnel tag changes makes every bridge edge 100% deck (checked against the unsimplified graph).
  - Side effect: this trims to the bbox *before* simplifying, while the one-step pull simplified first and then dropped whole edges that crossed the boundary. It keeps ~51 km more road, almost all within 1 km of the bbox edge (only +4 km deeper in).
  - ⚠️ Changing this recipe changes every `edge_id`; see Conventions.
- Save graph: `data/processed/graph.graphml` (routing team uses this)
- Edges → GeoDataFrame, `reset_index()`, build `edge_id`
- Cast `name`, `highway` to str (they're sometimes lists and break parquet)
- `is_bridge` / `is_tunnel` (bool): the OSM `bridge` / `tunnel` tag is present and not `"no"` (for a list, any such value). Values here: bridge = yes/movable/viaduct, tunnel = yes/building_passage.
  - The script prints tag value counts, lists bridge edges with mixed (list) tags or long lengths (> Q3 + 3·IQR) for spot-checking, and lists non-bridge edges > 50% on a bridge deck.
  - Current: 483 bridge edges (59.7 km), 0 mixed tags, 33 long ones (real long viaducts/causeways, 440 m–1 km), 41 tunnel edges. 2 non-bridge edges lie on a deck: `1994704076_3727834552_0` (NW 25th St, 20 m, 94%) and `5588347314_9259090961_0` (airport "Arrivals", 3 m, 100%). Both are tiny, likely ramps where a deck ends; left as-is.
  - Tunnels are flag-only: no score override, no exclusion.
- Output `data/processed/roads.parquet`: `edge_id, street_id, u, v, key, name, highway, length, is_bridge, is_tunnel, geometry`
- Edges are **directed** (MultiDiGraph): a two-way street is two edges (u→v and v→u) with separate `edge_id`s. Keep both — `edge_id` must match the routing graph. Features are computed per `edge_id`, no dedup.
- **`street_id` (geometry twin pairing):** edge u→v is paired with a reverse edge v→u when their Hausdorff distance in UTM is ≤ `config.TWIN_TOL_M` (1 m). The match must be one-to-one: each edge takes its closest candidate, and a pair only counts if both edges pick each other. Paired edges get `street_id = min(edge_id_a, edge_id_b)` (string comparison); unpaired edges get `street_id = edge_id`. So `street_id` is always a real `edge_id`.
  - Assertions: every `street_id` group has size 1 or 2, and every size-2 group is a u→v / v→u pair.
  - **Why not `f"{min(u,v)}_{max(u,v)}_{key}"`:** OSMnx keys aren't consistent across directions. On the earlier (pre-split) graph, with that rule, 88 pairs were two *separate* one-way carriageways joining the same nodes (e.g. SW 11th St, 7–10 m apart) wrongly merged into one street. Another 33 were parallel edges where u→v key 0 is the twin of v→u key 1, so each edge was paired with the wrong twin. The key rule also missed 14 real twins whose keys differ.
  - Pre-split graph: 13,060 pairs, 9,269 unpaired → 22,329 streets (key rule: 22,255; gap = +88 split carriageways − 14 recovered cross-key twins = +74).
  - Current (split graph): 13,208 pairs (max Hausdorff 0.0000 m, none between 0.1 and 1 m), 9,955 unpaired edges → 23,163 physical streets.
- List-valued OSM tags are joined with `;` (e.g. `residential;tertiary`); missing names stay null (not `"nan"`).
- **Verify:** edge count (expect ~36.4k edges ≈ ~23.2k physical streets; ~73% of edges have a reverse twin), plot of the network

### Step 2 — FEMA flood zones
- FEMA NFHL ArcGIS REST service, Flood Hazard Zones layer:
  `https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query`
  First confirm layer 28 is "Flood Hazard Zones" via `.../MapServer/28?f=json`.
- Query by envelope: `geometry=BBOX`, `geometryType=esriGeometryEnvelope`, `inSR=4326`, `outSR=4326`, `outFields=FLD_ZONE,ZONE_SUBTY,SFHA_TF`, `f=geojson`. Paginate with `resultOffset` / `resultRecordCount` (+ `orderByFields=OBJECTID`). **Use pages of 100**: 1000-record pages return HTTP 500 (polygons are large). ~679 zones in bbox, ~20 s total.
- Build GeoDataFrame via `gpd.GeoDataFrame.from_features(...)`, cache to `data/raw/fema_zones.geojson`
- Join on edge midpoint (`geometry.interpolate(0.5, normalized=True)`) with `sjoin`
- Output `features/fema.parquet`: `edge_id, fema_zone, zone_subtype, in_sfha` (bool), `fema_risk_level` (int8)
  - `zone_subtype`: raw `ZONE_SUBTY`, null where empty. Null for all A*/V* zones here; only zone X has a subtype.
  - `fema_risk_level`: 3 = SFHA (`SFHA_TF == 'T'`: AE/AH/VE here), 2 = X with `0.2 PCT ANNUAL CHANCE FLOOD HAZARD`, 1 = X with `AREA OF MINIMAL FLOOD HAZARD`, 0 = anything else or unmapped (e.g. D, levee-reduced, no zone). The script prints the zone × subtype combinations and flags any level-0 edges. Subtype strings are matched exactly, so a new variant falls to 0 and gets flagged rather than guessed.
  - Current bbox: level 3 = 10,142 edges, level 2 = 3,150, level 1 = 23,079, level 0 = none.
  - FEMA describes the ground, so bridge decks get the zone underneath. Raw features are left as-is; Step 8 caps the score instead.
- **Verify:** zone value counts, map colored by zone

### Step 3 — Elevation
- `py3dep.get_dem(BBOX, resolution=10)` → save `data/raw/dem.tif` with rioxarray
- Sample ~5 evenly spaced points per edge with rasterio (`src.sample`)
- Output `features/elevation.parquet`: `edge_id, elev_mean, elev_min` (meters)
- **Verify:** value range is plausible for Miami (roughly 0–5 m, no huge negatives or nodata values), map colored by `elev_min`

### Step 4 — % impervious
- **Manual step (me):** download NLCD Impervious (latest year), clipped to bbox, from the MRLC Data Viewer → `data/raw/nlcd_imperv.tif`. Ask me if it's not there.
- Buffer edges 15 m in UTM, reproject buffers to the raster's CRS (NLCD is Albers, EPSG:5070 — read it from the file, don't assume)
- `rasterstats.zonal_stats(..., stats=["mean"])`
- Output `features/impervious.parquet`: `edge_id, pct_impervious`
- **Verify:** range 0–100, downtown/Brickell should be high

### Step 5 — 311 flood reports
- Source: Miami-Dade and/or City of Miami 311 service requests (open data portals). **Ask me for the dataset URL** if not confirmed.
- First list distinct request types and show them to me. We pick the flood-related ones together (flooding, standing water, drainage, etc.).
- Filter to bbox, build points, `gpd.sjoin_nearest` in UTM with `max_distance=30`
- **Exclude `is_bridge` edges from the snapping candidates**, so ground-level reports attach to ground-level roads, not decks overhead. Bridge edges get 0 reports.
- Output `features/reports_311.parquet`: `edge_id, flood_reports_311` (count)
- **Verify:** folium map of the report points plus count histogram

### Step 6 — Drain density
- Source: Miami-Dade GIS stormwater inlets / catch basins layer. **Ask me for the URL**; if it doesn't exist or is unusable, skip this step and tell me.
- Count inlets within 50 m of each edge (UTM), normalize: `drains_per_100m = count / length * 100`
- **Exclude `is_bridge` edges from counting**: drains under a deck aren't on the deck. Bridge edges get 0.
- Output `features/drains.parquet`
- **Verify:** map of inlets over roads

### Step 7 — Low-point depth (OPTIONAL — only if time allows)
- pysheds: `fill_pits` → `fill_depressions` on `dem.tif`; `depth = filled - dem`; save `data/raw/sink_depth.tif`
- Sample max depth along each edge
- Output `features/lowpoints.parquet`: `edge_id, sink_depth`

### Step 8 — Build segments
- Merge `roads.parquet` with every file in `data/features/` on `edge_id` (left join; must work with any subset of layers present). Carry `street_id` through.
- Fill missing counts with 0; leave missing continuous values as null
- `risk_score` (0–1): min-max normalize and weight whichever features exist, e.g. `fema_risk_level` (not `in_sfha`, which stays a column only), low `elev_min`, `flood_reports_311`, `pct_impervious`, `sink_depth`. Weights in `config.py` so we can tune them. Placeholder until the ML model replaces it.
  - Implemented as: each feature in `config.RISK_WEIGHTS` that is present is scaled to 0–1 (min-max, or the fixed range in `config.RISK_FIXED_RANGE`: `fema_risk_level` uses 0–3, so minimal-hazard X = 0.33), and features in `config.RISK_INVERT` (`elev_min`) are flipped. The score is the weighted mean, with weights renormalized over the features each row has non-null.
  - Zero-filled columns: `config.FILL_ZERO`. Top-20 check groups by `street_id`.
- **Bridge cap (final override):** for `is_bridge` edges, `risk_score = min(risk_score, config.BRIDGE_RISK_CAP)` (0.1) and `score_note = "bridge_capped"`; `score_note` is null otherwise. Only `risk_score` changes; raw features (FEMA, elevation, …) stay untouched. Tunnels: flag only.
- Output `data/processed/segments.parquet` (+ `score_note`)
- **Verify:** map colored by `risk_score`; top 20 riskiest segments by name. Sanity check with me.

### Step 9 — Load to MongoDB Atlas
- **Manual step (me):** create the cluster, put `MONGO_URI` in `.env`, allow my IP
- Reproject to 4326; one doc per segment: `_id = edge_id`, `street_id`, `u, v, key`, `geometry` (GeoJSON LineString via `__geo_interface__`), all feature columns
- Clean geometries with `shapely.remove_repeated_points` (duplicate vertices break 2dsphere)
- Cast numpy types to plain Python; NaN → None
- `delete_many({})` then `insert_many`, then `create_index([("geometry", "2dsphere")])` and `create_index("street_id")`
- **Verify:** doc count matches parquet; run a `$near` query around FIU and a `$geoWithin` query around Brickell

---

## Priority / timeline

1. **First ~2 hours:** Steps 0, 1, 2, 8, 9 with other columns null. Publish so backend/frontend can start.
2. Then Steps 3 → 5 → 4 → 6, rerunning 8 and 9 after each lands.
3. Step 7 last, only if ahead.

## Progress

- [x] Step 0 — Scaffold
- [x] Step 1 — Roads
- [x] Step 2 — FEMA
- [ ] Step 3 — Elevation
- [ ] Step 4 — Impervious
- [ ] Step 5 — 311 reports
- [ ] Step 6 — Drains
- [ ] Step 7 — Low points (optional)
- [x] Step 8 — Build segments (FEMA only so far; rerun after each new layer)
- [ ] Step 9 — Load to Atlas
