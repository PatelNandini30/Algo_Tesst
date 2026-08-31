"""
Regression for the "above N combos -> skip tradesheets ZIP + WOW/MOM" feature.

The worker (services/optimizer/parallel.py) skips the per-combo tradesheet and
WOW/MOM writes above OPTIMIZE_SKIP_TRADESHEETS_ABOVE_COMBOS, and the download
endpoints (routers/optimize.py) refuse to build those artifacts on the SAME
threshold + job total via `_large_sweep_skip`. This locks the threshold math so
the two sides can never drift apart (a mismatch would 404/500 or return an empty
ZIP instead of a clean 409).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routers import optimize as O  # noqa: E402


class TestLargeSweepSkip(unittest.TestCase):
    def _skip(self, total, threshold):
        with mock.patch.dict(os.environ, {"OPTIMIZE_SKIP_TRADESHEETS_ABOVE_COMBOS": str(threshold)}):
            return O._large_sweep_skip({"total": total})

    def test_off_by_default(self):
        # 0 threshold = feature off, no matter how big the sweep.
        self.assertEqual(self._skip(10 ** 9, 0), 0)

    def test_strictly_above_only(self):
        # At-threshold is NOT skipped (matches the worker's `> threshold`).
        self.assertEqual(self._skip(5000, 5000), 0)
        self.assertEqual(self._skip(4999, 5000), 0)

    def test_above_threshold_returns_threshold(self):
        self.assertEqual(self._skip(5001, 5000), 5000)
        self.assertEqual(self._skip(60000, 5000), 5000)

    def test_missing_or_bad_total_is_safe(self):
        with mock.patch.dict(os.environ, {"OPTIMIZE_SKIP_TRADESHEETS_ABOVE_COMBOS": "5000"}):
            self.assertEqual(O._large_sweep_skip({}), 0)
            self.assertEqual(O._large_sweep_skip(None), 0)
            self.assertEqual(O._large_sweep_skip({"total": None}), 0)


if __name__ == "__main__":
    unittest.main()
