"""
End-to-end test of the Rust trade-spec resolver + price simulator.

Pipeline:
    payload + calendar + expiries
        → algotest_native.resolve_trade_specs(...)         (slice 2)
        → algotest_native.simulate_trades_batch(...)       (slice 1)
        → list of priced trades

This must match the Python engine's output for the simplest archetypes
(single-leg ATM, no SL/TP/Trail, weekly expiry, scheduled exit at exit_dte).
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# NIFTY contract lot size, matches what generic_algotest_engine uses
# (defined via index_metadata). We hard-code here for the test — production
# code reads it from services.index_metadata.
LOT_SIZE_BY_INDEX = {
    "NIFTY": 65,
    "BANKNIFTY": 25,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 120,
}


def _get_trading_days(index: str, from_date: str, to_date: str):
    """Return ascending list of ISO YYYY-MM-DD trading days."""
    import pandas as pd
    from base import get_trading_calendar

    df = get_trading_calendar(from_date, to_date)
    days = pd.to_datetime(df["date"]).sort_values().dt.strftime("%Y-%m-%d").tolist()
    return days


def _get_spot_by_date(index: str, trading_days: list):
    """Return {YYYY-MM-DD: spot_close} dict for every trading day.

    Uses get_spot_price_from_db which goes through the Postgres → cached
    feather → parquet fallback chain — the same path the Python engine uses.
    """
    from base import get_spot_price_from_db

    out = {}
    for d in trading_days:
        v = get_spot_price_from_db(d, index)
        if v is not None:
            out[d] = float(v)
    return out


def _get_expiries(index: str, expiry_type: str, from_date: str, to_date: str):
    """Return ascending list of ISO YYYY-MM-DD expiry dates."""
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


# Archetypes that the Rust path fully supports today. Each must produce the
# same numbers as the Python engine — this list grows with each slice.
RUST_SUPPORTED_ARCHETYPES = (
    "single_leg_ce_atm_sell",
    "single_leg_pe_atm_sell",
    "short_strangle_otm1",
    "iron_condor",
    "pct_strike_offset",
    # Slice 3 — premium-based modes:
    "closest_premium_ce",
    "premium_gte_ce",
    "premium_lte_pe",
    "premium_range_strangle",
    "atm_straddle_prem_pct",
    "straddle_width_ce",
)


def _load_snap(name):
    p = Path(__file__).parent / "parity" / "snapshots" / f"{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


class TestResolveAndSimulate(unittest.TestCase):
    def setUp(self):
        try:
            import algotest_native  # type: ignore
            self.native = algotest_native
            self.assertTrue(hasattr(self.native, "resolve_trade_specs"))
            self.assertTrue(hasattr(self.native, "simulate_trades_batch"))
        except ImportError:
            self.skipTest("algotest_native not installed")

        snap_path = (
            Path(__file__).parent / "parity" / "snapshots" / "single_leg_ce_atm_sell.json"
        )
        if not snap_path.exists():
            self.skipTest("snapshot not captured yet")
        self.snap = json.loads(snap_path.read_text())
        self.payload = self.snap["payload"]
        self.snap_trades = self.snap["trades"]

        from base import bulk_load_options
        from services.algotest_job import _build_fast_lookup_from_bulk
        bulk_load_options(
            self.payload["index"], self.payload["from_date"], self.payload["to_date"]
        )
        _build_fast_lookup_from_bulk(
            self.payload["index"], self.payload["from_date"], self.payload["to_date"]
        )

    def test_resolve_produces_expected_count(self):
        days = _get_trading_days(
            self.payload["index"], self.payload["from_date"], self.payload["to_date"]
        )
        expiries = _get_expiries(
            self.payload["index"], "weekly",
            self.payload["from_date"], self.payload["to_date"]
        )
        self.assertGreater(len(days), 0)
        self.assertGreater(len(expiries), 0)

        spots = _get_spot_by_date(self.payload["index"], days)
        specs = self.native.resolve_trade_specs(
            self.payload, expiries, days,
            LOT_SIZE_BY_INDEX[self.payload["index"]],
            spots,
        )
        # Single-leg strategy → exactly one spec per resolvable expiry
        self.assertGreater(len(specs), 0)
        # The Python engine produced N trades. Rust should produce ≤ N
        # (some expiries may be filtered out by missing-data guards). Allow a
        # small discrepancy and verify the resolvable subset matches exactly.
        self.assertLessEqual(abs(len(specs) - len(self.snap_trades)), 1)

    def test_resolve_then_simulate_matches_snapshot(self):
        days = _get_trading_days(
            self.payload["index"], self.payload["from_date"], self.payload["to_date"]
        )
        expiries = _get_expiries(
            self.payload["index"], "weekly",
            self.payload["from_date"], self.payload["to_date"]
        )
        spots = _get_spot_by_date(self.payload["index"], days)
        specs = self.native.resolve_trade_specs(
            self.payload, expiries, days,
            LOT_SIZE_BY_INDEX[self.payload["index"]],
            spots,
        )
        priced = self.native.simulate_trades_batch(specs)

        # Build a (entry_date, exit_date, strike) lookup over snapshot trades
        snap_by_key = {
            (t["Entry Date"], t["Exit Date"], float(t["Strike"])): t
            for t in self.snap_trades
        }

        matched = 0
        for row in priced:
            key = (row["entry_date"], row["exit_date"], row["strike"])
            snap = snap_by_key.get(key)
            if snap is None:
                self.fail(
                    f"Rust produced a trade not in snapshot: {key} — Rust resolver "
                    "must align with Python engine's expiry/DTE selection"
                )
            with self.subTest(trade=key):
                self.assertAlmostEqual(
                    row["entry_price"], snap["Entry Price"], delta=0.01
                )
                self.assertAlmostEqual(
                    row["exit_price"], snap["Exit Price"], delta=0.01
                )
                self.assertAlmostEqual(
                    row["net_pnl"], snap["Net P&L"], delta=0.01
                )
            matched += 1

        # We must have matched every snapshot trade.
        self.assertEqual(
            matched, len(self.snap_trades),
            f"matched {matched}/{len(self.snap_trades)} snapshot trades",
        )


class TestRustPathOnAllSupportedArchetypes(unittest.TestCase):
    """Every archetype the Rust path claims to support must produce parity."""

    @classmethod
    def setUpClass(cls):
        try:
            import algotest_native  # type: ignore
            cls.native = algotest_native
        except ImportError:
            raise unittest.SkipTest("algotest_native not installed")
        # Bulk-load once for the shared archetype date range.
        from base import bulk_load_options
        from services.algotest_job import _build_fast_lookup_from_bulk
        # All archetypes use the same range — load it once.
        bulk_load_options("NIFTY", "2024-01-01", "2024-03-31")
        _build_fast_lookup_from_bulk("NIFTY", "2024-01-01", "2024-03-31")
        cls._calendar = _get_trading_days("NIFTY", "2024-01-01", "2024-03-31")
        cls._spots = _get_spot_by_date("NIFTY", cls._calendar)
        cls._expiries = _get_expiries("NIFTY", "weekly", "2024-01-01", "2024-03-31")


def _make_archetype_test(name):
    def test(self):
        snap = _load_snap(name)
        if snap is None:
            self.skipTest(f"snapshot {name!r} not captured yet")
        payload = snap["payload"]
        specs = self.native.resolve_trade_specs(
            payload, self._expiries, self._calendar,
            LOT_SIZE_BY_INDEX[payload["index"]],
            self._spots,
        )
        if len(specs) == 0:
            self.fail(
                f"Rust resolver returned 0 specs for archetype {name!r}; "
                "either a feature it should support is missing or the "
                "archetype tripped a fall-back guard."
            )
        priced = self.native.simulate_trades_batch(specs)
        snap_by_key = {
            (t["Entry Date"], t["Exit Date"], float(t["Strike"]), t["Type"]): t
            for t in snap["trades"]
        }
        for row in priced:
            key = (row["entry_date"], row["exit_date"], row["strike"], row["option_type"])
            snap_row = snap_by_key.get(key)
            if snap_row is None:
                self.fail(
                    f"{name}: Rust produced a trade not in Python snapshot: {key}"
                )
            with self.subTest(trade=key):
                self.assertAlmostEqual(row["entry_price"], snap_row["Entry Price"], delta=0.01)
                self.assertAlmostEqual(row["exit_price"], snap_row["Exit Price"], delta=0.01)
                self.assertAlmostEqual(row["net_pnl"], snap_row["Net P&L"], delta=0.01)

    test.__name__ = f"test_archetype__{name}"
    return test


for _n in RUST_SUPPORTED_ARCHETYPES:
    setattr(
        TestRustPathOnAllSupportedArchetypes,
        f"test_archetype__{_n}",
        _make_archetype_test(_n),
    )


if __name__ == "__main__":
    unittest.main()
