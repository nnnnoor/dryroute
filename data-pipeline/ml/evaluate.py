"""Evaluate every eligible held-out street-day, without negative sampling."""
import json

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from threadpoolctl import threadpool_limits

from ml.data import baseline
from ml.model import features
from ml.settings import OUTPUT, VALIDATION_END, WINDOWS
from ml.weather import RAIN_FEATURES


def evaluate_population(streets, positives, weather, model, prevalence, split, output,
                        start_date=None, end_date=None):
    from ml.train import report_metrics
    heldout = split == "unseen_streets_test"
    candidates = streets.loc[~streets.is_bridge & streets.heldout_street.eq(heldout)]
    positive_map = positives.groupby("date").street_id.agg(set).to_dict()
    weather = weather.set_index("date", verify_integrity=True)
    pieces, probabilities, physical_scores = [], [], []
    writer = None
    try:
        for jurisdiction, (window_start, end) in WINDOWS.items():
            local = candidates.loc[candidates.jurisdiction.eq(jurisdiction)].copy()
            if local.empty:
                continue
            base = baseline(local)[0].to_numpy()
            start = max(pd.Timestamp(window_start), pd.Timestamp(start_date) if start_date else pd.Timestamp(VALIDATION_END) + pd.Timedelta(days=1))
            stop = min(pd.Timestamp(end), pd.Timestamp(end_date) if end_date else pd.Timestamp(end))
            for date in pd.date_range(start, stop):
                frame = local.copy()
                for column in RAIN_FEATURES:
                    frame[column] = weather.loc[date, column]
                probability = model.predict_proba(features(frame))[:, 1]
                target = frame.street_id.isin(positive_map.get(date, set())).to_numpy(dtype=np.int8)
                compact = pd.DataFrame({"target": target, "sample_weight": 1.,
                                        "jurisdiction": pd.Categorical([jurisdiction] * len(frame), categories=["city", "county"])})
                pieces.append(compact)
                probabilities.append(probability)
                physical_scores.append(base)
                saved = pd.DataFrame({"street_id": frame.street_id.to_numpy(), "date": date,
                                      "target": target, "report_probability_estimate": probability,
                                      "physical_baseline": base})
                arrow = pa.Table.from_pandas(saved, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(output / f"{split}_predictions.parquet", arrow.schema)
                writer.write_table(arrow)
    finally:
        if writer is not None:
            writer.close()
    frame = pd.concat(pieces, ignore_index=True)
    return {
        "model": report_metrics(frame, np.concatenate(probabilities)),
        "physical_baseline": report_metrics(frame, np.concatenate(physical_scores), False),
        "constant_baseline": report_metrics(frame, np.full(len(frame), prevalence)),
    }


def refresh(output=OUTPUT):
    bundle = joblib.load(output / "model.joblib")
    panel = pd.read_parquet(output / "training_panel.parquet")
    development = panel.loc[panel.split.isin(["train", "validation"])]
    prevalence = float(np.average(development.target, weights=development.sample_weight))
    streets, positives, weather = (pd.read_parquet(output / name) for name in
        ["streets.parquet", "positive_street_days.parquet", "weather_daily.parquet"])
    result = {}
    for split in ["future_test", "unseen_streets_test"]:
        result[split] = evaluate_population(streets, positives, weather, bundle["model"], prevalence, split, output)
        print(split, json.dumps(result[split]["model"]["overall"]), flush=True)
    bundle["metadata"]["tests"] = result
    bundle["metadata"]["test_evaluation"] = "Full eligible street-day populations; no negative sampling in tests"
    bundle["metadata"]["limitations"] = [s for s in bundle["metadata"]["limitations"] if not s.startswith("Metrics use")]
    bundle["metadata"]["limitations"].append("Validation uses inverse-probability weighted sampled negatives; final test metrics use all negatives")
    joblib.dump(bundle, output / "model.joblib")
    (output / "evaluation.json").write_text(json.dumps(bundle["metadata"], indent=2) + "\n")
    from ml.train import plot_results
    plot_results(output, bundle["metadata"], baseline(streets)[0])
    return bundle


if __name__ == "__main__":
    with threadpool_limits(limits=2):
        refresh()
