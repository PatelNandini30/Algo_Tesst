"""Charge-adjusted P&L recalc scales by lots (Task 5).

Self-contained sibling of test_lot_quantity_scaling.py — created separately
because that file is being edited concurrently by other agents. Same class
name / assertions as specified in the Task 5 brief, just isolated here.

`_recalculate_trade_prices(trades, charges_enabled=False)` in
backend/routers/backtest.py re-prices per-unit Entry/Exit Price (charges
folded in when enabled) and then must scale the resulting points difference
by the row's own `lots` (derived as Qty / lot_size) to land in the same
"points x lots" unit as the engine. lot_size itself must NEVER be hardcoded
(e.g. MIDCPNIFTY was 75 before 2024-11-20, is not 75 forever) - it must be
read via get_lot_size(index, entry_date).
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestChargeAdjustedPnlScales(unittest.TestCase):
    """With charges OFF, the recalc must reproduce points x lots."""

    def test_recalc_scales_by_lots_when_charges_disabled(self):
        from routers.backtest import _recalculate_trade_prices

        rows = [{
            "Trade": "1", "Leg": 1, "Index": "NIFTY", "Type": "CE", "B/S": "SELL",
            "Strike": 21500, "Qty": 130, "Entry Price": 150.0, "Exit Price": 90.0,
            "Net P&L": 120.0, "Entry Spot": 21500.0,
            "Entry Date": "2024-01-01", "Exit Date": "2024-01-04",
        }]
        out = _recalculate_trade_prices(rows, charges_enabled=False)
        # NIFTY lot size = 65 (flat), Qty=130 -> lots=2.
        # (150 - 90) x 2 lots = 120, NOT 60
        self.assertAlmostEqual(out[0]["Net P&L"], 120.0, places=2)

    def test_recalc_charges_enabled_lots_1_is_noop_scaling(self):
        """charges_enabled=True at 1 lot must match current (pre-scaling)
        behaviour: charges are folded into the per-unit prices, and the
        x1 lot scaling is a no-op on top of that."""
        from routers.backtest import _recalculate_trade_prices, _normalize_recalc_numeric
        from engines.generic_algotest_engine import _calculate_fo_charges

        rows = [{
            "Trade": "1", "Leg": 1, "Index": "NIFTY", "Type": "CE", "B/S": "SELL",
            "Strike": 21500, "Qty": 65, "Entry Price": 150.0, "Exit Price": 90.0,
            "Net P&L": 60.0, "Entry Spot": 21500.0,
            "Entry Date": "2024-01-01", "Exit Date": "2024-01-04",
        }]
        out = _recalculate_trade_prices(rows, charges_enabled=True)

        entry, exit_, qty = 150.0, 90.0, 65.0
        ch = _calculate_fo_charges(entry, exit_, qty, "SELL", "OPTION")
        expected_entry = round(entry - ch["entry_charge_per_unit"], 2)
        expected_exit = round(exit_ + ch["exit_charge_per_unit"], 2)
        expected_pnl = round((expected_entry - expected_exit) * 1, 2)

        self.assertAlmostEqual(out[0]["Entry Price"], expected_entry, places=2)
        self.assertAlmostEqual(out[0]["Exit Price"], expected_exit, places=2)
        self.assertAlmostEqual(out[0]["Net P&L"], expected_pnl, places=2)


if __name__ == "__main__":
    unittest.main()
