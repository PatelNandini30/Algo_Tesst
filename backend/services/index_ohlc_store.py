"""
services/index_ohlc_store.py
============================
Feather store + lazy Rust loader for the `index_ohlc` table (Midcap cross-index
overlay). ADDITIVE: this never touches the options/spot MarketCache or the
existing build_cache/load_cache path.

Flow:
    Postgres index_ohlc  --build_index_ohlc_feather-->  <SYMBOL>.feather (Arrow IPC)
                         --ensure_index_ohlc_loaded-->  native INDEX_OHLC (mmap, O(1))

The dataset is tiny + static (~6k rows/symbol), so we keep one full-history
feather per symbol, rebuilt only when the table's (row_count, max_date) changes.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from sqlalchemy import text

from database import get_engine
from services import rust_fast_path as _rf

logger = logging.getLogger(__name__)

# Process-local memo of which symbols are loaded and with what signature, so we
# don't rebuild/reload on every overlay request.
_lock = threading.Lock()
_loaded: Dict[str, Tuple[int, str]] = {}


def _feather_dir() -> Path:
    d = _rf._cache_root() / "index_ohlc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _feather_path(symbol: str) -> Path:
    return _feather_dir() / f"{symbol.upper()}.feather"


def _meta_path(symbol: str) -> Path:
    return _feather_dir() / f"{symbol.upper()}.meta.json"


def _db_signature(symbol: str) -> Tuple[int, str]:
    """(row_count, max_trade_date) for the symbol — cheap staleness key."""
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT count(*) AS n, COALESCE(max(trade_date)::text, '') AS mx "
                "FROM index_ohlc WHERE symbol = :s"
            ),
            {"s": symbol.upper()},
        ).fetchone()
    return (int(row[0]) if row else 0, (row[1] if row else "") or "")


def build_index_ohlc_feather(symbol: str, *, force: bool = False) -> Optional[Path]:
    """Export one symbol's full history to <SYMBOL>.feather. Rebuilds only when
    the table signature changed (unless force). Returns the path, or None if the
    symbol has no rows."""
    symbol = symbol.upper()
    sig = _db_signature(symbol)
    if sig[0] == 0:
        logger.warning("[INDEX_OHLC] no rows in index_ohlc for symbol=%s", symbol)
        return None

    path = _feather_path(symbol)
    meta = _meta_path(symbol)

    if not force and path.exists() and meta.exists():
        try:
            cached = json.loads(meta.read_text())
            if (int(cached.get("rows", -1)), str(cached.get("max_date", ""))) == sig:
                return path  # up to date
        except Exception:
            pass  # rebuild on any meta issue

    try:
        import polars as pl
    except Exception as exc:  # pragma: no cover
        logger.warning("[INDEX_OHLC] polars unavailable, cannot build feather: %s", exc)
        return None

    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT trade_date, open_price, high_price, low_price, close_price "
                "FROM index_ohlc WHERE symbol = :s ORDER BY trade_date"
            ),
            {"s": symbol},
        ).fetchall()

    if not rows:
        return None

    df = pl.DataFrame(
        {
            "Date": [r[0] for r in rows],
            "Symbol": [symbol] * len(rows),
            "Open": [float(r[1]) if r[1] is not None else None for r in rows],
            "High": [float(r[2]) if r[2] is not None else None for r in rows],
            "Low": [float(r[3]) if r[3] is not None else None for r in rows],
            "Close": [float(r[4]) if r[4] is not None else None for r in rows],
        }
    )

    # _write_feather casts Date → Date32 (what the Rust loader expects).
    _rf._write_feather(df, path, ["Date", "Symbol", "Open", "High", "Low", "Close"])
    try:
        meta.write_text(json.dumps({"rows": sig[0], "max_date": sig[1]}))
    except Exception:
        pass
    logger.info("[INDEX_OHLC] built feather %s (%d rows, max %s)", path, sig[0], sig[1])
    return path


def ensure_index_ohlc_loaded(symbol: str) -> bool:
    """Make sure <symbol>'s OHLC is available to the native lookup. Builds the
    feather if missing/stale and loads it into the Rust INDEX_OHLC cache exactly
    once per (process, signature). Returns True if the native lookup is ready.

    Falls back gracefully (returns False) when the native extension or feather
    machinery is unavailable — callers then use the DB path."""
    symbol = symbol.upper()

    # Fast path: already loaded in this process and Rust cache is warm.
    # Skip the DB staleness query entirely — the data doesn't change mid-run
    # and the DB query fired on every combo causes connection pool exhaustion
    # when called 200+ times from a ProcessPoolExecutor.
    with _lock:
        if _loaded.get(symbol) and _rf.index_ohlc_is_loaded():
            return True

    # Slow path: first call (or Rust cache was cleared) — check DB for staleness.
    sig = _db_signature(symbol)
    if sig[0] == 0:
        return False

    with _lock:
        if _loaded.get(symbol) == sig and _rf.index_ohlc_is_loaded():
            return True

        path = build_index_ohlc_feather(symbol)
        if path is None or not path.exists():
            return False
        if not _rf.load_index_ohlc(path):
            return False
        _loaded[symbol] = sig
        return True
