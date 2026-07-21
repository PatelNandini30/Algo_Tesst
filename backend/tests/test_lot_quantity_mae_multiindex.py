"""Task 7c: multi-index overlay MAE/MFE must scale by the leg's own lots.

Scope: services/multi_index_feature.py's `_overlay_legs_onto_base` row
builder (the write site at ~:1545), which emits
`"Exit Reason": "OVERLAY", "MAE": ..., "MFE": ...` for each overlay
(non-base-index) leg row.

Why: native/src/summary_metrics.rs:336 compounds the NAV by `% P&L` (now
lots-scaled, points x lots) while :362 applies `MAE` to that same NAV as
`prev_cum * (1 + mae/100)`. Leaving MAE/MFE unscaled understates Live DD /
Max DD by ~1/lots on multi-index runs, exactly as it did on the base
backtest path before Task 7a.

`_mae`/`_mfe` are populated by one of two branches, both flowing into the
SAME pair of locals that the :1545 write reads:
  - futures leg  (~:1322-1326) via services.engine_rust._fut_leg_mae_mfe
  - option leg   (~:1489-1497) via engines.generic_algotest_engine._calculate_leg_mae_mfe
Both helpers return a plain UNSCALED ratio (confirmed by reading their
implementations) — so scaling once at the :1545 write, using the `lots`
local already in scope for that leg's iteration (set once per leg at
~:1255, sourced from leg config, never from Qty/lot_size), is correct and
covers both branches without touching either helper or double-scaling.

All external data (Rust-cache spot/premium/future-price lookups, data-expiry
scans, trading calendar, the MAE/MFE ratio itself) is mocked so this test
drives the real `_overlay_legs_onto_base` control flow — strike/contract
resolution, per-leg lots, the row dict assembly — without touching Postgres
or the Rust feather cache. Safe to run standalone; no multi-index backtest,
no bulk data load, no OOM risk.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd


def _base_df() -> "pd.DataFrame":
    """One base trade: 2024-01-01 -> 2024-01-04 (matches the mocked expiry/
    calendar fixtures below)."""
    return pd.DataFrame([
        {"Trade": 1, "Leg": 1, "Entry Date": "2024-01-01", "Exit Date": "2024-01-04"},
    ])


class TestOverlayMaeMfeScalesByLots(unittest.TestCase):
    """Each overlay leg row's MAE/MFE scales by THAT leg's own lots — not the
    other leg's, and not lots**2."""

    def _run(self, opt_lots: int, fut_lots: int) -> dict:
        from services import multi_index_feature as mif

        overlay_legs = [
            {
                "segment": "OPTIONS", "option_type": "CE", "position": "SELL",
                "lots": opt_lots, "index": "NIFTY", "expiry": "MONTHLY",
                "strike_interval": 50,
                "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
            },
            {
                "segment": "FUTURES", "position": "BUY",
                "lots": fut_lots, "index": "NIFTY", "expiry": "MONTHLY",
            },
        ]

        with (
            mock.patch.object(mif, "_data_expiries", return_value=["2024-01-04"]),
            mock.patch.object(mif, "_fut_illiquid_days", return_value=set()),
            mock.patch("base.get_trading_calendar",
                       side_effect=RuntimeError("no calendar in unit test")),
            mock.patch("services.futures_cache_store.ensure_futures_loaded"),
            mock.patch("services.rust_fast_path.ensure_symbol_merged"),
            mock.patch("services.rust_fast_path.get_spot_price", return_value=21500.0),
            mock.patch(
                "services.rust_fast_path.get_future_price",
                side_effect=lambda sym, day, exp: 21600.0 if day == "2024-01-04" else 21500.0,
            ),
            mock.patch(
                "services.rust_fast_path.get_option_price",
                side_effect=lambda day, sym, strike, opt, exp: 90.0 if day == "2024-01-04" else 150.0,
            ),
            # The two raw-ratio sources — both UNSCALED by design (mae.rs /
            # the Python mirror are pure ratios). This is what :1545 must scale.
            mock.patch(
                "engines.generic_algotest_engine._calculate_leg_mae_mfe",
                return_value=(8.0, 5.0),
            ),
            mock.patch(
                "services.engine_rust._fut_leg_mae_mfe",
                return_value=(6.0, 3.0),
            ),
        ):
            rows = mif._overlay_legs_onto_base(
                _base_df(), overlay_legs, "NIFTY", "2024-01-01", "2024-01-04",
            )
        self.assertEqual(len(rows), 2, "expected one row per overlay leg")
        by_type = {r["Type"]: r for r in rows}
        self.assertIn("CE", by_type)
        self.assertIn("FUT", by_type)
        return by_type

    def test_lots_1_is_byte_identical_to_unscaled_ratio(self):
        """lots=1 must reproduce the raw ratio exactly — multiplying by 1 is
        a no-op."""
        rows = self._run(opt_lots=1, fut_lots=1)
        self.assertEqual(rows["CE"]["MAE"], 8.0)
        self.assertEqual(rows["CE"]["MFE"], 5.0)
        self.assertEqual(rows["FUT"]["MAE"], 6.0)
        self.assertEqual(rows["FUT"]["MFE"], 3.0)

    def test_each_leg_scales_by_its_own_lots(self):
        """3x2 spread (not equal, not each other's value) — distinguishes
        `x lots` from `x lots**2` and from cross-leg contamination.
        Correct: CE MAE = 8*3=24, FUT MAE = 6*2=12.
        lots**2 (bug): CE MAE = 8*9=72, FUT MAE = 6*4=24.
        Cross-leg (bug): CE MAE scaled by fut_lots=2 -> 16, etc.
        """
        rows = self._run(opt_lots=3, fut_lots=2)
        self.assertAlmostEqual(rows["CE"]["MAE"], 24.0, places=6)
        self.assertAlmostEqual(rows["CE"]["MFE"], 15.0, places=6)
        self.assertAlmostEqual(rows["FUT"]["MAE"], 12.0, places=6)
        self.assertAlmostEqual(rows["FUT"]["MFE"], 6.0, places=6)


class TestOverlayPnlScalesByLotsMatchingMae(unittest.TestCase):
    """Task 7d fix: the reviewer-flagged bug was MAE/MFE scaled by lots at
    :1554 while Net/CE/PE/FUT P&L on the SAME row (built at :1300 futures /
    :1455 options) stayed at 1x — a row carrying 2x MAE against 1x P&L.
    P&L must scale by that leg's OWN lots, exactly like MAE/MFE, so the two
    stay commensurate (both x lots, at the same factor)."""

    def _run(self, opt_lots: int, fut_lots: int) -> dict:
        from services import multi_index_feature as mif

        overlay_legs = [
            {
                "segment": "OPTIONS", "option_type": "CE", "position": "SELL",
                "lots": opt_lots, "index": "NIFTY", "expiry": "MONTHLY",
                "strike_interval": 50,
                "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
            },
            {
                "segment": "FUTURES", "position": "BUY",
                "lots": fut_lots, "index": "NIFTY", "expiry": "MONTHLY",
            },
        ]

        with (
            mock.patch.object(mif, "_data_expiries", return_value=["2024-01-04"]),
            mock.patch.object(mif, "_fut_illiquid_days", return_value=set()),
            mock.patch("base.get_trading_calendar",
                       side_effect=RuntimeError("no calendar in unit test")),
            mock.patch("services.futures_cache_store.ensure_futures_loaded"),
            mock.patch("services.rust_fast_path.ensure_symbol_merged"),
            mock.patch("services.rust_fast_path.get_spot_price", return_value=21500.0),
            mock.patch(
                "services.rust_fast_path.get_future_price",
                side_effect=lambda sym, day, exp: 21600.0 if day == "2024-01-04" else 21500.0,
            ),
            mock.patch(
                "services.rust_fast_path.get_option_price",
                side_effect=lambda day, sym, strike, opt, exp: 90.0 if day == "2024-01-04" else 150.0,
            ),
            mock.patch(
                "engines.generic_algotest_engine._calculate_leg_mae_mfe",
                return_value=(8.0, 5.0),
            ),
            mock.patch(
                "services.engine_rust._fut_leg_mae_mfe",
                return_value=(6.0, 3.0),
            ),
        ):
            rows = mif._overlay_legs_onto_base(
                _base_df(), overlay_legs, "NIFTY", "2024-01-01", "2024-01-04",
            )
        self.assertEqual(len(rows), 2)
        return {r["Type"]: r for r in rows}

    def test_lots_1_pnl_matches_raw_points(self):
        """lots=1: unscaled points, no-op multiplication (byte-identical)."""
        rows = self._run(opt_lots=1, fut_lots=1)
        # CE SELL: entry 150.0 -> exit 90.0 => pnl = 150 - 90 = 60
        self.assertAlmostEqual(rows["CE"]["Net P&L"], 60.0, places=6)
        self.assertAlmostEqual(rows["CE"]["CE P&L"], 60.0, places=6)
        # FUT BUY: entry 21500.0 -> exit 21600.0 => pnl = 21600 - 21500 = 100
        self.assertAlmostEqual(rows["FUT"]["Net P&L"], 100.0, places=6)
        self.assertAlmostEqual(rows["FUT"]["FUT P&L"], 100.0, places=6)

    def test_each_leg_pnl_scales_by_its_own_lots(self):
        """3x2 spread mirrors the MAE/MFE test: CE P&L = 60*3=180,
        FUT P&L = 100*2=200 — not lots**2, not cross-leg."""
        rows = self._run(opt_lots=3, fut_lots=2)
        self.assertAlmostEqual(rows["CE"]["Net P&L"], 180.0, places=6)
        self.assertAlmostEqual(rows["FUT"]["Net P&L"], 200.0, places=6)

    def test_pnl_and_mae_scale_by_the_SAME_lots_factor(self):
        """The actual bug: MAE/lots-ratio and P&L/points-ratio must agree on
        the multiplier applied to each row so the row is internally
        consistent (both x lots, never one x1 and the other xlots)."""
        rows = self._run(opt_lots=3, fut_lots=2)
        ce_pnl_factor = rows["CE"]["Net P&L"] / 60.0     # raw CE points = 60
        ce_mae_factor = rows["CE"]["MAE"] / 8.0           # raw CE MAE ratio = 8.0
        self.assertAlmostEqual(ce_pnl_factor, ce_mae_factor, places=6)
        self.assertAlmostEqual(ce_pnl_factor, 3.0, places=6)  # == opt_lots

        fut_pnl_factor = rows["FUT"]["Net P&L"] / 100.0   # raw FUT points = 100
        fut_mae_factor = rows["FUT"]["MAE"] / 6.0          # raw FUT MAE ratio = 6.0
        self.assertAlmostEqual(fut_pnl_factor, fut_mae_factor, places=6)
        self.assertAlmostEqual(fut_pnl_factor, 2.0, places=6)  # == fut_lots

    def test_rows_carry_explicit_lots_key(self):
        """Downstream (routers/backtest.py charges recalc) must read an
        explicit `lots` off the row instead of guessing from Qty/lot_size or
        misreading `Index` (a trade number here, not a symbol)."""
        rows = self._run(opt_lots=3, fut_lots=2)
        self.assertEqual(rows["CE"]["lots"], 3)
        self.assertEqual(rows["FUT"]["lots"], 2)


class TestOverlayBlankRowMaeMfeStayNone(unittest.TestCase):
    """Audit finding for :1516 — a leg priced off a non-trading bar (stale
    close) blanks the whole row including MAE/MFE = None. There is no ratio
    to scale here (Net/CE/PE/FUT P&L are also None by the same guard), so
    lot scaling must NOT touch this branch. This is a regression guard, not
    expected to go red — it documents/locks the audited behaviour."""

    def test_blank_reason_row_keeps_mae_mfe_none_regardless_of_lots(self):
        from services import multi_index_feature as mif

        overlay_legs = [{
            "segment": "FUTURES", "position": "BUY", "lots": 5,
            "index": "NIFTY", "expiry": "MONTHLY",
        }]

        with (
            mock.patch.object(mif, "_data_expiries", return_value=["2024-01-04"]),
            # NO_VOLUME: the picked contract didn't trade on entry -> blank_reason set.
            mock.patch.object(mif, "_fut_illiquid_days",
                              return_value={("2024-01-01", "2024-01-04")}),
            mock.patch("base.get_trading_calendar",
                       side_effect=RuntimeError("no calendar in unit test")),
            mock.patch("services.futures_cache_store.ensure_futures_loaded"),
            mock.patch("services.rust_fast_path.ensure_symbol_merged"),
            mock.patch("services.rust_fast_path.get_spot_price", return_value=21500.0),
            mock.patch(
                "services.rust_fast_path.get_future_price",
                side_effect=lambda sym, day, exp: 21600.0 if day == "2024-01-04" else 21500.0,
            ),
            mock.patch("services.engine_rust._fut_leg_mae_mfe", return_value=(6.0, 3.0)),
        ):
            rows = mif._overlay_legs_onto_base(
                _base_df(), overlay_legs, "NIFTY", "2024-01-01", "2024-01-04",
            )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(str(row["Exit Reason"]).startswith("NO_VOLUME"))
        self.assertIsNone(row["MAE"])
        self.assertIsNone(row["MFE"])
        self.assertIsNone(row["Net P&L"])


if __name__ == "__main__":
    unittest.main()
