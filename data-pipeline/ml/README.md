# DryRoute ML

## Experimental yes/no report alerts

After training (and again after every retrain), run:

```sh
.venv/bin/python -m ml.alerts
.venv/bin/python -m ml.forecast --days 3
```

`ml.alerts` now compares logistic regression, boosting, and class-balanced boosting
on **every** validation road-day. Each gets the lowest probability cutoff that
flags at most 1% of validation road-days, preserving tied scores. Choose the model
with the highest validation recall at that budget; precision breaks ties. This is
a prototype workload tradeoff, not a validated operational/safety threshold or a
guaranteed future alert rate. Class-balanced boosting corrects the class-weight
odds shift before exporting its estimates; that correction is not empirical
calibration. The original F2 policy is retained for historical comparison below.
The cutoff is frozen before reevaluating all test road-days. The report is saved to
`artifacts/ml/alerts/evaluation.json` with confusion counts, precision, recall,
false-positive rate, false-discovery rate, accuracy and alert rate, overall and
by jurisdiction. A saved run is in `ml/alert_results.json`.

The existing ranking model was refit through September 2023, including validation
labels. To avoid selecting a threshold from in-sample predictions, alerts use a
separate estimator selected on full validation and fitted only through June 2023.
It is saved as `alert_model` in the same bundle and is not refit after selection.
Thus `alert_report_probability_estimate` can differ from the ranking model's
`report_probability_estimate`. Compare the former to `alert_threshold` with `>=`.

Forecast exports include `report_alert` (true/false), `alert_threshold`,
`alert_report_probability_estimate`, and `alert_status`. Unsupported roads have
null alerts, not false. Bundles without an alert model return unavailable/null
alerts. These are report-proxy alerts, not assertions that flooding will occur.

A false positive here means an alert on a **day without a recorded report**, not
a verified false flooding warning. False-positive rate is FP/(FP+TN), whereas
false-discovery rate is FP/(TP+FP): with rare reports, a low false-positive rate
can coexist with mostly unmatched alerts. Accuracy alone is misleading.

Original F2-cutoff results (before the full-validation budget policy):

| Test | Precision | Recall | False-positive rate | TP / FP / FN |
|---|---:|---:|---:|---:|
| Later dates | 0.615% | 3.72% | 0.0956% | 21 / 3,396 / 544 |
| Unseen streets + later dates | 0.339% | 2.54% | 0.1033% | 3 / 883 / 115 |

These original results were too weak for dependable user warnings. The alert function is
available for experimentation and evaluation, not recommended for automatic
road-closure decisions or claims that an area will flood. The threshold was not
selected only on validation data. Because these test periods have already been
examined in earlier iterations, revised results are retrospective comparisons,
not a fresh blind test. See `alert_results.json` for the current policy and results.

## Predict the next few days

```sh
# From data-pipeline/, after the setup/training below:
.venv/bin/python -m ml.forecast --days 3
```

This uses the trained predictive model with forecast rainfall for today and the
following two local calendar days. `--days` supports 1–7 days. Outputs are in
`artifacts/ml/forecast/`:

- `segment_forecasts.jsonl` / `.parquet`: one row per directed road segment per day.
- `area_forecast.json`: one summary per day for the FIU–Downtown/Brickell study network.
- `weather.json`: the exact rainfall inputs, retrievable again offline with
  `python -m ml.forecast --weather artifacts/ml/forecast/weather.json`.

For future dates, lagged rainfall may itself include forecast rainfall from earlier
forecast days. These are daily estimates, not flood arrival times. Longer forecast
horizons have not been separately validated against historical forecasts.

The existing dataset supports **recorded flood reports** as the prediction target.
Every export therefore includes `prediction_target=recorded_flood_report`,
`validation_status=experimental_report_proxy`, and a null
`flood_probability_estimate`. `report_probability_estimate` remains the model's
report estimate; `risk_score` remains a relative index. Do not display either as
the probability that the road will flood. No yes/no flood prediction or alert
threshold is justified by the current evaluation.

The area index is the road-length-weighted mean of modeled physical-street ranks.
Reverse directions count once; bridge/unknown-feature fallbacks are excluded, with
coverage counts included. It is **not** a probability that any road or the entire
area floods. No independence between neighboring roads is assumed. It describes
the existing study network, not arbitrary cities or neighborhoods.

To train against actual flooding later, obtain dated, road-linked observations of
both confirmed flooding and confirmed non-flooded conditions with documented
coverage. Unreported roads cannot automatically be treated as confirmed dry.
The current trained model is retained rather than silently changing its labels.

This package prepares street-day training data, trains and evaluates a daily
flood-report model, and returns standardized scores for every original road edge.
It does not implement routing, Calendar, an HTTP server, or database updates.

## Run it

From `data-pipeline/`, using Python 3.12+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r ml/requirements.txt
.venv/bin/python -m ml.weather
.venv/bin/python -m ml.train
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ml.verify
```

On Windows replace `.venv/bin/python` with `.venv\Scripts\python`.
Only the weather command needs network access. Training uses the existing reference
parquets and cached weather; it never rebuilds the graph or modifies source data.
The full-population test evaluation can take several minutes.

Get today's weather and score the road network:

```sh
.venv/bin/python -m ml.weather --forecast
.venv/bin/python -m ml.predict --weather artifacts/ml/current_weather.json
```

An explicitly synthetic offline demonstration is also available:

```sh
.venv/bin/python -m ml.predict --weather ml/example_weather.json --output artifacts/ml/demo_segment_risk.jsonl
```

The example is not live weather. It is dated and labeled as a synthetic scenario.

## Backend integration

Load the trusted local model once, then call the Python function when weather changes:

```python
import joblib
import pandas as pd
from ml.predict import score_segments

segments = pd.read_parquet("data/processed/segments.parquet")
bundle = joblib.load("artifacts/ml/model.joblib")
weather = {
    "date": "2026-09-26",             # America/New_York calendar day
    "rain_mm": 35.0,                  # full-day precipitation estimate
    "rain_lag1_mm": 12.0,             # previous complete day
    "rain_prior3_mm": 24.0,           # previous 3 complete days, includes lag1
    "source": "your_weather_provider",
    "updated_at": "2026-09-26T12:00:00-04:00"
}
scores = score_segments(segments, bundle, weather)
```

`segment_risk.jsonl` and `segment_risk.parquet` contain one row per original
`edge_id`, matching the routing graph. Opposite directions share a street prediction.
Keep IDs as strings; with `pd.read_json` explicitly set
`dtype={"edge_id": "string", "street_id": "string"}` to prevent numeric inference.

| Output | Meaning |
|---|---|
| `edge_id`, `street_id` | Original directed edge and physical street IDs |
| `risk_score` | 0–1 relative susceptibility index; higher means higher modeled report propensity |
| `report_probability_estimate` | Experimental daily report probability estimate; null for unsupported roads |
| `score_source` | Model reference percentile, bridge policy fallback, or physical fallback |
| `score_note`, `feature_coverage` | Static-data and fallback flags |
| `valid_from`, `valid_to` | Local calendar-day interval, end exclusive; correct DST offsets |
| `weather_source`, `weather_updated_at` | Provider and input timestamp |
| `weather_out_of_training_range` | Rainfall exceeds the training range |
| `model_version`, `model_created_at` | Model identity |

**A risk score of 0.8 does not mean an 80% flood probability.** Model scores are
percentiles against a fixed, weighted development-data reference. They are not
re-ranked within each request, so weather can change the score for the same street.
Refitting changes the reference; compare scores within the same model version/build.
The separate probability estimates refer to recorded reports, not confirmed floods,
and have not been calibrated against actual road flooding or closures.

Bridges have no training labels by construction. They receive the existing pipeline's
physical-score cap of 0.1, explicitly marked `bridge_policy_fallback`, with null
model probability. Roads with no physical features get a flagged 0.5 fallback.
These fallback values are heuristics, not model percentiles or evidence of safe
passage. Consumers must inspect `score_source`; do not treat fallback roads as
validated low-risk alternatives. There are no validated alert or closure thresholds.

The caller should refresh weather regularly and reject stale inputs according to
the app's refresh policy. The scorer preserves timestamps and validates the schema;
it deliberately also supports historical dates for evaluation. Failed weather
downloads raise an error instead of silently substituting zero rain.

## Training design

- Deduplicate directed road twins into physical streets; average numerical features.
- Exclude bridge groups. FEMA level 0 and water-only elevation are missing features.
- Label a street-day positive when at least one `category=flood` report occurs.
  Drain complaints do not become flood labels. Repeated tickets/directions on the
  same street-day count once.
- Only generate dates inside documented coverage: city 2022-10-01 through
  2024-08-09, county 2022-01-01 through 2023-12-31. Out-of-window links are counted
  in the audit and excluded. No report means an uncertain negative, not verified dry.
- Keep all positives and sample 2% of negatives, separately by date, jurisdiction
  and street partition. Inverse-inclusion weights preserve the population balance
  in fitting and validation; there is no extra class balancing.
- Use an explicit feature allowlist: FEMA, elevation, ponding, road length, drain
  inventory count, tunnel flag, jurisdiction, rainfall that day, previous-day rain,
  preceding three-day rain, and a rain/static-risk interaction. Drain inventory
  coverage is uneven; a zero is not proof of absent drainage. Historical complaint
  totals, existing `risk_score`, IDs and labels are excluded from model inputs.

Historical precipitation comes from the [Open-Meteo archive](https://open-meteo.com/en/docs/historical-weather-api).
The [forecast adapter](https://open-meteo.com/en/docs) supplies equivalent daily
features using a single point near the study-area center (25.76, -80.29). All dates
are America/New_York; all precipitation is in millimeters. The archive is reanalysis,
while the forecast endpoint provides forecast/model data. This is not a backtest
of forecasts available at the historical prediction time, and it does not resolve
hourly commute conditions or local rainfall differences across the study area.

## Leakage control and evaluation

Physical street groups and distinct streets tied to the same ticket stay in the
same approximately 80/20 partition. The held-out 20% never enters model selection
or fitting. Neighboring streets without shared tickets can still cross partitions;
this is not a spatial-block holdout.

1. Fit candidates through 2023-06-30 on development streets.
2. Select logistic regression versus small gradient-boosted trees by weighted
   validation average precision on 2023-07-01 through 2023-09-30.
3. Refit the selected architecture through 2023-09-30, still without held-out streets.
4. Evaluate **every eligible street-day** from 2023-10-01 onward, separately for
   development streets (`future_test`) and unseen streets (`unseen_streets_test`).
   County evaluation ends in 2023; city evaluation extends through 2024-08-09.

Imputation/scaling are fit only on development data. Test labels do not select
architecture, parameters, thresholds or the score reference. Static features reflect
the current snapshot, not a reconstruction of their state in 2022–2024.

Report average precision, ROC-AUC and Brier score, overall and by jurisdiction.
The physical baseline excludes report counts; the existing placeholder contains
labels and would give a misleading comparison. A constant-probability baseline
provides a prevalence reference. Average precision is a PR summary, not accuracy
and not the same as trapezoidal PR-AUC. Small positive counts make county metrics
unstable; report the counts alongside them. See `results.json` for this run.

### Initial results (2026-09-26)

Gradient boosting won validation. The sampled preparation table contains 277,495
rows; final tests cover all 4,409,344 eligible held-out street-days.

| Test | Report-positive days | Model AP | Physical baseline AP | Constant baseline AP | Model ROC-AUC |
|---|---:|---:|---:|---:|---:|
| Later dates, development streets | 565 / 3,554,108 | 0.001564 | 0.000275 | 0.000159 | 0.815 |
| Later dates, unseen streets | 118 / 855,236 | 0.001694 | 0.000198 | 0.000138 | 0.804 |

These results show ranking signal, but **absolute precision is low**. They do not
establish reliable flood detection. County tests contain only 8 and 6 positive
street-days respectively, so their metrics are especially unstable. The much
larger number of no-report days is not a count of independently verified dry roads.

An explicit dry-versus-heavy-rain scenario check increased the score on all 35,888
modeled edges; median relative score rose from 0.519 to 0.755. The 483 bridge edges
use the flagged policy fallback. This verifies weather responsiveness in that
scenario, not monotonicity for every possible rainfall input or forecast accuracy.

## Artifacts

Generated files stay in gitignored `artifacts/ml/` to keep Source Control focused
on source code. They can be regenerated with the commands above.

| Artifact | Purpose |
|---|---|
| `model.joblib` | Fitted estimator, preprocessing, reference ranks and metadata; load only trusted files |
| `evaluation.json` | Metrics, split counts, input hashes, versions and limitations |
| `training_panel.parquet` | Weighted sampled rows with explicit split assignments |
| `streets.parquet`, `positive_street_days.parquet` | Street features/groups and deduplicated positives |
| `weather_daily.parquet` | Cached daily rainfall and lag features |
| `baseline_segments.parquet` | Physical baseline per original edge |
| `future_test_predictions.parquet`, `unseen_streets_test_predictions.parquet` | Full-population test predictions |
| `segment_risk.jsonl`, `segment_risk.parquet` | Current weather-based export for the backend |

`data/checks/ml_evaluation.png` shows baseline distribution and held-out results.
Model probabilities and relative ranks are experimental. Before using them for
safety-critical recommendations, validate against observed road flooding/closures,
test historical forecasts, add spatial holdouts, and obtain more recent labels.
