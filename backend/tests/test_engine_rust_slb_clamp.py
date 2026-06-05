"""
Unit tests for the SL-with-Buffer fill invariant in the Rust engine pipeline
(services/engine_rust._clamp_sl_buffer_fill).

Background — the bug this guards against:
  The Rust SL-with-Buffer pre-pass computes its (date, price) override on the
  INITIAL priced rows, BEFORE the fixed-strike re-anchor / strike-correction
  step. On an adjusted or rolled trade the stored price can therefore belong to
  the WRONG (un-adjusted) contract — a cheap, far-OTM number that turns a real
  stop-out into an impossible PROFIT. Observed live examples (NIFTY PE SELL,
  60% SL):
    • Trade 14 (fixed-strike adjustment): entry 246.70, stop 394.72, but filled
      at 103.36 of the un-adjusted 11500 strike -> fake +143.34 instead of a loss.
    • Trade 23 / 122 / 318 (rollover chain, no adjustment): filled at tiny prices
      -> large fake profits.

The invariant: a SELL stop-loss can never fill BELOW its trigger level, and a
BUY stop never ABOVE it. _clamp_sl_buffer_fill re-derives the stop level from
the FINAL row's own entry premium and clamps, so the booked exit always reflects
the contract actually held.

Loaded via importlib so the test needs neither pandas nor the Rust native module.
"""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE_RUST = os.path.normpath(os.path.join(_HERE, "..", "services", "engine_rust.py"))

_spec = importlib.util.spec_from_file_location("engine_rust_under_test", _ENGINE_RUST)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_clamp = _mod._clamp_sl_buffer_fill


class TestSLBufferFillClamp(unittest.TestCase):

    # ── Real trades from the live sheet: stale cheap fill -> clamped to stop ──
    def test_sell_adjustment_trade_14(self):
        # entry 246.70, 60% stop = 394.72; stale fill 103.36 (un-adjusted strike)
        self.assertAlmostEqual(
            _clamp(103.36, 246.70, 60, "pct", "SELL"), 394.72, places=2
        )

    def test_sell_rollover_trade_122(self):
        self.assertAlmostEqual(
            _clamp(20.24, 307.75, 60, "pct", "SELL"), 492.40, places=2
        )

    def test_sell_rollover_trade_23(self):
        self.assertAlmostEqual(
            _clamp(9.92, 196.15, 60, "pct", "SELL"), 313.84, places=2
        )

    def test_sell_deep_itm_trade_318(self):
        self.assertAlmostEqual(
            _clamp(39.20, 539.35, 60, "pct", "SELL"), 862.96, places=2
        )

    # ── Correct trades must be left untouched ────────────────────────────────
    def test_sell_already_at_stop_unchanged(self):
        # Trade 49: fill already equals the stop level -> no change.
        self.assertAlmostEqual(
            _clamp(461.68, 288.55, 60, "pct", "SELL"), 461.68, places=2
        )

    def test_sell_valid_gap_fill_above_stop_kept(self):
        # Trade 206: a genuine gap fills ABOVE the stop (a bigger loss) -> kept.
        self.assertAlmostEqual(
            _clamp(548.55, 323.50, 60, "pct", "SELL"), 548.55, places=2
        )

    def test_sell_resulting_pnl_is_a_loss(self):
        # The whole point: a SELL SL-with-buffer exit can never be a profit.
        for stale, entry in [(103.36, 246.70), (20.24, 307.75), (9.92, 196.15)]:
            fill = _clamp(stale, entry, 60, "pct", "SELL")
            self.assertLessEqual(entry - fill, 0.0, "SELL stop must book a loss")

    # ── BUY mirror ───────────────────────────────────────────────────────────
    def test_buy_clamped_from_above(self):
        # BUY stop level = entry*(1-0.60) = 80; a stale fill of 500 (a fake gain)
        # must clamp DOWN to 80.
        self.assertAlmostEqual(_clamp(500.0, 200.0, 60, "pct", "BUY"), 80.0, places=2)

    def test_buy_already_below_stop_unchanged(self):
        self.assertAlmostEqual(_clamp(70.0, 200.0, 60, "pct", "BUY"), 70.0, places=2)

    # ── Points mode ──────────────────────────────────────────────────────────
    def test_points_mode_sell(self):
        # 60-point stop: entry 200 -> stop 260; stale 50 -> 260.
        self.assertAlmostEqual(_clamp(50.0, 200.0, 60, "points", "SELL"), 260.0, places=2)

    # ── Modes / inputs that must pass through unchanged ──────────────────────
    def test_underlying_mode_passes_through(self):
        # Spot-anchored stops have no clean premium floor -> leave as-is.
        self.assertAlmostEqual(
            _clamp(9.92, 196.15, 60, "underlying_pts", "SELL"), 9.92, places=2
        )

    def test_missing_or_zero_config_passes_through(self):
        self.assertAlmostEqual(_clamp(9.92, 196.15, 0, "pct", "SELL"), 9.92, places=2)
        self.assertAlmostEqual(_clamp(9.92, 196.15, None, "pct", "SELL"), 9.92, places=2)
        self.assertAlmostEqual(_clamp(9.92, 0, 60, "pct", "SELL"), 9.92, places=2)


if __name__ == "__main__":
    unittest.main()
