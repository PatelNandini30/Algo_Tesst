"""
services/futures_cache_store.py
===============================
Feather store + lazy Rust loader for FUTIDX (futures) rows from `option_data`.
ADDITIVE: never touches the options/spot MarketCache or the existing
build_cache/load_cache path. Mirrors services/index_ohlc_store.py.

Flow:
    Postgres option_data (instrument LIKE 'FUT%')
        --build_futures_feather-->  futures/<SYMBOL>.feather (Arrow IPC: Date,Symbol,ExpiryDate,Close)
        --ensure_futures_loaded-->  native FUTURES cache (O(1) get_future_price)

Futures are tiny + static (a few thousand rows/symbol), so we keep one
full-history feather per symbol, rebuilt only when (row_count, max_date) changes.
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

_lock = threading.Lock()
_loaded: Dict[str, Tuple[int, str]] = {}


def _feather_dir() -> Path:
    d = _rf._cache_root() / "futures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _feather_path(symbol: str) -> Path:
    return _feather_dir() / f"{symbol.upper()}.feather"


def _meta_path(symbol: str) -> Path:
    return _feather_dir() / f"{symbol.upper()}.meta.json"


def _db_signature(symbol: str) -> Tuple[int, str]:
    """(row_count, max_date) for the symbol's FUTIDX rows — cheap staleness key."""
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT count(*) AS n, COALESCE(max(date)::text, '') AS mx "
                "FROM option_data WHERE symbol = :s AND instrument LIKE 'FUT%'"
            ),
            {"s": symbol.upper()},
        ).fetchone()
    return (int(row[0]) if row else 0, (row[1] if row else "") or "")


def build_futures_feather(symbol: str, *, force: bool = False) -> Optional[Path]:
    """Export one symbol's full FUTIDX history to futures/<SYMBOL>.feather.
    Rebuilds only when the table signature changed (unless force). Returns the
    path, or None if the symbol has no futures rows."""
    symbol = symbol.upper()
    sig = _db_signature(symbol)
    if sig[0] == 0:
        logger.warning("[FUTURES] no FUTIDX rows in option_data for symbol=%s", symbol)
        return None

    path = _feather_path(symbol)
    meta = _meta_path(symbol)

    if not force and path.exists() and meta.exists():
        try:
            cached = json.loads(meta.read_text())
            if (int(cached.get("rows", -1)), str(cached.get("max_date", ""))) == sig:
                return path
        except Exception:
            pass

    try:
        import polars as pl
    except Exception as exc:  # pragma: no cover
        logger.warning("[FUTURES] polars unavailable, cannot build feather: %s", exc)
        return None

    eng = get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT date, expiry_date, close FROM option_data "
                "WHERE symbol = :s AND instrument LIKE 'FUT%' "
                "ORDER BY date, expiry_date"
            ),
            {"s": symbol},
        ).fetchall()

    if not rows:
        return None

    df = pl.DataFrame(
        {
            "Date": [r[0] for r in rows],
            "Symbol": [symbol] * len(rows),
            "ExpiryDate": [r[1] for r in rows],
            "Close": [float(r[2]) if r[2] is not None else None for r in rows],
        }
    )

    _rf._write_feather(df, path, ["Date", "Symbol", "ExpiryDate", "Close"])
    try:
        meta.write_text(json.dumps({"rows": sig[0], "max_date": sig[1]}))
    except Exception:
        pass
    logger.info("[FUTURES] built feather %s (%d rows, max %s)", path, sig[0], sig[1])
    return path


def ensure_futures_loaded(symbol: str) -> bool:
    """Ensure <symbol>'s FUTIDX closes are available to native get_future_price.
    Builds the feather if missing/stale and loads it into the Rust FUTURES cache
    once per (process, signature). Returns True if the native lookup is ready.
    Falls back gracefully (False) if the native extension/feather is unavailable."""
    symbol = symbol.upper()

    with _lock:
        if _loaded.get(symbol) and _rf.futures_is_loaded():
            return True

    sig = _db_signature(symbol)
    if sig[0] == 0:
        return False

    with _lock:
        if _loaded.get(symbol) == sig and _rf.futures_is_loaded():
            return True
        path = build_futures_feather(symbol)
        if path is None or not path.exists():
            return False
        if not _rf.load_futures_cache(path):
            return False
        _loaded[symbol] = sig
        return True
