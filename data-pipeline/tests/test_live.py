import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import requests

from ml.live import live_conditions, parse_alerts, parse_rain, parse_tide

NOW = datetime(2026, 9, 26, 22, 0, tzinfo=timezone.utc)
DEPARTS = NOW + timedelta(hours=1)


def alert(event, ends, geometry=None):
    return {"geometry": geometry, "properties": {"id": event, "event": event, "severity": "Minor",
        "urgency": "Expected", "onset": None, "ends": ends, "expires": None,
        "headline": event, "areaDesc": "Coastal Miami Dade"}}


def tide_series(start, levels, step=6):
    return [{"t": (start + timedelta(minutes=step * i)).strftime("%Y-%m-%d %H:%M"), "v": str(v)}
            for i, v in enumerate(levels)]


def rain_payload(values, start=NOW - timedelta(hours=2)):
    times = [(start + timedelta(minutes=15 * (i + 1))).strftime("%Y-%m-%dT%H:%M") for i in range(len(values))]
    return {"latitude": 25.76, "longitude": -80.29, "minutely_15_units": {"precipitation": "mm"},
            "minutely_15": {"time": times, "precipitation": values}}


class LiveTests(unittest.TestCase):
    def test_alerts_keep_flood_events_in_effect_at_departure(self):
        payload = {"features": [alert("Coastal Flood Statement", "2026-09-27T13:00:00-04:00"),
            alert("Rip Current Statement", "2026-09-27T13:00:00-04:00"),
            alert("Flood Advisory", "2026-09-26T18:30:00-04:00", {"type": "Polygon"})]}
        out = parse_alerts(payload, DEPARTS)
        self.assertEqual([a["event"] for a in out], ["Coastal Flood Statement"])
        self.assertFalse(out[0]["has_polygon"])

    def test_tide_adds_current_anomaly_to_window_peak(self):
        predictions = {"predictions": tide_series(NOW - timedelta(hours=1), [11. + .01 * i for i in range(41)])}
        observed = {"data": tide_series(NOW - timedelta(minutes=6), [12.5])}
        out = parse_tide(observed, predictions, {"nws_minor": 13.66}, DEPARTS, NOW)
        # Observation at step 9 (predicted 11.09); departure window is steps 10-30.
        self.assertAlmostEqual(out["anomaly_ft"], 12.5 - 11.09)
        self.assertAlmostEqual(out["predicted_peak_ft"], 11.3)
        self.assertAlmostEqual(out["expected_peak_ft"], 11.3 + 12.5 - 11.09)
        self.assertFalse(out["above_minor"])

    def test_stale_tide_observation_uses_predictions_only(self):
        predictions = {"predictions": tide_series(NOW - timedelta(hours=1), [14.] * 41)}
        observed = {"data": tide_series(NOW - timedelta(hours=2), [20.])}
        out = parse_tide(observed, predictions, {"nws_minor": 13.66}, DEPARTS, NOW)
        self.assertFalse(out["anomaly_applied"])
        self.assertEqual(out["expected_peak_ft"], 14.)
        self.assertTrue(out["above_minor"])

    def test_tide_requires_window_coverage(self):
        predictions = {"predictions": tide_series(NOW - timedelta(hours=5), [11.] * 10)}
        with self.assertRaises(ValueError):
            parse_tide({"data": []}, predictions, {"nws_minor": 13.66}, DEPARTS, NOW)

    def test_rain_sums_use_preceding_quarter_hours(self):
        # 8 past quarters (2 mm each), 4 before departure (1 mm), 4 during the trip (3 mm).
        out = parse_rain(rain_payload([2.] * 8 + [1.] * 4 + [3.] * 4), DEPARTS, NOW)[0]
        self.assertEqual((out["past_2h_mm"], out["before_departure_mm"], out["during_trip_mm"]), (16., 4., 12.))
        self.assertEqual(out["max_15min_mm"], 3.)

    def test_rain_gaps_are_unknown_not_zero(self):
        out = parse_rain(rain_payload([0.] * 8 + [None] * 8), DEPARTS, NOW)[0]
        self.assertIsNone(out["during_trip_mm"])
        self.assertIsNone(out["max_15min_mm"])
        self.assertIsNone(parse_rain(rain_payload([0.] * 8), DEPARTS, NOW)[0]["during_trip_mm"])

    def test_failed_source_is_unavailable(self):
        with patch("ml.live.requests.get", side_effect=requests.ConnectionError("down")):
            out = live_conditions(DEPARTS, now=NOW)
        for name in ["nws_alerts", "tide", "rain"]:
            self.assertEqual(out[name]["status"], "unavailable")

    def test_departure_must_be_near_future_and_aware(self):
        for departs in [NOW - timedelta(minutes=1), NOW + timedelta(hours=4), DEPARTS.replace(tzinfo=None)]:
            with self.assertRaises(ValueError):
                live_conditions(departs, now=NOW)


if __name__ == "__main__":
    unittest.main()
