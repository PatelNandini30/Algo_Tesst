import os
import tempfile
import unittest
import polars as pl
import pyarrow.parquet as pq

from backend.services import intraday_spot_writer


def _good_spot():
    return pl.DataFrame({
        "ts_min": [3787755, 3787756],
        "open_x100":  [2200000, 2200500],
        "high_x100":  [2201000, 2201500],
        "low_x100":   [2199500, 2200000],
        "close_x100": [2200500, 2201200],
        "volume":     [1_000_000, 1_200_000],
    }).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int64),
    ])


class TestSpotWriter(unittest.TestCase):
    def test_writes_and_schema_correct(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "spot.parquet")
            intraday_spot_writer.write(df=_good_spot(), output_path=path)
            self.assertTrue(os.path.exists(path))
            t = pq.read_table(path)
            self.assertEqual(set(t.column_names), {
                "ts_min", "open_x100", "high_x100", "low_x100", "close_x100", "volume"
            })

    def test_sorted_by_ts_min(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "spot.parquet")
            unsorted = _good_spot().sort(by="ts_min", descending=True)
            intraday_spot_writer.write(df=unsorted, output_path=path)
            out = pl.read_parquet(path)
            ts = out["ts_min"].to_list()
            self.assertEqual(ts, sorted(ts))


if __name__ == "__main__":
    unittest.main()
