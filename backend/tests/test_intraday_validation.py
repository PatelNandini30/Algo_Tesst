import unittest
from datetime import date
import polars as pl

from backend.services.intraday_ingest import validation
from backend.services.intraday_ingest.base import IntradayValidationError


def _good_frame():
    return pl.DataFrame({
        "ts_min": [3787755, 3787755, 3787756, 3787756],
        "trade_date": [date(2024, 3, 15)] * 4,
        "symbol": ["NIFTY"] * 4,
        "expiry_date": [date(2024, 3, 21)] * 4,
        "strike_x100": [2200000, 2200000, 2200000, 2200000],
        "opt_type": [0, 1, 0, 1],
        "open_x100": [12345, 5520, 12380, 5530],
        "high_x100": [12400, 5580, 12450, 5550],
        "low_x100":  [12250, 5490, 12360, 5450],
        "close_x100":[12380, 5530, 12420, 5480],
        "volume":    [1500, 2200, 1800, 2400],
        "oi":        [12000, 18000, 12100, 18200],
    }).with_columns(
        pl.col("ts_min").cast(pl.Int32),
        pl.col("strike_x100").cast(pl.Int32),
        pl.col("opt_type").cast(pl.Int8),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int32),
        pl.col("oi").cast(pl.Int32),
    )


class TestValidation(unittest.TestCase):
    def test_good_frame_passes(self):
        validation.validate(_good_frame(), trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_null_in_pk_rejected(self):
        bad = _good_frame().with_columns(
            pl.when(pl.col("ts_min") == 3787755).then(None).otherwise(pl.col("strike_x100"))
              .cast(pl.Int32).alias("strike_x100")
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_strike_not_multiple_of_step_rejected(self):
        # NIFTY step is 50; 22001.00 → 2200100 is not a multiple of 5000
        bad = _good_frame().with_columns(
            pl.lit(2200100).cast(pl.Int32).alias("strike_x100")
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_high_below_low_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit(12000).cast(pl.Int32).alias("high_x100"),  # high < low (12250)
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_expiry_in_past_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit(date(2024, 3, 14)).alias("expiry_date")  # before trade_date
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_expiry_too_far_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit(date(2024, 9, 1)).alias("expiry_date")  # >90 days
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_wrong_symbol_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit("BANKNIFTY").alias("symbol")
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")

    def test_wrong_trade_date_rejected(self):
        bad = _good_frame().with_columns(
            pl.lit(date(2024, 3, 14)).alias("trade_date")
        )
        with self.assertRaises(IntradayValidationError):
            validation.validate(bad, trade_date=date(2024, 3, 15), symbol="NIFTY")


if __name__ == "__main__":
    unittest.main()
