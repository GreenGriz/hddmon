import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hddmon.forecast import predict_device, predict_trends

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def series(values, step_days=1):
    return [(T0 + timedelta(days=i * step_days), v) for i, v in enumerate(values)]


class TestForecast(unittest.TestCase):
    def test_linear_growth_eta(self):
        # 10 -> +1/день, цель 100: ETA = 90 дней
        t = predict_trends(series(list(range(10, 40))), "reallocated")
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t.slope_per_day, 1.0, places=2)
        self.assertAlmostEqual(t.eta_days, 61.0, delta=2)

    def test_flat_no_eta(self):
        t = predict_trends(series([5] * 10), "reallocated")
        self.assertIsNotNone(t)
        self.assertIsNone(t.eta_days)

    def test_too_few_points(self):
        self.assertIsNone(predict_trends(series([1, 2]), "reallocated"))

    def test_already_past_target(self):
        t = predict_trends(series([200, 201, 202]), "reallocated")
        self.assertIsNotNone(t)
        self.assertEqual(t.eta_days, 0.0)

    def test_predict_device_sorts_by_eta(self):
        out = predict_device({
            "reallocated": series([0, 1, 2, 3]),
            "pending": series([0, 0, 0, 0]),
        })
        self.assertEqual(len(out), 1)  # flat pending без ETA не попадает
        self.assertEqual(out[0].column, "reallocated")


if __name__ == "__main__":
    unittest.main()
