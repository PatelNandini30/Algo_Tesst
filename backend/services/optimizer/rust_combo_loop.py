"""
Rust combo-loop scaffold (Phase 1 groundwork) — flag + whitelist + shadow differ.

This module is PURELY ADDITIVE. Nothing in the live optimizer path imports it yet;
Phase 1 wires it in. With ``OPTIMIZE_RUST_LOOP=0`` (the default) the Rust batch path
is never reached and the optimizer behaves exactly as today.

Three pieces (see RUST_COMBO_LOOP_DESIGN.md §5, §6.3):

1. ``rust_loop_mode()`` — the ``OPTIMIZE_RUST_LOOP`` flag: ``{"0","shadow","1"}``.
     0       today's fork-pool path only (Rust batch unreached) — the permanent fallback.
     shadow  Python stays authoritative & produces the real output; the Rust batch runs
             alongside and a differ logs mismatches. Risk-free evidence on real jobs.
     1       Rust authoritative for whitelisted combos; everything else falls back to
             the proven Python engine (``needs_python``).

2. ``needs_python(merged_payload)`` — a FAIL-CLOSED positive whitelist (design R5). It
   inspects the EFFECTIVE post-merge config (the output of
   ``param_expander.apply_combo_for_optim``, which force-enables spot-adj / midcap /
   pct_of_atm / straddle_width) and returns a reason string for any shape the pure-Rust
   batch does not yet own — anything unrecognized routes to Python. Returns ``None`` only
   for shapes proven identical.

3. The shadow differ — ``diff_summary`` / ``diff_trades`` / ``diff_redis_row`` /
   ``diff_xlsx_cells`` — compares the Python-authoritative artifacts against the Rust
   shadow artifacts and returns human-readable diffs (logged, never raised).

Nothing here changes a single number: the flag gates whether the Rust path runs at all,
the whitelist only ever *narrows* what Rust touches, and the differ is read-only.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── 1. Flag ──────────────────────────────────────────────────────────────────

def rust_loop_mode() -> str:
    """Return the OPTIMIZE_RUST_LOOP mode: one of "0" | "shadow" | "1".

    Unknown / unset values fall back to "0" (today's fork-pool path) so a typo can
    never silently enable the Rust path.
    """
    v = str(os.environ.get("OPTIMIZE_RUST_LOOP", "0")).strip().lower()
    return v if v in ("0", "shadow", "1") else "0"


def rust_loop_enabled() -> bool:
    """True when the Rust batch should run at all (shadow OR authoritative)."""
    return rust_loop_mode() in ("shadow", "1")


def rust_loop_authoritative() -> bool:
    """True only in mode "1" — Rust output is served for whitelisted combos."""
    return rust_loop_mode() == "1"


# ── 2. needs_python — fail-closed whitelist ──────────────────────────────────
#
# The current pure-Rust batch owns ONLY: resolve_trade_specs_core +
# simulate_trades_batch_core + (Python-side, for now) analytics/MAE-MFE. It does NOT
# yet own the SL/Target/Trail scan (Phase 0b) nor any Python-orchestrated feature
# (spot-adj, midcap, re-entry, next-weekly, lazy, filters, futures). So the whitelist
# below rejects ALL of those. As each capability lands the corresponding reject is
# lifted — and only after the parity corpus is clean for it (design R5, §7).

# Strike-selection modes compute_strike_for_leg resolves in Rust (engine_rust.py:1189+).
_RUST_STRIKE_TYPES = frozenset({
    "", "strike_type",
    "rel_leg", "pct_of_atm",
    "closest_premium", "premium_gte", "premium_lte", "premium_range",
    "straddle_width", "atm_straddle_prem_pct",
})
# expiry values that mean "trade a contract one expiry further out" — Python-orchestrated.
_NEXT_EXPIRY_TYPES = frozenset({"NEXT_WEEKLY", "WEEKLY_T1", "NEXT_MONTHLY", "MONTHLY_T1"})


def _truthy(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "none")
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) > 0
    return bool(v)


def _leg_has_exit_scan(leg: Dict[str, Any]) -> bool:
    """True if the leg carries an SL/Target/Trail or buffer-strike exit — none of
    which the pure-Rust batch owns yet (Phase 0b extracts the SL scan)."""
    for k in ("stopLoss", "targetProfit", "trailSL"):
        v = leg.get(k)
        if isinstance(v, dict) and any(_truthy(v.get(sub)) for sub in
                                       ("enabled", "value", "trigger", "move", "type")):
            return True
        elif not isinstance(v, dict) and _truthy(v):
            return True
    if _truthy(leg.get("buffer_strike_enabled")):
        return True
    return False


def _leg_is_futures(leg: Dict[str, Any]) -> bool:
    seg = str(leg.get("segment") or "").strip().lower()
    ot = str(leg.get("option_type") or "").strip().upper()
    return seg in ("futures", "future", "fut") or ot in ("FUT", "FUTIDX")


def _leg_is_lazy(leg: Dict[str, Any]) -> bool:
    return (_truthy(leg.get("lazy_leg_config")) or _truthy(leg.get("lazyLegConfig"))
            or _truthy(leg.get("is_lazy")))


def _leg_is_next_expiry(leg: Dict[str, Any]) -> bool:
    return str(leg.get("expiry") or "").strip().upper() in _NEXT_EXPIRY_TYPES


def _leg_has_reentry(leg: Dict[str, Any]) -> bool:
    return _truthy(leg.get("reEntryOnSL")) or _truthy(leg.get("reEntryOnTarget"))


def needs_python(merged_payload: Dict[str, Any]) -> Optional[str]:
    """Return None if the combo can run in the pure-Rust batch, else a short reason
    string it must fall back to the proven Python engine.

    FAIL-CLOSED: `merged_payload` MUST be the effective post-merge config
    (apply_combo_for_optim output) so force-enabled flags are visible. Anything not
    positively recognized returns a reason (→ Python). Never returns None on doubt.
    """
    try:
        p = merged_payload or {}
        if not isinstance(p, dict):
            return "payload-not-dict"

        # ── Payload-level orchestration features (all Python-orchestrated today) ──
        if _truthy(p.get("spot_adjustment_enabled")):
            return "spot_adjustment"
        msa = p.get("midcap_spot_adjustment")
        if _truthy(p.get("midcap_legs")) or (isinstance(msa, dict) and _truthy(msa.get("enabled"))):
            return "midcap"
        if _truthy(p.get("filter_segments")) or _truthy(p.get("filter_config")):
            return "filter"
        if _truthy(p.get("overall_sl_value")) or _truthy(p.get("overall_target_value")):
            return "overall_sl_target"  # Phase 0b (SL scan) not yet extracted

        legs = p.get("legs") or []
        if not isinstance(legs, list) or len(legs) == 0:
            return "no-legs"

        for i, leg in enumerate(legs):
            if not isinstance(leg, dict):
                return f"leg{i}-not-dict"
            if _leg_is_futures(leg):
                return f"leg{i}-futures"
            if _leg_is_lazy(leg):
                return f"leg{i}-lazy"
            if _leg_is_next_expiry(leg):
                return f"leg{i}-next_expiry"
            if _leg_has_reentry(leg):
                return f"leg{i}-reentry"
            if _leg_has_exit_scan(leg):
                return f"leg{i}-sl_target_trail"  # Phase 0b
            sel = leg.get("strike_selection") or {}
            sel_type = str((sel.get("type") if isinstance(sel, dict) else "") or "").strip().lower()
            recognized = (sel_type in _RUST_STRIKE_TYPES
                          or sel_type.startswith(("atm", "itm", "otm")))
            if not recognized:
                return f"leg{i}-strike:{sel_type or 'unknown'}"

        return None  # every gate passed → the pure-Rust batch owns this shape
    except Exception as exc:  # fail-closed on ANY unexpected shape
        return f"whitelist-error:{exc}"


def combo_supported(merged_payload: Dict[str, Any]) -> bool:
    """Convenience boolean — True iff the pure-Rust batch owns this combo."""
    return needs_python(merged_payload) is None


# ── 3. Shadow differ ─────────────────────────────────────────────────────────
#
# Each field is rounded before storage, so exact equality is the target; a tiny
# tolerance guards against float-repr noise only (design §5). Any real mismatch is a
# bug, not noise. All differs are READ-ONLY and return lists of human strings.

_DEFAULT_TOL = 1e-6


def _num(v: Any) -> Optional[float]:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _values_match(a: Any, b: Any, tol: float) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= tol + tol * max(abs(fa), abs(fb))
    return str(a) == str(b)


def diff_summary(py: Dict[str, Any], rust: Dict[str, Any], tol: float = _DEFAULT_TOL) -> List[str]:
    """Key-by-key diff of two summary dicts over the keys they share, plus any key
    present in one but not the other. Numbers compared with a tiny tolerance."""
    diffs: List[str] = []
    py = py or {}
    rust = rust or {}
    for k in sorted(set(py) | set(rust)):
        if k not in py:
            diffs.append(f"summary[{k}]: only in rust ({rust[k]!r})")
        elif k not in rust:
            diffs.append(f"summary[{k}]: only in python ({py[k]!r})")
        elif not _values_match(py[k], rust[k], tol):
            diffs.append(f"summary[{k}]: py={py[k]!r} rust={rust[k]!r}")
    return diffs


def diff_trades(py_df: Any, rust_df: Any, tol: float = _DEFAULT_TOL,
                max_report: int = 40) -> List[str]:
    """Cell-level diff of two trades DataFrames (the CSV/tradesheet source). Reports
    shape/column mismatches first, then up to `max_report` differing cells."""
    diffs: List[str] = []
    try:
        py_cols = list(getattr(py_df, "columns", []) or [])
        rust_cols = list(getattr(rust_df, "columns", []) or [])
        if py_cols != rust_cols:
            only_py = [c for c in py_cols if c not in rust_cols]
            only_rs = [c for c in rust_cols if c not in py_cols]
            if only_py:
                diffs.append(f"trades cols only in python: {only_py}")
            if only_rs:
                diffs.append(f"trades cols only in rust: {only_rs}")
        n_py, n_rs = len(py_df), len(rust_df)
        if n_py != n_rs:
            diffs.append(f"trades row count: py={n_py} rust={n_rs}")
        common_cols = [c for c in py_cols if c in rust_cols]
        for r in range(min(n_py, n_rs)):
            for c in common_cols:
                a = py_df.iloc[r][c]
                b = rust_df.iloc[r][c]
                if not _values_match(a, b, tol):
                    diffs.append(f"trades[row={r},{c}]: py={a!r} rust={b!r}")
                    if len(diffs) >= max_report:
                        diffs.append("… (truncated)")
                        return diffs
    except Exception as exc:
        diffs.append(f"trades-diff-error: {exc}")
    return diffs


def diff_redis_row(py_row: Dict[str, Any], rust_row: Dict[str, Any],
                   tol: float = _DEFAULT_TOL) -> List[str]:
    """Deep-diff the per-combo Redis result row (combo_columns, objective_value,
    trade_count, has_midcap, inline_finalized, nested summary, …)."""
    diffs: List[str] = []

    def _walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) or isinstance(b, dict):
            a = a if isinstance(a, dict) else {}
            b = b if isinstance(b, dict) else {}
            for k in sorted(set(a) | set(b)):
                _walk(a.get(k), b.get(k), f"{path}.{k}")
        elif isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
            a = list(a) if isinstance(a, (list, tuple)) else []
            b = list(b) if isinstance(b, (list, tuple)) else []
            if len(a) != len(b):
                diffs.append(f"row{path}: len py={len(a)} rust={len(b)}")
            for i in range(min(len(a), len(b))):
                _walk(a[i], b[i], f"{path}[{i}]")
        elif not _values_match(a, b, tol):
            diffs.append(f"row{path}: py={a!r} rust={b!r}")

    _walk(py_row or {}, rust_row or {}, "")
    return diffs


def diff_xlsx_cells(py_path: str, rust_path: str, max_report: int = 40) -> List[str]:
    """Cell-diff two .xlsx files (two writers differ in zip/XML metadata, so byte-diff
    is meaningless — compare sheet names/order, dims, and per-cell (value, number_format)).
    """
    diffs: List[str] = []
    try:
        from openpyxl import load_workbook
        wa = load_workbook(py_path)
        wb = load_workbook(rust_path)
        if wa.sheetnames != wb.sheetnames:
            diffs.append(f"xlsx sheets: py={wa.sheetnames} rust={wb.sheetnames}")
        for sheet in [s for s in wa.sheetnames if s in wb.sheetnames]:
            sa, sb = wa[sheet], wb[sheet]
            if (sa.max_row, sa.max_column) != (sb.max_row, sb.max_column):
                diffs.append(f"xlsx[{sheet}] dims: py={sa.max_row}x{sa.max_column} "
                             f"rust={sb.max_row}x{sb.max_column}")
            for row in range(1, min(sa.max_row, sb.max_row) + 1):
                for col in range(1, min(sa.max_column, sb.max_column) + 1):
                    ca, cb = sa.cell(row=row, column=col), sb.cell(row=row, column=col)
                    if not _values_match(ca.value, cb.value, _DEFAULT_TOL) \
                            or ca.number_format != cb.number_format:
                        diffs.append(
                            f"xlsx[{sheet}]!{ca.coordinate}: "
                            f"py=({ca.value!r},{ca.number_format}) "
                            f"rust=({cb.value!r},{cb.number_format})")
                        if len(diffs) >= max_report:
                            diffs.append("… (truncated)")
                            return diffs
    except Exception as exc:
        diffs.append(f"xlsx-diff-error: {exc}")
    return diffs


def log_shadow_diffs(job_id: str, combo_label: str, kind: str, diffs: List[str]) -> bool:
    """Emit shadow-diff results to the log. Returns True if clean (no diffs)."""
    tag = f"[RUST_SHADOW job={str(job_id)[:8]} combo={combo_label!r} {kind}]"
    if not diffs:
        logger.info("%s CLEAN", tag)
        return True
    logger.warning("%s %d diff(s):", tag, len(diffs))
    for d in diffs[:50]:
        logger.warning("%s   %s", tag, d)
    return False
