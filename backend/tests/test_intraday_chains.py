import unittest
import polars as pl

from backend.services.intraday_snapshot import chains
from backend.services.intraday_snapshot.format import (
    MINUTES_PER_DAY, STRIKES_IN_CHAIN,
)


def _opts_for_expiry(strike_step=50):
    """Generate synthetic 1-minute options data for ATM±10 strikes for one expiry,
    expiry_idx=0, all 375 minutes, both CE and PE."""
    rows = []
    for m in range(MINUTES_PER_DAY):
        for k_offset in range(-10, 11):
            strike_x100 = (22000 + k_offset * strike_step) * 100
            for ot in (0, 1):
                base = 100 + abs(k_offset) * 10
                rows.append({
                    "ts_min": m,
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


class TestChains(unittest.TestCase):
    def test_chain_dimensions(self):
        opts = _opts_for_expiry()
        anchor_atm = 22000 * 100
        chain = chains.build_chain(opts, anchor_atm_x100=anchor_atm, strike_step_x100=5000)
        self.assertEqual(chain["close"].shape, (STRIKES_IN_CHAIN, 2, MINUTES_PER_DAY))
        self.assertEqual(chain["high"].shape, (STRIKES_IN_CHAIN, 2, MINUTES_PER_DAY))
        self.assertEqual(chain["low"].shape, (STRIKES_IN_CHAIN, 2, MINUTES_PER_DAY))
        self.assertEqual(chain["volume"].shape, (STRIKES_IN_CHAIN, 2, MINUTES_PER_DAY))

    def test_chain_strikes_are_atm_plus_minus_5(self):
        opts = _opts_for_expiry()
        anchor_atm = 22000 * 100
        chain = chains.build_chain(opts, anchor_atm_x100=anchor_atm, strike_step_x100=5000)
        self.assertEqual(
            list(chain["strikes_x100"]),
            [(22000 + d * 50) * 100 for d in range(-5, 6)],
        )

    def test_chain_close_values_match_input(self):
        opts = _opts_for_expiry()
        anchor_atm = 22000 * 100
        chain = chains.build_chain(opts, anchor_atm_x100=anchor_atm, strike_step_x100=5000)
        # Chain index 5 = ATM (22000), opt_type=0 (CE), minute=0
        # In the synthetic data, ATM CE close at minute 0 was base=100 → 10000
        self.assertEqual(chain["close"][5, 0, 0], 10000)

    def test_missing_strikes_filled_with_zero(self):
        opts = _opts_for_expiry()
        anchor_atm = 22500 * 100  # +500 from data center
        chain = chains.build_chain(opts, anchor_atm_x100=anchor_atm, strike_step_x100=5000)
        self.assertEqual(chain["close"][-1, 0, 0], 0)


if __name__ == "__main__":
    unittest.main()
