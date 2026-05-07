#!/usr/bin/env python3
"""
Ingest NIFTY per-strike CSVs from the Zerodha network-share format.

Network share layout: one file per (expiry, strike, option_type)
  Filename:  NIFTY{DDMMMYY}{STRIKE}{CE|PE}.csv
  Header:    Ticker,Date,Time,Expiry Date,Open,High,Low,Close,Volume,Open Interest,Padding Flag

Pipeline expects per-trading-date files in clean_2023 format:
  Header:    Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,Open,High,Low,Close,Volume,OI

Memory model: files are processed in expiry-date order (oldest expiry first).
Trade dates older than (current_expiry - 7 days) are flushed to disk immediately
after collection. Peak RAM stays bounded to roughly 90 days of option data (~900 MB).

Usage (run from repo root):
    python3 -m backend.scripts.ingest_nifty_per_strike \\
        --source-dir /run/user/1000/gvfs/smb-share:.../NIFTY \\
        --data-root /data/intraday \\
        [--start-date 2024-08-01] [--end-date 2024-12-31] \\
        [--dry-run]

Idempotency: dates already recorded in the SQLite manifest are skipped.
Run one date range at a time — do NOT start two instances simultaneously.
"""
from __future__ import annotations

import gc
import os
import re
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import polars as pl

# ── constants ─────────────────────────────────────────────────────────────────

SYMBOL = "NIFTY"
CLEAN_HEADER = "Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,Open,High,Low,Close,Volume,OI"

_FILE_RE = re.compile(
    r"^(?P<sym>NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY)"
    r"(?P<dd>\d{2})(?P<mon>[A-Z]{3})(?P<yr>\d{2})"
    r"(?P<strike>\d+)"
    r"(?P<opt>CE|PE)\.csv$"
)
_TICKER_RE = re.compile(
    r"^(?P<sym>NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY)"
    r"\d{2}[A-Z]{3}\d{2}"
    r"(?P<strike>\d+)"
    r"(?P<opt>CE|PE)\.NFO$"
)
_MON = {m: i for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"], 1
)}


def _parse_file_expiry(name: str) -> Optional[date]:
    m = _FILE_RE.match(name)
    if not m:
        return None
    mo = _MON.get(m.group("mon"))
    if not mo:
        return None
    return date(int(m.group("yr")) + 2000, mo, int(m.group("dd")))


def _load_sqlite_done(manifest_db: str) -> set[date]:
    if not os.path.exists(manifest_db):
        return set()
    conn = sqlite3.connect(manifest_db)
    try:
        rows = conn.execute(
            "SELECT trade_date FROM imports WHERE symbol = ?", (SYMBOL,)
        ).fetchall()
        return {date.fromisoformat(r[0]) for r in rows}
    except Exception:
        return set()
    finally:
        conn.close()


def _read_one_file(path: str, target_start: str, target_end: str) -> Optional[pl.DataFrame]:
    """Read one per-strike CSV, filter to target date range, return clean_2023 columns."""
    try:
        df = pl.read_csv(
            path,
            has_header=True,
            schema_overrides={
                "Ticker": pl.Utf8,
                "Date": pl.Utf8,
                "Time": pl.Utf8,
                "Expiry Date": pl.Utf8,
                "Open": pl.Float64,
                "High": pl.Float64,
                "Low": pl.Float64,
                "Close": pl.Float64,
                "Volume": pl.Int64,
                "Open Interest": pl.Int64,
                "Padding Flag": pl.Float64,
            },
        )
        if df.is_empty():
            return None

        # Filter to target range before any further processing
        df = df.filter((pl.col("Date") >= target_start) & (pl.col("Date") <= target_end))
        if df.is_empty():
            return None

        # Parse strike + option_type from Ticker (e.g. NIFTY07NOV2424050CE.NFO)
        tickers = df["Ticker"].to_list()
        strikes, opts = [], []
        for t in tickers:
            m = _TICKER_RE.match(t)
            if m:
                strikes.append(float(m.group("strike")))
                opts.append(m.group("opt"))
            else:
                strikes.append(None)
                opts.append(None)

        df = df.with_columns([
            pl.Series("StrikePrice", strikes, dtype=pl.Float64),
            pl.Series("OptionType", opts, dtype=pl.Utf8),
        ]).filter(pl.col("StrikePrice").is_not_null())

        if df.is_empty():
            return None

        return df.with_columns(
            pl.col("Time").str.slice(0, 5),   # HH:MM:SS → HH:MM
            pl.lit(SYMBOL).alias("Symbol"),
        ).rename({
            "Expiry Date": "ExpiryDate",
            "Open Interest": "OI",
        }).select([
            "Date", "Time", "Symbol", "ExpiryDate",
            "StrikePrice", "OptionType",
            "Open", "High", "Low", "Close",
            "Volume", "OI",
        ])
    except Exception as e:
        print(f"  WARN: {Path(path).name}: {e}", file=sys.stderr)
        return None


def _write_and_publish(
    td: date,
    frames: list[pl.DataFrame],
    tmpdir: str,
    data_root: str,
    publish_fn,
    dry_run: bool,
) -> bool:
    """Combine frames for one trade date, write temp CSV, publish. Returns True on success."""
    combined = pl.concat(frames).unique(
        subset=["Time", "ExpiryDate", "StrikePrice", "OptionType"],
        keep="last",
    ).sort(["Time", "ExpiryDate", "StrikePrice", "OptionType"])

    if dry_run:
        print(f"  [dry] {td}  rows={combined.height}")
        return True

    csv_path = os.path.join(tmpdir, f"NIFTY_{td.isoformat()}.csv")
    rows_written = 0
    with open(csv_path, "w") as f:
        f.write(CLEAN_HEADER + "\n")
        for row in combined.iter_rows():
            d, t, sym, exp, strike, opt, o, h, l, c, vol, oi = row
            f.write(f"{d},{t},{sym},{exp},{strike:.2f},{opt},{o},{h},{l},{c},{vol},{oi}\n")
            rows_written += 1

    try:
        publish_fn(
            symbol=SYMBOL,
            trading_date=td,
            source_path=csv_path,
            data_root=data_root,
        )
        print(f"OK  {td}  rows={rows_written}")
        return True
    except Exception as e:
        print(f"ERR {td}: {e}", file=sys.stderr)
        return False
    finally:
        try:
            os.unlink(csv_path)
        except OSError:
            pass


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Ingest NIFTY per-strike CSVs from network share")
    p.add_argument("--source-dir", required=True)
    p.add_argument("--data-root", default="/data/intraday")
    p.add_argument("--start-date", default="2024-08-01")
    p.add_argument("--end-date", help="YYYY-MM-DD (default: yesterday)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Re-ingest dates already in the SQLite manifest")
    p.add_argument("--name-list", metavar="FILE",
                   help="Text file with one filename per line (workaround for GVFS os.listdir bug)")
    p.add_argument("--flush-lag", type=int, default=150, metavar="DAYS",
                   help="Flush completed trade dates when expiry advances past date+DAYS (default 150)")
    args = p.parse_args(argv)

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        print(f"ERROR: source-dir not found: {source_dir}", file=sys.stderr)
        return 1

    target_start = date.fromisoformat(args.start_date)
    target_end = date.fromisoformat(args.end_date) if args.end_date \
        else date.today() - timedelta(days=1)
    ts_str, te_str = target_start.isoformat(), target_end.isoformat()

    manifest_db = os.path.join(args.data_root, "_manifest.db")
    done_dates = set() if args.force else _load_sqlite_done(manifest_db)
    print(f"Manifest: {len(done_dates)} dates already ingested{' (force mode: ignoring)' if args.force else ''}")

    # Build list of (expiry_date, filepath) sorted by expiry asc.
    # Files with earlier expiry have earlier trade dates → process oldest first.
    expiry_min = target_start - timedelta(days=7)
    expiry_max = target_end + timedelta(days=120)

    file_list: list[tuple[date, str]] = []
    if args.name_list:
        with open(args.name_list) as f:
            names = [l.strip() for l in f if l.strip()]
    else:
        names = os.listdir(source_dir)
    for name in names:
        exp = _parse_file_expiry(name)
        if exp and expiry_min <= exp <= expiry_max:
            file_list.append((exp, str(source_dir / name)))
    file_list.sort()   # oldest expiry first

    print(f"Source: {len(file_list)} files for {target_start} → {target_end}")
    if not file_list:
        print("No relevant files found.")
        return 0

    if not args.dry_run:
        # backend/ modules use bare `import database` (Docker sets WORKDIR=/backend).
        # Add backend/ to sys.path so the same imports work when running from repo root.
        _backend_dir = str(Path(__file__).resolve().parent.parent)
        if _backend_dir not in sys.path:
            sys.path.insert(0, _backend_dir)
        try:
            from services import intraday_publish  # type: ignore
            publish_fn = intraday_publish.publish
        except Exception as e:
            print(f"ERROR: could not import intraday_publish: {e}", file=sys.stderr)
            return 1
    else:
        publish_fn = None

    # Streaming accumulator: group rows by trade date.
    # A trade date D is safe to flush once all files with expiry <= D+FLUSH_LAG
    # have been processed. NIFTY options expire within 90 days of listing, so
    # 150 days is safe and keeps peak RAM bounded to ~150 days of data (~2-3 GB).
    FLUSH_LAG = timedelta(days=args.flush_lag)

    by_date: dict[date, list[pl.DataFrame]] = defaultdict(list)
    ok = fail = skip = 0
    total = len(file_list)
    processed = 0
    current_expiry_group = file_list[0][0]

    with tempfile.TemporaryDirectory(prefix="nifty_ingest_") as tmpdir:

        def flush_ready(up_to: date):
            nonlocal ok, fail, skip
            flush_dates = sorted(d for d in by_date if d < up_to)
            for td in flush_dates:
                frames = by_date.pop(td)
                if td in done_dates:
                    skip += 1
                    continue
                success = _write_and_publish(
                    td, frames, tmpdir, args.data_root, publish_fn, args.dry_run
                )
                if success:
                    ok += 1
                else:
                    fail += 1
            gc.collect()

        for exp, path in file_list:
            processed += 1
            if processed % 500 == 0:
                print(f"  [{processed}/{total}] expiry={exp}  "
                      f"buffered_dates={len(by_date)}  ok={ok}")

            # When expiry group advances, flush dates that are safely complete
            if exp > current_expiry_group:
                flush_ready(current_expiry_group - FLUSH_LAG)
                current_expiry_group = exp

            df = _read_one_file(path, ts_str, te_str)
            if df is not None and not df.is_empty():
                for td_str in df["Date"].unique().to_list():
                    td = date.fromisoformat(td_str)
                    by_date[td].append(df.filter(pl.col("Date") == td_str))

        # Flush all remaining dates
        flush_ready(target_end + timedelta(days=1))

    print(f"\nDone. ok={ok}  failed={fail}  skipped(already done)={skip}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
