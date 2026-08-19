"""Tests for services.optimizer.combo_labeler."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.optimizer.combo_labeler import label_combo, safe_filename  # noqa: E402


def _payload(**overrides):
    base = {
        "expiry_window": "weekly_expiry",
        "entry_dte": 1,
        "exit_dte": 1,
        "legs": [
            {
                "option_type": "CE",
                "position": "sell",
                "strike_selection": {
                    "type": "pct_of_atm",
                    "value": 0.5,
                    "direction": "OTM",
                },
            },
            {
                "option_type": "PE",
                "position": "sell",
                "strike_selection": {
                    "type": "pct_of_atm",
                    "value": 0.5,
                    "direction": "ITM",
                },
            },
        ],
        "spot_adjustment_enabled": False,
    }
    base.update(overrides)
    return base


class TestComboLabel(unittest.TestCase):
    def test_matches_sample_filename(self):
        # Each leg is self-identifying by its own option type + side + strike —
        # no leg-number tag needed, and no leg-number is emitted even when two
        # legs share an option type (strike/side already disambiguate them).
        # Expiry is per-leg (inline, right after the SYMBOL/idx), not one
        # combo-level token at the end — required for same-index mixed-expiry
        # strategies where legs don't share one expiry. Per FILENAME_FORMAT.md.
        payload = _payload()
        out = label_combo(payload)
        self.assertEqual(
            out["combo_label"],
            "NIFTY_W_CE_S_pctoA0.5_OTM_W_PE_S_pctoA0.5_ITM_NoAdjustment_T-1_To_T-1",
        )

    def test_worked_example_target_and_sl(self):
        # FILENAME_FORMAT.md worked example: short straddle, leg 1 with a 40%
        # target + 30% SL -> "NIFTY_W_CE_S_ATM_TP40pct_SL30pct_W_PE_S_ATM".
        payload = _payload(
            legs=[
                {
                    "option_type": "CE", "position": "sell",
                    "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
                    "targetProfit": {"mode": "PERCENT", "value": 40},
                    "stopLoss": {"mode": "PERCENT", "value": 30},
                },
                {
                    "option_type": "PE", "position": "sell",
                    "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
                },
            ],
        )
        out = label_combo(payload)
        self.assertEqual(
            out["combo_label"],
            "NIFTY_W_CE_S_ATM_TP40pct_SL30pct_W_PE_S_ATM_NoAdjustment_T-1_To_T-1",
        )

    def test_worked_example_rel_leg_premium_and_per_leg_adjustment(self):
        # Adapted from FILENAME_FORMAT.md's Relative-to-Leg(Delta) worked example
        # -- this app has no Delta strike mode, so leg 2 uses 'Relative to Leg
        # Premium' (rel_leg_premium) instead, which maps to the spec's default
        # ("All"/premium) RtL basis with no _TV/_D suffix: 'RtL1'.
        payload = _payload(
            expiry_window="monthly_expiry",
            legs=[
                {
                    "option_type": "PE", "position": "sell",
                    "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
                    "spot_adjustment": {"enabled": True, "pct": 1, "units": "percent", "direction": "both"},
                },
                {
                    "option_type": "PE", "position": "buy",
                    "strike_selection": {"type": "rel_leg_premium", "ref_leg": 1},
                },
            ],
        )
        out = label_combo(payload)
        # No trailing "NoAdjustment" here: leg 1's own per-leg adjustment already
        # covers the "is anything adjusted" question, so the strategy-level
        # (disabled) knob isn't redundantly appended — see label_combo's comment
        # on `per_leg_adj_seg`.
        self.assertEqual(
            out["combo_label"],
            "NIFTY_M_PE_S_ATM_adj_both_1pct_M_PE_B_RtL1_T-1_To_T-1",
        )

    def test_spot_adjustment_up(self):
        payload = _payload(
            spot_adjustment_enabled=True,
            spot_adjustment_direction="up",
            spot_adjustment_pct=1,
        )
        out = label_combo(payload)
        self.assertEqual(out["spot_adjustment"], "RiseBy1%")
        self.assertIn("RiseBy1%", out["combo_label"])

    def test_spot_adjustment_either(self):
        payload = _payload(
            spot_adjustment_enabled=True,
            spot_adjustment_direction="both",
            spot_adjustment_pct=3,
        )
        self.assertEqual(label_combo(payload)["spot_adjustment"], "RisesOrFallsBy3%")

    def test_shift_label_t_minus_2(self):
        payload = _payload(entry_dte=2, exit_dte=2)
        self.assertEqual(label_combo(payload)["shifting"], "T-2_To_T-2")

    def test_shift_label_t_zero(self):
        payload = _payload(entry_dte=0, exit_dte=0)
        self.assertEqual(label_combo(payload)["shifting"], "T-0_To_T-0")

    def test_atm_strike(self):
        payload = _payload()
        payload["legs"][0]["strike_selection"] = {"type": "strike_type", "strike_type": "ATM"}
        self.assertEqual(label_combo(payload)["call_strike_label"], "ATM")

    def test_safe_filename(self):
        self.assertEqual(safe_filename("a:b/c"), "a_b_c")
        self.assertEqual(safe_filename("0.5%_OTM"), "0.5%_OTM")
        self.assertEqual(safe_filename("T-1_To_T-1"), "T-1_To_T-1")


if __name__ == "__main__":
    unittest.main()
