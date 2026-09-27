import unittest

import numpy as np

from ml.alerts import budget_threshold, choose_threshold, classification_metrics
from ml.predict import score_segments
from ml.model import PriorAdjustedClassifier
from test_ml import NO_HISTORY, FakeModel, segments


class AlertTests(unittest.TestCase):
    def test_balanced_odds_are_not_exposed_as_probabilities(self):
        class ConstantBalanced:
            def predict_proba(self, x):
                return np.array([[.5, .5], [.1, .9]])
        adjusted = PriorAdjustedClassifier(ConstantBalanced(), 9).predict_proba(None)
        np.testing.assert_allclose(adjusted[:, 1], [.1, .5])
        np.testing.assert_allclose(adjusted.sum(axis=1), 1)

    def test_budget_respects_ties_and_rejects_invalid_inputs(self):
        p = np.array([.9, .8, .8, .1])
        cutoff = budget_threshold(p, .5)
        self.assertEqual(cutoff, .9)
        self.assertLessEqual((p >= cutoff).mean(), .5)
        self.assertGreater(budget_threshold([1., 1., 1., 1.], .01), 1.)
        for p in [[], [np.nan], [-1], [1.1]]:
            with self.assertRaises(ValueError):
                budget_threshold(p)
        with self.assertRaises(ValueError):
            budget_threshold([.1], 0)

    def test_budget_does_not_use_labels(self):
        p = np.linspace(0, 1, 1000)
        cutoff = budget_threshold(p)
        self.assertEqual(int((p >= cutoff).sum()), 10)

    def test_confusion_matrix_and_boundary(self):
        result = classification_metrics([1, 1, 0, 0], [.9, .1, .5, .2], .5)
        self.assertEqual([result[k] for k in ["tp", "fp", "fn", "tn"]], [1, 1, 1, 1])
        self.assertEqual(result["precision"], .5)
        self.assertEqual(result["recall"], .5)
        self.assertEqual(result["false_positive_rate"], .5)

    def test_weighted_selection_maximizes_validation_f2(self):
        y, p, w = [1, 0, 1, 0], [.9, .8, .4, .1], [1, 100, 1, 100]
        cutoff = choose_threshold(y, p, w)
        self.assertEqual(cutoff, .9)
        self.assertEqual(classification_metrics(y, p, .4, w)["fp"], 100)
        with self.assertRaises(ValueError):
            choose_threshold([0, 0], [.1, .2], [1, 1])

    def test_no_alerts_undefined_precision(self):
        result = classification_metrics([0, 1], [.1, .2], .9)
        self.assertIsNone(result["precision"])
        self.assertEqual(result["recall"], 0)
        self.assertEqual(result["false_positive_rate"], 0)

    def test_alerts_use_separate_probability_and_skip_bridges(self):
        bundle = {"model": FakeModel(), "reference": np.linspace(0, .1, 1001),
            "metadata": {"version": "test", "created_at": "2023-09-30T00:00:00Z"},
            "weather_max": {"rain_mm": 100, "rain_lag1_mm": 100, "rain_prior3_mm": 200},
            "alert_model": FakeModel(), "alert_policy": {"threshold": .01}, "history": NO_HISTORY}
        s = segments(4)
        s.loc[s.street_id.eq("street_3"), "is_bridge"] = True
        w = {"date": "2026-09-26", "rain_mm": 5, "rain_lag1_mm": 0, "rain_prior3_mm": 0,
             "source": "test", "updated_at": "2026-09-26T10:00:00Z"}
        result = score_segments(s, bundle, w)
        self.assertTrue(result.loc[result.street_id.ne("street_3"), "report_alert"].all())
        self.assertTrue(result.loc[result.street_id.eq("street_3"), "report_alert"].isna().all())


if __name__ == "__main__":
    unittest.main()
