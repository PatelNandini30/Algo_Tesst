"""
Guard tests for the Rust combo-loop whitelist + flag (services/optimizer/rust_combo_loop).

The whitelist is the entire safety argument for the Rust batch path (design R5): it
MUST fail-closed — only proven-simple shapes return None (run in Rust); every
orchestration feature and every unrecognized shape routes to the Python engine. These
tests pin that contract so a future edit can't silently widen what Rust touches.

Pure logic, no data/native dependency — safe to run anywhere.
"""
import unittest

from services.optimizer.rust_combo_loop import (
    rust_loop_mode,
    needs_python,
    combo_supported,
    diff_summary,
    diff_redis_row,
)


def _leg(**kw):
    base = {"option_type": "CE", "position": "SELL",
            "strike_selection": {"type": "strike_type"}}
    base.update(kw)
    return base


class TestFlagDefault(unittest.TestCase):
    def test_defaults_off(self):
        # With OPTIMIZE_RUST_LOOP unset the mode is "0" (today's fork-pool path).
        import os
        os.environ.pop("OPTIMIZE_RUST_LOOP", None)
        self.assertEqual(rust_loop_mode(), "0")

    def test_unknown_value_falls_back_to_off(self):
        import os
        os.environ["OPTIMIZE_RUST_LOOP"] = "yes-please"
        try:
            self.assertEqual(rust_loop_mode(), "0")
        finally:
            os.environ.pop("OPTIMIZE_RUST_LOOP", None)


class TestWhitelistAcceptsSimpleShapes(unittest.TestCase):
    def test_single_leg_atm(self):
        self.assertIsNone(needs_python({"legs": [_leg()]}))
        self.assertTrue(combo_supported({"legs": [_leg()]}))

    def test_multi_leg_straddle(self):
        p = {"legs": [_leg(option_type="CE"), _leg(option_type="PE")]}
        self.assertIsNone(needs_python(p))

    def test_recognized_strike_modes(self):
        for t in ("strike_type", "", "pct_of_atm", "rel_leg", "closest_premium",
                  "premium_gte", "premium_lte", "premium_range", "straddle_width",
                  "atm_straddle_prem_pct", "atm", "itm2", "otm1"):
            p = {"legs": [_leg(strike_selection={"type": t})]}
            self.assertIsNone(needs_python(p), f"{t!r} should be supported")

    def test_rollover_is_supported(self):
        p = {"legs": [_leg()], "rollover_toggle": True, "expiry_type": "WEEKLY"}
        self.assertIsNone(needs_python(p))


class TestWhitelistFailsClosed(unittest.TestCase):
    """Every excluded feature MUST route to Python (needs_python returns a reason)."""

    def _reject(self, payload, contains):
        r = needs_python(payload)
        self.assertIsNotNone(r, f"expected reject, got None for {payload}")
        self.assertIn(contains, r)

    def test_spot_adjustment(self):
        self._reject({"legs": [_leg()], "spot_adjustment_enabled": True}, "spot_adjustment")

    def test_midcap_legs(self):
        self._reject({"legs": [_leg()], "midcap_legs": [{"x": 1}]}, "midcap")

    def test_midcap_sa(self):
        self._reject({"legs": [_leg()], "midcap_spot_adjustment": {"enabled": True}}, "midcap")

    def test_filter_segments(self):
        self._reject({"legs": [_leg()], "filter_segments": [{"start": "2024-01-01"}]}, "filter")

    def test_overall_sl(self):
        self._reject({"legs": [_leg()], "overall_sl_value": 30}, "overall_sl_target")

    def test_leg_stop_loss(self):
        self._reject({"legs": [_leg(stopLoss={"enabled": True, "value": 30})]}, "sl_target_trail")

    def test_leg_target(self):
        self._reject({"legs": [_leg(targetProfit={"enabled": True, "value": 50})]}, "sl_target_trail")

    def test_leg_trail(self):
        self._reject({"legs": [_leg(trailSL={"enabled": True, "trigger": 20})]}, "sl_target_trail")

    def test_buffer_strike(self):
        self._reject({"legs": [_leg(buffer_strike_enabled=True)]}, "sl_target_trail")

    def test_futures_leg(self):
        self._reject({"legs": [_leg(option_type="FUT", segment="futures")]}, "futures")

    def test_next_weekly(self):
        for exp in ("NEXT_WEEKLY", "WEEKLY_T1", "NEXT_MONTHLY", "MONTHLY_T1"):
            self._reject({"legs": [_leg(expiry=exp)]}, "next_expiry")

    def test_reentry(self):
        self._reject({"legs": [_leg(reEntryOnSL=True)]}, "reentry")
        self._reject({"legs": [_leg(reEntryOnTarget=True)]}, "reentry")

    def test_lazy_leg(self):
        self._reject({"legs": [_leg(lazy_leg_config={"x": 1})]}, "lazy")

    def test_unknown_strike_mode(self):
        self._reject({"legs": [_leg(strike_selection={"type": "delta_neutral"})]}, "strike:")

    def test_no_legs(self):
        self._reject({"legs": []}, "no-legs")

    def test_malformed_payload(self):
        self.assertIsNotNone(needs_python(None))
        self.assertIsNotNone(needs_python({"legs": [42]}))


class TestDiffers(unittest.TestCase):
    def test_summary_identical(self):
        self.assertEqual(diff_summary({"a": 1.0, "b": 2}, {"a": 1.0, "b": 2}), [])

    def test_summary_tolerates_float_noise(self):
        self.assertEqual(diff_summary({"a": 1.0}, {"a": 1.0 + 1e-9}), [])

    def test_summary_reports_mismatch(self):
        self.assertTrue(diff_summary({"a": 1.0}, {"a": 1.5}))

    def test_summary_reports_missing_key(self):
        self.assertTrue(diff_summary({"a": 1}, {"a": 1, "b": 2}))

    def test_redis_row_nested(self):
        a = {"summary": {"pnl": 100.0}, "trade_count": 5}
        b = {"summary": {"pnl": 100.0}, "trade_count": 5}
        self.assertEqual(diff_redis_row(a, b), [])
        b["trade_count"] = 6
        self.assertTrue(diff_redis_row(a, b))


if __name__ == "__main__":
    unittest.main()
