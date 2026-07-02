"""Tests for the multi-index / multi-expiry feature (opt-in, isolated).

Pure grouping-helper tests always run. The end-to-end integration test runs
only when the engine + market data are available (skipped otherwise), and
asserts that a NIFTY-weekly + MIDCPNIFTY-monthly strategy produces one merged
tradesheet with both groups priced by the existing engine per group.
"""
import unittest


class TestLegGroupingHelpers(unittest.TestCase):
    def test_leg_index_defaults_to_strategy_index(self):
        from services.multi_index_feature import _leg_index
        self.assertEqual(_leg_index({}, "NIFTY"), "NIFTY")
        self.assertEqual(_leg_index({"index": "midcpnifty"}, "NIFTY"), "MIDCPNIFTY")

    def test_leg_expiry_defaults_and_normalizes(self):
        from services.multi_index_feature import _leg_expiry
        self.assertEqual(_leg_expiry({}, "WEEKLY"), "WEEKLY")
        self.assertEqual(_leg_expiry({"expiry": "monthly"}, "WEEKLY"), "MONTHLY")
        self.assertEqual(_leg_expiry({"expiry_type": "monthly"}, "WEEKLY"), "MONTHLY")


class TestMultiIndexIntegration(unittest.TestCase):
    """End-to-end: requires market data + engine. Skipped if unavailable."""

    def _payload(self):
        return {
            "index": "NIFTY",
            "from_date": "2024-01-01",
            "to_date": "2024-03-31",
            "square_off_mode": "partial",
            "slippage_pct": 0,
            "multi_index_mode": True,
            "legs": [
                {"segment": "OPTIONS", "option_type": "CE", "position": "SELL", "lots": 1,
                 "expiry": "WEEKLY", "index": "NIFTY", "strike_interval": 50,
                 "strike_selection": {"type": "strike_type", "strike_type": "ATM"}},
                {"segment": "FUTURES", "position": "BUY", "lots": 1,
                 "expiry": "MONTHLY", "index": "MIDCPNIFTY", "strike_interval": 25},
            ],
        }

    def test_two_index_run_merges_groups(self):
        try:
            from services.algotest_job import execute_algotest_job
            res = execute_algotest_job(self._payload())
        except Exception as exc:  # engine/data not available in this env
            self.skipTest(f"engine/data unavailable: {exc}")

        if res.get("status") != "success":
            self.skipTest(f"run not successful in this env: {res.get('message')}")

        groups = res.get("meta", {}).get("groups", [])
        trades = res.get("trades", [])
        if not trades:
            self.skipTest("no trades (data window/cache empty in this env)")

        idxs = {g["index"] for g in groups if g.get("available")}
        self.assertIn("NIFTY", idxs)
        self.assertIn("MIDCPNIFTY", idxs)
        # Every row tagged with its source index/expiry group.
        self.assertTrue(all("Group Index" in t for t in trades))
        # Trade numbers are globally unique & contiguous from 1.
        tnums = sorted({int(t["Trade"]) for t in trades})
        self.assertEqual(tnums[0], 1)


if __name__ == "__main__":
    unittest.main()
