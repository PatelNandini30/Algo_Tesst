import os
import tempfile
import unittest
from datetime import date
import polars as pl
import pyarrow.parquet as pq

from backend.services import intraday_parquet_writer


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
    }).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("strike_x100").cast(pl.Int32),
        pl.col("opt_type").cast(pl.Int8),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int32),
        pl.col("oi").cast(pl.Int32),
    ])


class TestParquetWriter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "options.parquet")

    def test_writes_file(self):
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        self.assertTrue(os.path.exists(self.path))

    def test_schema_matches_spec(self):
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        table = pq.read_table(self.path)
        cols = set(table.column_names)
        self.assertEqual(cols, {
            "ts_min", "expiry_idx", "strike_x100", "opt_type",
            "open_x100", "high_x100", "low_x100", "close_x100",
            "volume", "oi",
        })
        self.assertEqual(str(table.schema.field("expiry_idx").type), "int16")
        self.assertEqual(str(table.schema.field("opt_type").type), "int8")
        self.assertEqual(str(table.schema.field("ts_min").type), "int32")

    def test_sort_order_is_expiry_type_strike_ts(self):
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        df = pl.read_parquet(self.path)
        for i in range(1, df.height):
            prev = df.row(i - 1)
            cur = df.row(i)
            cols = ["expiry_idx", "opt_type", "strike_x100", "ts_min"]
            prev_key = tuple(prev[df.columns.index(c)] for c in cols)
            cur_key = tuple(cur[df.columns.index(c)] for c in cols)
            self.assertLessEqual(prev_key, cur_key)

    def test_idempotent_same_content_no_change(self):
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        intraday_parquet_writer.write(
            df=_good_frame(),
            output_path=self.path,
            expiry_dim={date(2024, 3, 21): 0},
        )
        df1 = pl.read_parquet(self.path)
        self.assertEqual(df1.height, 4)


if __name__ == "__main__":
    unittest.main()
