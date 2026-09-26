"""Prepare sampled street-days, select on validation, and evaluate untouched tests."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from threadpoolctl import threadpool_limits

import config
from ml.data import baseline, prepare_panel
from ml.model import FEATURES, features, fit_model, reference_quantiles
from ml.settings import MODEL_VERSION, OUTPUT, ROLLING_WINDOWS, TRAIN_END, VALIDATION_END, WEATHER_CACHE
from ml.weather import RAIN_FEATURES, load_archive


def metrics(frame, probability, is_probability=True):
    y, w = frame.target.to_numpy(), frame.sample_weight.to_numpy()
    both = len(np.unique(y)) == 2
    return {"evaluated_rows": len(frame), "positive_rows": int(y.sum()),
        "represented_street_days": float(w.sum()), "prevalence": float(np.average(y, weights=w)),
        "average_precision": float(average_precision_score(y, probability, sample_weight=w)) if both else None,
        "roc_auc": float(roc_auc_score(y, probability, sample_weight=w)) if both else None,
        "brier_score": float(brier_score_loss(y, probability, sample_weight=w)) if is_probability else None}


def report_metrics(frame, predictions, is_probability=True):
    out = {"overall": metrics(frame, predictions, is_probability)}
    for name in ["city", "county"]:
        mask = frame.jurisdiction.eq(name).to_numpy()
        if mask.any():
            out[name] = metrics(frame.loc[mask], predictions[mask], is_probability)
    return out


def rolling_validation(development, name):
    """Fit through each cutoff and score the next quarter.

    Selection uses the median window AP: a single top-ranked report can dominate one
    window's AP (Jul-Sep 2023 did), but not the median of three.
    """
    windows = []
    for train_end, start, end in ROLLING_WINDOWS:
        fit_rows = development.loc[development.date <= pd.Timestamp(train_end)]
        rows = development.loc[development.date.between(pd.Timestamp(start), pd.Timestamp(end))]
        if rows.target.nunique() != 2:
            raise ValueError(f"Validation window {start}–{end} needs positive and negative labels")
        p = fit_model(name, fit_rows).predict_proba(features(rows))[:, 1]
        windows.append({"train_through": train_end, "validate": [start, end], **report_metrics(rows, p)})
    return {"windows": windows,
            "median_average_precision": float(np.median([w["overall"]["average_precision"] for w in windows])),
            "mean_roc_auc": float(np.mean([w["overall"]["roc_auc"] for w in windows]))}


def run(output=OUTPUT, negative_fraction=.02):
    output.mkdir(parents=True, exist_ok=True)
    segments_path, reports_path = config.PROC / "segments.parquet", config.PROC / "flood_reports.parquet"
    segments, reports = pd.read_parquet(segments_path), pd.read_parquet(reports_path)
    weather = load_archive()
    streets, positives, panel, audit = prepare_panel(segments, reports, weather, negative_fraction)
    panel.to_parquet(output / "training_panel.parquet", index=False)
    positives.to_parquet(output / "positive_street_days.parquet", index=False)
    weather.to_parquet(output / "weather_daily.parquet", index=False)
    streets.to_parquet(output / "streets.parquet", index=False)
    print("Prepared:", json.dumps(audit), flush=True)
    development = panel.loc[panel.split.isin(["train", "validation"])]
    candidates = {}
    for name in ["logistic", "gradient_boosting"]:
        candidates[name] = rolling_validation(development, name)
        print("Rolling validation", name, json.dumps({k: candidates[name][k] for k in ["median_average_precision", "mean_roc_auc"]}),
              [round(w["overall"]["average_precision"], 5) for w in candidates[name]["windows"]], flush=True)
    chosen = max(candidates, key=lambda n: (candidates[n]["median_average_precision"], candidates[n]["mean_roc_auc"]))
    model = fit_model(chosen, development)
    development_probability = model.predict_proba(features(development))[:, 1]
    reference = reference_quantiles(development_probability, development.sample_weight)
    train_prevalence = float(np.average(development.target, weights=development.sample_weight))
    from ml.evaluate import evaluate_population
    evaluations = {}
    for split in ["future_test", "unseen_streets_test"]:
        evaluations[split] = evaluate_population(streets, positives, weather, model, train_prevalence, split, output)
        print(split, json.dumps(evaluations[split]["model"]["overall"]), flush=True)
    fingerprints = {str(p.relative_to(config.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [segments_path, reports_path, WEATHER_CACHE]}
    metadata = {
        "version": MODEL_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental", "selected_model": chosen, "features": FEATURES,
        "prediction_target": "recorded_flood_report",
        "training_end": TRAIN_END, "refit_through": VALIDATION_END,
        "audit": audit, "data_sha256": fingerprints,
        "sklearn_version": sklearn.__version__, "pandas_version": pd.__version__,
        "split_summary": panel.groupby("split").agg(rows=("target", "size"), positives=("target", "sum"), represented_days=("sample_weight", "sum")).to_dict("index"),
        "selection_rule": "Median average precision over rolling quarterly validation windows; mean ROC-AUC breaks ties",
        "validation": candidates, "tests": evaluations,
        "test_evaluation": "Full eligible street-day populations; no negative sampling in tests",
        "limitations": ["Labels are reports, not confirmed flooding; unreported days are noisy negatives",
            "Validation uses inverse-probability weighted sampled negatives; final test metrics use all negatives",
            "Historical rainfall is reanalysis, not a forecast backtest",
            "One weather point for the whole area; daily timing cannot resolve hourly commutes",
            "Static layers reflect current data, not necessarily their historical state",
            "Held-out groups prevent twin/ticket leakage, not all nearby-street spatial dependence",
            "Bridge labels are structurally absent; bridge scores use an explicit policy fallback",
            "Relative scores are ranks, not physical flood probabilities or road-passability guarantees",
            "Report history measures where people report, not only where it floods; serving history ends with the last 311 record (2024-08-09)"],
    }
    bundle = {"model": model, "reference": reference, "metadata": metadata,
              "weather_max": development[RAIN_FEATURES].max().to_dict(),
              "history": positives[["street_id", "date"]].copy()}
    joblib.dump(bundle, output / "model.joblib")
    (output / "evaluation.json").write_text(json.dumps(metadata, indent=2) + "\n")
    baseline_scores, coverage = baseline(streets)
    base = streets[["street_id", "is_bridge"]].copy()
    base["risk_score"], base["feature_coverage"] = baseline_scores, coverage
    base.loc[base.is_bridge, "risk_score"] = base.loc[base.is_bridge, "risk_score"].clip(upper=config.BRIDGE_RISK_CAP)
    base["score_kind"] = "physical_baseline_index"
    base["score_note"] = np.where(base.is_bridge, "bridge_policy_cap", np.where(coverage < 1, "missing_features", ""))
    segments[["edge_id", "street_id"]].merge(base, on="street_id", validate="many_to_one").to_parquet(output / "baseline_segments.parquet", index=False)
    plot_results(output, metadata, baseline_scores)
    return bundle


def plot_results(output, metadata, scores):
    os.environ.setdefault("MPLCONFIGDIR", str(output / ".matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    axes[0].hist(scores, bins=30, color="#238b8d")
    axes[0].set(xlabel="Physical baseline score", ylabel="Streets", title="Baseline distribution")
    groups = list(metadata["tests"])
    for i, source in enumerate(["model", "physical_baseline", "constant_baseline"]):
        values = [metadata["tests"][g][source]["overall"]["average_precision"] for g in groups]
        axes[1].bar(np.arange(len(groups)) + (i-1)*.25, values, width=.25, label=source)
    axes[1].set_xticks(range(len(groups)), ["Later dates", "Unseen streets + later dates"])
    axes[1].set(ylabel="Average precision", title="Held-out evaluation")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    config.CHECKS.mkdir(parents=True, exist_ok=True)
    fig.savefig(config.CHECKS / "ml_evaluation.png", dpi=140)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--negative-fraction", type=float, default=.02)
    args = parser.parse_args()
    with threadpool_limits(limits=2):
        run(args.output, args.negative_fraction)


if __name__ == "__main__":
    main()
