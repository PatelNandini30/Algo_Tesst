"""
Compare a freshly-run engine result against a frozen snapshot.

The Python and (eventually) Rust engines must produce the same numbers on
the same input. This module is the parity gate: every diff it reports is a
bug to fix in the new implementation.

Conventions
-----------
* Numeric values match with ``atol=DEFAULT_ATOL`` (0.01 by default — absorbs
  IEEE-754 dust without hiding meaningful drift).
* Date values match exactly after normalization to ISO format.
* String values match exactly (case-sensitive).
* Missing/extra columns or rows are always a diff.

Public API
----------
* ``run_engine(payload) -> EngineResult`` — invoke the active engine.
* ``compare(snapshot, fresh, atol=...) -> List[str]`` — produce a list of
  human-readable diff lines. Empty list = parity.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_ATOL = float(os.environ.get("PARITY_ATOL", "0.01"))


# ── Engine invocation ───────────────────────────────────────────────────────

@dataclass
class EngineResult:
    trades: List[Dict[str, Any]]
    summary: Dict[str, Any]
    pivot: Dict[str, Any]


def run_engine(payload: Dict[str, Any]) -> EngineResult:
    """
    Run the active engine on a payload and return a JSON-friendly result.

    Backend selection:
      ENGINE_BACKEND=python   (default — current implementation)
      ENGINE_BACKEND=rust     (Phase 2b; raises NotImplementedError today)
      ENGINE_BACKEND=auto     (rust where covered, python fallback)
    """
    backend = (os.environ.get("ENGINE_BACKEND") or "python").lower()
    if backend not in ("python", "rust", "auto"):
        raise ValueError(f"invalid ENGINE_BACKEND: {backend!r}")

    if backend == "rust":
        return _run_rust(payload)
    if backend == "auto":
        try:
            return _run_rust(payload)
        except NotImplementedError:
            return _run_python(payload)
    return _run_python(payload)


def _run_python(payload: Dict[str, Any]) -> EngineResult:
    """Direct call into the existing Python engine — bypasses Celery."""
    import pandas as pd
    from base import compute_analytics, build_pivot, bulk_load_options
    from engines.generic_algotest_engine import run_algotest_backtest
    from services.algotest_job import _normalize_request, _resolve_effective_request

    norm = _resolve_effective_request(_normalize_request(dict(payload)))
    # The full pipeline (services.algotest_job.execute_algotest_job) calls
    # bulk_load_options before invoking the engine; the parity test bypasses
    # that, so OHLC-based features like SL-with-Buffer don't see day open/high/
    # low. Replicate the bulk-load here so the parity test matches production.
    try:
        idx = str(norm.get("index") or norm.get("symbol") or "NIFTY")
        d_from = str(norm.get("from_date") or norm.get("date_from"))
        d_to = str(norm.get("to_date") or norm.get("date_to"))
        if d_from and d_to:
            bulk_load_options(idx, d_from, d_to)
    except Exception:
        pass
    trades_df, _engine_summary, _engine_pivot = run_algotest_backtest(norm)
    if trades_df is None or trades_df.empty:
        return EngineResult(trades=[], summary={}, pivot={"headers": [], "rows": []})
    for col in ("Entry Date", "Exit Date"):
        if col in trades_df.columns:
            trades_df[col] = pd.to_datetime(trades_df[col], dayfirst=True, errors="coerce")
    trades_df, summary = compute_analytics(trades_df)
    pivot = build_pivot(trades_df, "Exit Date")

    # Convert datetime → ISO strings so snapshots are JSON-serialisable and
    # comparison is timezone-stable.
    for col in trades_df.columns:
        if pd.api.types.is_datetime64_any_dtype(trades_df[col]):
            trades_df[col] = trades_df[col].apply(
                lambda v: v.strftime("%Y-%m-%d") if pd.notna(v) else None
            )

    return EngineResult(
        trades=trades_df.to_dict("records"),
        summary=summary,
        pivot=pivot,
    )


def _run_rust(payload: Dict[str, Any]) -> EngineResult:
    """Phase 2b entry point — not yet implemented."""
    try:
        import algotest_native  # type: ignore

        if not hasattr(algotest_native, "run_optimization_batch"):
            raise NotImplementedError
        algotest_native.run_optimization_batch(payload, [])
        # If the stub ever returns instead of raising, surface a clear error.
        raise NotImplementedError("Rust engine returned without producing a result")
    except Exception as exc:
        # The Rust stub currently raises NotImplementedError — re-raise so
        # callers using ENGINE_BACKEND=auto fall back to Python.
        raise NotImplementedError(str(exc))


# ── Comparison ──────────────────────────────────────────────────────────────

def _close(a: Any, b: Any, atol: float) -> bool:
    """True if a and b are numerically within `atol`, or both NaN/None."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        fa = float(a)
        fb = float(b)
    except (TypeError, ValueError):
        return a == b
    if math.isnan(fa) and math.isnan(fb):
        return True
    if math.isnan(fa) or math.isnan(fb):
        return False
    return abs(fa - fb) <= atol


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6f}"
    return repr(v)


def _diff_dict(
    snap: Dict[str, Any],
    fresh: Dict[str, Any],
    atol: float,
    path: str,
) -> List[str]:
    out: List[str] = []
    snap_keys = set(snap.keys())
    fresh_keys = set(fresh.keys())
    for k in sorted(snap_keys - fresh_keys):
        out.append(f"{path}.{k}: missing in fresh result")
    for k in sorted(fresh_keys - snap_keys):
        out.append(f"{path}.{k}: unexpected new key in fresh result (value={_fmt(fresh[k])})")
    for k in sorted(snap_keys & fresh_keys):
        s = snap[k]
        f = fresh[k]
        if isinstance(s, dict) and isinstance(f, dict):
            out.extend(_diff_dict(s, f, atol, f"{path}.{k}"))
        elif isinstance(s, list) and isinstance(f, list):
            out.extend(_diff_list(s, f, atol, f"{path}.{k}"))
        elif not _close(s, f, atol) and s != f:
            out.append(f"{path}.{k}: snapshot={_fmt(s)} fresh={_fmt(f)}")
    return out


def _diff_list(
    snap: List[Any],
    fresh: List[Any],
    atol: float,
    path: str,
) -> List[str]:
    out: List[str] = []
    if len(snap) != len(fresh):
        out.append(f"{path}: length differs (snapshot={len(snap)}, fresh={len(fresh)})")
    for i, (s, f) in enumerate(zip(snap, fresh)):
        if isinstance(s, dict) and isinstance(f, dict):
            out.extend(_diff_dict(s, f, atol, f"{path}[{i}]"))
        elif isinstance(s, list) and isinstance(f, list):
            out.extend(_diff_list(s, f, atol, f"{path}[{i}]"))
        elif not _close(s, f, atol) and s != f:
            out.append(f"{path}[{i}]: snapshot={_fmt(s)} fresh={_fmt(f)}")
    return out


def compare(
    snapshot: Dict[str, Any],
    fresh: EngineResult,
    atol: float = DEFAULT_ATOL,
    max_diffs: Optional[int] = 50,
) -> List[str]:
    """
    Return a list of human-readable diff strings. Empty list = parity.

    `max_diffs` caps the report length so a totally-broken port doesn't dump
    thousands of lines; set to None to disable.
    """
    fresh_dict = {
        "trades": fresh.trades,
        "summary": fresh.summary,
        "pivot": fresh.pivot,
    }
    snap_dict = {
        "trades": snapshot.get("trades", []),
        "summary": snapshot.get("summary", {}),
        "pivot": snapshot.get("pivot", {}),
    }
    diffs: List[str] = []
    diffs.extend(_diff_list(snap_dict["trades"], fresh_dict["trades"], atol, "trades"))
    diffs.extend(_diff_dict(snap_dict["summary"], fresh_dict["summary"], atol, "summary"))
    diffs.extend(_diff_dict(snap_dict["pivot"], fresh_dict["pivot"], atol, "pivot"))
    if max_diffs is not None and len(diffs) > max_diffs:
        truncated = diffs[:max_diffs]
        truncated.append(f"... and {len(diffs) - max_diffs} more diffs (capped at {max_diffs})")
        return truncated
    return diffs
