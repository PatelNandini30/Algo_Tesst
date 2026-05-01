"""
services/fast_lookup.py
=======================
Zero-copy, O(1) in-memory lookup layer built ONCE from the bulk Polars DataFrame.

Problem: get_bulk_option_price() scans 700k+ rows per call via Polars .filter().
Fix: convert to Python dict once at backtest start. Every lookup = O(1).

IMPORTANT: No calculation logic is changed. Only HOW data is fetched.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)

_opt_lookup: Dict[Tuple[str, str, int, str, str], float] = {}
_spot_lookup: Dict[Tuple[str, str], float] = {}
_strikes_index: Dict[Tuple[str, str, str, str], list] = {}
_is_built: bool = False
_build_symbol: Optional[str] = None
_loaded_cache_key: Optional[str] = None


def _cache_version() -> str:
    return "arrow-v2"


def _native_ready() -> bool:
    try:
        from services import rust_fast_path as _rf
        return _rf.is_available()
    except Exception:
        return False


def build_fast_lookup(options_df, spot_df, cache_key_override: Optional[str] = None) -> None:
    """
    Convert bulk Polars DataFrames into Python dicts for O(1) lookups.
    Called once per backtest, before the engine loop.
    """
    global _opt_lookup, _spot_lookup, _strikes_index, _is_built, _build_symbol, _loaded_cache_key
    t0 = time.perf_counter()
    _opt_lookup = {}
    _spot_lookup = {}
    _strikes_index = {}
    _build_symbol = None

    try:
        from services.rust_fast_path import build_cache as _build_rust_cache
        try:
            from services import data_loader as _data_loader
            cache_key = cache_key_override or getattr(_data_loader, "_bulk_loaded_key", None)
        except Exception:
            cache_key = cache_key_override
        if _build_rust_cache(options_df, spot_df, cache_key=cache_key):
            _loaded_cache_key = f"{_cache_version()}:{cache_key}" if cache_key else None
            _is_built = True
            elapsed = time.perf_counter() - t0
            logger.info(
                "[FAST_LOOKUP] Native cache ready for %s in %.2fs",
                cache_key or "derived-cache",
                elapsed,
            )
            return
    except Exception as exc:
        logger.debug("[FAST_LOOKUP] Rust cache build skipped: %s", exc)

    # Fallback path only for environments without the native extension.
    n_opt = 0
    if options_df is not None and not options_df.is_empty():
        try:
            rows = options_df.select([
                "Date", "Symbol", "ExpiryDate", "OptionType", "StrikePrice", "Close"
            ]).to_dicts()
            for row in rows:
                date_s = _to_date_str(row["Date"])
                expiry_s = _to_date_str(row["ExpiryDate"])
                if date_s is None or expiry_s is None:
                    continue
                symbol_v = str(row["Symbol"]).upper()
                if _build_symbol is None and symbol_v:
                    _build_symbol = symbol_v
                opt_type = str(row["OptionType"]).upper()
                strike_key = _strike_to_key(row["StrikePrice"])
                if strike_key is None:
                    continue
                try:
                    close_f = float(row["Close"])
                except (TypeError, ValueError):
                    continue
                _opt_lookup[(date_s, symbol_v, strike_key, opt_type, expiry_s)] = close_f
                idx_key = (date_s, symbol_v, expiry_s, opt_type)
                if idx_key not in _strikes_index:
                    _strikes_index[idx_key] = []
                _strikes_index[idx_key].append((float(row["StrikePrice"]), close_f))
                n_opt += 1
            for k in _strikes_index:
                _strikes_index[k].sort(key=lambda x: x[0])
        except Exception as exc:
            logger.error("[FAST_LOOKUP] Options build failed: %s", exc)

    n_spot = 0
    if spot_df is not None and not spot_df.is_empty():
        try:
            has_symbol_col = "Symbol" in spot_df.columns
            rows = spot_df.select(
                ["Date", "Symbol", "Close"] if has_symbol_col else ["Date", "Close"]
            ).to_dicts()
            for row in rows:
                date_s = _to_date_str(row["Date"])
                if date_s is None:
                    continue
                symbol_v = str(row.get("Symbol", _build_symbol or "")).upper()
                if _build_symbol is None and symbol_v:
                    _build_symbol = symbol_v
                try:
                    close_f = float(row["Close"])
                except (TypeError, ValueError):
                    continue
                _spot_lookup[(date_s, symbol_v)] = close_f
                n_spot += 1
        except Exception as exc:
            logger.error("[FAST_LOOKUP] Spot build failed: %s", exc)

    _is_built = True
    elapsed = time.perf_counter() - t0
    logger.info(
        "[FAST_LOOKUP] Built fallback: %d option rows -> %d keys, %d spot keys in %.2fs",
        n_opt, len(_opt_lookup), n_spot, elapsed,
    )


def is_built() -> bool:
    if _native_ready():
        return _is_built
    return _is_built and bool(_opt_lookup)


def get_option_price_fast(
    date,
    index: str,
    strike: float,
    opt_type: str,
    expiry,
) -> Optional[float]:
    """O(1) option premium lookup. Returns None on miss — caller falls back to DB."""
    if _native_ready():
        try:
            from services.rust_fast_path import get_option_price as _rust_get_option_price
            result = _rust_get_option_price(date=date, index=index, strike=strike, opt_type=opt_type, expiry=expiry)
            return None if result is None else float(result)
        except Exception:
            pass
    if not _is_built:
        return None
    date_s = _norm_date_str(date)
    expiry_s = _norm_date_str(expiry)
    if date_s is None or expiry_s is None:
        return None
    strike_key = _strike_to_key(strike)
    if strike_key is None:
        return None
    key = (date_s, str(index).upper(), strike_key, str(opt_type).upper(), expiry_s)
    return _opt_lookup.get(key)


def get_spot_price_fast(date, index: str) -> Optional[float]:
    """O(1) spot price lookup. Returns None on miss — caller falls back to DB."""
    if _native_ready():
        try:
            from services.rust_fast_path import get_spot_price as _rust_get_spot_price
            result = _rust_get_spot_price(date=date, index=index)
            return None if result is None else float(result)
        except Exception:
            pass
    if not _is_built:
        return None
    date_s = _norm_date_str(date)
    if date_s is None:
        return None
    return _spot_lookup.get((date_s, str(index).upper()))


def get_strikes_for_date_fast(
    date: str,
    index: str,
    expiry: str,
    opt_type: str,
) -> Optional[list]:
    """Sorted list of (strike, close) for premium-based strike selectors."""
    if _native_ready():
        try:
            from services.rust_fast_path import get_strikes_for_date as _rust_get_strikes_for_date
            result = _rust_get_strikes_for_date(date=date, index=index, expiry=expiry, opt_type=opt_type)
            return list(result) if result else []
        except Exception:
            pass
    if not _is_built:
        return None
    date_s = _norm_date_str(date)
    expiry_s = _norm_date_str(expiry)
    if date_s is None or expiry_s is None:
        return None
    idx_key = (date_s, str(index).upper(), expiry_s, str(opt_type).upper())
    return _strikes_index.get(idx_key)


def clear_fast_lookup(clear_native: bool = True) -> None:
    """Release all memory. Call in finally-block of execute_algotest_job."""
    global _opt_lookup, _spot_lookup, _strikes_index, _is_built, _build_symbol, _loaded_cache_key
    _opt_lookup = {}
    _spot_lookup = {}
    _strikes_index = {}
    _is_built = False
    _build_symbol = None
    if clear_native:
        _loaded_cache_key = None
    logger.info("[FAST_LOOKUP] Cleared")
    if clear_native:
        try:
            from services.rust_fast_path import clear_cache as _clear_rust_cache
            _clear_rust_cache()
        except Exception:
            pass


def _to_date_str(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else None
    try:
        return value.strftime("%Y-%m-%d")
    except AttributeError:
        pass
    try:
        import datetime
        return (datetime.date(1970, 1, 1) + datetime.timedelta(days=int(value))).isoformat()
    except Exception:
        return None


def _norm_date_str(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if len(s) >= 10 and s[4] == '-':
            return s[:10]
        try:
            import pandas as pd
            return pd.Timestamp(s).strftime("%Y-%m-%d")
        except Exception:
            return None
    try:
        return value.strftime("%Y-%m-%d")
    except AttributeError:
        return None


def _strike_to_key(strike) -> Optional[int]:
    """Convert strike to int key x100 to avoid float hash collisions."""
    try:
        return round(float(strike) * 100)
    except (TypeError, ValueError):
        return None
