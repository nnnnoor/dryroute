# ML handoff: flood-risk model

For whoever trains the predictive flood model. Everything below is in this repo (branch
`data-processing`) or in Atlas (db `flood`, collection `segments`). Built 2026-09-26.

## TL;DR

- **Features:** one row per directed road edge, `data/processed/segments.parquet` (36,371 rows).
- **Labels:** 311 flood reports with dates, `data/processed/flood_reports.parquet` (one row per report-to-edge link).
- **Weather is not included.** Join historical daily rainfall yourself (Open-Meteo archive, below).
- **Suggested task:** P(flood report on street *s* on day *d* | rain on *d*, *d−1*, static features of *s*).
- **Leakage rules:** drop `flood_report_days` and `risk_score` from the inputs, split by `street_id` (ideally spatially), and exclude bridges.

## Files

| File | What | Load |
|---|---|---|
| `data/processed/segments.parquet` | Static features per edge, EPSG:4326 | `geopandas.read_parquet` |
| `data/processed/flood_reports.parquet` | 311 reports snapped to edges | `pandas.read_parquet` |
| `data/processed/graph.graphml` | Routing graph; edge `(u, v, key)` ↔ `edge_id = f"{u}_{v}_{key}"` | `osmnx.load_graphml` |

Edge ids are identical across all three and Atlas `_id`s (asserted on every load). Predictions keyed
by `edge_id` plug straight into routing.

## Units: edges vs streets

Edges are **directed**. A two-way street is two edges (u→v and v→u) with identical geometry and
identical features, sharing a `street_id`. A report near a two-way street is linked to **both**
edges. **Model at `street_id` level** (dedupe features by `street_id`, labels by
`street_id` + `date`), then give both edges the street's prediction. Modeling per edge duplicates
rows and leaks across a random split.

## Feature columns (`segments.parquet`)

| Column | Meaning | Notes |
|---|---|---|
| `edge_id`, `street_id`, `u`, `v`, `key` | Keys | Don't use as features |
| `name`, `highway`, `length` (m) | OSM road attributes | `highway` is categorical (`;`-joined if mixed) |
| `is_bridge`, `is_tunnel` | OSM flags | **Exclude bridges from training** (0 reports by construction) |
| `fema_zone`, `zone_subtype`, `in_sfha`, `fema_risk_level` | FEMA flood zone at edge midpoint | Level 3 = SFHA (AE/AH/VE), 2 = X 0.2%, 1 = X minimal |
| `elev_mean`, `elev_min`, `elev_p10` | Ground elevation along edge, m NAVD88 (USGS 3DEP 10 m, bare earth) | `elev_p10` is the most robust. Motorway embankments read up to ~12 m |
| `elev_water_only` | Edge sits entirely on water in the DEM | Currently all False |
| `sink_depth`, `sink_p90` | How deep water can pond before spilling (filled DEM − DEM), m | `sink_p90` is the most robust; max can hit single lake/pit pixels |
| `flood_criteria_m`, `freeboard_p10_m`, `below_criteria_frac` | Miami-Dade 2060 design flood level (10-yr/24-hr storm + sea-level rise) and how far the road's ground sits above it (m; negative = below) | Most roads are below (it's a future standard), so use the magnitude. Within a jurisdiction it's about as predictive as `elev_p10` (AUC ~0.59 city / 0.66 county) and only partly correlated (0.46) |
| `drain_count` | Storm inlets within 50 m | **0 can mean "not inventoried"** (Coral Gables / Grove / south Little Havana are sparse) |
| `drains_per_100m` | `drain_count / length × 100` | Inflated on short edges; prefer `drain_count` |
| `jurisdiction` | `city` (City of Miami) or `county` | **Include as a feature**: reporting rates differ a lot |
| `drain_issue_days` | Distinct days with county drain complaints (clogged/repair/canal) | Label-adjacent; fine as a feature if the label is flood reports, but check importance |
| `flood_report_days` | Distinct days with flood reports | **This is the label aggregated. Never use as a feature** |
| `risk_score`, `score_note` | Hand-weighted placeholder score (includes `flood_report_days`) | **Not a feature, not a label.** Baseline to beat |

## Labels (`flood_reports.parquet`)

Columns: `edge_id, date` (local America/New_York), `source` (city|county), `type`, `category`
(flood|drain), `ticket_id`, `snap_m` (distance to the edge, ≤ 30 m).

| source | category | types | tickets | date range |
|---|---|---|---|---|
| city | flood | `COMPWSF` (STORM FLOOD/ DRAINAGE) | 1,887 | 2022-10-01 → 2024-08-09 |
| county | flood | FLOODING / STANDING WATER - LOCALIZED | 121 | 2022-02-23 → 2023-12-20 |
| county | drain | DRAIN CLOGGED / CLEANING, DRAIN - REPAIR, RER DRAINAGE CANALS FLOOD COMPLAINT, CANAL - BLOCKED | 476 | 2022-01-19 → 2023-12-26 |

Use `category == "flood"` for the label: 2,848 edge-days across 409 distinct dates. Several rows share a
`ticket_id` when a report ties between edges (mostly two-way twins).

**Observation windows differ by jurisdiction.** City data covers 2022-10-01 → 2024-08-09, and county
data covers 2022-01-01 → 2023-12-31. A street-day outside its jurisdiction's window is **unknown, not
a negative**. Only build rows inside the window.

**Label biases to keep in mind:**
- 0 reports ≠ didn't flood. Nobody may have reported it, so treat negatives as noisy.
- City residents report far more (city edges max 9 report-days, county max 3). Include `jurisdiction` and evaluate per jurisdiction.
- Reports cluster on storm days, and one storm produces many tickets. Counts are per distinct day for that reason.

## Weather (you add this)

Open-Meteo historical archive (free, no key; tested 2026-09-26):

```
https://archive-api.open-meteo.com/v1/archive?latitude=25.76&longitude=-80.29
  &start_date=2022-01-01&end_date=2024-08-31
  &daily=precipitation_sum,precipitation_hours&hourly=precipitation&timezone=America%2FNew_York
```

It's reanalysis-based and coarse (the whole study area is a few grid cells), so one or a few points
is enough. Suggested features: rain on day *d*, *d−1*, the 3-day sum, and the max hourly intensity.
The report date is when the ticket was **created**, so people often report the day after. The lag
features matter.

## Suggested setup

1. Street table: dedupe `segments` by `street_id`, drop bridges.
2. Panel: street × day inside the jurisdiction's window. That's ~23k streets × ~700 days ≈ 16M rows, so
   either keep only days with rain > a few mm (reports on dry days are rare) or sample negatives.
3. Label: 1 if the street has a `category == "flood"` report on that date.
4. Split: grouped by `street_id`, ideally spatial blocks (e.g. grid cells), since neighboring streets
   are nearly identical. Also try a time split (train on 2022–2023, test on 2024 city data).
5. Metrics: PR-AUC (positives are rare), per jurisdiction. The baseline is `risk_score` alone.
6. Serving: the backend combines live forecast rain with the static features at request time and writes the
   probability per `street_id`, broadcast to both `edge_id`s.

## Not available / caveats

- Impervious surface (Step 4) was skipped, so there's no `pct_impervious` column.
- County 311 for 2024+ isn't public (`data_311_2024` needs a token).
- Details for every layer are in `data-pipeline/CLAUDE.md` (Build plan → each Step's *Implemented* notes).
