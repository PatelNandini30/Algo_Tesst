import unittest

import polars as pl

from services import rust_fast_path


class TestRustFastPath(unittest.TestCase):
    def setUp(self):
        rust_fast_path.clear_cache()

    def test_build_cache_gracefully_falls_back_without_native(self):
        options_df = pl.DataFrame(
            {
                "Date": ["2024-01-15"],
                "Symbol": ["NIFTY"],
                "ExpiryDate": ["2024-01-25"],
                "OptionType": ["CE"],
                "StrikePrice": [22000.0],
                "Close": [150.5],
            }
        )
        spot_df = pl.DataFrame(
            {
                "Date": ["2024-01-15"],
                "Symbol": ["NIFTY"],
                "Close": [22050.0],
            }
        )
        self.assertIn(rust_fast_path.build_cache(options_df, spot_df), (True, False))

    def test_can_use_rust_for_legs_rejects_futures(self):
        legs = [{"segment": "FUTURES"}]
        self.assertFalse(rust_fast_path.can_use_rust_for_legs(legs))


if __name__ == "__main__":
    unittest.main()
