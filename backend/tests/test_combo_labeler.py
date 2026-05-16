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
        payload = _payload()
        out = label_combo(payload)
        self.assertEqual(
            out["combo_label"],
            "CE_0.5%_OTM_Sell_PE_0.5%_ITM_Sell_NoAdjustment_Weekly_Expiry_T-1_To_T-1",
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
