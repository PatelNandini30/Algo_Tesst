"""Lot-quantity scaling: Net P&L = points x lots, applied per leg.

lot_size is NOT part of P&L - it feeds only the display Qty column.
At lots=1 every value must be byte-identical to the pre-change engine.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _spec(lots: int) -> dict:
    """A single CE SELL leg. Only `lots` varies between calls."""
    return {
        "trade_id": 1,
        "leg_id": 1,
        "index": "NIFTY",
        "entry_date": "2024-01-01",
        "exit_date": "2024-01-04",
        "expiry": "2024-01-04",
        "strike": 21500.0,
        "strike_interval": 50.0,
        "option_type": "CE",
        "position": "SELL",
        "lots": lots,
        "lot_size": 65,
        "slippage_pct": 0.0,
    }


class TestRustNetPnlScalesWithLots(unittest.TestCase):
    def setUp(self):
        try:
            import algotest_native  # noqa: F401
        except ImportError:
            self.skipTest("algotest_native not built")

    def test_net_pnl_doubles_when_lots_doubles(self):
        import algotest_native

        one, _ = algotest_native.simulate_trades_batch([_spec(1)])
        two, _ = algotest_native.simulate_trades_batch([_spec(2)])

        if not one or one[0].get("missing"):
            self.skipTest("no market data for this spec")

        self.assertAlmostEqual(two[0]["net_pnl"], one[0]["net_pnl"] * 2, places=2)

    def test_prices_do_not_scale(self):
        import algotest_native

        one, _ = algotest_native.simulate_trades_batch([_spec(1)])
        two, _ = algotest_native.simulate_trades_batch([_spec(2)])

        if not one or one[0].get("missing"):
            self.skipTest("no market data for this spec")

        self.assertEqual(two[0]["entry_price"], one[0]["entry_price"])
        self.assertEqual(two[0]["exit_price"], one[0]["exit_price"])


if __name__ == "__main__":
    unittest.main()
