"""Pure validators on a cleaned intraday options DataFrame."""
from datetime import date, timedelta
import polars as pl

from backend.services.intraday_ingest.base import IntradayValidationError

PK_COLUMNS = ("ts_min", "expiry_date", "strike_x100", "opt_type")
STRIKE_STEP_X100 = {
    "NIFTY": 5000,       # 50 INR
    "BANKNIFTY": 10000,  # 100 INR
    "FINNIFTY": 5000,    # 50 INR
    "MIDCPNIFTY": 2500,  # 25 INR
}


def validate(df: pl.DataFrame, *, trade_date: date, symbol: str) -> None:
    if df.is_empty():
        raise IntradayValidationError("empty frame")

    # 1) PK columns have no nulls
    for col in PK_COLUMNS:
        if df[col].null_count() > 0:
            raise IntradayValidationError(f"nulls in PK column {col}")

    # 2) Symbol matches the expected one (single-symbol files)
    distinct_symbols = df["symbol"].unique().to_list()
    if distinct_symbols != [symbol]:
        raise IntradayValidationError(
            f"symbol mismatch: file has {distinct_symbols}, expected [{symbol}]"
        )

    # 3) trade_date matches
    distinct_dates = df["trade_date"].unique().to_list()
    if distinct_dates != [trade_date]:
        raise IntradayValidationError(
            f"trade_date mismatch: file has {distinct_dates}, expected [{trade_date}]"
        )

    # 4) Strike multiples
    step = STRIKE_STEP_X100[symbol]
    bad_strikes = df.filter(pl.col("strike_x100") % step != 0).height
    if bad_strikes > 0:
        raise IntradayValidationError(
            f"{bad_strikes} rows have strike not multiple of {step / 100} INR"
        )

    # 5) OHLC sanity: high >= max(open, close) >= min(open, close) >= low
    bad_ohlc = df.filter(
        (pl.col("high_x100") < pl.col("open_x100"))
        | (pl.col("high_x100") < pl.col("close_x100"))
        | (pl.col("high_x100") < pl.col("low_x100"))
        | (pl.col("low_x100") > pl.col("open_x100"))
        | (pl.col("low_x100") > pl.col("close_x100"))
    ).height
    if bad_ohlc > 0:
        raise IntradayValidationError(f"{bad_ohlc} rows have OHLC out of order")

    # 6) Expiry sanity: must be on/after trade_date and within 90 days
    earliest_allowed = trade_date
    latest_allowed = trade_date + timedelta(days=90)
    bad_expiry = df.filter(
        (pl.col("expiry_date") < earliest_allowed)
        | (pl.col("expiry_date") > latest_allowed)
    ).height
    if bad_expiry > 0:
        raise IntradayValidationError(
            f"{bad_expiry} rows have expiry outside [{earliest_allowed}..{latest_allowed}]"
        )
