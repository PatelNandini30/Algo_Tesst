#!/usr/bin/env python3
"""
Load a daily index OHLC CSV into the `index_ohlc` table.

NEW, additive feature (Midcap100 cross-index overlay). Touches nothing in the
existing migration path — it only writes the standalone `index_ohlc` table.

Source CSV shape (e.g. strikeData/MIDCAP100.csv):
    Ticker, Date/Time, Open, High, Low, Close
    NIFTYMIDCAP100, 1/1/2003, 1000, 1000, 1000, 1000

Reuses the battle-tested parsing/upsert primitives from migrate_data.py
(read_csv_any, parse_date, parse_num, norm_symbol, _conn, _copy_stage, _upsert)
so date/number handling and COPY-staged upserts behave exactly like the rest of
the importer.

Usage (inside the backend container, where /app is on sys.path):
    python scripts/load_index_ohlc.py
    python scripts/load_index_ohlc.py --csv strikeData/MIDCAP100.csv --symbol NIFTYMIDCAP100
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the backend package importable whether run as `python scripts/load_index_ohlc.py`
# from /app, or directly by path.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import pandas as pd  # noqa: E402

from migrate_data import (  # noqa: E402
    read_csv_any,
    parse_num,
    norm_symbol,
    _col,
    _conn,
    _copy_stage,
    _upsert,
)

logger = logging.getLogger("load_index_ohlc")


def _parse_date_series(s: pd.Series) -> pd.Series:
    """Parse the index CSV's Date/Time column as US month-first (M/D/YYYY).

    DO NOT use migrate_data.parse_date here: it tries %d/%m/%Y BEFORE %m/%d/%Y,
    which silently SWAPS month/day for any date whose day <= 12 — and these index
    CSVs (e.g. MIDCAP100.csv "1/2/2003" = Jan 2) are US month-first. We parse the
    exact format first, then fall back to pandas' month-first inference, then ISO.
    """
    cleaned = s.astype(str).str.strip()
    out = pd.to_datetime(cleaned, format="%m/%d/%Y", errors="coerce")
    bad = out.isna() & cleaned.ne("")
    if bad.any():
        # month-first (dayfirst=False) inference for any stragglers / ISO dates
        out[bad] = pd.to_datetime(cleaned[bad], errors="coerce", dayfirst=False)
    return out.dt.date

TABLE = "index_ohlc"
ALL_COLS = ["symbol", "trade_date", "open_price", "high_price", "low_price", "close_price"]
KEY_COLS = ["symbol", "trade_date"]
COL_TYPES = {
    "symbol": "VARCHAR",
    "trade_date": "DATE",
    "open_price": "NUMERIC",
    "high_price": "NUMERIC",
    "low_price": "NUMERIC",
    "close_price": "NUMERIC",
}


def _date_column(raw: pd.DataFrame) -> pd.Series:
    """Resolve the date column. `_col` handles most aliases, but the Midcap CSV
    header is literally 'Date/Time' (slash) which the normaliser doesn't fold to
    'Date' — so fall back to it explicitly."""
    s = _col(raw, "Date")
    if s is not None and len(s) > 0:
        return s
    for cand in ("Date/Time", "DateTime", "Date Time", "Date"):
        if cand in raw.columns:
            return raw[cand]
    raise ValueError(f"No date column found. Headers: {list(raw.columns)}")


def build_frame(csv_path: Path, symbol_override: str | None) -> pd.DataFrame:
    raw = read_csv_any(csv_path)

    symbol = norm_symbol(_col(raw, "Symbol"))
    if symbol_override:
        symbol = pd.Series([symbol_override.strip().upper()] * len(raw), index=raw.index)

    df = pd.DataFrame(
        {
            "symbol": symbol,
            "trade_date": _parse_date_series(_date_column(raw)),
            "open_price": parse_num(_col(raw, "Open"), "Open"),
            "high_price": parse_num(_col(raw, "High"), "High"),
            "low_price": parse_num(_col(raw, "Low"), "Low"),
            "close_price": parse_num(_col(raw, "Close"), "Close"),
        }
    )

    before = len(df)
    # A row is useless without a symbol, a date, or a close price.
    df = df.dropna(subset=["symbol", "trade_date", "close_price"])
    dropped = before - len(df)
    if dropped:
        logger.warning("Dropped %d rows missing symbol/date/close", dropped)
    return df[ALL_COLS]


def load(csv_path: Path, symbol_override: str | None = None) -> tuple[int, int]:
    df = build_frame(csv_path, symbol_override)
    if df.empty:
        raise SystemExit(f"No usable rows parsed from {csv_path}")

    stage = "stage_index_ohlc"
    conn = _conn()
    try:
        with conn.cursor() as cur:
            _copy_stage(cur, df, stage)
            upd, ins = _upsert(cur, stage, TABLE, KEY_COLS, ALL_COLS, COL_TYPES)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return upd, ins


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Load index OHLC CSV into index_ohlc")
    ap.add_argument("--csv", default="strikeData/MIDCAP100.csv",
                    help="Path to the OHLC CSV (default: strikeData/MIDCAP100.csv)")
    ap.add_argument("--symbol", default=None,
                    help="Override symbol for every row (else taken from the CSV Ticker)")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        # Resolve relative to the repo root (parent of backend/) so it works
        # both from /app and from a host checkout.
        for base in (Path.cwd(), _BACKEND_DIR.parent, _BACKEND_DIR):
            cand = base / args.csv
            if cand.exists():
                csv_path = cand
                break
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    upd, ins = load(csv_path, args.symbol)
    logger.info("index_ohlc load complete: %d inserted, %d updated (from %s)",
                ins, upd, csv_path)


if __name__ == "__main__":
    main()
