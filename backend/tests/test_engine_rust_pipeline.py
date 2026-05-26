"""
Slice 4 parity test.

Runs the engine_rust orchestrator end-to-end on archetypes that include
per-leg risk controls (Stop Loss, Target Profit, Trail SL) and verifies the
output matches the Python-engine snapshots within 0.01 on every trade.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

LOT_SIZE_BY_INDEX = {
    "NIFTY": 65,
    "BANKNIFTY": 25,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 120,
}


def _trading_days(from_date: str, to_date: str):
    import pandas as pd
    from base import get_trading_calendar
    df = get_trading_calendar(from_date, to_date)
    return pd.to_datetime(df["date"]).sort_values().dt.strftime("%Y-%m-%d").tolist()


def _expiries(index: str, expiry_type: str, from_date: str, to_date: str):
    import pandas as pd
    from base import get_expiry_dates
    df = get_expiry_dates(index, expiry_type, from_date, to_date)
    if df is None or df.empty:
        return []
    col = "Current Expiry" if "Current Expiry" in df.columns else df.columns[0]
    return (
        pd.to_datetime(df[col])
        .sort_values()
        .dt.strftime("%Y-%m-%d")
        .unique()
        .tolist()
    )


def _spots(index: str, days):
    from base import get_spot_price_from_db
    out = {}
    for d in days:
        v = get_spot_price_from_db(d, index)
        if v is not None:
            out[d] = float(v)
    return out


def _load_snap(name):
    p = Path(__file__).parent / "parity" / "snapshots" / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


# Slice 4 archetypes — these all have per-leg SL/Target/Trail set.
# The "aggressive" variants use tight thresholds that DO fire on the 3-month
# NIFTY 2024 window; without them the test would no-op pass even if the SL
# detection were broken.
SLICE_4_ARCHETYPES = (
    "with_sl_and_target",
    "with_sl_aggressive",
    "with_target_aggressive",
    "with_trail_sl",
    "with_trail_sl_aggressive",
    # Long-window variants — these have 4-day holding windows so SL/Target/
    # Trail can actually fire MID-TRADE with exit_date < expiry. This is the
    # strongest parity check for slice 4 because the SL path produces a
    # measurably different output than the no-SL path.
    "long_window_with_sl",
    "long_window_with_target",
    "long_window_with_trail_sl",
    # Slice 4b
    "with_sl_buffer",
    # Slice 5 — Overall SL/Target
    "two_leg_with_overall_sl",
    # Slice 6 — Re-entry on SL (RE_ASAP, ATM only)
    "single_leg_reentry_sl_re_asap",
    # Slice 7a — Spot adjustment exit trigger (rise/fall/both)
    "single_leg_spot_adjustment_both",
    # Slice 8a — STR (super_trend) filter
    "single_leg_str_5x1",
    # Slice NEXT_WEEKLY — per-leg NEXT_WEEKLY expiry (calendar spread)
    "single_leg_next_weekly",
    # Slice 10 — LAZY_LEG: on SL, enter a different option leg (PE BUY ATM)
    "single_leg_lazy_pe_buy",
    # Slice RE_MOMENTUM — scan for momentum signal before re-entering on SL
    "single_leg_reentry_sl_re_momentum",
    # Slice 8b — filter_entry_mode='fixed' with rollover chaining
    "filter_entry_fixed",
    # Slice 9 — RE_ASAP_REV: reversed-position re-entry on SL
    "reentry_re_asap_rev",
    # Slice 9b — rollover_strike_mode='fixed': reuse first cycle's strike
    "rollover_fixed_strike",
    # FUTURES — single-leg monthly futures SELL, no SL/Target
    "single_leg_futures_monthly",
    # Gap 1: FUTURES + per-leg SL / TrailSL (uses _scan_futures_sl_target)
    "futures_with_sl",
    "futures_with_trail_sl",
    # Gap 2: FUTURES + re-entry on SL
    "futures_with_reentry_sl",
    # Gap 3: FUTURES + NEXT_WEEKLY mixed strategies
    "futures_next_weekly_mix",
)


class TestEngineRustPipeline(unittest.TestCase):
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
        cls._days = _trading_days("2024-01-01", "2024-03-31")
        cls._expiries = _expiries("NIFTY", "weekly", "2024-01-01", "2024-03-31")
        cls._spots = _spots("NIFTY", cls._days)


def _make_archetype_test(name):
    def test(self):
        snap = _load_snap(name)
        if snap is None:
            self.skipTest(f"snapshot {name!r} not captured yet")
        payload = snap["payload"]

        from services.engine_rust import run_rust_engine_pipeline
        priced = run_rust_engine_pipeline(
            payload,
            expiry_dates=self._expiries,
            trading_days=self._days,
            lot_size=LOT_SIZE_BY_INDEX[payload["index"]],
            spot_by_date=self._spots,
            square_off_mode=payload.get("square_off_mode", "partial"),
        )
        if priced is None:
            self.fail(
                f"engine_rust pipeline rejected {name!r} — feature not yet "
                "supported by the Rust slices currently shipped."
            )
        if not priced:
            self.fail(f"engine_rust pipeline produced 0 trades for {name!r}")

        def _snap_strike(s):
            try:
                return float(s)
            except (TypeError, ValueError):
                return 0.0

        snap_by_key = {
            (t["Entry Date"], t["Exit Date"], _snap_strike(t["Strike"]), t["Type"]): t
            for t in snap["trades"]
        }

        for row in priced:
            strike_key = 0.0 if row.get("option_type") == "FUT" else row["strike"]
            key = (row["entry_date"], row["exit_date"], strike_key, row["option_type"])
            snap_row = snap_by_key.get(key)
            if snap_row is None:
                self.fail(
                    f"{name}: Rust pipeline produced a trade not in Python "
                    f"snapshot: {key}"
                )
            with self.subTest(trade=key):
                self.assertAlmostEqual(
                    row["entry_price"], snap_row["Entry Price"], delta=0.01,
                    msg=f"entry_price diff at {key}",
                )
                self.assertAlmostEqual(
                    row["exit_price"], snap_row["Exit Price"], delta=0.01,
                    msg=f"exit_price diff at {key}",
                )
                self.assertAlmostEqual(
                    row["net_pnl"], snap_row["Net P&L"], delta=0.01,
                    msg=f"net_pnl diff at {key}",
                )

        # We must have matched every snapshot trade.
        self.assertEqual(
            len(priced), len(snap["trades"]),
            f"{name}: Rust produced {len(priced)} trades vs Python {len(snap['trades'])}",
        )

    test.__name__ = f"test_pipeline__{name}"
    return test


for _n in SLICE_4_ARCHETYPES:
    setattr(TestEngineRustPipeline, f"test_pipeline__{_n}", _make_archetype_test(_n))


if __name__ == "__main__":
    unittest.main()
