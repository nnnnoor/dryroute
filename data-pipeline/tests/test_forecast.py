import unittest
from datetime import datetime

import numpy as np
import pandas as pd

from ml.forecast import predict_horizon
from ml.weather import forecast_records
from test_ml import NO_HISTORY, FakeModel, rainfall, segments


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {"model": FakeModel(), "reference": np.linspace(0, .1, 1001),
            "metadata": {"version": "test", "created_at": "2023-09-30T00:00:00Z"},
            "weather_max": {"rain_mm": 100, "rain_lag1_mm": 100, "rain_prior3_mm": 200},
            "history": NO_HISTORY}
        self.weather = forecast_records(rainfall(), 3, datetime.fromisoformat("2023-09-30T10:00:00-04:00"))

    def test_future_lags_include_forecast_rain(self):
        self.assertEqual([w["date"] for w in self.weather], ["2023-09-30", "2023-10-01", "2023-10-02"])
        self.assertEqual(self.weather[1]["rain_lag1_mm"], 4)
        self.assertEqual(self.weather[1]["rain_prior3_mm"], 9)
        self.assertEqual(self.weather[2]["rain_prior3_mm"], 12)

    def test_incomplete_horizon_rejected(self):
        with self.assertRaises(ValueError):
            forecast_records(rainfall(), 7, datetime.fromisoformat("2023-09-30T10:00:00-04:00"))

    def test_targets_and_no_twin_double_counting(self):
        s = segments(10)
        out, summary = predict_horizon(s, self.bundle, self.weather)
        self.assertEqual(len(out), 33)
        self.assertTrue(out.flood_probability_estimate.isna().all())
        self.assertTrue(out.prediction_target.eq("recorded_flood_report").all())
        self.assertEqual(summary[0]["total_streets"], 10)
        self.assertEqual(summary[0]["modeled_length_m"], 1000)
        self.assertIsNone(summary[0]["flood_probability_estimate"])
        _, without_twins = predict_horizon(s.drop_duplicates("street_id"), self.bundle, self.weather)
        self.assertEqual(summary, without_twins)

    def test_fallbacks_excluded_from_area_index(self):
        s = segments(10)
        s["is_bridge"] = True
        out, summary = predict_horizon(s, self.bundle, self.weather)
        self.assertTrue(out.report_probability_estimate.isna().all())
        self.assertIsNone(summary[0]["relative_risk_index"])
        self.assertEqual(summary[0]["fallback_streets"], 10)

    def test_duplicate_or_missing_days_rejected(self):
        for weather in [[], [self.weather[0]] * 2, [self.weather[0], self.weather[2]]]:
            with self.assertRaises(ValueError):
                predict_horizon(segments(), self.bundle, weather)


if __name__ == "__main__":
    unittest.main()
