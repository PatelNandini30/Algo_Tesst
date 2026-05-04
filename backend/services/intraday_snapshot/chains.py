"""Pure: extract ATM±5 chain arrays for one expiry from cleaned options data."""
import numpy as np
import polars as pl

from .format import (
    MINUTES_PER_DAY, STRIKE_RADIUS, STRIKES_IN_CHAIN, OPT_TYPES,
)


def build_chain(
    opts_for_expiry: pl.DataFrame,
    *,
    anchor_atm_x100: int,
    strike_step_x100: int,
) -> dict:
    """Return dict with:
       strikes_x100: tuple[int, ...] length STRIKES_IN_CHAIN
       close, high, low, volume: int32 numpy arrays shape (STRIKES_IN_CHAIN, OPT_TYPES, MINUTES_PER_DAY)
    """
    chain_strikes = tuple(
        anchor_atm_x100 + d * strike_step_x100
        for d in range(-STRIKE_RADIUS, STRIKE_RADIUS + 1)
    )

    close = np.zeros((STRIKES_IN_CHAIN, OPT_TYPES, MINUTES_PER_DAY), dtype=np.int32)
    high = np.zeros_like(close)
    low = np.zeros_like(close)
    volume = np.zeros_like(close)

    for k_idx, strike_x100 in enumerate(chain_strikes):
        slice_for_strike = opts_for_expiry.filter(pl.col("strike_x100") == strike_x100)
        if slice_for_strike.is_empty():
            continue
        for ot in (0, 1):
            slc = slice_for_strike.filter(pl.col("opt_type") == ot).sort(by="ts_min")
            if slc.is_empty():
                continue
            ts = slc["ts_min"].to_numpy()
            valid = (ts >= 0) & (ts < MINUTES_PER_DAY)
            ts = ts[valid]
            if len(ts) == 0:
                continue
            close[k_idx, ot, ts] = slc["close_x100"].to_numpy()[valid]
            high[k_idx, ot, ts] = slc["high_x100"].to_numpy()[valid]
            low[k_idx, ot, ts] = slc["low_x100"].to_numpy()[valid]
            volume[k_idx, ot, ts] = slc["volume"].to_numpy()[valid]

    return {
        "strikes_x100": chain_strikes,
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
    }
