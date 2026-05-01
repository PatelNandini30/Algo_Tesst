"""
services/rust_fast_path.py
==========================
Optional Rust-backed scan/cache path for the verified AlgoTest engine.

This module is intentionally defensive:
- If the native extension is unavailable, every public entrypoint falls back.
- It only accelerates the supported option/spot scan path.
- Futures-heavy paths fall back to the existing Python implementation.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

try:
    import pyarrow as pa
    import pyarrow.feather as feather
except Exception:  # pragma: no cover - optional at runtime
    pa = None
    feather = None

_native = None
_native_error = None
_calendar_cache: Dict[int, List[str]] = {}
_loaded_cache_key: Optional[str] = None


def _load_native():
    global _native, _native_error
    if _native is not None or _native_error is not None:
        return _native
    try:
        import algotest_native as native
        _native = native
        return _native
    except Exception as exc:  # pragma: no cover - exercised in local env without rust
        _native_error = exc
        logger.info("[RUST_FAST] native extension unavailable: %s", exc)
        return None


def is_available() -> bool:
    return _load_native() is not None


def clear_cache() -> None:
    global _loaded_cache_key
    native = _load_native()
    _calendar_cache.clear()
    _loaded_cache_key = None
    if native is None:
        return
    try:
        native.clear_cache()
    except Exception as exc:
        logger.warning("[RUST_FAST] clear_cache failed: %s", exc)


def _cache_root() -> Path:
    root = Path(os.getenv("ALGO_RUST_CACHE_DIR", Path(tempfile.gettempdir()) / "algotest_arrow_cache"))
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception as exc:
        fallback = Path(tempfile.gettempdir()) / "algotest_arrow_cache"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning("[RUST_FAST] cache root unavailable at %s, using %s: %s", root, fallback, exc)
        return fallback


def _cache_version() -> str:
    return "arrow-v2"


def _cache_key_for_df(options_df, spot_df, cache_key: Optional[str]) -> str:
    if cache_key:
        return f"{_cache_version()}:{cache_key}"
    parts = []
    try:
        if options_df is not None and not options_df.is_empty():
            parts.append(str(options_df.height))
            parts.append(str(options_df.select(["Date"]).to_series().min()))
            parts.append(str(options_df.select(["Date"]).to_series().max()))
    except Exception:
        pass
    try:
        if spot_df is not None and not spot_df.is_empty():
            parts.append(str(spot_df.height))
            parts.append(str(spot_df.select(["Date"]).to_series().min()))
            parts.append(str(spot_df.select(["Date"]).to_series().max()))
    except Exception:
        pass
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{_cache_version()}:rust-{digest}"


def _write_feather(df, path: Path, columns: List[str]) -> None:
    if df is None or df.is_empty():
        return
    if pa is None or feather is None:
        return
    try:
        table = df.select(columns).to_arrow()
        feather.write_feather(table, path, compression="uncompressed")
    except Exception as exc:
        logger.warning("[RUST_FAST] feather write failed for %s: %s", path, exc)


def build_cache(options_df, spot_df, cache_key: Optional[str] = None) -> bool:
    global _loaded_cache_key
    native = _load_native()
    if native is None:
        return False
    try:
        key = _cache_key_for_df(options_df, spot_df, cache_key)
        if _loaded_cache_key == key:
            logger.info("[RUST_FAST] cache already loaded for %s", key)
            return True
        root = _cache_root() / key
        root.mkdir(parents=True, exist_ok=True)
        options_path = root / "options.feather"
        spot_path = root / "spot.feather"
        if not options_path.exists() or not spot_path.exists():
            _write_feather(options_df, options_path, ["Date", "Symbol", "ExpiryDate", "OptionType", "StrikePrice", "Close"])
            _write_feather(spot_df, spot_path, ["Date", "Symbol", "Close"] if "Symbol" in getattr(spot_df, "columns", []) else ["Date", "Close"])
        native.load_cache(str(options_path), str(spot_path))
        _loaded_cache_key = key
        logger.info("[RUST_FAST] cache loaded from %s", root)
        return True
    except Exception as exc:
        logger.warning("[RUST_FAST] build_cache failed: %s", exc)
        return False


def _calendar_dates(trading_calendar) -> List[str]:
    if trading_calendar is None:
        return []
    cached = _calendar_cache.get(id(trading_calendar))
    if cached is not None:
        return cached
    dates: List[str] = []
    try:
        if isinstance(trading_calendar, pd.DataFrame):
            if "date" in trading_calendar.columns:
                dates = pd.to_datetime(trading_calendar["date"], errors="coerce").dt.strftime("%Y-%m-%d").dropna().tolist()
            elif "Date" in trading_calendar.columns:
                dates = pd.to_datetime(trading_calendar["Date"], errors="coerce").dt.strftime("%Y-%m-%d").dropna().tolist()
        elif isinstance(trading_calendar, (list, tuple)):
            for item in trading_calendar:
                s = _to_date_str(item)
                if s:
                    dates.append(s)
    except Exception:
        dates = []
    _calendar_cache[id(trading_calendar)] = dates
    return dates


def _to_date_str(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s[:10] if len(s) >= 10 else None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return None


def _restore_result_dates(result):
    if isinstance(result, list):
        return [_restore_result_dates(item) for item in result]
    if isinstance(result, dict):
        fixed = dict(result)
        exit_date = fixed.get("exit_date")
        if isinstance(exit_date, str):
            try:
                fixed["exit_date"] = pd.Timestamp(exit_date)
            except Exception:
                pass
        return fixed
    return result


def get_option_price(date, index: str, strike: float, opt_type: str, expiry) -> Optional[float]:
    native = _load_native()
    if native is None:
        return None
    try:
        return native.get_option_price(
            _to_date_str(date),
            str(index).upper(),
            float(strike),
            str(opt_type).upper(),
            _to_date_str(expiry),
        )
    except Exception as exc:
        logger.debug("[RUST_FAST] get_option_price failed: %s", exc)
        return None


def get_spot_price(date, index: str) -> Optional[float]:
    native = _load_native()
    if native is None:
        return None
    try:
        return native.get_spot_price(_to_date_str(date), str(index).upper())
    except Exception as exc:
        logger.debug("[RUST_FAST] get_spot_price failed: %s", exc)
        return None


def get_strikes_for_date(date, index: str, expiry: str, opt_type: str) -> List[tuple]:
    native = _load_native()
    if native is None:
        return []
    try:
        return list(native.get_strikes_for_date(
            _to_date_str(date),
            str(index).upper(),
            _to_date_str(expiry),
            str(opt_type).upper(),
        ))
    except Exception as exc:
        logger.debug("[RUST_FAST] get_strikes_for_date failed: %s", exc)
        return []


def can_use_rust_for_legs(legs_config) -> bool:
    for leg in legs_config or []:
        if str((leg or {}).get("segment", "OPTION")).upper() in ("FUTURE", "FUTURES"):
            return False
    return is_available()


def can_use_rust_for_overall(trade_legs) -> bool:
    for leg in trade_legs or []:
        if str((leg or {}).get("segment", "OPTION")).upper() in ("FUTURE", "FUTURES"):
            return False
    return is_available()


def check_leg_stop_loss_target_rust(
    entry_date,
    exit_date,
    expiry_date,
    entry_spot,
    legs_config,
    index,
    trading_calendar,
    square_off_mode,
    slippage_pct=0.0,
    original_python_fn=None,
):
    native = _load_native()
    if native is None or not can_use_rust_for_legs(legs_config):
        return original_python_fn(
            entry_date,
            exit_date,
            expiry_date,
            entry_spot,
            legs_config,
            index,
            trading_calendar,
            square_off_mode,
            slippage_pct=slippage_pct,
        )
    try:
        result = native.check_leg_stop_loss_target(
            _to_date_str(entry_date),
            _to_date_str(exit_date),
            _to_date_str(expiry_date),
            float(entry_spot) if entry_spot is not None else 0.0,
            legs_config,
            str(index).upper(),
            _calendar_dates(trading_calendar),
            str(square_off_mode or "complete"),
            float(slippage_pct or 0.0),
        )
        return _restore_result_dates(result)
    except Exception as exc:
        logger.warning("[RUST_FAST] check_leg_stop_loss_target fallback: %s", exc)
        return original_python_fn(
            entry_date,
            exit_date,
            expiry_date,
            entry_spot,
            legs_config,
            index,
            trading_calendar,
            square_off_mode,
            slippage_pct=slippage_pct,
        )


def check_overall_stop_loss_target_rust(
    entry_date,
    exit_date,
    expiry_date,
    trade_legs,
    index,
    trading_calendar,
    sl_threshold_rs,
    tgt_threshold_rs,
    per_leg_results=None,
    overall_sl_type=None,
    overall_target_type=None,
    slippage_pct=0.0,
    original_python_fn=None,
):
    native = _load_native()
    if native is None or not can_use_rust_for_overall(trade_legs):
        return original_python_fn(
            entry_date,
            exit_date,
            expiry_date,
            trade_legs,
            index,
            trading_calendar,
            sl_threshold_rs,
            tgt_threshold_rs,
            per_leg_results=per_leg_results,
            overall_sl_type=overall_sl_type,
            overall_target_type=overall_target_type,
            slippage_pct=slippage_pct,
        )
    try:
        result = native.check_overall_stop_loss_target(
            _to_date_str(entry_date),
            _to_date_str(exit_date),
            _to_date_str(expiry_date),
            trade_legs,
            str(index).upper(),
            _calendar_dates(trading_calendar),
            None if sl_threshold_rs is None else float(sl_threshold_rs),
            None if tgt_threshold_rs is None else float(tgt_threshold_rs),
            per_leg_results,
            overall_sl_type,
            overall_target_type,
            float(slippage_pct or 0.0),
        )
        return _restore_result_dates(result)
    except Exception as exc:
        logger.warning("[RUST_FAST] check_overall_stop_loss_target fallback: %s", exc)
        return original_python_fn(
            entry_date,
            exit_date,
            expiry_date,
            trade_legs,
            index,
            trading_calendar,
            sl_threshold_rs,
            tgt_threshold_rs,
            per_leg_results=per_leg_results,
            overall_sl_type=overall_sl_type,
            overall_target_type=overall_target_type,
            slippage_pct=slippage_pct,
        )
