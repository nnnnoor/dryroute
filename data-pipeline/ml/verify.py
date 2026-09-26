"""Check generated artifacts against source IDs, split rules and weather scenarios."""
import hashlib
import json
import xml.etree.ElementTree as ET

import joblib
import pandas as pd

import config
from ml.predict import score_segments
from ml.settings import OUTPUT, TRAIN_END, VALIDATION_END


def main():
    segments = pd.read_parquet(config.PROC / "segments.parquet")
    bundle = joblib.load(OUTPUT / "model.joblib")
    for relative, expected in bundle["metadata"]["data_sha256"].items():
        assert hashlib.sha256((config.ROOT / relative).read_bytes()).hexdigest() == expected, relative
    graph = ET.parse(config.PROC / "graph.graphml")
    graph_ids = {f"{e.attrib['source']}_{e.attrib['target']}_{e.attrib['id']}"
                 for e in graph.iter("{http://graphml.graphdrawing.org/xmlns}edge")}
    assert graph_ids == set(segments.edge_id), "Graph and segment IDs differ"
    panel = pd.read_parquet(OUTPUT / "training_panel.parquet")
    assert not panel.duplicated(["street_id", "date"]).any()
    assert panel.loc[panel.split.eq("train"), "date"].max() <= pd.Timestamp(TRAIN_END)
    assert panel.loc[panel.split.eq("validation"), "date"].min() > pd.Timestamp(TRAIN_END)
    assert panel.loc[panel.split.eq("validation"), "date"].max() <= pd.Timestamp(VALIDATION_END)
    assert panel.loc[panel.split.isin(["future_test", "unseen_streets_test"]), "date"].min() > pd.Timestamp(VALIDATION_END)
    development = set(panel.loc[panel.split.isin(["train", "validation"]), "split_group"])
    unseen = set(panel.loc[panel.split.eq("unseen_streets_test"), "split_group"])
    assert development.isdisjoint(unseen)
    assert not panel.is_bridge.any()
    weather = json.loads((config.ROOT / "ml" / "example_weather.json").read_text())
    dry = score_segments(segments, bundle, {**weather, "rain_mm": 0., "rain_lag1_mm": 0., "rain_prior3_mm": 0.})
    wet = score_segments(segments, bundle, {**weather, "rain_mm": 50., "rain_lag1_mm": 20., "rain_prior3_mm": 40.})
    for scores in [dry, wet]:
        assert set(scores.edge_id) == graph_ids and scores.edge_id.is_unique
        assert scores.risk_score.between(0, 1).all()
        assert scores.groupby("street_id").risk_score.nunique().eq(1).all()
    modeled = dry.score_source.eq("model_reference_percentile")
    summary = {"segments_verified": len(dry), "sampled_rows_verified": len(panel),
        "source_hashes_match": True, "graph_ids_match": True, "split_rules_pass": True,
        "dry_median_score": float(dry.loc[modeled, "risk_score"].median()),
        "wet_median_score": float(wet.loc[modeled, "risk_score"].median()),
        "modeled_edges_increasing_in_wet_scenario": int((wet.loc[modeled, "risk_score"] > dry.loc[modeled, "risk_score"]).sum()),
        "modeled_edges_decreasing_in_wet_scenario": int((wet.loc[modeled, "risk_score"] < dry.loc[modeled, "risk_score"]).sum())}
    assert (wet.loc[modeled, "risk_score"] != dry.loc[modeled, "risk_score"]).any(), "Model does not respond to this weather scenario"
    (OUTPUT / "verification.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
