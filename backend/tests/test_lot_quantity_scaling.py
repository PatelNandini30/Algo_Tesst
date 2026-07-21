"""Lot-quantity scaling: Net P&L = points x lots, applied per leg.

lot_size is NOT part of P&L - it feeds only the display Qty column.
At lots=1 every value must be byte-identical to the pre-change engine.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


import json
from pathlib import Path


class TestRustNetPnlScalesWithLots(unittest.TestCase):
    """Rust net_pnl scales by the leg's own lots; prices must not.

    Reuses the captured parity snapshot + its cache-warming helper, the same
    way tests/test_simulate_rust.py does. A synthetic spec cannot work here:
    simulate_one prices against the in-process Rust cache, which is cold in a
    bare unittest process, so every row comes back `missing`.
    """

    def setUp(self):
        try:
            import algotest_native  # type: ignore
            self.native = algotest_native
        except ImportError:
            self.skipTest("algotest_native not installed in this environment")

        from tests.test_simulate_rust import _bulk_load_for_snapshot, _trade_to_spec

        snap_path = (
            Path(__file__).parent / "parity" / "snapshots" / "single_leg_ce_atm_sell.json"
        )
        if not snap_path.exists():
            self.skipTest("snapshot single_leg_ce_atm_sell not captured yet")

        snap = json.loads(snap_path.read_text())
        self.payload = snap["payload"]
        self.trades = snap["trades"]
        self.assertGreater(len(self.trades), 0)

        try:
            _bulk_load_for_snapshot(self.payload)
        except Exception as exc:
            self.skipTest(f"could not load market data: {exc}")
        self._to_spec = _trade_to_spec

    def _run(self, lots: int) -> list:
        """Price the whole snapshot with every leg forced to `lots`."""
        specs = []
        for t in self.trades:
            spec = self._to_spec(t, self.payload)
            spec["lots"] = lots
            specs.append(spec)
        # simulate_trades_batch returns a FLAT list (the (results, bad_trades)
        # tuple belongs to the private _core fn, not the PyO3 wrapper).
        return list(self.native.simulate_trades_batch(specs))

    def _paired(self):
        one, two = self._run(1), self._run(2)
        pairs = [(a, b) for a, b in zip(one, two) if not a["missing"]]
        self.assertGreater(len(pairs), 0, "snapshot produced no priced rows")
        return pairs

    def test_net_pnl_doubles_when_lots_doubles(self):
        for a, b in self._paired():
            self.assertAlmostEqual(b["net_pnl"], a["net_pnl"] * 2, places=2)

    def test_prices_do_not_scale(self):
        for a, b in self._paired():
            self.assertEqual(b["entry_price"], a["entry_price"])
            self.assertEqual(b["exit_price"], a["exit_price"])


class TestTradesheetRecordsScale(unittest.TestCase):
    """priced_to_tradesheet_records must scale per-leg P&L by that leg's lots."""

    def _rows(self, lots_leg1: int, lots_leg2: int) -> list:
        return [
            {
                "trade_id": "1", "leg_id": 1, "index": "NIFTY",
                "entry_date": "2024-01-01", "exit_date": "2024-01-04",
                "expiry": "2024-01-04", "option_type": "CE", "strike": 21500.0,
                "position": "SELL", "entry_price": 150.0, "exit_price": 90.0,
                "entry_spot": 21500.0, "exit_spot": 21600.0,
                "lots": lots_leg1, "lot_size": 65, "net_pnl": 60.0 * lots_leg1,
            },
            {
                "trade_id": "1", "leg_id": 2, "index": "NIFTY",
                "entry_date": "2024-01-01", "exit_date": "2024-01-04",
                "expiry": "2024-01-04", "option_type": "PE", "strike": 21500.0,
                "position": "SELL", "entry_price": 130.0, "exit_price": 145.0,
                "entry_spot": 21500.0, "exit_spot": 21600.0,
                "lots": lots_leg2, "lot_size": 65, "net_pnl": -15.0 * lots_leg2,
            },
        ]

    def test_per_leg_pnl_scales_by_that_legs_lots(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)

        ce = next(r for r in recs if r["Type"] == "CE")
        pe = next(r for r in recs if r["Type"] == "PE")

        # CE: (150 - 90) x 2 lots = 120 ; PE: (130 - 145) x 1 lot = -15
        self.assertAlmostEqual(ce["CE P&L"], 120.0, places=4)
        self.assertAlmostEqual(pe["PE P&L"], -15.0, places=4)

    def test_qty_is_lots_times_lot_size(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)

        ce = next(r for r in recs if r["Type"] == "CE")
        pe = next(r for r in recs if r["Type"] == "PE")
        self.assertEqual(ce["Qty"], 130)   # 2 lots x 65
        self.assertEqual(pe["Qty"], 65)    # 1 lot  x 65

    def test_prices_stay_per_unit(self):
        from services.engine_rust import priced_to_tradesheet_records

        recs = priced_to_tradesheet_records(self._rows(2, 1), {"index": "NIFTY"}, 65)
        ce = next(r for r in recs if r["Type"] == "CE")
        self.assertEqual(ce["Entry Price"], 150.0)
        self.assertEqual(ce["Exit Price"], 90.0)


if __name__ == "__main__":
    unittest.main()
