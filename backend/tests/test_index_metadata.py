import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.index_metadata import get_lot_size_for_index, validate_index_payload


class TestIndexMetadata(unittest.TestCase):
    def test_banknifty_monthly_payload_is_allowed(self):
        validate_index_payload({
            "index": "BANKNIFTY",
            "expiry_type": "MONTHLY",
            "legs": [
                {"segment": "OPTIONS", "expiry": "MONTHLY"},
                {"segment": "FUTURES", "expiry": "next_monthly"},
            ],
        })

    def test_midcpnifty_weekly_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "MIDCPNIFTY is monthly-only"):
            validate_index_payload({
                "index": "MIDCPNIFTY",
                "expiry_type": "WEEKLY",
                "legs": [{"segment": "OPTIONS", "expiry": "WEEKLY"}],
            })

    def test_banknifty_weekly_leg_is_rejected_even_with_monthly_basis(self):
        with self.assertRaisesRegex(ValueError, "BANKNIFTY is monthly-only"):
            validate_index_payload({
                "index": "BANKNIFTY",
                "expiry_type": "MONTHLY",
                "legs": [{"segment": "OPTIONS", "expiry": "NEXT_WEEKLY"}],
            })

    def test_sensex_is_visible_but_not_backtest_enabled(self):
        with self.assertRaisesRegex(ValueError, "SENSEX backtest data is not available"):
            validate_index_payload({
                "index": "SENSEX",
                "expiry_type": "WEEKLY",
                "legs": [{"segment": "OPTIONS", "expiry": "WEEKLY"}],
            })

    def test_lot_sizes_do_not_fall_back_to_one_for_monthly_only_indices(self):
        self.assertEqual(get_lot_size_for_index("BANKNIFTY", "2025-08-01"), 35)
        self.assertEqual(get_lot_size_for_index("BANKNIFTY", "2026-01-27"), 30)
        self.assertEqual(get_lot_size_for_index("MIDCPNIFTY", "2025-08-01"), 140)
        self.assertEqual(get_lot_size_for_index("MIDCPNIFTY", "2026-01-27"), 120)


if __name__ == "__main__":
    unittest.main()
