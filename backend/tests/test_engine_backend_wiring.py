"""
Slice 11 integration test — ENGINE_BACKEND=rust wiring.

Validates that the Phase 2b Rust orchestrator path in
``services.algotest_job._try_rust_engine`` produces a valid tradesheet
DataFrame + summary + pivot for archetypes the Rust slices cover.

This is NOT a full column-parity check vs Python — there are deliberate
gaps documented in ``priced_to_tradesheet_records`` (MAE/MFE = 0, Exit
Reason always 'Expiry', etc.). The test asserts:
  * Rust path returns a non-empty DataFrame
  * Critical numeric columns (Net P&L, Entry/Exit Price, Spot fields)
    match the Python snapshot within 0.01
  * compute_analytics added Cumulative/Peak/DD/%DD without error
  * summary contains the expected keys

Run via:
    ENGINE_BACKEND=rust python -m unittest backend.tests.test_engine_backend_wiring
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _load_snap(name):
    p = Path(__file__).parent / "parity" / "snapshots" / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


class TestEngineBackendWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import algotest_native  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("algotest_native not installed")
        from base import bulk_load_options
        from services.algotest_job import _build_fast_lookup_from_bulk
        bulk_load_options("NIFTY", "2024-01-01", "2024-03-31")
        _build_fast_lookup_from_bulk("NIFTY", "2024-01-01", "2024-03-31")

    def _run(self, archetype):
        snap = _load_snap(archetype)
        self.assertIsNotNone(snap, f"snapshot {archetype!r} missing")
        from services.algotest_job import _try_rust_engine
        trades_df, summary, pivot = _try_rust_engine(
            snap["payload"],
            snap["payload"]["index"],
            snap["payload"]["from_date"],
            snap["payload"]["to_date"],
        )
        return snap, trades_df, summary, pivot

    def test_single_leg_ce_atm_sell_round_trips(self):
        snap, trades_df, summary, pivot = self._run("single_leg_ce_atm_sell")
        self.assertIsNotNone(trades_df)
        self.assertFalse(trades_df.empty)
        # Required analytics columns must be present after compute_analytics.
        for col in ("Cumulative", "Peak", "DD", "%DD"):
            self.assertIn(col, trades_df.columns, f"compute_analytics missed {col!r}")
        self.assertIn("total_pnl", summary)
        self.assertIn("count", summary)
        self.assertEqual(summary["count"], len(snap["trades"]))

    def test_critical_columns_match_snapshot(self):
        snap, trades_df, _summary, _pivot = self._run("single_leg_ce_atm_sell")
        # Snapshot has the engine's tradesheet records — verify Rust's
        # critical numeric columns match within 0.01 per (entry, exit, strike, type).
        snap_by_key = {
            (
                t["Entry Date"],
                t["Exit Date"],
                float(t["Strike"]),
                t["Type"],
            ): t
            for t in snap["trades"]
        }
        import pandas as pd
        for _, row in trades_df.iterrows():
            entry_iso = pd.Timestamp(row["Entry Date"]).strftime("%Y-%m-%d")
            exit_iso = pd.Timestamp(row["Exit Date"]).strftime("%Y-%m-%d")
            key = (entry_iso, exit_iso, float(row["Strike"]), row["Type"])
            snap_row = snap_by_key.get(key)
            self.assertIsNotNone(snap_row, f"missing snapshot row for {key}")
            for col in ("Entry Price", "Exit Price", "Net P&L", "Entry Spot", "Exit Spot"):
                self.assertAlmostEqual(
                    float(row[col]), float(snap_row[col]), delta=0.01,
                    msg=f"{col} divergence at {key}",
                )


if __name__ == "__main__":
    unittest.main()
