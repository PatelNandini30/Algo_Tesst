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
# Tracks the directory that holds the most recently loaded feather pair.
# Set by build_cache; read by get_loaded_feather_root() so the optimizer
# parent can hand the path to child workers (skip DB reload entirely).
_loaded_feather_root: Optional[str] = None


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


def _safe_cast_date_expr(col_name: str, dtype):
    """Return a Polars expression that casts a column to pl.Date regardless of source dtype."""
    import polars as pl
    # String columns may contain datetime representations like "2021-11-11 00:00:00.000000000"
    # which cast(pl.Date) rejects. Slice the ISO-date prefix and parse instead.
    if dtype in (pl.Utf8, pl.String):
        return pl.col(col_name).str.slice(0, 10).str.to_date(strict=False)
    return pl.col(col_name).cast(pl.Date)


def _write_feather(df, path: Path, columns: List[str]) -> None:
    if df is None or df.is_empty():
        return
    if pa is None or feather is None:
        return
    try:
        import polars as pl
        # Cast Datetime/string columns to pl.Date (Arrow Date32) — Rust code only handles Date32
        date_cols = [
            c for c in columns
            if c in df.columns and df[c].dtype != pl.Date
            and (c == "Date" or c.endswith("Date"))
        ]
        if date_cols:
            df = df.with_columns([_safe_cast_date_expr(c, df[c].dtype) for c in date_cols])
        available = [c for c in columns if c in df.columns]
        if set(columns) - set(available):
            logger.warning(
                "[RUST_FAST] feather write: columns missing from df, skipping: %s",
                sorted(set(columns) - set(available)),
            )
        table = df.select(available).to_arrow()
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
        df = df.with_columns([_safe_cast_date_expr(c, df[c].dtype) for c in date_cols])
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
    global _loaded_cache_key, _loaded_cache_signature, _loaded_feather_root
    native = _load_native()
    if native is None:
        return False
    try:
        # Align options DF to spot DF's max date before writing.
        # When Parquet options extend further than DB spot data (common when spot
        # imports lag behind options imports), writing without trimming creates a
        # feather pair where options_max > spot_max. The staleness check in
        # base.py then deletes the feather on every request, forcing a 34-second
        # DB load every time instead of using the feather shortcut. Trimming here
        # ensures the pair is always internally consistent.
        if (options_df is not None and not options_df.is_empty()
                and spot_df is not None and not spot_df.is_empty()):
            try:
                import polars as _pl_trim
                _opt_max = str(options_df["Date"].max())[:10]
                _spt_max = str(spot_df["Date"].max())[:10]
                if _spt_max < _opt_max:
                    logger.debug(
                        "[RUST_FAST] Trimming options DF from %s to spot max %s (spot behind Parquet)",
                        _opt_max, _spt_max,
                    )
                    _spt_max_val = spot_df["Date"].max()
                    options_df = options_df.filter(_pl_trim.col("Date") <= _spt_max_val)
            except Exception as _trim_exc:
                logger.debug("[RUST_FAST] options trim failed (non-fatal): %s", _trim_exc)

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
        # Force regeneration if feather exists but is missing Open/High/Low,
        # but ONLY when the incoming options_df actually has those columns.
        # If the caller's data lacks them (e.g. bulk-loaded from Parquet/DB
        # which only has Close), skip the regen to avoid an infinite delete-rewrite loop.
        if options_path.exists():
            try:
                import polars as _pl_schema
                _hdr = _pl_schema.read_ipc(str(options_path), n_rows=0)
                _required = ("Open", "High", "Low")
                if any(c not in _hdr.columns for c in _required):
                    _df_has_ohlc = (
                        options_df is not None
                        and not options_df.is_empty()
                        and all(c in options_df.columns for c in _required)
                    )
                    if _df_has_ohlc:
                        logger.info("[RUST_FAST] options.feather missing Open/High/Low — forcing regeneration")
                        options_path.unlink()
                        if options_df is None:
                            return False
                    else:
                        logger.debug("[RUST_FAST] options.feather missing Open/High/Low but caller data also lacks them — keeping feather")
                # Contracts column is used by the strike-shift validator to
                # detect zero-turnover (stale-price) strikes. Older feathers
                # built before this column was added need to be regenerated
                # so the toward-ATM shift can fire; without it every strike
                # looks "tradeable" even when it has 0 contracts in Postgres.
                # We delete the stale feather unconditionally: if options_df
                # has Contracts the rewrite below picks it up; otherwise the
                # caller falls back to a DB reload that re-creates the file.
                if options_path.exists() and "Contracts" not in _hdr.columns:
                    logger.info("[RUST_FAST] options.feather missing Contracts — forcing regeneration")
                    options_path.unlink()
                    if options_df is None:
                        return False
                # SettledPrice column is used as MAE/MFE fallback when High and
                # Low are both 0 (illiquid strike with no intraday trades but a
                # published settlement). Older feathers built before this column
                # was added need to be regenerated.
                if options_path.exists() and "SettledPrice" not in _hdr.columns:
                    logger.info("[RUST_FAST] options.feather missing SettledPrice — forcing regeneration")
                    options_path.unlink()
                    if options_df is None:
                        return False
            except Exception as _sc:
                logger.debug("[RUST_FAST] schema check failed: %s", _sc)

        if not options_path.exists() or not spot_path.exists():
            # Contracts is additive — used by the strike-shift validator to skip
            # zero-turnover (stale-price) records.  Older feather files without
            # this column still work: the Rust cache treats absence as "tradeable"
            # for backwards compatibility.
            _write_feather(options_df, options_path, ["Date", "Symbol", "ExpiryDate", "OptionType", "StrikePrice", "Open", "High", "Low", "Close", "Contracts", "SettledPrice"])
            _write_feather(spot_df, spot_path, ["Date", "Symbol", "Close"] if "Symbol" in getattr(spot_df, "columns", []) else ["Date", "Close"])
        # Memory guard: Rust AHashMaps use ~5× the feather file size at runtime
        # (uncompressed IPC + AHashMap key/value storage + alignment overhead).
        # Skip the load if that would leave less than 1 GB for the OS/other processes.
        # RUST_CACHE_MAX_MEMORY_MB caps the cache size independently of available RAM
        # (default 0 = uncapped). BULK_LOAD_MAX_MEMORY_MB is for Python bulk loading only.
        _feather_bytes = options_path.stat().st_size if options_path.exists() else 0
        _estimated_mb = _feather_bytes * 5 / (1024 ** 2)
        _avail_mb = 0
        try:
            with open("/proc/meminfo") as _mf:
                for _line in _mf:
                    if _line.startswith("MemAvailable:"):
                        _avail_mb = int(_line.split()[1]) // 1024
                        break
        except Exception:
            pass  # unknown — proceed without check
        _oom_risk = _avail_mb > 0 and _estimated_mb > (_avail_mb - 1024)
        # Default 2500 MB keeps total worker RSS within the 3200 MB --max-memory-per-child
        # limit (2500 MB cache + ~700 MB Python/Celery overhead). Override via env var.
        _hard_cap_mb = int(os.environ.get("RUST_CACHE_MAX_MEMORY_MB", "2500"))
        _cap_exceeded = _hard_cap_mb > 0 and _estimated_mb > _hard_cap_mb
        if _oom_risk or _cap_exceeded:
            reason = (
                f"OOM risk (need ~{_estimated_mb:.0f} MB, only {_avail_mb - 1024:.0f} MB headroom)"
                if _oom_risk
                else f"exceeds RUST_CACHE_MAX_MEMORY_MB={_hard_cap_mb} MB"
            )
            # Strict Rust-only mode: do NOT skip to Python — the memory-gate
            # (services/memory_gate.py) already ensures only one heavy job is active,
            # and the SSD swap is the backstop, so load anyway rather than degrade.
            _strict_rust = os.environ.get("FAST_LOOKUP_MODE", "auto").strip().lower() == "rust"
            if _strict_rust:
                logger.warning(
                    "[RUST_FAST] FAST_LOOKUP_MODE=rust — loading despite memory guard "
                    "(feather %.0f MB × 5 ≈ %.0f MB: %s). Admission gate + swap are the backstop.",
                    _feather_bytes / (1024 ** 2), _estimated_mb, reason,
                )
            else:
                logger.warning(
                    "[RUST_FAST] Skipping cache load — feather %.0f MB × 5 ≈ %.0f MB: %s. "
                    "Set RUST_CACHE_MAX_MEMORY_MB=0 to uncap or reduce feather size.",
                    _feather_bytes / (1024 ** 2), _estimated_mb, reason,
                )
                return False
        native.load_cache(str(options_path), str(spot_path))
        _loaded_cache_key = key
        _loaded_cache_signature = (_file_signature(options_path), _file_signature(spot_path))
        _loaded_feather_root = str(root)
        logger.info("[RUST_FAST] cache loaded from %s", root)
        return True
    except Exception as exc:
        logger.warning("[RUST_FAST] build_cache failed: %s", exc)
        return False


def get_loaded_feather_root() -> Optional[str]:
    """Return the directory of the most recently loaded feather pair, or None."""
    return _loaded_feather_root


def load_cache_from_root(root: str) -> bool:
    """
    Load the Rust MarketCache from a pre-built feather directory.

    Skips all DB access and Python bulk-load. Used by optimizer workers when
    the parent process has already built the feather — workers just mmap the
    same on-disk files via the OS page cache (no extra disk I/O after the
    first load).
    """
    global _loaded_cache_key, _loaded_cache_signature, _loaded_feather_root
    native = _load_native()
    if native is None:
        return False
    p = Path(root)
    options_path = p / "options.feather"
    spot_path = p / "spot.feather"
    if not options_path.exists() or not spot_path.exists():
        logger.warning("[RUST_FAST] load_cache_from_root: feather missing at %s", root)
        return False
    try:
        native.load_cache(str(options_path), str(spot_path))
        _loaded_cache_key = root
        _loaded_cache_signature = (_file_signature(options_path), _file_signature(spot_path))
        _loaded_feather_root = root
        logger.info("[RUST_FAST] cache loaded from pre-built feather at %s", root)
        return True
    except Exception as exc:
        logger.warning("[RUST_FAST] load_cache_from_root failed: %s", exc)
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


# ─────────────────────────────────────────────────────────────────────────────
# Index OHLC — ADDITIVE wrappers for the Midcap cross-index overlay.
# These hit a separate native INDEX_OHLC cache that is independent of the
# options/spot MarketCache; they never build or touch the heavy backtest cache.
# ─────────────────────────────────────────────────────────────────────────────

def index_ohlc_available() -> bool:
    """True if the native extension exposes the index-OHLC functions."""
    native = _load_native()
    return native is not None and hasattr(native, "load_index_ohlc")


def load_index_ohlc(path) -> bool:
    """Load (merge) a per-symbol index-OHLC feather into the native cache."""
    native = _load_native()
    if native is None or not hasattr(native, "load_index_ohlc"):
        return False
    try:
        native.load_index_ohlc(str(path))
        return True
    except Exception as exc:
        logger.warning("[RUST_FAST] load_index_ohlc failed for %s: %s", path, exc)
        return False


def index_ohlc_is_loaded() -> bool:
    native = _load_native()
    if native is None or not hasattr(native, "index_ohlc_is_loaded"):
        return False
    try:
        return bool(native.index_ohlc_is_loaded())
    except Exception:
        return False


def clear_index_ohlc() -> None:
    native = _load_native()
    if native is None or not hasattr(native, "clear_index_ohlc"):
        return
    try:
        native.clear_index_ohlc()
    except Exception as exc:
        logger.debug("[RUST_FAST] clear_index_ohlc failed: %s", exc)


def get_index_ohlc_close(symbol: str, date) -> Optional[float]:
    native = _load_native()
    if native is None or not hasattr(native, "get_index_ohlc_close"):
        return None
    try:
        return native.get_index_ohlc_close(_to_date_str(date), str(symbol).upper())
    except Exception as exc:
        logger.debug("[RUST_FAST] get_index_ohlc_close failed: %s", exc)
        return None


def get_index_ohlc(symbol: str, date):
    """Return (open, high, low, close) tuple or None."""
    native = _load_native()
    if native is None or not hasattr(native, "get_index_ohlc"):
        return None
    try:
        return native.get_index_ohlc(_to_date_str(date), str(symbol).upper())
    except Exception as exc:
        logger.debug("[RUST_FAST] get_index_ohlc failed: %s", exc)
        return None


def compute_midcap_legs_available() -> bool:
    native = _load_native()
    return native is not None and hasattr(native, "compute_midcap_legs")


def compute_midcap_legs(rows, midcap_legs, spot_adjustment, symbol):
    """Rust-native Midcap overlay math (parity with services.midcap_overlay).
    Returns the parsed result dict, or None if the native path is unavailable."""
    import json
    native = _load_native()
    if native is None or not hasattr(native, "compute_midcap_legs"):
        return None
    try:
        out = native.compute_midcap_legs(
            json.dumps(rows),
            json.dumps(midcap_legs or []),
            json.dumps(spot_adjustment or {}),
            str(symbol).upper(),
        )
        return json.loads(out)
    except Exception as exc:
        logger.warning("[RUST_FAST] compute_midcap_legs failed: %s", exc)
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
