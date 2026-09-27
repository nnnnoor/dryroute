import unittest

import numpy as np

from ml.publish import labels


class PublishTests(unittest.TestCase):
    def test_labels_follow_alert_cutoff(self):
        out = labels(np.array([0.002, 0.001, 0.0006, 0.0004, np.nan]), threshold=0.001)
        self.assertEqual(out.tolist(), ["high", "high", "medium", "low", None])


if __name__ == "__main__":
    unittest.main()
