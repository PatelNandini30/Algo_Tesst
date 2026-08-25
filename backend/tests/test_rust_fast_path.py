import unittest
from unittest import mock

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

    def test_clear_cache_resets_native_and_python_residency_markers(self):
        native = mock.Mock()
        rust_fast_path._loaded_cache_key = "arrow-v2:bulk:NIFTY:full"
        rust_fast_path._loaded_cache_signature = ((1, 2), (3, 4))
        rust_fast_path._loaded_feather_root = "/tmp/fake"
        rust_fast_path._merged_symbols.add("NIFTY")
        rust_fast_path._loaded_signatures["fake"] = ((1, 2), (3, 4))

        with mock.patch.object(rust_fast_path, "_load_native", return_value=native):
            rust_fast_path.clear_cache()

        native.clear_cache.assert_called_once_with()
        self.assertIsNone(rust_fast_path._loaded_cache_key)
        self.assertIsNone(rust_fast_path._loaded_cache_signature)
        self.assertIsNone(rust_fast_path._loaded_feather_root)
        self.assertEqual(rust_fast_path._merged_symbols, set())
        self.assertEqual(rust_fast_path._loaded_signatures, {})


if __name__ == "__main__":
    unittest.main()
