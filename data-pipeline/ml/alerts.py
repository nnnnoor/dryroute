"""Select an experimental report-alert cutoff on validation only, then freeze it."""
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve
from threadpoolctl import threadpool_limits

from ml.evaluate import evaluate_population
from ml.model import features, fit_model
from ml.settings import OUTPUT, TRAIN_END, VALIDATION_END


def classification_metrics(y, probability, threshold, weight=None):
    y, probability = np.asarray(y), np.asarray(probability)
    w = np.ones(len(y)) if weight is None else np.asarray(weight, dtype=float)
    predicted = probability >= threshold
    tp = float(w[(y == 1) & predicted].sum())
    fp = float(w[(y == 0) & predicted].sum())
    fn = float(w[(y == 1) & ~predicted].sum())
    tn = float(w[(y == 0) & ~predicted].sum())
    def ratio(a, b):
        return a / b if b else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "false_positive_rate": ratio(fp, fp + tn),
            "false_discovery_rate": ratio(fp, tp + fp),
            "accuracy": ratio(tp + tn, w.sum()),
            "alert_rate": ratio(tp + fp, w.sum()),
            "f2": ratio(5 * tp, 5 * tp + 4 * fn + fp)}


def choose_threshold(y, probability, weight):
    y, probability, weight = np.asarray(y), np.asarray(probability), np.asarray(weight)
    if set(np.unique(y)) != {0, 1}:
        raise ValueError("Threshold validation needs both positive and negative labels")
    if not np.isfinite(probability).all() or ((probability < 0) | (probability > 1)).any():
        raise ValueError("Probabilities must be finite and within 0–1")
    if not np.isfinite(weight).all() or (weight <= 0).any():
        raise ValueError("Weights must be finite and positive")
    precision, recall, thresholds = precision_recall_curve(y, probability, sample_weight=weight)
    denominator = 4 * precision[:-1] + recall[:-1]
    f2 = np.divide(5 * precision[:-1] * recall[:-1], denominator,
                   out=np.zeros_like(denominator), where=denominator > 0)
    # Deterministic tie break: higher cutoff/fewer alerts.
    return float(thresholds[np.flatnonzero(f2 == f2.max())[-1]])


def budget_threshold(probability, max_alert_rate=.01):
    """Lowest cutoff admitting no more than the full-validation alert budget.

    Include complete tied-score groups; never split identical probabilities.
    A cutoff just above 1 represents abstention when even the top tie exceeds budget.
    """
    p = np.asarray(probability, dtype=float)
    if p.ndim != 1 or not len(p) or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Expected nonempty finite probabilities in [0,1]")
    if not 0 < max_alert_rate <= 1:
        raise ValueError("Alert budget must be in (0,1]")
    values, counts = np.unique(p, return_counts=True)
    within = np.cumsum(counts[::-1]) <= int(np.floor(len(p) * max_alert_rate))
    return float(values[::-1][np.flatnonzero(within)[-1]]) if within.any() else float(np.nextafter(1., 2.))


def run(output=OUTPUT):
    bundle = joblib.load(output / "model.joblib")
    panel = pd.read_parquet(output / "training_panel.parquet")
    train = panel.loc[panel.split.eq("train")]
    # The ranking model was refit on validation labels. It cannot supply honest
    # validation predictions. Keep a separate alert estimator trained only on train.
    streets, positives, weather = (pd.read_parquet(output / name) for name in
        ["streets.parquet", "positive_street_days.parquet", "weather_daily.parquet"])
    destination = output / "alerts"
    destination.mkdir(parents=True, exist_ok=True)
    if "alert_policy" in bundle and not (destination / "previous_policy.json").exists():
        (destination / "previous_policy.json").write_text(json.dumps(bundle["alert_policy"], indent=2) + "\n")
    prevalence = float(np.average(train.target, weights=train.sample_weight))
    candidates, fitted = {}, {}
    for name in ["logistic", "gradient_boosting", "balanced_boosting"]:
        fitted[name] = fit_model(name, train)
        evaluate_population(streets, positives, weather, fitted[name], prevalence,
            f"validation_{name}", destination,
            start_date=(pd.Timestamp(TRAIN_END) + pd.Timedelta(days=1)).date().isoformat(), end_date=VALIDATION_END)
        validation = pd.read_parquet(destination / f"validation_{name}_predictions.parquet")
        cutoff = budget_threshold(validation.report_probability_estimate)
        candidates[name] = {"threshold": cutoff, "metrics": classification_metrics(
            validation.target, validation.report_probability_estimate, cutoff)}
        print("Full validation", name, json.dumps(candidates[name]), flush=True)
    chosen = max(candidates, key=lambda n: (candidates[n]["metrics"]["recall"] or 0,
                                            candidates[n]["metrics"]["precision"] or 0))
    model, threshold = fitted[chosen], candidates[chosen]["threshold"]
    metadata = {"status": "experimental_report_alert", "prediction_target": "recorded_flood_report",
        "threshold": threshold, "comparison": ">=", "training_through": TRAIN_END,
        "policy_version": "full_validation_budget_v2", "selected_model": chosen,
        "selection_rule": "Maximum full-validation recall with at most 1% validation road-days flagged; precision breaks model ties",
        "validation_alert_budget": .01,
        "selection_note": "Budget is a prototype tradeoff, not a safety threshold or a guaranteed future rate. Test periods were examined in earlier iterations: results are retrospective, not a fresh blind test.",
        "validation_candidates": candidates,
        "validation": candidates[chosen]["metrics"],
        "tests": {}}
    for split in ["future_test", "unseen_streets_test"]:
        evaluate_population(streets, positives, weather, model, prevalence, split, destination)
        predictions = pd.read_parquet(destination / f"{split}_predictions.parquet")
        predictions = predictions.merge(streets[["street_id", "jurisdiction"]], on="street_id", validate="many_to_one")
        subsets = [("overall", predictions), *list(predictions.groupby("jurisdiction"))]
        metadata["tests"][split] = {name: classification_metrics(rows.target, rows.report_probability_estimate, threshold)
                                    for name, rows in subsets}
        print(split, json.dumps(metadata["tests"][split]["overall"]), flush=True)
    bundle["alert_model"] = model
    bundle["alert_policy"] = metadata
    joblib.dump(bundle, output / "model.joblib")
    (destination / "evaluation.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


if __name__ == "__main__":
    with threadpool_limits(limits=2):
        run()
