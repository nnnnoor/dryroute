# Data Pipeline: Flood-Aware Routing (South Florida)

ShellHacks 2026 project: routing around flood-prone roads between FIU and Downtown/Brickell, Miami.

`data-pipeline/` builds the `segments` dataset: every drivable road segment in the study area with
static flood-risk features and a `risk_score`. Details in [data-pipeline/CLAUDE.md](data-pipeline/CLAUDE.md).

## Getting started: what you need depends on your part

You do **not** need to run the pipeline to use its data.

| You're working on | Use | Setup |
|---|---|---|
| Backend / dashboard | MongoDB Atlas, db `flood`, collection `segments` | Ask the Atlas owner for your own database user, and add your IP under Atlas **Network Access** |
| Routing | `data-pipeline/data/processed/graph.graphml` (OSMnx graph) | Load with `osmnx.load_graphml` |
| ML / analysis | `data-pipeline/data/processed/segments.parquet` + `flood_reports.parquet` | Read [data-pipeline/ML_HANDOFF.md](data-pipeline/ML_HANDOFF.md) first |

**Don't rebuild the graph yourself.** Rebuilding pulls fresh OpenStreetMap data, which can change
`edge_id`s. The committed `graph.graphml` is the reference: its edge ids match Mongo `_id`s and the
parquet `edge_id`s (`f"{u}_{v}_{key}"`).

### Segment documents in Mongo

One doc per directed road edge: `_id` (= `edge_id`), `street_id` (groups the two directions of a
two-way street), `u`, `v`, `key`, `name`, `highway`, `length` (m), `is_bridge`, `is_tunnel`, FEMA
columns (`fema_zone`, `zone_subtype`, `in_sfha`, `fema_risk_level`), elevation columns (`elev_mean`,
`elev_min`, `elev_p10` in m NAVD88, `elev_water_only`), 311 columns (`flood_report_days`,
`drain_issue_days`, `jurisdiction` = city|county), drain columns (`drain_count`, `drains_per_100m`;
not scored, and 0 can mean "not inventoried"), low-point columns (`sink_depth`, `sink_p90`: how deep
water can pond along the road, m), flood-criteria columns (`flood_criteria_m`, `freeboard_p10_m`,
`below_criteria_frac`: road ground vs the county's 2060 design flood level; not scored), `risk_score` (0-1), `score_note`
(`bridge_capped`, `elev_water_only` or null), and `geometry` (GeoJSON LineString, 2dsphere-indexed).
`street_id` is indexed too. More feature columns (e.g. impervious) may be added as those
layers land.

**Status:** Atlas has FEMA + elevation + 311 reports + storm drains + low points + county flood criteria (loaded 2026-09-26). `risk_score` is a placeholder
(weighted FEMA risk level, low ground via `elev_p10`, 311 flood-report days as a percentile within city/county, ponding depth via `sink_p90`)
until the ML model replaces it. Per-report 311 links for the ML model: `data/processed/flood_reports.parquet`.

### Road closures in Mongo (`closures` collection)

For routing, separate from flood risk. One doc per (closure, edge): `edge_id`, `source` (`fl511` live
FDOT closures, or `county_ucc` planned county construction), `kind` (`closure` | `roadwork` |
`planned_construction`), `full_closure`, `direction`, `start`/`end` (UTC, `end` null = open-ended),
`name`, `description`, `lanes_affected`. Active right now:
`{"start": {"$lte": now}, "$or": [{"end": {"$gte": now}}, {"end": null}]}`. Refresh it by rerunning
`python -m pipeline.10_closures` (e.g. every 15 min). Live FL511 data needs `FL511_API_KEY` in `.env`.

### FIU parking + hotspots in Mongo (`parking`, `fiu_hotspots`)

FIU Modesto Maidique Campus, from a 1 m elevation model. `parking`: one doc per lot/garage (46; OSM
names like "Gold Parking Garage", "W10 Parking Lot") with `parking_score` (0-1, higher = more
flood-exposed), `type` (garage|surface|street_side), `pond_frac` (share of the lot that ponds),
`freeboard_p10_m`, `fema_zone`, `access_roads`, `access_risk_min`; polygon `geometry` (2dsphere).
`fiu_hotspots`: campus areas where water pools >= 15 cm (polygons, `area_m2`, `depth_max_m`, and the
`lots`/`roads` they touch). Terrain-based exposure, not observed flooding (311 doesn't cover campus).
Garages read the ground under them, i.e. their ground-level entrance (upper decks stay dry).

## Running the pipeline (only if you're changing it)

```bash
cd data-pipeline
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
cp .env.example .env                                       # then fill in MONGO_URI (never commit .env)
.venv/Scripts/python -m pipeline.01_roads
.venv/Scripts/python -m pipeline.02_fema
.venv/Scripts/python -m pipeline.03_elevation
.venv/Scripts/python -m pipeline.05_311
.venv/Scripts/python -m pipeline.06_drains
.venv/Scripts/python -m pipeline.07_lowpoints
.venv/Scripts/python -m pipeline.11_flood_criteria         # needs dem.tif from Step 3
.venv/Scripts/python -m pipeline.12_fiu_parking            # needs Steps 2, 8, 11; loads Mongo itself
.venv/Scripts/python -m pipeline.08_build_segments
.venv/Scripts/python -m pipeline.09_load_mongo              # --ping to only test the connection
.venv/Scripts/python -m pipeline.10_closures               # closures; rerun often (--selftest offline)
```

Raw downloads (`data/raw/`), per-layer features, check plots and the OSM cache are not committed;
the scripts regenerate them.
