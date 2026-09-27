# CLAUDE.md — Flood-Aware Routing: Data Pipeline

## Project context

**Focus (2026-09-26): FIU students driving to Modesto Maidique Campus** (flooded routes + where to park). See Step 12. Open issue for the team: the bbox ends ~0.5 km west of campus and at SW 40th St, so commutes from Kendall/Doral/Sweetwater/Westchester start outside it; expanding it changes every `edge_id`.

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
- Raw downloads go in `data/raw/` and are never committed. Only `data/processed/` is committed (the team uses `graph.graphml` / `segments.parquet` from git); `data/features/` and `data/checks/` are regenerated.
- Secrets live in `.env` (`MONGO_URI=...`), loaded with `python-dotenv`.

## Repo structure

```
data-pipeline/
├── CLAUDE.md
├── .env                  # MONGO_URI=...  (gitignored)
├── .env.example          # template for .env
├── .gitignore            # .env, data/raw|features|checks/, __pycache__/, .venv/, .ipynb_checkpoints/, cache/
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
│   ├── 09_load_mongo.py
│   ├── 10_closures.py    # live closures -> Mongo `closures` (rerun often)
│   ├── 11_flood_criteria.py
│   └── 12_fiu_parking.py  # FIU MMC lots/garages + ponding hotspots (1 m DEM) -> Mongo
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
`data/processed/` is committed; `data/raw|features|checks/`, `cache/` and `.venv/` are not, so only someone changing the pipeline reruns 01 → 02 → 08 (→ 09). Rebuilt on a second machine: identical counts (Step 1 + FEMA levels), so edge_ids are stable while OSM is unchanged.
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
  - **Sampling (approved change from "~5 points"):** one sample every `config.ELEV_SAMPLE_M` (10 m, the DEM resolution) along the edge, minimum 5, both ends included, so long edges don't skip dips (463k samples, median 10/edge). `dem.tif` is downloaded once (~2 min); delete it to refresh. DEM is EPSG:5070 (read from file), meters NAVD88, bare earth.
  - Output adds **`elev_p10`**: 10th percentile of the edge's samples (linear interpolation). **Scoring uses `elev_p10`, not `elev_min`** (Step 8). `elev_min` stays as a column. Reason: min takes the single lowest pixel, so edges ending at a bridge picked up water pixels (~−0.5 m) and topped the ranking.
  - **Water-only rule:** a non-bridge edge whose samples are *all* < 0 m sits on water in the DEM: `elev_p10` = null (elevation not scored; weights renormalize to FEMA) and `elev_water_only` = True; Step 8 sets `score_note = "elev_water_only"`. Currently catches **0 edges** (6 bridge edges are all-water, unaffected). Grove Isle Dr doesn't qualify: 2 of its 5 samples are −0.32, 3 are +0.96. Decision: keep the 0.5 m floor and leave the remaining top ties (incl. Grove Isle Dr, E Fairview St) to Step 5 (311 reports).
  - Current: 0 nulls, 0 nodata. `elev_min` median 2.17 m (1st–99th pct 0.58–4.84); `elev_p10` median 2.20 (0.68–4.93). Pattern matches the Miami Rock Ridge (high through Brickell/Grove/Gables; low around FIU, airport, Miami River). Median `elev_min` falls with FEMA risk: X-minimal 2.62, X-0.2% 1.96, AH 1.64, AE 1.12, VE 0.73.
  - Outliers, left as-is: 31 edges with `elev_mean` > 10 m (max 12.8) are all motorway embankments/interchange ramps (real fill). 23 edges with `elev_min` < 0: 19 are bridges reading water (~−0.5, capped in Step 8), 4 are Grove Isle approach edges. With p10, Fair Isle St drops to street rank 83, but Grove Isle Dr (12.6 m edge, all 5 samples on water, p10 −0.32) and E Fairview St / S Bayshore Ln (bayfront, p10 0.09) still score 1.0.

### Step 4 — % impervious
> **SKIPPED for now (decided 2026-09-26).** Low value here: nearly the whole study area is heavily paved, so it barely separates roads, and it overlaps with the city 311 signal. Needs a manual NLCD download. `risk_score` renormalizes over present features, so nothing depends on it. Revisit if the ML model wants it as an input feature.

- **Manual step (me):** download NLCD Impervious (latest year), clipped to bbox, from the MRLC Data Viewer → `data/raw/nlcd_imperv.tif`. Ask me if it's not there.
- Buffer edges 15 m in UTM, reproject buffers to the raster's CRS (NLCD is Albers, EPSG:5070 — read it from the file, don't assume)
- `rasterstats.zonal_stats(..., stats=["mean"])`
- Output `features/impervious.parquet`: `edge_id, pct_impervious`
- **Verify:** range 0–100, downtown/Brickell should be high

### Step 5 — 311 flood reports
- Source: Miami-Dade and/or City of Miami 311 service requests (open data portals). **Ask me for the dataset URL** if not confirmed.
- First list distinct request types and show them to me. We pick the flood-related ones together (flooding, standing water, drainage, etc.).
  - **Endpoints confirmed 2026-09-26** (resolved via the ArcGIS items API, tested):
    - City of Miami, "City of Miami 311 Service Requests Since 2015" (owner CityMiamiFL): `https://services1.arcgis.com/CvuPhqcTQpZPT9qY/arcgis/rest/services/City_of_Miami_311_Service_Requests_Since_2015/FeatureServer/0`. Points; bbox envelope query works. 59,946 in bbox, but created dates only 2022-10-01 → 2024-08-10 despite the title. `issue_type` is a code (e.g. `COMPWSF`), meaning in `issue_Description`.
    - Miami-Dade County, yearly: `https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/data_311_{YEAR}/FeatureServer/0` for 2013–2023 (owner MDPublisher). **Tables, no geometry**: filter on `latitude`/`longitude` in `where` (envelope is ignored). ~37k–46k in bbox per year. `issue_type` is plain text. No 2024+ service exists.
    - Unusable: MDC `311_Service_Request/FeatureServer/0` exposes only `OBJECTID`.
    - Location/CRS (checked on samples): city geometry is Web Mercator (wkid 102100), returned as 4326 with `outSR=4326`; both sources carry `latitude`/`longitude` (WGS84) and State Plane FL East **EPSG:2236 ft** X/Y (`x_coordinate`/`y_coordinate` city, `sr_xcoordinate`/`sr_ycoordinate` county), matching lat/lon projected to 2236 within 0.2 ft. Use lat/lon. Missing/zero lat-lon: city 39 of 88k; county 2,292 (2023) / 2,493 (2022), of which only 87 / 70 fall in the bbox via their X/Y (recoverable, small).
    - Possible coverage gap (unverified): other municipalities in the bbox (Coral Gables, Sweetwater, West Miami) may route drainage complaints to their own systems. Check the spatial spread of the chosen types before trusting counts there.
  - Type counts in bbox saved to `data/checks/311_request_types.csv` (all 295 types, city vs county) and `data/checks/311_city_types.csv` (city code → description).
- Filter to bbox, build points, `gpd.sjoin_nearest` in UTM with `max_distance=30`
- **Exclude `is_bridge` edges from the snapping candidates**, so ground-level reports attach to ground-level roads, not decks overhead. Bridge edges get 0 reports.
- Output `features/reports_311.parquet`: `edge_id, flood_report_days, drain_issue_days, jurisdiction` (see Implemented)
- **Verify:** folium map of the report points plus count histogram
- **Implemented (types/years chosen 2026-09-26; all settings in `config.py`):**
  - Scored → `flood_report_days`: city `COMPWSF` (STORM FLOOD/ DRAINAGE), county `FLOODING / STANDING WATER - LOCALIZED`. Not scored → `drain_issue_days`: county `DRAIN CLOGGED / CLEANING`, `DRAIN - REPAIR`, `RER DRAINAGE CANALS FLOOD COMPLAINT`, `CANAL - BLOCKED`. Everything else excluded (swale grading, missing cover, cave-in, drain tops cleaned, ...).
  - Years: since 2022-01-01. County `data_311_2022` + `2023`; `data_311_2024` exists but needs a token (not public), 2025+ don't exist. City as-is (2022-10 → 2024-08); its service has one layer and no tables, so no older city records anywhere.
  - Fetch only the chosen types, server-side filtered to the bbox by lat/lon, or by State Plane X/Y (EPSG:2236) where lat/lon is missing (recovered 0 this time). Raw → `data/raw/311_reports_raw.parquet`.
  - Snap to non-bridge edges, `sjoin_nearest` in UTM, `max_distance=30`. Equidistant edges (mostly two-way twins) all get the report (1.59 edges/report).
  - Counts are **distinct dates** (America/New_York) per edge, not rows.
  - `jurisdiction` per edge: `city` if the edge midpoint is inside the City of Miami boundary (CityMiamiFL `City_Boundary/FeatureServer/0`, cached to `data/raw/city_of_miami_boundary.geojson`), else `county`.
  - Scoring (Step 8, `config.RISK_PCT_WITHIN`): 0 days → 0; > 0 → percentile rank (`rank(method="average", pct=True)`) among the jurisdiction's edges with > 0. Reason: the city logs far more reports per road (city edges max 9 days, county max 3).
  - Outputs: `features/reports_311.parquet` (`edge_id, flood_report_days, drain_issue_days, jurisdiction`); `processed/flood_reports.parquet` (one row per report-edge link: `edge_id, date, source, type, category, ticket_id, snap_m`) for the ML model; checks `311_reports.html` (folium, flood reports by source + city boundary), `311_counts.png`.
  - Current: 2,807 reports fetched, all in bbox; 2,484 snapped (323 > 30 m dropped, median snap 19 m). Snapped / distinct dates: city COMPWSF 1,887 / 381; county flooding 121 / 74; county drain types 476 total. Dates 2022-01-19 → 2024-08-09. Edges with ≥ 1 flood day: city 1,909 of 16,514, county 151 of 19,857. Bridge edges: 0 (asserted).
  - Effect: only 1 street at the max (was 24). E Fairview St / S Bayshore Ln #1 (5 flood days); Grove Isle Dr (0 reports) fell to #327. Top 20 is mostly City of Miami streets with reports + AE zone + low ground (NW 10th Ave, W Glencoe St, NE 22nd Ter, NE 23rd St, SW 9th St, ...).

### Step 6 — Drain density
- Source: Miami-Dade GIS stormwater inlets / catch basins layer. **Ask me for the URL**; if it doesn't exist or is unusable, skip this step and tell me.
- Count inlets within 50 m of each edge (UTM), normalize: `drains_per_100m = count / length * 100`
- **Exclude `is_bridge` edges from counting**: drains under a deck aren't on the deck. Bridge edges get 0.
- Output `features/drains.parquet`
- **Verify:** map of inlets over roads
- **Implemented 2026-09-26:**
  - Source: Miami-Dade "Stormwater Point" (MDPublisher; Hub page gis-mdc.opendata.arcgis.com/datasets/MDC::stormwater-point) → `https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/StormWaterPoint_gdb/FeatureServer/0`. Points, 193k total, 43.8k in bbox. Covers the City of Miami too (17.5k city-maintained points); CityMiamiFL publishes no stormwater layer.
  - Counted types: `config.INLET_TYPES` = CATCH BASIN, RISER CATCH BASIN, YARD DRAIN (surface intakes). Excluded: MANHOLE (6.8k), CROSS SECTION (2.9k canal survey points), DRAINAGE WELL, VERTICAL FRENCH DRAIN, structures, pumps, valves, etc. All maintainers kept (incl. ~6.4k private).
  - 33,088 fetched, 33,008 after dropping empty/duplicate points; cached to `data/raw/stormwater_inlets.geojson`. 27,569 lie within 50 m of a non-bridge edge.
  - Output `features/drains.parquet`: `edge_id, drain_count` (inlets within `config.DRAIN_BUFFER_M` = 50 m), `drains_per_100m` (= count / length × 100). Bridge edges: 0 (asserted via printout).
  - **Not scored** (not in `RISK_WEIGHTS`): direction is ambiguous, and coverage is uneven. Dense in Doral/Sweetwater/FIU/airport, sparse in Coral Gables, Coconut Grove and south Little Havana (swale/french-drain areas or uninventoried), so **0 means "none inventoried", not "no drainage"**. Columns are there for the ML model and dashboard.
  - Current: 11,961 edges with 0; median `drain_count` 3, `drains_per_100m` 3.4 (city 3.39 vs county 3.20 on edges ≥ 20 m). Caveat: `drains_per_100m` inflates on short edges (median 10.4 for < 20 m vs 3.3; max 1,602) because the 50 m buffer's round ends dominate; prefer `drain_count` or filter by length.

### Step 7 — Low-point depth (OPTIONAL — only if time allows)
- pysheds: `fill_pits` → `fill_depressions` on `dem.tif`; `depth = filled - dem`; save `data/raw/sink_depth.tif`
- Sample max depth along each edge
- Output `features/lowpoints.parquet`: `edge_id, sink_depth` (max along edge), `sink_p90` (90th percentile; used for scoring)
- **Implemented 2026-09-26:** pysheds 0.5 `fill_pits` → `fill_depressions` on `dem.tif` (~3 min); `depth = filled − dem` → `data/raw/sink_depth.tif` (same grid). Sampled with Step 3's sampler (every `ELEV_SAMPLE_M` = 10 m, min 5; the function is duplicated since scripts don't import each other). Check plot: `data/checks/lowpoints.png`.
  - Raster: 22.8% of cells > 0.05 m deep, 2.8% > 1 m, max 11.5 m (lakes/quarry pits in the west, flat-water surfaces inside banks). East: a branching network of closed lows across the Rock Ridge (Coral Way / Silver Bluff / Shenandoah), likely old drainage paths with no surface outlet; elevation alone misses this since the area is relatively high.
  - Edges: `sink_depth` median 0.08 m, 95th pct 0.61, max 2.91; 25.7% are 0. Bridges median 0.09 (not reading deep water).
  - **Scoring uses `sink_p90`, fixed range (0, 1.0) m** (`config.RISK_FIXED_RANGE`), same reasoning as `elev_p10`: max picks up single deep pixels (e.g. SW 12th St max 2.14, p90 0.50), and a data min/max (~2.9 m) would squeeze most edges. Component: median 0.053, 29% at 0, 1.1% at 1.
  - Effect (FEMA + elevation + 311 + sinks): risk_score median 0.32, max 0.957, 1 street at max. Top 20 shifts toward Brickell/downtown (SW 9th St #1, NW 18th Ct, SW 1st Ave, NE 23rd St, SW 2nd St, SW 10th St, ...); E Fairview St now #16.

### Step 8 — Build segments
- Merge `roads.parquet` with every file in `data/features/` on `edge_id` (left join; must work with any subset of layers present). Carry `street_id` through.
- Fill missing counts with 0; leave missing continuous values as null
- `risk_score` (0–1): min-max normalize and weight whichever features exist, e.g. `fema_risk_level` (not `in_sfha`, which stays a column only), low `elev_min`, `flood_reports_311`, `pct_impervious`, `sink_depth`. Weights in `config.py` so we can tune them. Placeholder until the ML model replaces it.
  - Implemented as: each feature in `config.RISK_WEIGHTS` that is present is scaled to 0–1 (min-max, or the fixed range in `config.RISK_FIXED_RANGE`: `fema_risk_level` uses 0–3, so minimal-hazard X = 0.33), and features in `config.RISK_INVERT` (`elev_min`) are flipped. The score is the weighted mean, with weights renormalized over the features each row has non-null.
  - Zero-filled columns: `config.FILL_ZERO`. Top-20 check groups by `street_id`.
  - Elevation enters as **`elev_p10`**, inverted, on the **fixed range (0.5, 5.0) m** (`config.RISK_FIXED_RANGE`): ≤ 0.5 m → 1, ≥ 5 m → 0, clipped. A data min/max would be stretched to ~12 m by motorway embankments, squeezing normal roads into part of the scale.
  - The script prints each scaled component's min/median/max and share at 0/1 and saves `data/checks/risk_components.png` (histograms). Current: `elev_p10` component spans 0–1 (0.9% at 0, 0.5% at 1, median 0.62, bimodal: Rock Ridge vs lowlands); `fema_risk_level` is 0.33/0.67/1.
  - Current (FEMA + elevation): 21,263 distinct scores, median 0.46. 24 streets tie at 1.0 (SFHA and `elev_p10` ≤ 0.5 m, i.e. clipped by the fixed range; 35 AE + 2 VE edges): bayfront Brickell Ave / SE 12th Ter / SE 13th St, NE 23rd St, NW North River Dr, Stadium Dr, etc. Top-20 ties are broken by length only.
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
  - Target: db `flood`, collection `segments` (`config.MONGO_DB` / `MONGO_COLLECTION`; the URI has no db name). `python -m pipeline.09_load_mongo --ping` only tests the connection.
  - `.env` key must be `MONGO_URI`. Paste the password **without** the `< >` from Atlas's `<db_password>` placeholder (that caused `bad auth`).
  - Current: 36,371 docs, 0 geometries changed by `remove_repeated_points`, all LineString. `$near` FIU (300 m) → East Campus Circle / University Drive; `$geoWithin` Brickell box → 341 edges, 247 streets. ~17 s total.
  - **Graph check:** before deleting anything, the script asserts `segments.parquet` has exactly the `edge_id`s of `graph.graphml` (rebuilt as `f"{u}_{v}_{key}"`), and after inserting asserts the Mongo `_id` set equals them. It also reports the load it is replacing vs the current graph (overlap, and how many of today's 483 bridge edge_ids it had: a pre-bridge-split load would lack most). 2026-09-26: previous load had the identical 36,371 ids (483/483 bridges), so no Atlas load ever predated the split.

### Step 10 — Road closures / construction (for routing, not risk)
Added 2026-09-26 (user request: "active road closures"). Time-varying, so it is **not** merged into `segments` and does **not** touch `risk_score`. It writes its own Mongo collection and is rerun independently (FL511: every ~15 min; the rate limit is ~10 calls/min).
- **Sources:**
  - **FL511** (FDOT traveler info), `https://fl511.com/api/v2/get/event?key=...&format=json`: the standard 511-platform events API (same schema as 511GA/511NY). Confirmed live: it returns `Invalid Key` without a key. Needs **`FL511_API_KEY` in `.env`** (free developer key from an FL511 account); **skipped with a message until then**. Keeps `EventType` in `config.FL511_EVENT_TYPES` (roadwork, closures). Fields used: `ID, EventType, IsFullClosure, DirectionOfTravel, StartDate/PlannedEndDate` (unix s), `LanesAffected, RoadwayName, Description, Organization, Severity`, geometry from `EncodedPolyline`, else primary→secondary lat/lon line, else a point. **Not yet run against live data** (no key). `--selftest` checks decode/parse/filter/matching offline.
  - **Miami-Dade Utility Coordination** (MDPublisher, 21 layers in `config.UCC_LAYERS`, shared schema `PRJNAME, PROJECTID, AGCYNAME, AGYPRJSTAT, GENPRJSTAT, STARTDATE, ENDDATE`): planned work zones, a weak proxy for closures. Kept: `GENPRJSTAT == 'Construction'`, `AGYPRJSTAT` not Const.Complete/Closed, `ENDDATE >= now`, window ≤ `UCC_MAX_WINDOW_DAYS` (730). **FDOT excluded** (`UCC_EXCLUDE_AGENCIES`): its windows are fiscal years (Jul 1 → Jun 30, median ~7 yr) and its real closures come from FL511. Excluded layers: Canal, Moratorium (no-cut rule, not a closure), and two with a different schema.
  - Checked and not usable: no Miami-Dade / City of Miami road-closure layer exists on ArcGIS Online; Waze for Cities needs a partnership.
- **Matching (UTM):** lines/polygons buffered `CLOSURE_BUFFER_M` (20 m), edge kept if ≥ `CLOSURE_MIN_OVERLAP` (50%) of its length is inside; points take the nearest edge(s) within `CLOSURE_SNAP_M` (30 m), ties within 1 m. Directional events (Northbound…) keep only edges heading within ±`CLOSURE_BEARING_TOL` (60°). Bridges are included (closures apply to decks).
- **Output:** `data/processed/closures.parquet` (gitignored) and Mongo `flood.closures`, replaced each run, one doc per (closure, edge): `_id = "<closure_id>|<edge_id>"`, `edge_id, closure_id, source` (fl511|county_ucc), `kind` (closure|roadwork|planned_construction), `full_closure, direction, start, end` (UTC; `end` null = open-ended), `name, description, lanes_affected, agency, status`. Indexes: `edge_id`, `(start, end)`.
- **Backend use:** active = `{"start": {"$lte": now}, "$or": [{"end": {"$gte": now}}, {"end": null}]}`. Suggested: `full_closure` → drop the edge from the routing graph; roadwork / planned_construction → a time penalty.
- **Current (county only):** 672 construction-phase records in bbox → 10 kept (124 FDOT, 66 complete/closed, 552 ended, 232 windows > 2 yr; overlapping) → 5 projects on 26 edges (2.6 km): NW 62 Ave, SW 1st St water main, SW 114th Ave, SW 11 St, NW 57 Ct. Each matched its named street plus short cross-street stubs at the ends.

### Step 11 — County Flood Criteria freeboard (feature, not scored)
Added 2026-09-26 (GIS gap list item: groundwater / design flood level).
- **Source:** Miami-Dade "County Flood Criteria 2022", the minimum required elevation (ft NAVD88) of developed land and **road crowns** for a 10-yr / 24-hr storm in the **2060 sea-level-rise** scenario (effective 2022-10-28). Raster: official zip `https://giswspro.miamidade.gov/opendata/flood/2022/CountyFloodCriteria.zip` (linked from the ArcGIS item "County Flood Criteria 2022 - Raster", MDPublisher). File Geodatabase raster, EPSG:2236 (US ft), 100 ft cells; GDAL 3.12 reads it directly (`rasterio.open(".../CountyFloodCriteria.gdb")`). Cached in `data/raw/flood_criteria/`.
  - Validated against the county contour layer `CountyFloodCriteria_gdb/FeatureServer/0` (13 lines, `ELEV` 7–19 ft in bbox): the raster reads 7.16 / 11.00 / 16.88 ft on the 7 / 11 / 16 ft contours. Bbox values 6–20 ft (median 7.3 ft = 2.24 m); ~10% of cells are nodata (water).
- **Method:** sample the criteria raster and `dem.tif` at the same points (Step 3 sampler), convert US ft → m, freeboard = ground − criteria per point.
- **Output** `features/flood_criteria.parquet`: `flood_criteria_m` (mean), `freeboard_p10_m` (10th pct of ground − criteria; negative = below the 2060 design level), `below_criteria_frac`. Check plot `data/checks/flood_criteria.png`.
- **Current:** 2 nulls (Venetian Way bridge over the bay). Median freeboard −0.37 m; 94% of non-bridge edges are partly below (the criteria is a *new-construction 2060* standard, so the magnitude is the signal, not below/above). Median by FEMA level 1/2/3: −0.31 / −0.44 / −0.47. Extremes: < −2 m on 282 non-bridge edges (Miami River-front roads picking up the dredged channel in the DEM, like Step 3's water pixels) + 127 bridges; up to +7.5 m on embankments/overpasses.
- **Signal check** (AUC for "edge has ≥ 1 flood report day", non-bridge): all edges: freeboard 0.615 vs `elev_p10` 0.531; within city 0.587 vs 0.583, within county 0.659 vs 0.653. So most of the overall gain is between jurisdictions; within one it's about as strong as elevation, and only partly overlapping (corr 0.46). **Not scored** in `risk_score` (would double count elevation); an ML feature.

### Step 12 — FIU parking + campus ponding hotspots (1 m DEM)
Added 2026-09-26. **Project focus: students driving to FIU Modesto Maidique Campus (MMC).** Terrain-based exposure: 311 does **not** cover campus (FIU runs its own grounds; 0 reports on the 225 campus-adjacent edges, 26 within 1 km), so nothing here is observed flooding.
- **Inputs:** OSM campus polygon (`amenity=university`, name `config.FIU_NAME`, 1.41 km²) and 46 on-campus parking polygons (38 surface, 7 garages = `parking=multi-storey`, 1 street-side); **1 m USGS 3DEP** bare-earth DEM (available here; checked with `py3dep.check_3dep_availability`) for campus + 300 m → `data/raw/fiu_dem_1m.tif`; FEMA zones (Step 2); flood criteria raster (Step 11); `segments` risk for access roads.
- **Water handling (important):** hydro-flattened lake/canal surfaces sit at 0.1–0.4 m, land from ~0.9 m (histogram trough 0.6–0.8 m). Water = DEM < `FIU_WATER_MAX_ELEV_M` (0.7) ∪ OSM water polygons, components ≥ 20 m² (3.9% of the DEM). Depression fill is done with `skimage.morphology.reconstruction` with **the raster edge and water cells as outlets**, and depth is 0 on water. Without this, lakes filled to their banks and showed as 1.5–2 m "hotspots" (first run: 23% of campus). FIU's lakes are stormwater retention tied to the canals, so treating them as drains is the realistic choice. (Step 7's 10 m sink map does not do this; lakes there show as deep sinks.)
- **Per lot** (`data/processed/fiu_parking.geojson`, Mongo `flood.parking`, `_id` = OSM id): `elev_p10`, `elev_median`, `pond_frac` (share of 1 m cells ponding ≥ 0.10 m), `pond_depth_p90`, `pond_depth_max`, `flood_criteria_m`, `freeboard_p10_m`, `fema_zone`, `in_sfha`, access roads (non-bridge drive edges within 25 m; interior lots whose aisles aren't in the drive graph take the nearest edge within 200 m: `access_method` adjacent|nearest), `access_risk_min/max`, `access_roads`, and **`parking_score`** = weighted mean of fixed-range components (`config.PARKING_WEIGHTS/RANGE`: pond_frac 0.35 [0–0.5], pond_depth_p90 0.20 [0–0.5 m], freeboard 0.20 [+0.5 → −1.0 m], access_risk_min 0.25). Garages: the bare-earth DEM reads the ground under them, i.e. their ground-level entrance exposure (upper decks stay dry).
- **Hotspots** (`data/processed/fiu_hotspots.geojson`, Mongo `flood.fiu_hotspots`): connected areas with depth ≥ 0.15 m and ≥ 50 m², minus those > 50% under an OSM building footprint (the DEM interpolates under buildings; 4 dropped). Fields: `area_m2, depth_max_m, depth_mean_m, lots, roads, building_frac`.
- **Current:** no nulls; `parking_score` 0.11–0.74 (median 0.33). Garages median 0.17 (all 0.11–0.25; Gold and Blue best, 0.11/0.12), surface lots median 0.36. Worst: North of University Towers (0.74, 85% of the lot ponds), Arena Loading Area, an unnamed lot off SW 11th St, W10, W7, Greek Housing. 166 hotspots, 123,890 m² (~9% of campus), max depth 0.96 m, mostly lawns/courtyards; 41 touch lots (largest: Greek Housing lot, 6,236 m², 0.59 m), 12 touch roads (SW 109th Ave, SW 112th Ave, SW 14th St, East Campus Circle, University Drive).
- **Bug fixed during the build:** lot stats were first assigned by index onto a filtered frame (misaligned rows); the lot frame is now `reset_index(drop=True)` before positional joins.
- **Limits:** depressions measure storage before spill, not flood depth (exfiltration/french drains remove water); no campus ground truth; OSM lot outlines/names may lag reality.

---

## Priority / timeline

1. **First ~2 hours:** Steps 0, 1, 2, 8, 9 with other columns null. Publish so backend/frontend can start.
2. Then Steps 3 → 5 → 4 → 6, rerunning 8 and 9 after each lands. (3, 5, 6, 7 done; 4 skipped for now.)
3. Step 7 last, only if ahead.

## ML next steps (decided 2026-09-26)

- Goal: warn about a user's saved route **≤ 1 hour before** the calendar departure. The daily model
  can't use that timing; an hourly/nowcast model is a later step.
- Order: stale `alert_results.json` refreshed (done) → live conditions `ml/live.py` (done) →
  street report-history feature (done, `daily_report_v2`) → route scoring `ml/route.py` with a
  departure time (**on hold**) → multi-point rain → hourly model.
- v2 report history: past report-days/year before d−7, frozen at the evaluated period's start
  in tests. Test AP 0.00156 → 0.00210 (later dates), 0.00169 → 0.00240 (unseen streets); gain is
  *where* (within-day AUC 0.731 → 0.755), not *when* (day-level AUC still ~0.58). Retrain needed:
  old bundles lack `history` and are refused by `ml.predict`.
- **Validation AP is unreliable:** the Jul–Sep 2023 validation AP of v2 (0.00684) hinges on one
  report ranked #1; without it 0.00070. Judge candidates on three rolling dev windows (train through
  2022-12-31 / 2023-03-31 / 2023-06-30, validate the next quarter; 627 reports) with a by-day
  bootstrap. `ml.train` now selects on the median AP of these windows (`ROLLING_WINDOWS`);
  boosting still wins, so the v2 model and its results are unchanged. **Last ML change (user decision).**
- Step 2 candidates (2026-09-26), all rejected vs v2 on the rolling windows: neighbor history within
  250 m (AP up in 1 of 2 informative windows, recall@1% 11.7 → 12.8%, 39% bootstrap wins); 7/30-day
  antecedent rain (no gain); boosting grid (best is a tie with v2); rank blend of model + rain × history
  rule (AP up in all 3 windows, recall@1% 11.7 → 15.5%, 90% wins, but ROC-AUC 0.860 → 0.809, which
  fails the "AUC must not drop" rule; a trade-off for alerts vs. whole-network ranking).
- New data tested 2026-09-26 (no keys): NOAA MRMS radar rain (AWS `noaa-mrms-pds`,
  `MultiSensor_QPE_24H_Pass2` at local midnight = local-day totals, 1 km, cropped 13×23 cells; decode
  with `eccodes`, pygrib won't build on Windows), USGS groundwater (39 wells, param 62610) and canal
  stage (3 gauges, 00065) via `waterservices.usgs.gov/nwis/dv` bBox query.
  - Canal stage: no signal. Groundwater (d−1): day-level AUC 0.625 alone, but mixed in the model.
  - **Per-street radar for d−1 and prior 3 days**: rolling windows AUC 0.860 → 0.868, Apr–Jun AP 2.3×,
    74% bootstrap wins (bar 80%). Held-out tests once: AP 0.00210 → 0.00501, 0.00240 → 0.00452; AUC
    0.8279 → 0.8284, 0.8114 → 0.8110 (misses the "AUC up on both" rule by 0.0004). City day-level AUC
    0.581 → 0.593 / 0.583 → 0.634; 1-km top-1% precision 11.5 → 16.1%. **Adoption pending user decision.**
  - Feb 13, 2024 (39-street dry-day spike): radar also shows ≤ 3.6 mm, so that spike isn't rain.
- Tide as a daily model feature: **tested and rejected (2026-09-26).** Daily max Virginia Key
  water level (d, d-1) + tide × low-ground: validation AP 0.00055 → 0.00039 (worse); tests mixed
  (city AUC 0.763 → 0.751, AP up slightly, 1-km top-1% precision 12% → 9%). Backtest: dry days at
  NWS minor had more city reports (69% vs ~45% of days, 32 days, mostly October king tides),
  but most dry-day report spikes had normal tides (likely rain the single weather point missed).
  Tide stays a live coastal signal in `ml/live.py`, not a model input.
- **No API keys or signups** for data sources (user decision). Live sources = NWS alerts,
  NOAA Virginia Key tides, Open-Meteo 15-minute rain.
- Miami-Dade Flooding Vulnerability Viewer (experience.arcgis.com/experience/2bdfdae22dea4bd699d11714c9aba709):
  planning layers only (FEMA, SLR, storm surge, 2040 groundwater, king-tide raster). No live data.
  The king-tide raster (`gisweb.miamidade.gov/.../VulnerabilityViewer/MD_SeaLevelRise/MapServer` layer 0)
  is identify-able but only thin shoreline slivers.
- Live sources tested 2026-09-26:
  - Works, no key: NWS alerts `api.weather.gov/alerts/active?point=lat,lon` (Coastal Flood Statement was
    active; zone FLZ173, no polygon for that type). NOAA CO-OPS Virginia Key `8723214` observed
    `water_level` + `predictions` (`datum=STND`, ft) and flood thresholds
    `mdapi/.../stations/8723214/floodlevels.json` (NWS minor 13.66 ft). Open-Meteo `minutely_15=precipitation`.
  - Needs a key: FL511 `fl511.com/api/v2/get/event` ("Invalid Key"); flood event content unconfirmed.
  - Not usable: USGS Real-Time Flood Impact (2 FL sites, none in Miami); City 311 FeatureServer frozen at
    2024-08-10; Floodi (no public API found).

## Progress

- [x] ML: daily rainfall join, weighted street-day training, temporal/grouped tests,
  baseline and model score exports (`ml/README.md`); original pipeline/Atlas unchanged.

- [x] Step 0 — Scaffold
- [x] Step 1 — Roads
- [x] Step 2 — FEMA
- [x] Step 3 — Elevation (scored via `elev_p10`; in Atlas)
- [ ] Step 4 — Impervious (skipped for now)
- [x] Step 5 — 311 reports (in Atlas)
- [x] Step 6 — Drains (unscored columns; in Atlas)
- [x] Step 7 — Low points (scored via `sink_p90`; in Atlas)
- [x] Step 8 — Build segments (FEMA + elevation + 311 + sinks scored, drains unscored; rerun after each new layer)
- [x] Step 9 — Load to Atlas (FEMA + elevation + 311 + drains + sinks; rerun after each new layer + Step 8)
- [x] Step 10 — Road closures (county planned construction live in Mongo `closures`; FL511 coded + selftested, needs `FL511_API_KEY`)
- [x] Step 11 — Flood criteria freeboard (unscored feature; in Atlas)
- [x] Step 12 — FIU parking + hotspots (Mongo `parking`, `fiu_hotspots`)
