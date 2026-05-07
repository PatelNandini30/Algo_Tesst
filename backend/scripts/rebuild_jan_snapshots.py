"""Rebuild broken Jan 1-7 2025 NIFTY snapshots from Rust-generated per-date Parquet files.

The re-ingestion run with --force overwrote Jan 1-7 snapshots with the old
Python format (wrong header / spot / chain layout). This script reads the
original Rust-generated per-date Parquet files and produces correct snapshots
using the now-fixed build_day_snapshot().
"""
import json
import os
import sys
from datetime import date, timedelta

import numpy as np
import polars as pl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.intraday_snapshot.builder import build_day_snapshot
from services.intraday_ingest.format_clean_2023 import TS_EPOCH_DATE

DATA_ROOT = os.environ.get("INTRADAY_DATA_DIR", "/data/intraday")
SYMBOL = "NIFTY"
STRIKE_STEP_X100 = 5000  # NIFTY 50-point step × 100

# Dates that were overwritten with broken Python-format snapshots
BROKEN_DATES = [
    date(2025, 1, 1),
    date(2025, 1, 2),
    date(2025, 1, 3),
    date(2025, 1, 6),
    date(2025, 1, 7),
]


def load_expiry_dim(symbol_dir: str) -> dict[date, int]:
    """Return {expiry_date: idx} from expiries.json."""
    with open(os.path.join(symbol_dir, "expiries.json")) as f:
        raw = json.load(f)  # {idx_str: date_str}
    return {date.fromisoformat(v): int(k) for k, v in raw.items()}


def to_epoch_ts(trade_date: date, ts_min_midnight: pl.Expr) -> pl.Expr:
    """Convert minutes-since-midnight column to epoch ts_min (as builder expects)."""
    base = (trade_date - TS_EPOCH_DATE).days * 1440
    return (ts_min_midnight + base).cast(pl.Int32)


def load_options_for_date(trade_date: date, expiry_dim: dict[date, int]) -> pl.DataFrame:
    """Read the Rust per-date Parquet and convert to builder-compatible schema."""
    parquet_path = os.path.join(
        DATA_ROOT, SYMBOL, "options",
        f"year={trade_date.year}", f"month={trade_date.month:02d}",
        f"{trade_date}.parquet",
    )
    df = pl.read_parquet(parquet_path)

    # Map expiry_date → expiry_idx
    date_to_idx = expiry_dim
    df = df.with_columns(
        pl.col("expiry_date").map_elements(
            lambda d: date_to_idx.get(d, -1), return_dtype=pl.Int16
        ).alias("expiry_idx")
    ).filter(pl.col("expiry_idx") >= 0)

    # Convert opt_type: Rust Boolean True=CE→0, False=PE→1
    df = df.with_columns(
        (~pl.col("opt_type")).cast(pl.Int8).alias("opt_type")
    )

    # Convert ts_min: minutes-since-midnight → epoch ts_min
    df = df.with_columns(
        to_epoch_ts(trade_date, pl.col("ts_min")).alias("ts_min")
    )

    return df.select(
        ["ts_min", "expiry_idx", "strike_x100", "opt_type",
         "open_x100", "high_x100", "low_x100", "close_x100", "volume"]
    )


def load_spot_for_date(trade_date: date) -> pl.DataFrame:
    """Read the Rust NIFTY-spot-2025.parquet and return one day in builder format."""
    spot_path = os.path.join(DATA_ROOT, SYMBOL, "spot", f"{SYMBOL}-spot-{trade_date.year}.parquet")
    df = pl.read_parquet(spot_path)
    df = df.filter(pl.col("trade_date") == trade_date)

    df = df.with_columns(
        to_epoch_ts(trade_date, pl.col("ts_min")).alias("ts_min")
    ).select(["ts_min", "open_x100", "high_x100", "low_x100", "close_x100"])

    return df


def rebuild_snapshot(trade_date: date, expiry_dim: dict[date, int]) -> None:
    print(f"  Loading options...", end="", flush=True)
    opts = load_options_for_date(trade_date, expiry_dim)
    expiry_indices = sorted(opts["expiry_idx"].unique().to_list())
    print(f" {opts.height} rows, expiry_idxs={expiry_indices}", flush=True)

    print(f"  Loading spot...", end="", flush=True)
    spot = load_spot_for_date(trade_date)
    print(f" {spot.height} rows", flush=True)

    snap_bytes = build_day_snapshot(
        symbol=SYMBOL,
        trade_date=trade_date,
        options_df=opts,
        spot_df=spot,
        strike_step_x100=STRIKE_STEP_X100,
    )

    snap_path = os.path.join(DATA_ROOT, SYMBOL, "snapshots", f"{trade_date}.arrow")
    tmp_path = snap_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(snap_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, snap_path)
    print(f"  Written {len(snap_bytes):,} bytes → {snap_path}", flush=True)


def verify_snapshot(trade_date: date) -> None:
    """Quick sanity-check: read header of rebuilt snapshot."""
    import struct
    snap_path = os.path.join(DATA_ROOT, SYMBOL, "snapshots", f"{trade_date}.arrow")
    with open(snap_path, "rb") as f:
        hdr = f.read(32)
    magic = hdr[0:4].decode()
    sym = hdr[5:21].rstrip(b"\x00").decode("utf-8", errors="replace")
    days = struct.unpack_from("<i", hdr, 21)[0]
    expiry_count = hdr[25]
    minute_count = struct.unpack_from("<H", hdr, 26)[0]
    snap_date = date(1970, 1, 1) + timedelta(days=days)
    print(f"  VERIFY: magic={magic} sym={sym!r} date={snap_date} "
          f"expiry_count={expiry_count} minute_count={minute_count}")


def main():
    symbol_dir = os.path.join(DATA_ROOT, SYMBOL)
    expiry_dim = load_expiry_dim(symbol_dir)
    print(f"Loaded expiry dim: {len(expiry_dim)} entries")

    for d in BROKEN_DATES:
        print(f"\n=== {d} ===")
        try:
            rebuild_snapshot(d, expiry_dim)
            verify_snapshot(d)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()

    print("\nDone.")


if __name__ == "__main__":
    main()
