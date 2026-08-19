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
import time
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


# Per-process memo for _db_signature. The fingerprint is a STALENESS key: it can
# only change when futures rows are imported, so recomputing it per combo is pure
# waste — and expensive waste. build_futures_feather() is called from
# engine_rust._fut_expiry_index PER COMBO in every fork worker, and the query is a
# DISTINCT + string_agg + md5 aggregate over the FUTIDX slice of option_data that
# needs a ~25 MB Postgres dynamic-shared-memory segment. Twelve fork workers running
# it at once exhausted the postgres container's 512 MB /dev/shm and failed 13 of 24
# combos with "psycopg2.errors.DiskFull: could not resize shared memory segment"
# (observed 2026-08-14, with 178 GB free on disk — it was never a disk problem).
#
# Keyed by the shared data_version that the import path already bumps
# (backtest_cache.bump_data_version), so a real import still invalidates it. Falls
# back to a short TTL when Redis is unreachable, rather than caching forever.
_SIG_MEMO: Dict[str, Tuple[float, str, Tuple[int, str, str]]] = {}
_SIG_TTL_SEC = 300.0


def _data_version_safe() -> str:
    try:
        from services.backtest_cache import get_data_version
        return str(get_data_version() or "")
    except Exception:
        return ""


def _sig_redis_get(key: str, dv: str):
    """Shared (cross-process) signature cache. The per-process memo alone does not
    help a fork pool: all 12 children miss simultaneously on their first combo and
    fire 12 concurrent DISTINCT+string_agg+md5 aggregates, each needing a ~25 MB
    Postgres DSM segment — that thundering herd is what exhausted the 512 MB
    /dev/shm and made ensure_futures_loaded fail (silently yielding trades=0)."""
    try:
        from services.backtest_cache import _dv_client as _get_client  # type: ignore
        c = _get_client()
        if c is None:
            return None
        raw = c.get(f"algotest:futsig:{dv}:{key}")
        if not raw:
            return None
        n, mx, fp = json.loads(raw)
        return (int(n), str(mx), str(fp))
    except Exception:
        return None


def _sig_redis_set(key: str, dv: str, sig) -> None:
    try:
        from services.backtest_cache import _dv_client as _get_client  # type: ignore
        c = _get_client()
        if c is not None:
            c.setex(f"algotest:futsig:{dv}:{key}", 86400, json.dumps(list(sig)))
    except Exception:
        pass


def _db_signature(symbol: str) -> Tuple[int, str, str]:
    key = symbol.upper()
    dv = _data_version_safe()
    hit = _SIG_MEMO.get(key)
    if hit is not None:
        ts, hit_dv, sig = hit
        if hit_dv == dv and (dv or (time.time() - ts) < _SIG_TTL_SEC):
            return sig
    shared = _sig_redis_get(key, dv)
    if shared is not None:
        _SIG_MEMO[key] = (time.time(), dv, shared)
        return shared
    sig = _db_signature_uncached(key)
    _SIG_MEMO[key] = (time.time(), dv, sig)
    _sig_redis_set(key, dv, sig)
    return sig


def _db_signature_uncached(symbol: str) -> Tuple[int, str, str]:
    """(row_count, max_date, expiry_fingerprint) for the symbol's FUTIDX rows — cheap
    staleness key. expiry_fingerprint is an md5 of the DISTINCT expiry_date set, so a
    pure LABEL correction (e.g. an NSE expiry reschedule that renames/merges contracts
    without changing the row count or the max trading date) still invalidates the cache
    and forces a rebuild. Without it, relabels are invisible to (count, max_date)."""
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT count(*) AS n, COALESCE(max(date)::text, '') AS mx, "
                "COALESCE(md5(string_agg(DISTINCT expiry_date::text, ',' "
                "ORDER BY expiry_date::text)), '') AS fp "
                "FROM option_data WHERE symbol = :s AND instrument LIKE 'FUT%'"
            ),
            {"s": symbol.upper()},
        ).fetchone()
    return (
        int(row[0]) if row else 0,
        (row[1] if row else "") or "",
        (row[2] if row else "") or "",
    )


def _futures_expiry_relabel_map(rows) -> dict:
    """Detect NSE expiry-revision relabels in FUTIDX data.

    When NSE re-dates a live contract mid-life (e.g. MIDCPNIFTY moved the Aug-2023
    monthly from 30-Aug to 28-Aug effective 17-Aug-2023), the SAME physical contract
    appears under TWO expiry labels: an 'orphan' whose data stops well before its
    declared expiry, immediately continued the next trading day by another contract
    with a nearby expiry. The engine otherwise treats them as separate contracts, so
    a trade held across the switch can't price the old label past the cut-over
    (exit falls back to entry → ~0 P&L) and shows the stale expiry.

    Returns {old_exp: final_exp} so the earlier rows can be relabeled to the
    contract's FINAL (revised) expiry, unifying one continuous price series. Only
    revisions of >=2 days are mapped — a ±1-day holiday shift is already handled by
    _fut_price's ±1 lookup, so it's left untouched (no change to existing labels).
    """
    import datetime as _dt, bisect as _bi
    def _D(v):
        try:
            return _dt.date.fromisoformat(str(v)[:10])
        except Exception:
            return None
    by: dict = {}
    alld = set()
    for r in rows:
        d0 = str(r[0])[:10]
        ex = str(r[1])[:10]
        alld.add(d0)
        f = by.get(ex)
        if f is None:
            by[ex] = [d0, d0]
        else:
            if d0 < f[0]:
                f[0] = d0
            if d0 > f[1]:
                f[1] = d0
    if not by:
        return {}
    sdays = sorted(alld)
    omax = sdays[-1]
    def _nxt(d):
        i = _bi.bisect_right(sdays, d)
        return sdays[i] if i < len(sdays) else None
    BUF, NEAR, SGAP = 4, 10, 5
    raw: dict = {}
    for ex, (fd, ld) in by.items():
        de, dl = _D(ex), _D(ld)
        if de is None or dl is None:
            continue
        if (de - dl).days <= BUF:      # data ends at/near its own expiry -> normal
            continue
        if ld >= omax:                 # current (still-trading) contract, not orphaned
            continue
        nx = _nxt(ld)
        if nx is None:
            continue
        best = None
        for e2, (f2, l2) in by.items():
            if e2 == ex:
                continue
            d2f, d2e = _D(f2), _D(e2)
            if d2f is None or d2e is None:
                continue
            if not (0 < (d2f - dl).days <= SGAP):   # must start right after the orphan
                continue
            diff = abs((d2e - de).days)
            if diff > NEAR:                          # too far to be the same slot
                continue
            # Pick the NEAREST-expiry continuation (that IS the same contract).
            cand = (diff, abs((d2f - _D(nx)).days), e2)
            if best is None or cand < best:
                best = cand
        # Relabel only when the true (nearest) continuation is a >=2-day revision.
        # A ±1-day holiday shift is left to _fut_price's ±1 tolerance — unchanged.
        if best and best[0] >= 2:
            raw[ex] = best[2]
    # Resolve chains (a contract could be relabeled more than once) to the FINAL expiry.
    fin: dict = {}
    for ex in raw:
        cur = ex
        seen = set()
        while cur in raw and cur not in seen:
            seen.add(cur)
            cur = raw[cur]
        fin[ex] = cur
    return fin


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
            # schema key forces a rebuild when we add columns (e.g. High/Low for MAE/MFE).
            # expiry_fp (sig[2]) forces a rebuild on a pure LABEL change (e.g. an NSE
            # expiry reschedule relabel) that leaves row-count and max-date untouched.
            if (int(cached.get("rows", -1)), str(cached.get("max_date", "")),
                    str(cached.get("expiry_fp", "")), str(cached.get("schema", ""))) == (
                    sig[0], sig[1], sig[2], "ohlc-v3"):
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
                "SELECT date, expiry_date, close, high, low FROM option_data "
                "WHERE symbol = :s AND instrument LIKE 'FUT%' "
                "ORDER BY date, expiry_date"
            ),
            {"s": symbol},
        ).fetchall()

    if not rows:
        return None

    # Unify NSE expiry-revision relabels: relabel an 'orphaned' contract's rows to
    # its FINAL (revised) expiry so a hold across the switch prices on one series
    # and shows the correct expiry (e.g. MIDCPNIFTY 2023-08-30 -> 2023-08-28).
    import datetime as _dtmod
    _relabel = _futures_expiry_relabel_map(rows)
    if _relabel:
        logger.info("[FUTURES] %s NSE expiry-revision relabels applied: %s", symbol, _relabel)
    def _norm_exp(v):
        m = _relabel.get(str(v)[:10])
        if m:
            try:
                return _dtmod.date.fromisoformat(m)
            except Exception:
                return v
        return v

    # High/Low are additive columns for feather-batch futures MAE/MFE; the native
    # loader reads only Date/Symbol/ExpiryDate/Close by name, so it ignores them.
    df = pl.DataFrame(
        {
            "Date": [r[0] for r in rows],
            "Symbol": [symbol] * len(rows),
            "ExpiryDate": [_norm_exp(r[1]) for r in rows],
            "Close": [float(r[2]) if r[2] is not None else None for r in rows],
            "High": [float(r[3]) if r[3] is not None else None for r in rows],
            "Low": [float(r[4]) if r[4] is not None else None for r in rows],
        }
    )

    _rf._write_feather(df, path, ["Date", "Symbol", "ExpiryDate", "Close", "High", "Low"])
    try:
        meta.write_text(json.dumps({"rows": sig[0], "max_date": sig[1], "expiry_fp": sig[2], "schema": "ohlc-v3"}))
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

    # NOT an early-return on `_loaded.get(symbol)` truthiness alone — that used
    # to return True the moment a symbol had been loaded ONCE in this process,
    # skipping the DB signature check below entirely (unreachable dead code) for
    # the rest of the worker's life. A worker recycles only every
    # --max-tasks-per-child (10-25) tasks, so a market-data re-import between
    # then and the next NIFTY job priced futures legs off stale closes with no
    # error — same row count/columns, only the VALUES wrong. Every call now
    # always fetches the current signature and compares it.
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
