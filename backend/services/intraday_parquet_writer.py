"""Write a sorted, ZSTD-compressed Parquet file matching the intraday options schema."""
import os
from datetime import date
from typing import Dict
import polars as pl

CANONICAL_COLUMNS = (
    "ts_min", "expiry_idx", "strike_x100", "opt_type",
    "open_x100", "high_x100", "low_x100", "close_x100",
    "volume", "oi",
)
SORT_KEYS = ("expiry_idx", "opt_type", "strike_x100", "ts_min")


def write(*, df: pl.DataFrame, output_path: str, expiry_dim: Dict[date, int]) -> None:
    """df must have columns including expiry_date and the canonical metric columns.
    expiry_idx is computed from expiry_dim. Output written atomically."""
    mapped = df.with_columns(
        pl.col("expiry_date").replace_strict(expiry_dim).cast(pl.Int16).alias("expiry_idx")
    ).select(list(CANONICAL_COLUMNS))

    sorted_df = mapped.sort(by=list(SORT_KEYS))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp = output_path + ".tmp"
    sorted_df.write_parquet(
        tmp,
        compression="zstd",
        compression_level=6,
        row_group_size=128 * 1024 * 1024 // 40,  # ~128 MB target
        statistics=True,
        use_pyarrow=True,
    )
    os.replace(tmp, output_path)
