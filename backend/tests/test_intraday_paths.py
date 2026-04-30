import unittest
from datetime import date
from backend.services import intraday_paths


class TestIntradayPaths(unittest.TestCase):
    def setUp(self):
        self.root = "/data/intraday"

    def test_options_parquet_path_for_known_date(self):
        p = intraday_paths.options_parquet_path(self.root, "NIFTY", date(2024, 3, 15))
        self.assertEqual(
            p, "/data/intraday/NIFTY/options/year=2024/month=03/options.parquet"
        )

    def test_spot_parquet_path(self):
        p = intraday_paths.spot_parquet_path(self.root, "NIFTY", year=2024)
        self.assertEqual(p, "/data/intraday/NIFTY/spot/year=2024/spot.parquet")

    def test_snapshot_path(self):
        p = intraday_paths.snapshot_path(self.root, "NIFTY", date(2024, 3, 15))
        self.assertEqual(p, "/data/intraday/NIFTY/snapshots/2024-03-15.arrow")

    def test_expiry_dim_path(self):
        p = intraday_paths.expiry_dim_path(self.root, "NIFTY")
        self.assertEqual(p, "/data/intraday/NIFTY/expiries.json")

    def test_symbol_dir(self):
        p = intraday_paths.symbol_dir(self.root, "BANKNIFTY")
        self.assertEqual(p, "/data/intraday/BANKNIFTY")

    def test_symbol_uppercased(self):
        p = intraday_paths.symbol_dir(self.root, "nifty")
        self.assertEqual(p, "/data/intraday/NIFTY")

    def test_invalid_symbol_rejected(self):
        with self.assertRaises(ValueError):
            intraday_paths.symbol_dir(self.root, "TCS")


if __name__ == "__main__":
    unittest.main()
