"""
Test the Rust simulate_trades_batch function against equivalent Python logic.

Verifies:
  - Round-trip: given a trade spec, Rust returns prices identical to what the
    Python engine produces for the same spec.
  - Numerical parity: net_pnl matches the existing engine to within 0.01.

The test reads the captured single_leg_ce_atm_sell snapshot, extracts trade
specs from it, and confirms Rust reproduces the same entry/exit prices.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _bulk_load_for_snapshot(payload):
    """Load market data so the Rust cache has the trades' price data."""
    from base import bulk_load_options
    from services.algotest_job import (
        _build_fast_lookup_from_bulk,
        _normalize_cache_date,
        _should_build_fast_lookup,
    )

    index = payload.get("index", "NIFTY")
    fd = _normalize_cache_date(payload.get("from_date") or payload.get("date_from"))
    td = _normalize_cache_date(payload.get("to_date") or payload.get("date_to"))
    bulk_load_options(index, fd, td)
    if _should_build_fast_lookup(payload, fd, td):
        _build_fast_lookup_from_bulk(index, fd, td)


def _trade_to_spec(trade_row, payload):
    """Convert a snapshot trade row + payload into a Rust simulate spec.

    Snapshot quirks worth noting:
      * `Exit Date` is YYYY-MM-DD (Rust-friendly).
      * `Leg Exit Date` is DD-MM-YYYY (display format) — don't use it.
      * `Index` holds the leg index ("1"), NOT the symbol. Use payload.
      * `B/S` is "SELL" / "BUY", not "S" / "B".
    """
    bs = str(trade_row.get("B/S") or "").upper().strip()
    position = "SELL" if bs.startswith("S") else "BUY"
    return {
        "trade_id": int(trade_row.get("Trade") or 0),
        "leg_id": int(trade_row.get("Leg") or 0),
        "index": str(payload.get("index") or "NIFTY"),
        "entry_date": str(trade_row["Entry Date"]),
        "exit_date": str(trade_row["Exit Date"]),
        "expiry": str(trade_row.get("Expiry") or trade_row["Exit Date"]),
        "strike": float(trade_row.get("Strike") or 0),
        "option_type": "CE" if str(trade_row.get("Type") or "").upper() == "CE" else "PE",
        "position": position,
        "lots": 1,
        "lot_size": int(trade_row.get("Qty") or 1),
        "slippage_pct": float(payload.get("slippage_pct") or 0),
    }


class TestSimulateTradesBatch(unittest.TestCase):
    def setUp(self):
        try:
            import algotest_native  # type: ignore
            self.native = algotest_native
        except ImportError:
            self.skipTest("algotest_native not installed in this environment")

        snap_path = (
            Path(__file__).parent
            / "parity"
            / "snapshots"
            / "single_leg_ce_atm_sell.json"
        )
        if not snap_path.exists():
            self.skipTest("snapshot single_leg_ce_atm_sell not captured yet")
        self.snap = json.loads(snap_path.read_text())
        self.payload = self.snap["payload"]
        self.trades = self.snap["trades"]
        self.assertGreater(len(self.trades), 0)

        try:
            _bulk_load_for_snapshot(self.payload)
        except Exception as exc:
            self.skipTest(f"could not load market data: {exc}")

    def test_simulate_matches_snapshot_prices(self):
        """Rust simulate_trades_batch must reproduce the snapshot's prices."""
        specs = [_trade_to_spec(t, self.payload) for t in self.trades]
        results = self.native.simulate_trades_batch(specs)
        self.assertEqual(len(results), len(self.trades))

        for snap_trade, rust_row in zip(self.trades, results):
            with self.subTest(trade_id=snap_trade.get("Trade")):
                self.assertFalse(
                    rust_row["missing"],
                    f"Rust reports missing price for trade {snap_trade.get('Trade')}",
                )
                self.assertAlmostEqual(
                    rust_row["entry_price"], snap_trade["Entry Price"], delta=0.01
                )
                self.assertAlmostEqual(
                    rust_row["exit_price"], snap_trade["Exit Price"], delta=0.01
                )
                self.assertAlmostEqual(
                    rust_row["raw_entry_price"],
                    snap_trade.get("Raw Entry Price", snap_trade["Entry Price"]),
                    delta=0.01,
                )
                # Net P&L per leg: the snapshot stores Net P&L at the trade
                # level (summed across all legs of that trade). For a
                # single-leg strategy these are equivalent.
                if "Net P&L" in snap_trade and len(self.trades) > 0:
                    self.assertAlmostEqual(
                        rust_row["net_pnl"],
                        snap_trade["Net P&L"],
                        delta=0.01,
                    )

    def test_parallel_consistency(self):
        """Running the same specs twice yields identical results."""
        specs = [_trade_to_spec(t, self.payload) for t in self.trades[:5]]
        a = self.native.simulate_trades_batch(specs)
        b = self.native.simulate_trades_batch(specs)
        for ra, rb in zip(a, b):
            self.assertEqual(ra, rb)


if __name__ == "__main__":
    unittest.main()
