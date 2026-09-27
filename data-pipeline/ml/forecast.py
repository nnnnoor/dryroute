"""Generate daily predictive scores and study-area summaries from existing data."""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import config
from ml.data import street_table
from ml.predict import score_segments
from ml.settings import OUTPUT
from ml.weather import fetch_forecasts


def predict_horizon(segments, bundle, weather_days):
    if not isinstance(weather_days, list) or not weather_days:
        raise ValueError("Expected a nonempty list of daily weather inputs")
    dates = pd.to_datetime([w["date"] for w in weather_days], errors="raise")
    if not dates.equals(pd.date_range(dates[0], periods=len(dates))):
        raise ValueError("Forecast days must be ordered, unique and consecutive")
    if len({w["updated_at"] for w in weather_days}) != 1:
        raise ValueError("All days must come from the same weather retrieval")
    scored = pd.concat([score_segments(segments, bundle, w) for w in weather_days], ignore_index=True)
    streets = street_table(segments)[["street_id", "length"]]
    summaries = []
    for date, group in scored.groupby("forecast_date", sort=True):
        # One physical street per row: reverse directions must not double-count exposure.
        unique = group.drop_duplicates("street_id").merge(streets, on="street_id", validate="one_to_one")
        modeled = unique.loc[unique.score_source.eq("model_reference_percentile")]
        usable = modeled.loc[modeled.length.notna() & modeled.length.gt(0)]
        mean_score = float(np.average(usable.risk_score, weights=usable.length)) if len(usable) else None
        summaries.append({"area": "FIU–Downtown/Brickell study network", "forecast_date": date,
            "prediction_target": "recorded_flood_report", "validation_status": "experimental_report_proxy",
            "flood_probability_estimate": None,
            "summary_kind": "length_weighted_mean_of_modeled_street_ranks",
            "relative_risk_index": mean_score,
            "modeled_streets": len(modeled), "fallback_streets": len(unique) - len(modeled),
            "streets_in_weighted_index": len(usable), "total_streets": len(unique),
            "modeled_length_m": float(usable.length.sum()),
            "weather_out_of_training_range": bool(group.weather_out_of_training_range.any()),
            "valid_from": group.valid_from.iloc[0], "valid_to": group.valid_to.iloc[0],
            "weather_updated_at": group.weather_updated_at.iloc[0],
            "weather_source": group.weather_source.iloc[0],
            "model_version": group.model_version.iloc[0],
            "note": "Relative susceptibility summary, not the probability that this area floods"})
    if scored.duplicated(["edge_id", "forecast_date"]).any():
        raise ValueError("Duplicate edge-day prediction")
    return scored, summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3, choices=range(1, 8))
    parser.add_argument("--weather", type=Path, help="Offline JSON list; otherwise fetch current forecast")
    parser.add_argument("--model", type=Path, default=OUTPUT / "model.joblib")
    parser.add_argument("--output", type=Path, default=OUTPUT / "forecast")
    args = parser.parse_args()
    weather = json.loads(args.weather.read_text()) if args.weather else fetch_forecasts(args.days)
    segments = pd.read_parquet(config.PROC / "segments.parquet")
    scores, summaries = predict_horizon(segments, joblib.load(args.model), weather)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "weather.json").write_text(json.dumps(weather, indent=2) + "\n")
    scores.to_parquet(args.output / "segment_forecasts.parquet", index=False)
    scores.to_json(args.output / "segment_forecasts.jsonl", orient="records", lines=True, double_precision=12)
    (args.output / "area_forecast.json").write_text(json.dumps(summaries, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"edge_day_predictions": len(scores), "days": len(summaries),
                      "area_forecasts": summaries}, indent=2))


if __name__ == "__main__":
    main()
