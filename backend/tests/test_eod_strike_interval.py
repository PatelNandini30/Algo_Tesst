import unittest
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.base import calculate_strike_from_selection
from backend.engines.generic_algotest_engine import _effective_strike_interval


class TestEodStrikeInterval(unittest.TestCase):
    def test_leg_strike_interval_override_accepts_50_or_100(self):
        self.assertEqual(_effective_strike_interval({'strike_interval': 50}, 100), 50)
        self.assertEqual(_effective_strike_interval({'strike_interval': '100'}, 50), 100)
        self.assertEqual(
            _effective_strike_interval({'strike_selection': {'strike_interval': 100}}, 50),
            100,
        )

    def test_leg_strike_interval_falls_back_for_old_payloads(self):
        self.assertEqual(_effective_strike_interval({}, 25), 25)
        self.assertEqual(_effective_strike_interval({'strike_interval': 25}, 50), 50)

    def test_atm_itm_otm_calculation_uses_selected_interval(self):
        self.assertEqual(calculate_strike_from_selection(24380, 50, 'ATM', 'CE'), 24400)
        self.assertEqual(calculate_strike_from_selection(24380, 100, 'ATM', 'CE'), 24400)
        self.assertEqual(calculate_strike_from_selection(24380, 50, 'OTM2', 'CE'), 24500)
        self.assertEqual(calculate_strike_from_selection(24380, 100, 'OTM2', 'CE'), 24600)
        self.assertEqual(calculate_strike_from_selection(24380, 50, 'ITM2', 'PE'), 24500)
        self.assertEqual(calculate_strike_from_selection(24380, 100, 'ITM2', 'PE'), 24600)


if __name__ == '__main__':
    unittest.main()
