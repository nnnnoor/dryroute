import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

from ml.data import assign_groups, baseline, prepare_panel, report_history, street_table
from ml.model import FEATURES, features
from ml.evaluate import evaluate_population
from ml.predict import score_segments
from ml.weather import daily_features


def segments(count=40):
    s = pd.DataFrame({"edge_id": [f"edge_{i}" for i in range(count)],
        "street_id": [f"street_{i}" for i in range(count)], "jurisdiction": "city",
        "is_bridge": False, "is_tunnel": False, "elev_water_only": False,
        "fema_risk_level": 3, "elev_p10": 1., "sink_p90": .1, "length": 100., "drain_count": 3.})
    twin = s.iloc[[0]].copy()
    twin["edge_id"] = "reverse_edge"
    return pd.concat([s, twin], ignore_index=True)


def rainfall():
    return {"timezone": "America/New_York", "daily_units": {"precipitation_sum": "mm"},
            "daily": {"time": pd.date_range("2023-09-27", "2023-10-03").strftime("%Y-%m-%d").tolist(),
                      "precipitation_sum": [1, 2, 3, 4, 5, 6, 7]}}


NO_HISTORY = pd.DataFrame({"street_id": pd.Series(dtype=str), "date": pd.Series(dtype="datetime64[ns]")})


class FakeModel:
    def predict_proba(self, x):
        p = (x.log_rain.to_numpy() + 1) / 100
        return np.column_stack([1-p, p])


class MLTests(unittest.TestCase):
    def test_weather_lags_exclude_current_and_future(self):
        f = daily_features(rainfall())
        row = f.loc[f.date.eq("2023-09-30")].iloc[0]
        self.assertEqual(row.rain_mm, 4)
        self.assertEqual(row.rain_lag1_mm, 3)
        self.assertEqual(row.rain_prior3_mm, 6)

    def test_weather_gaps_units_and_nulls_rejected(self):
        for change in ["gap", "units", "null"]:
            p = rainfall()
            if change == "gap":
                p["daily"]["time"].pop(1)
                p["daily"]["precipitation_sum"].pop(1)
            elif change == "units":
                p["daily_units"]["precipitation_sum"] = "inch"
            else:
                p["daily"]["precipitation_sum"][0] = None
            with self.assertRaises(ValueError):
                daily_features(p)

    def test_baseline_unknown_is_not_safe(self):
        s = segments()
        s["elev_water_only"] = True
        s["fema_risk_level"] = 0
        s["sink_p90"] = np.nan
        score, coverage = baseline(street_table(s))
        self.assertTrue(score.eq(.5).all())
        self.assertTrue(coverage.eq(0).all())

    def test_keys_and_jurisdiction_validation(self):
        s = segments()
        s.loc[1, "edge_id"] = s.edge_id.iloc[0]
        with self.assertRaises(ValueError):
            street_table(s)
        s = segments()
        s.loc[0, "jurisdiction"] = "unknown"
        with self.assertRaises(ValueError):
            street_table(s)

    def test_sampling_deduplication_groups_and_population_weights(self):
        s = segments()
        s.loc[s.street_id.eq("street_39"), "is_bridge"] = True
        reports = pd.DataFrame({"edge_id": ["edge_0", "reverse_edge", "edge_1", "edge_2", "edge_3", "edge_39"],
            "date": ["2023-10-01"] * 3 + ["2023-09-01", "2023-10-02", "2023-10-01"],
            "category": ["flood"] * 4 + ["drain", "flood"], "source": "city",
            "ticket_id": ["tie", "tie", "tie", "old", "drain", "bridge"]})
        with patch("ml.data.WINDOWS", {"city": ("2023-10-01", "2023-10-03")}):
            streets, positives, panel, audit = prepare_panel(s, reports, daily_features(rainfall()), .2)
        self.assertEqual(len(positives), 2)
        self.assertEqual(audit["outside_window_links"], 1)
        self.assertAlmostEqual(panel.sample_weight.sum(), 39 * 3)
        self.assertFalse(panel.duplicated(["street_id", "date"]).any())
        self.assertFalse(panel.street_id.eq("street_39").any())
        self.assertEqual(streets.set_index("street_id").loc[["street_0", "street_1"], "split_group"].nunique(), 1)
        self.assertTrue(panel.groupby("split_group").heldout_street.nunique().eq(1).all())

    def test_feature_allowlist_ignores_labels(self):
        f = street_table(segments())
        f["rain_mm"], f["rain_lag1_mm"], f["rain_prior3_mm"], f["report_rate"] = 5, 2, 4, 1.
        first = features(f)
        f["risk_score"], f["flood_report_days"], f["target"] = 1, 999, 1
        pd.testing.assert_frame_equal(first, features(f))
        self.assertEqual(list(first), FEATURES)

    def test_serving_contract_twins_bridge_weather_and_dst(self):
        s = segments()
        s.loc[s.street_id.eq("street_39"), "is_bridge"] = True
        bundle = {"model": FakeModel(), "reference": np.linspace(0, .1, 1001),
                  "metadata": {"version": "test", "created_at": "2026-01-01T00:00:00Z"},
                  "weather_max": {"rain_mm": 100, "rain_lag1_mm": 100, "rain_prior3_mm": 200},
                  "history": NO_HISTORY}
        weather = {"date": "2026-03-08", "rain_mm": 5, "rain_lag1_mm": 2,
                   "rain_prior3_mm": 4, "source": "test", "updated_at": "2026-03-08T10:00:00Z"}
        out = score_segments(s, bundle, weather)
        self.assertEqual(set(out.edge_id), set(s.edge_id))
        self.assertTrue(out.risk_score.between(0, 1).all())
        self.assertTrue(out.groupby("street_id").risk_score.nunique().eq(1).all())
        self.assertTrue(out.loc[out.street_id.eq("street_39"), "report_probability_estimate"].isna().all())
        self.assertTrue(out.valid_from.iloc[0].endswith("-05:00"))
        self.assertTrue(out.valid_to.iloc[0].endswith("-04:00"))
        wetter = score_segments(s, bundle, {**weather, "rain_mm": 50})
        self.assertGreater(wetter.risk_score.iloc[0], out.risk_score.iloc[0])
        with self.assertRaises(ValueError):
            score_segments(s, bundle, {**weather, "rain_mm": -1})
        with self.assertRaises(ValueError):
            score_segments(s, bundle, {**weather, "rain_prior3_mm": 0})

    def test_full_evaluation_uses_every_eligible_day(self):
        streets = street_table(segments(10))
        streets["heldout_street"] = [False] * 5 + [True] * 5
        positives = pd.DataFrame({"street_id": [streets.street_id.iloc[0]],
                                  "date": pd.to_datetime(["2023-10-01"])})
        with TemporaryDirectory() as folder, patch("ml.evaluate.WINDOWS", {"city": ("2023-10-01", "2023-10-03")}):
            result = evaluate_population(streets, positives, daily_features(rainfall()),
                                         FakeModel(), .01, "future_test", Path(folder))
            saved = pd.read_parquet(Path(folder) / "future_test_predictions.parquet")
        self.assertEqual(len(saved), 15)
        self.assertEqual(result["model"]["overall"]["positive_rows"], 1)
        self.assertEqual(result["model"]["overall"]["represented_street_days"], 15)
        self.assertFalse(saved.duplicated(["street_id", "date"]).any())

    def test_report_history_is_time_safe_and_frozen(self):
        # City window 2022-10-01 to 2024-08-09. Reports on 2023-01-01 and 2023-06-25.
        positives = pd.DataFrame({"street_id": ["a", "a"], "date": pd.to_datetime(["2023-01-01", "2023-06-25"])})
        frame = pd.DataFrame({"street_id": "a", "jurisdiction": "city",
            "date": pd.to_datetime(["2023-06-30", "2022-10-20", "2026-01-01"])})
        rate = report_history(frame, positives)
        self.assertAlmostEqual(rate[0], 1 / (265 / 365.25))  # 06-25 is inside the 7-day gap
        self.assertTrue(np.isnan(rate[1]))  # only 12 days of coverage
        self.assertAlmostEqual(rate[2], 2 / (679 / 365.25))  # coverage ends with the window
        frozen = report_history(frame.iloc[[0]], positives, freeze="2022-12-01")
        self.assertEqual(frozen[0], 0)

    def test_rolling_validation_fits_only_on_the_past(self):
        from ml.train import rolling_validation
        streets = street_table(segments(10)).assign(rain_mm=5., rain_lag1_mm=2., rain_prior3_mm=4., report_rate=1.)
        dates = pd.date_range("2023-01-01", "2023-01-06")
        dev = pd.concat([streets.assign(date=d, sample_weight=1., target=(streets.index == i % 10).astype(int))
                         for i, d in enumerate(dates)], ignore_index=True)
        windows = [("2023-01-02", "2023-01-03", "2023-01-04"), ("2023-01-04", "2023-01-05", "2023-01-06")]
        seen = []
        def fake_fit(name, frame):
            seen.append(frame.date.max())
            return FakeModel()
        with patch("ml.train.ROLLING_WINDOWS", windows), patch("ml.train.fit_model", fake_fit):
            result = rolling_validation(dev, "gradient_boosting")
        self.assertEqual(seen, [pd.Timestamp("2023-01-02"), pd.Timestamp("2023-01-04")])
        self.assertEqual([w["overall"]["evaluated_rows"] for w in result["windows"]], [20, 20])
        with patch("ml.train.ROLLING_WINDOWS", [("2023-01-02", "2023-02-01", "2023-02-02")]), \
             patch("ml.train.fit_model", fake_fit), self.assertRaises(ValueError):
            rolling_validation(dev, "gradient_boosting")

    def test_serving_requires_history(self):
        bundle = {"model": FakeModel(), "reference": np.linspace(0, .1, 1001),
                  "metadata": {"version": "test", "created_at": "2026-01-01T00:00:00Z"},
                  "weather_max": {"rain_mm": 100, "rain_lag1_mm": 100, "rain_prior3_mm": 200}}
        weather = {"date": "2026-03-08", "rain_mm": 5, "rain_lag1_mm": 2,
                   "rain_prior3_mm": 4, "source": "test", "updated_at": "2026-03-08T10:00:00Z"}
        with self.assertRaises(ValueError):
            score_segments(segments(), bundle, weather)


if __name__ == "__main__":
    unittest.main()
