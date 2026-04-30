"""Build a DaySnapshot binary buffer from cleaned options + spot for one trading day."""
import struct
from datetime import date
import numpy as np
import polars as pl

from backend.services.intraday_snapshot import format as snapfmt
from backend.services.intraday_snapshot.atm import atm_per_minute
from backend.services.intraday_snapshot.chains import build_chain
from backend.services.intraday_ingest.format_clean_2023 import (
    TS_EPOCH_DATE as INTRADAY_TS_EPOCH,
)


def build_day_snapshot(
    *,
    symbol: str,
    trade_date: date,
    options_df: pl.DataFrame,
    spot_df: pl.DataFrame,
    strike_step_x100: int,
) -> bytes:
    """Pack the full DaySnapshot for one (symbol, trade_date).

    `options_df` and `spot_df` must use absolute `ts_min` values
    (minutes since INTRADAY_TS_EPOCH = 2017-01-01). The builder converts
    to minute-of-day internally.
    """
    base_ts = (trade_date - INTRADAY_TS_EPOCH).days * 1440 + 9 * 60 + 15
    spot_local = spot_df.with_columns(
        (pl.col("ts_min") - base_ts).cast(pl.Int32).alias("ts_min")
    ).filter(
        (pl.col("ts_min") >= 0) & (pl.col("ts_min") < snapfmt.MINUTES_PER_DAY)
    ).sort(by="ts_min")

    options_local = options_df.with_columns(
        (pl.col("ts_min") - base_ts).cast(pl.Int32).alias("ts_min")
    ).filter(
        (pl.col("ts_min") >= 0) & (pl.col("ts_min") < snapfmt.MINUTES_PER_DAY)
    )

    expiry_indices = sorted(options_local["expiry_idx"].unique().to_list())

    spot_arrays = {
        c: np.zeros(snapfmt.MINUTES_PER_DAY, dtype=np.int32)
        for c in ("open_x100", "high_x100", "low_x100", "close_x100")
    }
    for c in spot_arrays:
        ts = spot_local["ts_min"].to_numpy()
        spot_arrays[c][ts] = spot_local[c].to_numpy()

    header = snapfmt.pack_header(
        symbol=symbol, trade_date=trade_date, expiry_count=len(expiry_indices)
    )

    spot_bytes = b"".join(spot_arrays[c].tobytes() for c in
                          ("open_x100", "high_x100", "low_x100", "close_x100"))

    expiry_payloads = []
    for eidx in expiry_indices:
        opts_e = options_local.filter(pl.col("expiry_idx") == eidx)
        strikes_in_data = sorted(opts_e["strike_x100"].unique().to_list())
        atm_arr = np.array(
            atm_per_minute(spot_local, strikes_in_data),
            dtype=np.int32,
        )
        anchor = int(atm_arr[0])
        chain = build_chain(
            opts_e, anchor_atm_x100=anchor, strike_step_x100=strike_step_x100
        )
        expiry_header = struct.pack("<h", eidx)  # int16
        atm_bytes = atm_arr.tobytes()
        chain_bytes = b"".join([
            chain["close"].tobytes(),
            chain["high"].tobytes(),
            chain["low"].tobytes(),
            chain["volume"].tobytes(),
        ])
        expiry_payloads.append(expiry_header + atm_bytes + chain_bytes)

    return header + spot_bytes + b"".join(expiry_payloads)
