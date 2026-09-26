"""Explicit feature allowlist shared by training and inference."""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.data import baseline
from ml.weather import RAIN_FEATURES

FEATURES = ["fema_risk_level", "elev_p10", "sink_p90", "log_length", "log_drain_count",
            "is_tunnel", "city", "log_rain", "log_rain_lag1", "log_rain_prior3", "rain_x_static",
            "log_report_rate", "rain_x_history"]


def features(frame):
    risk, _ = baseline(frame)
    x = frame[["fema_risk_level", "elev_p10", "sink_p90", "is_tunnel"]].copy()
    x["log_length"] = np.log1p(frame.length)
    x["log_drain_count"] = np.log1p(frame.drain_count)
    x["city"] = frame.jurisdiction.eq("city").astype(float)
    for source, target in zip(RAIN_FEATURES, ["log_rain", "log_rain_lag1", "log_rain_prior3"]):
        x[target] = np.log1p(frame[source])
    x["rain_x_static"] = x.log_rain * risk
    # report_rate comes from ml.data.report_history: time-safe, NaN when coverage is too short.
    x["log_report_rate"] = np.log1p(frame.report_rate)
    x["rain_x_history"] = x.log_rain * x.log_report_rate.fillna(0)
    return x[FEATURES].astype(float)


def make_model(name):
    if name == "logistic":
        estimator = LogisticRegression(max_iter=1000, random_state=42)
    elif name in {"gradient_boosting", "balanced_boosting"}:
        estimator = HistGradientBoostingClassifier(max_iter=120, max_leaf_nodes=7,
            min_samples_leaf=60, l2_regularization=1, early_stopping=False, random_state=42)
    else:
        raise ValueError(f"Unknown model {name}")
    return make_pipeline(SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
                         StandardScaler(), estimator)


def fit_model(name, frame):
    if frame.target.nunique() != 2:
        raise ValueError("Training requires positive and negative labels")
    model = make_model(name)
    final_step = model.steps[-1][0]
    weights = frame.sample_weight / frame.sample_weight.mean()
    multiplier = 1.
    if name == "balanced_boosting":
        multiplier = float(weights.loc[frame.target.eq(0)].sum() / weights.loc[frame.target.eq(1)].sum())
        weights = weights * np.where(frame.target.eq(1), multiplier, 1.)
        weights = weights / weights.mean()
    model.fit(features(frame), frame.target, **{f"{final_step}__sample_weight": weights})
    return PriorAdjustedClassifier(model, multiplier) if name == "balanced_boosting" else model


class PriorAdjustedClassifier:
    """Undo the training class-weight odds shift; this is not empirical calibration."""
    def __init__(self, model, multiplier):
        self.model, self.multiplier = model, multiplier

    def predict_proba(self, x):
        p = self.model.predict_proba(x)[:, 1]
        adjusted = p / (self.multiplier * (1 - p) + p)
        return np.column_stack([1 - adjusted, adjusted])


def reference_quantiles(probability, weight):
    order = np.argsort(probability)
    p, w = np.asarray(probability)[order], np.asarray(weight)[order]
    cumulative = np.cumsum(w) / np.sum(w)
    return np.interp(np.linspace(0, 1, 1001), cumulative, p)


def relative_score(probability, reference):
    # Collapse ties to midranks rather than arbitrarily mapping identical values to 1.
    unique, inverse = np.unique(reference, return_inverse=True)
    ranks = np.bincount(inverse, weights=np.linspace(0, 1, len(reference))) / np.bincount(inverse)
    return np.interp(probability, unique, ranks, left=0, right=1)
