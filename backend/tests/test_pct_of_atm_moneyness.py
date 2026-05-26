import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.engine_rust import _compute_strike_for_leg_python  # noqa: E402


class TestPctOfAtmMoneyness(unittest.TestCase):
    def _strike(self, option_type, value, direction):
        return _compute_strike_for_leg_python(
            {
                "option_type": option_type,
                "strike_selection": {
                    "type": "pct_of_atm",
                    "value": value,
                    "direction": direction,
                },
            },
            entry_spot=10735.45,
            interval=100,
        )

    def test_ce_semantic_otm_uses_absolute_value_above_spot(self):
        self.assertEqual(self._strike("CE", -5, "OTM"), 11300)
        self.assertEqual(self._strike("CE", 5, "OTM"), 11300)

    def test_ce_semantic_itm_uses_absolute_value_below_spot(self):
        self.assertEqual(self._strike("CE", -5, "ITM"), 10200)
        self.assertEqual(self._strike("CE", 5, "ITM"), 10200)

    def test_pe_semantic_otm_uses_absolute_value_below_spot(self):
        self.assertEqual(self._strike("PE", -5, "OTM"), 10200)
        self.assertEqual(self._strike("PE", 5, "OTM"), 10200)

    def test_signed_direction_keeps_signed_offset_behavior(self):
        self.assertEqual(self._strike("CE", -5, "+"), 10200)
        self.assertEqual(self._strike("CE", -5, "-"), 11300)

    def test_missing_direction_defaults_to_semantic_otm(self):
        strike = _compute_strike_for_leg_python(
            {
                "option_type": "CE",
                "strike_selection": {
                    "type": "pct_of_atm",
                    "value": -5,
                },
            },
            entry_spot=24004.75,
            interval=50,
        )
        self.assertEqual(strike, 25200)


if __name__ == "__main__":
    unittest.main()
