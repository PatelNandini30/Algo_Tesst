"""Atomic publish: CSV → Parquet + DaySnapshot + manifest row."""
import hashlib
import os
from datetime import date
import polars as pl

from backend.services import (
    intraday_paths,
    intraday_parquet_writer,
    intraday_spot_writer,
    intraday_expiry_dim,
    intraday_manifest,
)
from backend.services.intraday_ingest.base import detect_format
from backend.services.intraday_ingest import validation
from backend.services.intraday_snapshot.builder import build_day_snapshot
from backend.services.intraday_paths import _normalize_symbol  # type: ignore

# Strike steps in x100 units
_STEP_X100 = {"NIFTY": 5000, "BANKNIFTY": 10000, "FINNIFTY": 5000, "MIDCPNIFTY": 2500}


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(
    *,
    symbol: str,
    trading_date: date,
    source_path: str,
    data_root: str,
    spot_source_path: str = None,
    source_format_name: str = "clean_2023",
) -> None:
    symbol = _normalize_symbol(symbol)
    sha = _sha256_file(source_path)

    # Idempotency check
    existing = intraday_manifest.get(symbol, trading_date)
    if existing and existing["source_sha256"] == sha:
        return  # no-op

    # 1. Detect format + clean
    with open(source_path, "r") as f:
        handler = detect_format(f)
    cleaned = handler.clean(source_path)

    # 2. Validate
    validation.validate(cleaned, trade_date=trading_date, symbol=symbol)

    # 3. Update expiry dim
    dim_path = intraday_paths.expiry_dim_path(data_root, symbol)
    dim = intraday_expiry_dim.load(dim_path)
    expiries = sorted(set(cleaned["expiry_date"].to_list()))
    dim, dirty = intraday_expiry_dim.assign(dim, expiries)
    if dirty:
        intraday_expiry_dim.save(dim_path, dim)

    # 4. Write monthly Parquet
    parquet_path = intraday_paths.options_parquet_path(data_root, symbol, trading_date)
    intraday_parquet_writer.write(
        df=cleaned, output_path=parquet_path, expiry_dim=dim,
    )

    # 5. Build & write spot Parquet
    spot_df = _synthesize_spot_if_missing(cleaned, spot_source_path)
    spot_path = intraday_paths.spot_parquet_path(data_root, symbol, trading_date.year)
    intraday_spot_writer.write(df=spot_df, output_path=spot_path)

    # 6. Build & write DaySnapshot
    cleaned_with_idx = cleaned.with_columns(
        pl.col("expiry_date").replace_strict(dim).cast(pl.Int16).alias("expiry_idx")
    )
    snap_bytes = build_day_snapshot(
        symbol=symbol,
        trade_date=trading_date,
        options_df=cleaned_with_idx,
        spot_df=spot_df,
        strike_step_x100=_STEP_X100[symbol],
    )
    snap_path = intraday_paths.snapshot_path(data_root, symbol, trading_date)
    os.makedirs(os.path.dirname(snap_path), exist_ok=True)
    tmp = snap_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(snap_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, snap_path)

    # 7. Manifest upsert (last — it's the commit point)
    intraday_manifest.upsert(
        symbol=symbol,
        trading_date=trading_date,
        source_format=source_format_name,
        source_sha256=sha,
        parquet_path=parquet_path,
        snapshot_path=snap_path,
        row_count=cleaned.height,
        expiry_count=len(expiries),
    )


def publish_intraday_csv(
    symbol: str,
    csv_path: str,
    *,
    format_hint: str = "clean_2023",
    data_root: str | None = None,
) -> None:
    """Derive trading date from CSV filename (YYYY-MM-DD.csv) then call publish()."""
    import re
    if data_root is None:
        data_root = os.environ.get("INTRADAY_DATA_DIR", "/data/intraday")
    stem = re.sub(r"\.csv$", "", os.path.basename(csv_path), flags=re.IGNORECASE)
    trading_date = date.fromisoformat(stem)
    publish(
        symbol=symbol,
        trading_date=trading_date,
        source_path=csv_path,
        data_root=data_root,
        source_format_name=format_hint,
    )


def _synthesize_spot_if_missing(cleaned: pl.DataFrame, spot_source_path: str = None) -> pl.DataFrame:
    if spot_source_path:
        raise NotImplementedError("real spot ingest in Plan E")
    # MVP: derive a spot proxy from the median strike's CE close per minute
    medianish = cleaned.group_by("ts_min").agg(
        (pl.col("strike_x100").median().cast(pl.Int32)).alias("close_x100")
    ).sort(by="ts_min")
    return medianish.with_columns([
        pl.col("close_x100").alias("open_x100"),
        pl.col("close_x100").alias("high_x100"),
        pl.col("close_x100").alias("low_x100"),
        pl.lit(0).cast(pl.Int64).alias("volume"),
    ]).select([
        "ts_min", "open_x100", "high_x100", "low_x100", "close_x100", "volume"
    ])
