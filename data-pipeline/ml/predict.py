"""Serve per-edge scores from explicit daily weather input; no implicit network calls."""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

import config
from ml.data import baseline, street_table
from ml.model import features, relative_score
from ml.settings import OUTPUT, TIMEZONE
from ml.weather import RAIN_FEATURES


def score_segments(segments, bundle, weather):
    """weather: date, rain_mm, rain_lag1_mm, rain_prior3_mm, source, updated_at.

    date refers to a complete America/New_York calendar day. Current-day rain must
    combine accumulated and forecast rain. Prior-day/3-day rain covers complete
    preceding local days. Only load trusted local joblib models.
    """
    date = datetime.strptime(weather["date"], "%Y-%m-%d").date()
    updated = datetime.fromisoformat(weather["updated_at"].replace("Z", "+00:00"))
    if updated.tzinfo is None or not isinstance(weather.get("source"), str) or not weather["source"].strip():
        raise ValueError("Weather requires a timezone-aware updated_at and a source")
    values = np.array([float(weather[c]) for c in RAIN_FEATURES])
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Rainfall must be finite, nonnegative mm")
    if values[2] + 1e-9 < values[1]:
        raise ValueError("Prior three-day rainfall must include yesterday's rainfall")
    streets = street_table(segments)
    for c, value in zip(RAIN_FEATURES, values):
        streets[c] = value
    physical, coverage = baseline(streets)
    output = streets[["street_id"]].copy()
    output["risk_score"] = physical
    output["report_probability_estimate"] = np.nan
    # Existing labels describe reports. Never silently relabel these as actual floods.
    target = bundle["metadata"].get("prediction_target", "recorded_flood_report")
    if target != "recorded_flood_report":
        raise ValueError("This scorer requires a model trained on recorded flood reports")
    output["prediction_target"] = target
    output["flood_probability_estimate"] = np.nan
    output["validation_status"] = "experimental_report_proxy"
    output["score_source"] = "physical_fallback"
    output["score_note"] = "insufficient_static_features"
    supported = ~streets.is_bridge & (coverage > 0)
    with threadpool_limits(limits=2):
        p = bundle["model"].predict_proba(features(streets.loc[supported]))[:, 1] if supported.any() else np.array([])
    output.loc[supported, "report_probability_estimate"] = p
    output.loc[supported, "risk_score"] = relative_score(p, bundle["reference"])
    output.loc[supported, "score_source"] = "model_reference_percentile"
    output.loc[supported, "score_note"] = np.where(coverage.loc[supported] < 1, "partial_static_features", "")
    output["report_alert"] = pd.Series(pd.NA, index=output.index, dtype="boolean")
    output["alert_report_probability_estimate"] = np.nan
    output["alert_threshold"] = np.nan
    output["alert_status"] = "unavailable"
    if "alert_model" in bundle and "alert_policy" in bundle:
        threshold = bundle["alert_policy"]["threshold"]
        with threadpool_limits(limits=2):
            ap = bundle["alert_model"].predict_proba(features(streets.loc[supported]))[:, 1] if supported.any() else np.array([])
        output.loc[supported, "alert_report_probability_estimate"] = ap
        output.loc[supported, "report_alert"] = ap >= threshold
        output.loc[supported, "alert_threshold"] = threshold
        output.loc[supported, "alert_status"] = "experimental_report_alert"
    output.loc[streets.is_bridge, "risk_score"] = physical.loc[streets.is_bridge].clip(upper=config.BRIDGE_RISK_CAP)
    output.loc[streets.is_bridge, "score_source"] = "bridge_policy_fallback"
    output.loc[streets.is_bridge, "score_note"] = "no_bridge_training_labels"
    output["feature_coverage"] = coverage
    output["weather_out_of_training_range"] = any(value > bundle["weather_max"][c] for c, value in zip(RAIN_FEATURES, values))
    output["score_kind"] = "relative_susceptibility_index"
    output["model_version"] = bundle["metadata"]["version"]
    output["model_created_at"] = bundle["metadata"]["created_at"]
    output["valid_from"] = datetime.combine(date, datetime.min.time(), ZoneInfo(TIMEZONE)).isoformat()
    output["valid_to"] = datetime.combine(date + timedelta(days=1), datetime.min.time(), ZoneInfo(TIMEZONE)).isoformat()
    output["weather_source"], output["weather_updated_at"] = weather["source"], weather["updated_at"]
    output["forecast_date"] = date.isoformat()
    result = segments[["edge_id", "street_id"]].merge(output, on="street_id", validate="many_to_one")
    if not result.risk_score.between(0, 1).all() or len(result) != len(segments):
        raise ValueError("Invalid score export")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=OUTPUT / "model.joblib")
    parser.add_argument("--weather", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT / "segment_risk.jsonl")
    args = parser.parse_args()
    bundle = joblib.load(args.model)
    scores = score_segments(pd.read_parquet(config.PROC / "segments.parquet"), bundle, json.loads(args.weather.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    scores.to_json(args.output, orient="records", lines=True, double_precision=12)
    scores.to_parquet(args.output.with_suffix(".parquet"), index=False)
    print(f"Exported {len(scores)} segments; risk range {scores.risk_score.min():.4f}–{scores.risk_score.max():.4f}")


if __name__ == "__main__":
    main()
