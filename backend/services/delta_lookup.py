"""Actual-delta lookup for delta strike selection (EOD).

Replaces the Black-Scholes delta APPROXIMATION with the real per-(date, expiry,
strike) deltas ingested from the desk's EOD delta history
(ingest_delta_history.py -> /data/cache/delta_history.feather).

Loaded ONCE per process, lazily, and only when a delta strategy actually runs —
an ordinary run never touches this. The feather is ~1M rows; the in-memory index
is a few hundred MB, freed with the worker.

# ponytail: full dict index built on first use (~1M rows). If memory pressure
# shows up, swap to a Polars filter with an LRU cache keyed by (sym,date,expiry).
"""
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FEATHER = os.environ.get("DELTA_HISTORY_FEATHER", "/data/cache/delta_history.feather")

# (symbol, date, expiry) -> {"CE": [(strike, abs_delta), ...], "PE": [...]}
_INDEX: Optional[Dict[Tuple[str, str, str], Dict[str, List[Tuple[float, float]]]]] = None
_LOADED = False


def _load() -> Optional[Dict]:
    global _INDEX, _LOADED
    if _LOADED:
        return _INDEX
    _LOADED = True
    if not os.path.exists(FEATHER):
        logger.warning("[DELTA] history feather not found at %s — delta legs cannot "
                       "resolve to actual deltas.", FEATHER)
        _INDEX = None
        return None
    try:
        import polars as pl
        df = pl.read_ipc(FEATHER)
    except Exception as exc:
        logger.warning("[DELTA] failed to read %s: %s", FEATHER, exc)
        _INDEX = None
        return None
    idx: Dict[Tuple[str, str, str], Dict[str, List[Tuple[float, float]]]] = {}
    for sym, date, exp, strike, ce, pe in df.iter_rows():
        key = (str(sym).upper(), str(date), str(exp))
        slot = idx.get(key)
        if slot is None:
            slot = {"CE": [], "PE": []}
            idx[key] = slot
        if ce is not None:
            slot["CE"].append((float(strike), abs(float(ce))))
        if pe is not None:
            slot["PE"].append((float(strike), abs(float(pe))))
    _INDEX = idx
    logger.info("[DELTA] loaded %d (symbol,date,expiry) groups from %s", len(idx), FEATHER)
    return idx


def candidates_by_delta(symbol: str, date: str, expiry: str, opt_type: str,
                        target: float) -> Optional[List[float]]:
    """Strikes for (symbol, date, expiry, CE/PE) ordered by how close their ACTUAL
    |delta| is to `target` (closest first; ties -> nearer-the-target then lower
    strike). Returns None when no delta data exists for that (date, expiry) — the
    caller then leaves the engine's own pick and flags it.
    """
    idx = _load()
    if not idx:
        return None
    ot = "CE" if str(opt_type).upper() in ("CE", "CALL", "C") else "PE"
    rows = (idx.get((str(symbol).upper(), str(date), str(expiry))) or {}).get(ot)
    if not rows:
        return None
    tgt = abs(float(target))
    return [s for s, _ in sorted(rows, key=lambda sd: (abs(sd[1] - tgt), sd[0]))]


def has_data() -> bool:
    return bool(_load())
