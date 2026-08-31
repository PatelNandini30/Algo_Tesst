"""Spec parity for the per-leg 'Spot Above/Below Strike' + margin trigger.

Mirrors leg_spot_adjustment_above_below_margin_logic.md §6 (Cases A–H).
Tests the pure level/fire math (_vs_strike_fires); the surrounding scan just
unions the resulting dates into _spot_adj_breaches, which is already covered by
the per-leg spot-adjustment tests.
"""
import unittest

from backend.services.engine_rust import _vs_strike_fires


class TestVsStrikeFires(unittest.TestCase):
    def test_case_a_above_no_margin(self):
        # strike 23000, Above, margin OFF -> fires when spot > 23000 (strict)
        self.assertFalse(_vs_strike_fires(22990, 23000, "above", 0))
        self.assertFalse(_vs_strike_fires(23000, 23000, "above", 0))  # equal: no
        self.assertTrue(_vs_strike_fires(23010, 23000, "above", 0))

    def test_case_b_above_margin_1pct(self):
        # margin = round(23000*1/100)=230 -> cmp 22770; fires when spot > 22770
        self.assertFalse(_vs_strike_fires(22700, 23000, "above", 1.0))
        self.assertFalse(_vs_strike_fires(22770, 23000, "above", 1.0))  # equal: no
        self.assertTrue(_vs_strike_fires(22780, 23000, "above", 1.0))

    def test_case_c_below_no_margin(self):
        self.assertFalse(_vs_strike_fires(23010, 23000, "below", 0))
        self.assertFalse(_vs_strike_fires(23000, 23000, "below", 0))  # equal: no
        self.assertTrue(_vs_strike_fires(22990, 23000, "below", 0))

    def test_case_d_below_margin_1pct(self):
        # cmp = 23000 + 230 = 23230; fires when spot < 23230
        self.assertFalse(_vs_strike_fires(23250, 23000, "below", 1.0))
        self.assertFalse(_vs_strike_fires(23230, 23000, "below", 1.0))  # equal: no
        self.assertTrue(_vs_strike_fires(23220, 23000, "below", 1.0))

    def test_case_h_zero_margin_equals_off(self):
        # 0% margin collapses to raw-strike comparison (== Case A)
        for spot in (22990, 23000, 23010):
            self.assertEqual(
                _vs_strike_fires(spot, 23000, "above", 0.0),
                _vs_strike_fires(spot, 23000, "above", None),
            )

    def test_margin_recomputed_per_strike(self):
        # percentage floats with the live strike, not a fixed point buffer
        # 1% of 22900 = 229 -> cmp 22671
        self.assertTrue(_vs_strike_fires(22672, 22900, "above", 1.0))
        self.assertFalse(_vs_strike_fires(22671, 22900, "above", 1.0))  # equal: no


if __name__ == "__main__":
    unittest.main()
