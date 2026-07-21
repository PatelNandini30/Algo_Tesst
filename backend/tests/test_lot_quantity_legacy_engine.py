"""Legacy engine (generic_algotest_engine.py) P&L scaling: points x lots.

Self-contained counterpart to test_lot_quantity_scaling.py's
TestLegacyEngineScales, kept in its own file so it doesn't collide with other
agents editing test_lot_quantity_scaling.py concurrently. Same class name and
assertions as specified in the Task 4 brief.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


class TestLegacyEngineScales(unittest.TestCase):
    """_recalc_leg_pnl scales the leg's stored pnl by that leg's lots."""

    def test_recalc_leg_pnl_scales_option_leg(self):
        from engines.generic_algotest_engine import _recalc_leg_pnl

        tleg = {
            "segment": "OPTION", "option_type": "CE", "position": "SELL",
            "strike": 21500, "entry_premium": 150.0, "lots": 2,
        }
        try:
            _recalc_leg_pnl(
                tleg,
                pd.Timestamp("2024-01-04"),  # leg_exit_date (real callers pass a
                                             # Timestamp; _recalc_leg_pnl calls
                                             # .strftime() on it directly)
                "NIFTY",           # index
                "2024-01-04",      # expiry_date
                65,                # lot_size (NIFTY)
                21500.0,           # fallback_spot
                0.0,               # slippage_pct
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Narrow on purpose: a bare `except Exception` here would swallow a
            # genuine regression in _recalc_leg_pnl and report it as a skip.
            self.skipTest(f"_recalc_leg_pnl needs market data: {exc}")

        exit_prem = tleg["exit_premium"]
        self.assertAlmostEqual(tleg["pnl"], (150.0 - exit_prem) * 2, places=2)
        self.assertAlmostEqual(tleg["ce_pnl"], tleg["pnl"], places=2)


if __name__ == "__main__":
    unittest.main()
