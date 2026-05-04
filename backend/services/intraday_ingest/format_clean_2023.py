"""Handler for the clean 2023+ CSV format. See FORMATS.md."""
from datetime import date
import polars as pl

from .base import (
    BaseFormatHandler,
    register_handler,
)

# Epoch for ts_min: 2017-01-01 00:00 IST. 4-byte int32 covers ~100 years.
TS_EPOCH_DATE = date(2017, 1, 1)
TS_EPOCH_MINUTES_OFFSET = (TS_EPOCH_DATE - date(1970, 1, 1)).days * 1440


class CleanFormat2023Handler(BaseFormatHandler):
    HEADER_SIGNATURE = (
        "Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,"
        "Open,High,Low,Close,Volume,OI"
    )

    def clean(self, source_path: str) -> pl.DataFrame:
        raw = pl.read_csv(
            source_path,
            schema_overrides={
                "Date": pl.Utf8,
                "Time": pl.Utf8,
                "Symbol": pl.Utf8,
                "ExpiryDate": pl.Utf8,
                "StrikePrice": pl.Float64,
                "OptionType": pl.Utf8,
                "Open": pl.Float64,
                "High": pl.Float64,
                "Low": pl.Float64,
                "Close": pl.Float64,
                "Volume": pl.Int64,
                "OI": pl.Int64,
            },
        )

        df = raw.with_columns(
            pl.col("Date").str.strptime(pl.Date, "%Y-%m-%d").alias("trade_date"),
            pl.col("ExpiryDate").str.strptime(pl.Date, "%Y-%m-%d").alias("expiry_date"),
            (
                pl.col("Date").str.strptime(pl.Date, "%Y-%m-%d")
                .cast(pl.Int32)  # days since 1970-01-01
                * 1440
                + pl.col("Time").str.slice(0, 2).cast(pl.Int32) * 60
                + pl.col("Time").str.slice(3, 2).cast(pl.Int32)
                - TS_EPOCH_MINUTES_OFFSET
            ).cast(pl.Int32).alias("ts_min"),
            (pl.col("StrikePrice") * 100).round(0).cast(pl.Int32).alias("strike_x100"),
            pl.when(pl.col("OptionType") == "CE").then(0).otherwise(1)
              .cast(pl.Int8).alias("opt_type"),
            (pl.col("Open") * 100).round(0).cast(pl.Int32).alias("open_x100"),
            (pl.col("High") * 100).round(0).cast(pl.Int32).alias("high_x100"),
            (pl.col("Low") * 100).round(0).cast(pl.Int32).alias("low_x100"),
            (pl.col("Close") * 100).round(0).cast(pl.Int32).alias("close_x100"),
            pl.col("Volume").cast(pl.Int32).alias("volume"),
            pl.col("OI").cast(pl.Int32).alias("oi"),
            pl.col("Symbol").alias("symbol"),
        )

        return df.select([
            "ts_min", "trade_date", "symbol", "expiry_date",
            "strike_x100", "opt_type",
            "open_x100", "high_x100", "low_x100", "close_x100",
            "volume", "oi",
        ])


register_handler("clean_2023", CleanFormat2023Handler)
