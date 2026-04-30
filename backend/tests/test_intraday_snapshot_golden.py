import hashlib
import unittest
from datetime import date
import numpy as np
import polars as pl

from backend.services.intraday_snapshot.builder import build_day_snapshot
from backend.services.intraday_snapshot.format import (
    MINUTES_PER_DAY, HEADER_BYTES, MAGIC, STRIKES_IN_CHAIN, OPT_TYPES,
)
from backend.services.intraday_snapshot import format as snapfmt


def _synthetic_options():
    """Synthetic options DataFrame: 1 expiry (idx=0), 21 strikes, both types,
    all 375 minutes. ts_min is absolute (minutes since 2017-01-01)."""
    base_ts = (date(2024, 3, 15) - date(2017, 1, 1)).days * 1440 + 9 * 60 + 15
    rows = []
    for m in range(MINUTES_PER_DAY):
        for k in range(-10, 11):
            strike_x100 = (22000 + k * 50) * 100
            for ot in (0, 1):
                base = 100 + abs(k) * 10
                rows.append({
                    "ts_min": base_ts + m,
                    "expiry_idx": 0,
                    "strike_x100": strike_x100,
                    "opt_type": ot,
                    "open_x100": base * 100,
                    "high_x100": (base + 5) * 100,
                    "low_x100": (base - 5) * 100,
                    "close_x100": base * 100,
                    "volume": 100 + m,
                    "oi": 1000,
                })
    return pl.DataFrame(rows).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("expiry_idx").cast(pl.Int16),
        pl.col("strike_x100").cast(pl.Int32),
        pl.col("opt_type").cast(pl.Int8),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int32),
        pl.col("oi").cast(pl.Int32),
    ])


def _synthetic_spot():
    base_ts = (date(2024, 3, 15) - date(2017, 1, 1)).days * 1440 + 9 * 60 + 15
    rows = []
    for m in range(MINUTES_PER_DAY):
        rows.append({
            "ts_min": base_ts + m,
            "open_x100":  2200000 + (m * 5),
            "high_x100":  2200500 + (m * 5),
            "low_x100":   2199500 + (m * 5),
            "close_x100": 2200000 + (m * 5),
            "volume":     1000 + m,
        })
    return pl.DataFrame(rows).with_columns([
        pl.col("ts_min").cast(pl.Int32),
        pl.col("open_x100").cast(pl.Int32),
        pl.col("high_x100").cast(pl.Int32),
        pl.col("low_x100").cast(pl.Int32),
        pl.col("close_x100").cast(pl.Int32),
        pl.col("volume").cast(pl.Int64),
    ])


# Recorded after first run (regenerate by deleting and re-running)
EXPECTED_SHA256 = "becd1d619b3aa59e72fe40497b6dadf1bc915888436303f5b4fc1a12ea7846aa"


class TestSnapshotGolden(unittest.TestCase):
    def test_header_present(self):
        bs = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        self.assertEqual(bs[:4], MAGIC)

    def test_size_matches_expectation(self):
        bs = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        expected = (
            HEADER_BYTES
            + 4 * 4 * MINUTES_PER_DAY                                # spot O,H,L,C int32
            + (2 + 4 * MINUTES_PER_DAY                               # expiry header + atm
               + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY  # close
               + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY  # high
               + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY  # low
               + 4 * STRIKES_IN_CHAIN * OPT_TYPES * MINUTES_PER_DAY  # volume
              ) * 1  # 1 expiry in synthetic
        )
        self.assertEqual(len(bs), expected)

    def test_sha256_locked(self):
        bs = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        self.assertEqual(hashlib.sha256(bs).hexdigest(), EXPECTED_SHA256)

    def test_deterministic(self):
        bs1 = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        bs2 = build_day_snapshot(
            symbol="NIFTY", trade_date=date(2024, 3, 15),
            options_df=_synthetic_options(), spot_df=_synthetic_spot(),
            strike_step_x100=5000,
        )
        self.assertEqual(bs1, bs2)


if __name__ == "__main__":
    unittest.main()
