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


if __name__ == "__main__":
    unittest.main()
