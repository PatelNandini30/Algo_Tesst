#!/usr/bin/env python3
"""
Rebuild DaySnapshot .arrow files from existing Parquet data.

Use this when snapshots were built with wrong expiry-index mapping.
Reads options + spot Parquet, assigns expiry indices from current
expiries.json, and atomically rewrites each .arrow file.

Usage (from repo root):
    python3 -m backend.scripts.rebuild_snapshots_from_parquet \
        --symbol NIFTY \
        --start-date 2025-01-01 \
        --end-date 2025-12-31 \
        [--data-root /data/intraday] \
        [--dry-run]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl

_backend = str(Path(__file__).resolve().parent.parent)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from services.intraday_snapshot.builder import build_day_snapshot
from services.intraday_ingest.format_clean_2023 import TS_EPOCH_DATE
import services.intraday_expiry_dim as expiry_dim_mod

MINUTES_PER_DAY = 375
SESSION_START_MIN = 555  # 09:15 in minutes-since-midnight


def _read_parquet_normalised(path: str) -> pl.DataFrame:
    """Read one Parquet file and normalise column types across Rust/Python formats."""
    df = pl.read_parquet(path)
    casts = {
        "ts_min":     pl.Int32,
        "opt_type":   pl.Int8,
        "strike_x100": pl.Int32,
        "open_x100":  pl.Int32,
        "high_x100":  pl.Int32,
        "low_x100":   pl.Int32,
        "close_x100": pl.Int32,
        "volume":     pl.Int32,
        "oi":         pl.Int32,
    }
    exprs = [pl.col(c).cast(t) if c in df.columns else None
             for c, t in casts.items()]
    return df.with_columns([e for e in exprs if e is not None])


def _load_all_options(data_root: str, symbol: str) -> pl.DataFrame:
    """Load all options Parquet for the symbol, normalising schemas."""
    opts_dir = Path(data_root) / symbol / "options"
    files = sorted(opts_dir.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No Parquet files under {opts_dir}")
    frames = [_read_parquet_normalised(str(f)) for f in files]
    # Keep only columns needed for snapshot building
    needed = ["trade_date", "ts_min", "expiry_date", "strike_x100",
              "opt_type", "open_x100", "high_x100", "low_x100", "close_x100", "volume"]
    frames = [df.select([c for c in needed if c in df.columns]) for df in frames]
    return pl.concat(frames, how="diagonal_relaxed")


def _load_spot(data_root: str, symbol: str) -> pl.DataFrame:
    """Load all spot Parquet for the symbol, ts_min as minutes-since-midnight."""
    spot_dir = Path(data_root) / symbol / "spot"
    files = sorted(spot_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No spot Parquet files under {spot_dir}")
    return pl.concat([pl.read_parquet(str(f)) for f in files])


def _to_epoch_ts(trade_date: date, df: pl.DataFrame) -> pl.DataFrame:
    """Convert ts_min from minutes-since-midnight to minutes-since-2017-01-01."""
    epoch_base = (trade_date - TS_EPOCH_DATE).days * 1440
    return df.with_columns((pl.col("ts_min") + epoch_base).cast(pl.Int32))


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Rebuild DaySnapshot files from Parquet")
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--data-root", default="/data/intraday")
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    symbol = args.symbol
    data_root = args.data_root
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)

    strike_step_x100 = {"NIFTY": 5000, "BANKNIFTY": 10000,
                        "FINNIFTY": 5000, "MIDCPNIFTY": 2500}[symbol]

    dim_path = str(Path(data_root) / symbol / "expiries.json")
    dim = expiry_dim_mod.load(dim_path)

    print(f"Loading Parquet data for {symbol} ...")
    all_opts = _load_all_options(data_root, symbol)
    all_spot = _load_spot(data_root, symbol)
    print(f"  Options: {all_opts.height} rows | Spot: {all_spot.height} rows")

    snap_dir = Path(data_root) / symbol / "snapshots"
    ok = fail = skip = 0
    dim_dirty = False
    cur = start
    while cur <= end:
        opts_day = all_opts.filter(pl.col("trade_date") == cur)
        spot_day = all_spot.filter(pl.col("trade_date") == cur)

        if opts_day.is_empty():
            cur += timedelta(days=1)
            continue

        # Pick up to 4 nearest active expiries (strictly after trade_date)
        all_exp = sorted(set(opts_day["expiry_date"].to_list()))
        active_exp = [e for e in all_exp if e > cur][:4]
        if not active_exp:
            skip += 1
            cur += timedelta(days=1)
            continue

        # Assign indices; extend dim for any new expiry dates
        for e in active_exp:
            if e not in dim:
                next_idx = max(dim.values(), default=-1) + 1
                dim[e] = next_idx
                dim_dirty = True
                print(f"  new expiry {e} → idx {next_idx}")

        expiry_idx_map = {e: dim[e] for e in active_exp}

        # Filter options to active expiries and add expiry_idx column
        opts_active = opts_day.filter(pl.col("expiry_date").is_in(active_exp))
        opts_active = _to_epoch_ts(cur, opts_active).with_columns(
            pl.col("expiry_date").replace_strict(expiry_idx_map).cast(pl.Int16).alias("expiry_idx")
        )

        # Spot: use synthesized if empty (same as intraday_publish does)
        if spot_day.is_empty():
            spot_epoch = (
                opts_active.group_by("ts_min").agg(
                    pl.col("close_x100").median().cast(pl.Int32)
                ).sort("ts_min")
                .with_columns([
                    pl.col("close_x100").alias("open_x100"),
                    pl.col("close_x100").alias("high_x100"),
                    pl.col("close_x100").alias("low_x100"),
                ])
                .select(["ts_min", "open_x100", "high_x100", "low_x100", "close_x100"])
            )
        else:
            spot_epoch = _to_epoch_ts(cur, spot_day)

        if args.dry_run:
            print(f"[dry] {cur}  expiries={[e.isoformat() for e in active_exp]}")
            ok += 1
            cur += timedelta(days=1)
            continue

        try:
            snap_bytes = build_day_snapshot(
                symbol=symbol,
                trade_date=cur,
                options_df=opts_active,
                spot_df=spot_epoch,
                strike_step_x100=strike_step_x100,
            )
        except Exception as e:
            print(f"ERR {cur}: build failed: {e}", file=sys.stderr)
            fail += 1
            cur += timedelta(days=1)
            continue

        snap_path = snap_dir / f"{cur.isoformat()}.arrow"
        tmp = str(snap_path) + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(snap_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(snap_path))
            print(f"OK  {cur}  expiries={[e.isoformat() for e in active_exp]}  bytes={len(snap_bytes)}")
            ok += 1
        except Exception as e:
            print(f"ERR {cur}: write failed: {e}", file=sys.stderr)
            fail += 1
            try:
                os.unlink(tmp)
            except OSError:
                pass

        cur += timedelta(days=1)

    if dim_dirty and not args.dry_run:
        expiry_dim_mod.save(dim_path, dim)
        print(f"Updated expiries.json with new entries")

    print(f"\nDone. ok={ok}  failed={fail}  skipped(no options data)={skip}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
