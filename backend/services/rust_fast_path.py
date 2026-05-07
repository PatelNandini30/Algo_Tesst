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
# (options_path, options_mtime, options_size, spot_path, spot_mtime, spot_size)
# Used to detect when the on-disk feather has been replaced under us, so we can
# reload Rust's in-memory cache instead of reusing stale data.
_loaded_cache_signature: Optional[tuple] = None


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
        import polars as pl
        # Cast Datetime columns to pl.Date (Arrow Date32) — Rust code only handles Date32
        date_cols = [
            c for c in columns
            if c in df.columns and df[c].dtype != pl.Date
            and (c == "Date" or c.endswith("Date"))
        ]
        if date_cols:
            df = df.with_columns([pl.col(c).cast(pl.Date) for c in date_cols])
        table = df.select(columns).to_arrow()
        feather.write_feather(table, path, compression="uncompressed")
    except Exception as exc:
        logger.warning("[RUST_FAST] feather write failed for %s: %s", path, exc)


def _feather_dates_are_valid(path: Path) -> bool:
    """Return False if any date column in the feather file is not Arrow Date32."""
    try:
        import polars as pl
        df = pl.read_ipc(path, n_rows=1)
        for col in df.columns:
            if col == "Date" or col.endswith("Date"):
                if df[col].dtype != pl.Date:
                    return False
        return True
    except Exception:
        return False


def _fix_feather_date_format(path: Path) -> bool:
    """Convert all Datetime columns to Date (Date32) in an existing feather file, in-place."""
    import os
    tmp = Path(str(path) + ".tmp")
    try:
        import polars as pl
        df = pl.read_ipc(path, memory_map=False)
        date_cols = [
            c for c in df.columns
            if (c == "Date" or c.endswith("Date")) and df[c].dtype != pl.Date
        ]
        if not date_cols:
            return True
        df = df.with_columns([pl.col(c).cast(pl.Date) for c in date_cols])
        if feather is not None and pa is not None:
            feather.write_feather(df.to_arrow(), tmp, compression="uncompressed")
            os.replace(tmp, path)
            logger.info("[RUST_FAST] converted %s to Date32 format", path.name)
            return True
        return False
    except Exception as exc:
        logger.warning("[RUST_FAST] _fix_feather_date_format failed for %s: %s", path, exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _file_signature(p: Path) -> tuple:
    try:
        st = p.stat()
        return (str(p), st.st_mtime, st.st_size)
    except OSError:
        return (str(p), 0.0, 0)


def build_cache(options_df, spot_df, cache_key: Optional[str] = None) -> bool:
    global _loaded_cache_key, _loaded_cache_signature
    native = _load_native()
    if native is None:
        return False
    try:
        key = _cache_key_for_df(options_df, spot_df, cache_key)
        root = _cache_root() / key
        root.mkdir(parents=True, exist_ok=True)
        options_path = root / "options.feather"
        spot_path = root / "spot.feather"

        # If DataFrame is provided and the existing feather covers a narrower date
        # range, regenerate so the Rust cache reflects the full loaded dataset.
        # This range check costs ~ms via scan_ipc metadata-only read.
        _need_regen = False
        if (options_df is not None
                and not options_df.is_empty()
                and options_path.exists()):
            try:
                import polars as pl
                existing_dates = pl.scan_ipc(str(options_path)).select(["Date"]).collect()
                ex_min = str(existing_dates["Date"].min())[:10]
                ex_max = str(existing_dates["Date"].max())[:10]
                df_min = str(options_df["Date"].min())[:10]
                df_max = str(options_df["Date"].max())[:10]
                if df_min < ex_min or df_max > ex_max:
                    logger.info(
                        "[RUST_FAST] DataFrame (%s→%s) wider than feather (%s→%s) — regenerating",
                        df_min, df_max, ex_min, ex_max,
                    )
                    _need_regen = True
            except Exception as _ce:
                logger.debug("[RUST_FAST] feather range check error: %s", _ce)

        # Also check if spot.feather covers a narrower range than spot_df.
        # Prevents stale spot data when options were reloaded for a wider range but spot wasn't.
        if (not _need_regen
                and spot_df is not None
                and not spot_df.is_empty()
                and spot_path.exists()):
            try:
                import polars as pl
                ex_spot = pl.scan_ipc(str(spot_path)).select(["Date"]).collect()
                sp_ex_min = str(ex_spot["Date"].min())[:10]
                sp_ex_max = str(ex_spot["Date"].max())[:10]
                sp_df_min = str(spot_df["Date"].min())[:10]
                sp_df_max = str(spot_df["Date"].max())[:10]
                if sp_df_min < sp_ex_min or sp_df_max > sp_ex_max:
                    logger.info(
                        "[RUST_FAST] Spot DF (%s→%s) wider than spot feather (%s→%s) — regenerating",
                        sp_df_min, sp_df_max, sp_ex_min, sp_ex_max,
                    )
                    _need_regen = True
            except Exception as _sp_ce:
                logger.debug("[RUST_FAST] spot feather range check error: %s", _sp_ce)

        if _need_regen:
            options_path.unlink(missing_ok=True)
            spot_path.unlink(missing_ok=True)

        # Compute current file signature; reload Rust if it differs from what we
        # last loaded (catches the case where the feather was regenerated on
        # disk but our in-memory Rust cache still holds the old data).
        current_sig = (_file_signature(options_path), _file_signature(spot_path))
        if _loaded_cache_key == key and _loaded_cache_signature == current_sig:
            logger.info("[RUST_FAST] cache already loaded for %s", key)
            return True

        # Fix feather files that have wrong date format (Datetime[ns] instead of Date32)
        for p in (options_path, spot_path):
            if p.exists() and not _feather_dates_are_valid(p):
                if not _fix_feather_date_format(p):
                    logger.warning("[RUST_FAST] could not fix %s, deleting for regeneration", p.name)
                    p.unlink()
        if not options_path.exists() or not spot_path.exists():
            _write_feather(options_df, options_path, ["Date", "Symbol", "ExpiryDate", "OptionType", "StrikePrice", "Close"])
            _write_feather(spot_df, spot_path, ["Date", "Symbol", "Close"] if "Symbol" in getattr(spot_df, "columns", []) else ["Date", "Close"])
        native.load_cache(str(options_path), str(spot_path))
        _loaded_cache_key = key
        _loaded_cache_signature = (_file_signature(options_path), _file_signature(spot_path))
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
        # Rust returns a dict {exit_date, exit_reason}; engine expects a (date, reason) tuple.
        if isinstance(result, dict):
            exit_date_val = result.get("exit_date")
            exit_reason_val = result.get("exit_reason")
            if isinstance(exit_date_val, str):
                try:
                    exit_date_val = pd.Timestamp(exit_date_val)
                except Exception:
                    pass
            return exit_date_val, exit_reason_val
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
