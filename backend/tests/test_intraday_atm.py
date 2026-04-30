import unittest
import polars as pl

from backend.services.intraday_snapshot import atm


class TestAtm(unittest.TestCase):
    def test_atm_picks_closest_strike(self):
        spot = pl.DataFrame({
            "ts_min": [0, 1, 2],
            "close_x100": [2202300, 2204700, 2197600],
        }).with_columns([pl.col("ts_min").cast(pl.Int32),
                         pl.col("close_x100").cast(pl.Int32)])
        strikes_x100 = [2200000, 2205000, 2210000, 2195000, 2190000]
        # 22023 → closest to 22000 (dist 2300 vs 4700 for 22050)
        # 22047 → closest to 22050 (dist 300 vs 4700 for 22000)
        # 21976 → closest to 22000 (dist 2400 vs 2600 for 21950)
        result = atm.atm_per_minute(spot, strikes_x100)
        self.assertEqual(result[:3], [2200000, 2205000, 2200000])

    def test_tie_breaker_picks_lower_strike(self):
        # Spot exactly between two strikes → pick the lower
        spot = pl.DataFrame({
            "ts_min": [0],
            "close_x100": [2202500],
        }).with_columns([pl.col("ts_min").cast(pl.Int32),
                         pl.col("close_x100").cast(pl.Int32)])
        result = atm.atm_per_minute(spot, [2200000, 2205000])
        self.assertEqual(result[0], 2200000)

    def test_short_session_pads_to_minutes_per_day(self):
        spot = pl.DataFrame({
            "ts_min": [0, 1],
            "close_x100": [2200000, 2200500],
        }).with_columns([pl.col("ts_min").cast(pl.Int32),
                         pl.col("close_x100").cast(pl.Int32)])
        result = atm.atm_per_minute(spot, [2200000, 2205000], expected_minutes=5)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[2:], [2200000, 2200000, 2200000])

    def test_empty_strikes_raises(self):
        spot = pl.DataFrame({
            "ts_min": [0],
            "close_x100": [2200000],
        }).with_columns([pl.col("ts_min").cast(pl.Int32),
                         pl.col("close_x100").cast(pl.Int32)])
        with self.assertRaises(ValueError):
            atm.atm_per_minute(spot, [])


if __name__ == "__main__":
    unittest.main()
