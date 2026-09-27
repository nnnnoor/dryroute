"""Score every street and publish the run to Atlas: risk_scores + risk_runs (docs/api-contracts.md).

    python -m ml.publish --scenario live      # today's forecast rain (Open-Meteo daily); rerun at least every 3 h
    python -m ml.publish --scenario storm     # fixed heavy rain, for the demo toggle
    python -m ml.publish --dry-run            # score and print the summary, write nothing

Labels come from the alert model's frozen cutoff (bundle["alert_policy"]["threshold"]), not from fixed
risk_score cutoffs: risk_score is a percentile (a typical street is ~0.5 even on a dry day), while the
alert probability rises with rain. high = at or above the cutoff, medium = at or above half of it.
Bridges and unsupported streets get no label, so the backend falls back to its cutoffs (they score <= 0.1).

Write order: this run's scores first (_id "<run_id>|<street_id>"), then its risk_runs doc, then the older
runs' scores are deleted. The backend serves the newest run doc, so it never sees a half-written set.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne

import config
from ml.predict import score_segments
from ml.settings import OUTPUT, TIMEZONE
from ml.weather import RAIN_FEATURES, fetch_forecast

MEDIUM_FRACTION = 0.5  # medium = alert probability >= half the alert cutoff

# Demo storm: heavy rain inside the model's training range (max 101 mm/day)
STORM_DAILY = {"rain_mm": 80.0, "rain_lag1_mm": 30.0, "rain_prior3_mm": 60.0}


def labels(alert_probability, threshold):
    """low | medium | high per street; None where the alert model gave no estimate."""
    p = pd.Series(alert_probability, dtype=float)
    out = pd.Series(np.select([p >= threshold, p >= threshold * MEDIUM_FRACTION], ["high", "medium"], "low"),
                    index=p.index, dtype=object)
    return out.where(p.notna(), None)


def weather_for(scenario, now):
    """Daily model input for the scenario (ml.predict.score_segments format)."""
    if scenario == "live":
        return fetch_forecast()
    today = now.astimezone(ZoneInfo(TIMEZONE)).date().isoformat()
    return {"date": today, **STORM_DAILY, "source": "demo_storm_not_live_weather", "updated_at": now.isoformat()}


def build_run(scenario, segments, bundle, now):
    daily = weather_for(scenario, now)
    rain = {c: daily[c] for c in RAIN_FEATURES}  # the run's `rain` block: ML's daily fields (api-contracts.md)
    scored = score_segments(segments, bundle, daily).drop_duplicates("street_id")
    threshold = bundle["alert_policy"]["threshold"]
    scored["risk_label"] = labels(scored["alert_report_probability_estimate"].to_numpy(), threshold).to_numpy()
    run_id = f"{now:%Y-%m-%dT%H:%MZ}-{scenario}"
    scores = [{"_id": f"{run_id}|{s}", "street_id": s, "scenario": scenario, "run_id": run_id,
               "risk_score": round(float(r), 6), "risk_label": label, "score_source": src}
              for s, r, label, src in zip(scored["street_id"], scored["risk_score"], scored["risk_label"],
                                          scored["score_source"])]
    counts = scored["risk_label"].fillna("none").value_counts().to_dict()
    run = {"_id": run_id, "scenario": scenario, "computed_at": now,
           "model_version": bundle["metadata"]["version"], "rain": rain, "streets_scored": len(scores),
           "labels": {k: int(counts.get(k, 0)) for k in ("low", "medium", "high", "none")},
           "label_rule": {"kind": "alert_probability", "high_at": threshold,
                          "medium_at": threshold * MEDIUM_FRACTION,
                          "policy_version": bundle["alert_policy"].get("policy_version")},
           "daily_weather": daily,
           "weather_out_of_training_range": bool(scored["weather_out_of_training_range"].any())}
    return run, scores


def publish(db, run, scores):
    db.risk_scores.create_index([("run_id", ASCENDING)])
    db.risk_runs.create_index([("scenario", ASCENDING), ("computed_at", DESCENDING)])
    db.risk_scores.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in scores], ordered=False)
    written = db.risk_scores.count_documents({"run_id": run["_id"]})
    if written != len(scores):
        raise RuntimeError(f"{written} of {len(scores)} scores written; run doc not published")
    db.risk_runs.replace_one({"_id": run["_id"]}, run, upsert=True)
    old = db.risk_scores.delete_many({"scenario": run["scenario"], "run_id": {"$ne": run["_id"]}}).deleted_count
    print(f"published {run['_id']}: {written} scores; deleted {old} scores from older {run['scenario']} runs")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=["live", "storm"], default="live")
    parser.add_argument("--dry-run", action="store_true", help="score and summarize, write nothing")
    parser.add_argument("--log", type=Path, help="append output and errors here (for scheduled runs, e.g. pythonw)")
    args = parser.parse_args()
    if args.log:
        sys.stdout = sys.stderr = open(args.log, "a", encoding="utf-8", buffering=1)
        print(f"\n--- {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {args.scenario}")

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    segments = pd.read_parquet(config.PROC / "segments.parquet")
    run, scores = build_run(args.scenario, segments, joblib.load(OUTPUT / "model.joblib"), now)
    print(json.dumps(run, indent=2, default=str) if not args.log else f"{run['_id']}: labels {run['labels']}")
    if args.dry_run:
        print(f"dry run: {len(scores)} scores not written. Sample: {scores[0]}")
        return
    load_dotenv(config.ROOT / ".env")
    client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=15000)
    publish(client[config.MONGO_DB], run, scores)


if __name__ == "__main__":
    main()
