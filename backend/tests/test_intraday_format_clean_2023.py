import os
import unittest
from datetime import date
import polars as pl

from backend.services.intraday_ingest.format_clean_2023 import CleanFormat2023Handler

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "intraday", "synthetic_one_day.csv"
)


class TestCleanFormat2023(unittest.TestCase):
    def setUp(self):
        self.handler = CleanFormat2023Handler()

    def test_header_signature(self):
        self.assertEqual(
            self.handler.HEADER_SIGNATURE,
            "Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,Open,High,Low,Close,Volume,OI",
        )

    def test_clean_returns_polars_dataframe(self):
        df = self.handler.clean(FIXTURE)
        self.assertIsInstance(df, pl.DataFrame)

    def test_cleaned_schema(self):
        df = self.handler.clean(FIXTURE)
        expected_columns = {
            "ts_min", "trade_date", "symbol", "expiry_date",
            "strike_x100", "opt_type",
            "open_x100", "high_x100", "low_x100", "close_x100",
            "volume", "oi",
        }
        self.assertEqual(set(df.columns), expected_columns)

    def test_ts_min_is_minutes_since_epoch_2017(self):
        df = self.handler.clean(FIXTURE)
        # 2024-03-15 09:15 IST → minutes since 2017-01-01 00:00:00
        # (date(2024,3,15) - date(2017,1,1)).days = 2630; 2630*1440 + 9*60+15 = 3787755
        first_ts = df.select("ts_min").row(0)[0]
        self.assertEqual(first_ts, 3787755)

    def test_strike_and_prices_are_x100_int32(self):
        df = self.handler.clean(FIXTURE)
        self.assertEqual(df["strike_x100"].dtype, pl.Int32)
        self.assertEqual(df["close_x100"].dtype, pl.Int32)
        # 22000.00 * 100 = 2200000
        self.assertEqual(df.select("strike_x100").row(0)[0], 2200000)
        # CE row 0 close is 123.80
        ce_close = df.filter(pl.col("opt_type") == 0).select("close_x100").row(0)[0]
        self.assertEqual(ce_close, 12380)

    def test_opt_type_encoded_as_int8(self):
        df = self.handler.clean(FIXTURE)
        self.assertEqual(df["opt_type"].dtype, pl.Int8)
        ce_count = df.filter(pl.col("opt_type") == 0).height
        pe_count = df.filter(pl.col("opt_type") == 1).height
        self.assertEqual(ce_count, 2)
        self.assertEqual(pe_count, 2)

    def test_row_count(self):
        df = self.handler.clean(FIXTURE)
        self.assertEqual(df.height, 4)


if __name__ == "__main__":
    unittest.main()
