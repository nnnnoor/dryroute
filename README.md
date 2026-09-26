# Flood-Aware Routing: South Florida

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
water can pond along the road, m), `risk_score` (0-1), `score_note`
(`bridge_capped`, `elev_water_only` or null), and `geometry` (GeoJSON LineString, 2dsphere-indexed).
`street_id` is indexed too. More feature columns (e.g. impervious) may be added as those
layers land.

**Status:** Atlas has FEMA + elevation + 311 reports + storm drains + low points (loaded 2026-09-26). `risk_score` is a placeholder
(weighted FEMA risk level, low ground via `elev_p10`, 311 flood-report days as a percentile within city/county, ponding depth via `sink_p90`)
until the ML model replaces it. Per-report 311 links for the ML model: `data/processed/flood_reports.parquet`.

## Running the pipeline (only if you're changing it)

For the ML portion, see [ML setup and backend contract](data-pipeline/ml/README.md).
It trains a daily weather-aware flood-report model, evaluates later dates and
held-out streets, and exports a 0–1 relative score per original road segment.
Generated model/data files are kept in gitignored `data-pipeline/artifacts/ml/`.
Scores are experimental susceptibility indices, not probabilities of road flooding.
Use `python -m ml.forecast --days 3` from `data-pipeline/` for predictive daily road
scores and a study-area summary using forecast rainfall. The learned target is
recorded flood reports; actual flooding probability remains unvalidated.

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
.venv/Scripts/python -m pipeline.08_build_segments
.venv/Scripts/python -m pipeline.09_load_mongo              # --ping to only test the connection
```

Raw downloads (`data/raw/`), per-layer features, check plots and the OSM cache are not committed;
the scripts regenerate them.
