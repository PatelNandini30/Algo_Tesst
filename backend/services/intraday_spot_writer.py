"""Write a year of intraday spot data to Parquet."""
import os
import polars as pl

CANONICAL_COLUMNS = (
    "ts_min", "open_x100", "high_x100", "low_x100", "close_x100", "volume",
)


def write(*, df: pl.DataFrame, output_path: str) -> None:
    sorted_df = df.select(list(CANONICAL_COLUMNS)).sort(by="ts_min")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    sorted_df.write_parquet(
        tmp, compression="zstd", compression_level=6, statistics=True, use_pyarrow=True
    )
    os.replace(tmp, output_path)
