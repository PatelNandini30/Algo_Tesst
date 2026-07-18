"""
Rust combo-loop scaffold (Phase 1 groundwork) — flag + whitelist + shadow differ.

This module is PURELY ADDITIVE. Nothing in the live optimizer path imports it yet;
Phase 1 wires it in. With ``OPTIMIZE_RUST_LOOP=0`` (the default) the Rust batch path
is never reached and the optimizer behaves exactly as today.

HARD RULE (user): the optimizer must be **Rust-bounded only — NO Python fallback at
any point**. See [[rust-only-no-python-fallback]]. There is no pure-Python engine on
any live path (``run_algotest_backtest`` is already deleted). Today's per-combo path
(``run_rust_engine_pipeline`` — Rust-priced, Python-orchestrated) is retained **solely
as an offline / shadow PARITY REFERENCE**, never as a runtime fallback. When the Rust
batch is authoritative, a combo it does not own **hard-fails (RuntimeError)** — it is
never silently routed anywhere else.

Three pieces (see RUST_COMBO_LOOP_DESIGN.md §5, §6.3):

1. ``rust_loop_mode()`` — the ``OPTIMIZE_RUST_LOOP`` flag: ``{"0","shadow","1"}``.
     0       today's engine only (Rust batch unreached) — the default while porting.
     shadow  today's engine produces the real output AND is the parity reference; the
             Rust batch runs alongside for the shapes it owns and a differ logs any
             mismatch. Risk-free evidence on real jobs. (This is the ONLY place today's
             engine is used at runtime — as the reference, not a fallback.)
     1       Rust batch is the ONLY runtime calculation path. A combo the batch does not
             own hard-fails via ``require_rust_supported`` — NO fallback. Turned on only
             after every in-scope feature is ported to Rust and shadow-clean.

2. ``rust_batch_unsupported(merged_payload)`` — a FAIL-CLOSED coverage gate (design R5).
   It inspects the EFFECTIVE post-merge config (the output of
   ``param_expander.apply_combo_for_optim``, which force-enables spot-adj / midcap /
   pct_of_atm / straddle_width) and returns a reason string for any shape the pure-Rust
   batch does not yet OWN. Returns ``None`` only for shapes the batch fully owns.
   ``require_rust_supported`` raises RuntimeError on any non-None reason (the authoritative
   hard-fail). As features are ported to Rust, the corresponding reject is lifted — and
   only after that feature's parity corpus is clean — until the gate returns None for
   everything and there is nothing left to hard-fail on.

3. The shadow differ — ``diff_summary`` / ``diff_trades`` / ``diff_redis_row`` /
   ``diff_xlsx_cells`` — compares the reference (today's engine) artifacts against the
   Rust batch artifacts and returns human-readable diffs (logged, never raised).

Nothing here changes a single number: the flag gates whether the Rust path runs at all,
the coverage gate only ever *narrows* what Rust touches (and hard-fails otherwise —
never falls back), and the differ is read-only.
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
    """True only in mode "1" — the Rust batch is the ONLY runtime path and a combo it
    does not own hard-fails (no fallback)."""
    return rust_loop_mode() == "1"


# ── 2. Rust-batch coverage gate — fail-closed, hard-fail (no Python fallback) ─
#
# The current pure-Rust batch owns ONLY: resolve_trade_specs_core +
# simulate_trades_batch_core + (Python-side, for now) analytics/MAE-MFE. It does NOT
# yet own the SL/Target/Trail scan (Phase 0b) nor any Python-orchestrated feature
# (spot-adj, midcap, re-entry, next-weekly, lazy, filters, futures). So the gate below
# rejects ALL of those. A rejected combo does NOT fall back to Python — in authoritative
# mode it hard-fails (require_rust_supported → RuntimeError). As each capability lands
# the corresponding reject is lifted — and only after the parity corpus is clean for it
# (design R5, §7) — until the gate returns None for everything.

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


def rust_batch_unsupported(merged_payload: Dict[str, Any]) -> Optional[str]:
    """Return None if the pure-Rust batch fully OWNS this combo, else a short reason
    string it does not (yet).

    This is a COVERAGE gate, NOT a routing-to-Python gate: a non-None reason means the
    Rust batch can't produce this combo — in authoritative mode that HARD-FAILS
    (see require_rust_supported); there is no Python fallback.

    FAIL-CLOSED: `merged_payload` MUST be the effective post-merge config
    (apply_combo_for_optim output) so force-enabled flags are visible. Anything not
    positively recognized returns a reason. Never returns None on doubt.
    """
    try:
        p = merged_payload or {}
        if not isinstance(p, dict):
            return "payload-not-dict"

        # ── Payload-level orchestration features (all Python-orchestrated today) ──
        # Multi-index (per-leg index) runs price each leg from its OWN index series
        # via run_multi_index_feature / run_sync_weekly_cadence — the single-index
        # Rust batch must never claim them (it would misprice cross-index legs).
        if _truthy(p.get("multi_index_mode")):
            return "multi_index"
        if _truthy(p.get("spot_adjustment_enabled")):
            return "spot_adjustment"
        msa = p.get("midcap_spot_adjustment")
        if _truthy(p.get("midcap_legs")) or (isinstance(msa, dict) and _truthy(msa.get("enabled"))):
            return "midcap"
        if _truthy(p.get("filter_segments")) or _truthy(p.get("filter_config")):
            return "filter"
        if _truthy(p.get("overall_sl_value")) or _truthy(p.get("overall_target_value")):
            return "overall_sl_target"  # Phase 0b (SL scan) not yet extracted
        # YEARLY pins the contract to a December expiry while the cadence list
        # drives entry/exit, and needs the Python-resolved `yearly_cycles` to do
        # it. This batch loop builds its own expiry inputs, so it would silently
        # trade the CADENCE contract. Exclude explicitly rather than let an
        # unrecognised expiry kind fall through as "supported".
        if str(p.get("expiry_type") or "").upper() == "YEARLY":
            return "yearly_expiry"

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
        return f"coverage-gate-error:{exc}"


def combo_supported(merged_payload: Dict[str, Any]) -> bool:
    """Convenience boolean — True iff the pure-Rust batch owns this combo."""
    return rust_batch_unsupported(merged_payload) is None


def require_rust_supported(merged_payload: Dict[str, Any]) -> None:
    """Authoritative-mode hard-fail: raise RuntimeError if the Rust batch does not own
    this combo. This is the enforcement of the "Rust-bounded only, no Python fallback"
    rule — an unsupported combo errors out; it is NEVER routed to today's engine at
    runtime (that engine exists only as the offline/shadow parity reference).
    """
    reason = rust_batch_unsupported(merged_payload)
    if reason is not None:
        raise RuntimeError(
            f"OPTIMIZE_RUST_LOOP=1 (Rust-bounded, no fallback): combo not owned by the "
            f"Rust batch yet ({reason}). Port this feature to Rust before enabling "
            f"authoritative mode for it."
        )


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


# ── Shadow wiring — prove the ported Rust summary on real combos ─────────────

def run_shadow_summary_check(
    combo_id, combo_label, merged_payload, trades_df,
    base_summary, python_flat_summary, python_summary_pw,
    midcap_legs=None, midcap_spot_adjustment=None, midcap_symbol="NIFTYMIDCAP100",
    filter_segments=None,
) -> None:
    """Shadow mode: recompute the per-combo summary with the RUST engine
    (compute_optim_metrics + compute_summary_metrics, overall + patchwise) and diff it,
    key-by-key, against the authoritative Python summary. Read-only — logs diffs, never
    raises, never changes output. Only runs when OPTIMIZE_RUST_LOOP=shadow.

    This is the design's §5 shadow gate: run it across a real sweep and it exercises the
    ported summary on every strategy the corpus can't cover; a family graduates to
    Rust-authoritative only when this is clean.
    """
    if rust_loop_mode() != "shadow":
        return
    reason = rust_batch_unsupported(merged_payload)
    if reason is not None:
        logger.info("[RUST_SHADOW combo=%s] not owned by Rust yet (%s) — skipped", combo_id, reason)
        return
    try:
        import algotest_native  # type: ignore
        if trades_df is None or (hasattr(trades_df, "empty") and trades_df.empty):
            return
        records = trades_df.where(trades_df.notna(), None).to_dict("records")
        mbt, msumm = None, None
        if midcap_legs:
            from services.optimizer.excel_builder import compute_midcap_for_rows
            mbt, msumm, _has = compute_midcap_for_rows(
                records, midcap_legs, midcap_spot_adjustment, midcap_symbol or "NIFTYMIDCAP100")
            mbt = mbt or None

        opt_m = algotest_native.compute_optim_metrics(records, base_summary)
        merged_summary = {**base_summary, **opt_m}
        sm_over = algotest_native.compute_summary_metrics(records, merged_summary, False, filter_segments, mbt, msumm)
        rust_flat = {**merged_summary, **sm_over}
        log_shadow_diffs(combo_id, combo_label, "summary-overall",
                         diff_summary(python_flat_summary or {}, rust_flat))

        if python_summary_pw is not None:
            # python_summary_pw is the RAW _cmetrics(patchwise) output (not merged with
            # the base summary), so diff against the raw Rust _cmetrics output.
            sm_pw = algotest_native.compute_summary_metrics(records, merged_summary, True, filter_segments, mbt, msumm)
            log_shadow_diffs(combo_id, combo_label, "summary-patchwise",
                             diff_summary(python_summary_pw or {}, sm_pw))
    except Exception as exc:  # shadow is best-effort; never disturb the real run
        logger.warning("[RUST_SHADOW combo=%s] shadow check errored: %s", combo_id, exc)


def rust_authoritative_summary(
    trades_df, base_summary, merged_payload,
    midcap_legs=None, midcap_spot_adjustment=None, midcap_symbol="NIFTYMIDCAP100",
    filter_segments=None,
):
    """OPTIMIZE_RUST_LOOP=1: compute the per-combo summary ENTIRELY in Rust — the
    Rust-bounded, NO-Python-fallback path. The caller MUST first call
    require_rust_supported(merged_payload) (raises on any un-ported shape); this
    function does not fall back to Python for anything.

    Returns (flat_summary, summary_pw) exactly as the Python path would have built them:
      flat_summary = {**base_summary, **compute_optim_metrics, **compute_summary_metrics(overall)}
      summary_pw   = compute_summary_metrics(patchwise)   (raw, as _cmetrics returns it)
    """
    import algotest_native  # type: ignore
    if trades_df is None or (hasattr(trades_df, "empty") and trades_df.empty):
        return dict(base_summary or {}), None
    records = trades_df.where(trades_df.notna(), None).to_dict("records")
    mbt, msumm = None, None
    if midcap_legs:
        from services.optimizer.excel_builder import compute_midcap_for_rows
        mbt, msumm, _has = compute_midcap_for_rows(
            records, midcap_legs, midcap_spot_adjustment, midcap_symbol or "NIFTYMIDCAP100")
        mbt = mbt or None
    opt_m = algotest_native.compute_optim_metrics(records, base_summary or {})
    flat = {**(base_summary or {}), **opt_m}
    over = algotest_native.compute_summary_metrics(records, flat, False, filter_segments, mbt, msumm)
    flat = {**flat, **over}
    pw = algotest_native.compute_summary_metrics(records, flat, True, filter_segments, mbt, msumm)
    return flat, pw
