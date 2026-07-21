#!/usr/bin/env python3
"""
Backtest <-> Optimizer per-combo PARITY HARNESS (read-only, report-only).

WHY THIS EXISTS
---------------
This is the safety gate for the planned "unify the tradesheet/summary builder"
refactor described in RUST_COMBO_LOOP_DESIGN.md (SS6b, single source of truth;
SS5, the dual-run differ discipline). Before we consolidate the four divergent
builders into one, we must first PROVE what the current baseline is: for the
SAME strategy and the SAME trades, does the BACKTEST summary already equal the
OPTIMIZER per-combo summary today, field by field? Whatever this harness reports
today is exactly what the refactor must preserve.

It does NOT change any production code. It only imports and calls the existing
functions and diffs their outputs.

WHAT IT DOES
------------
For each of a few representative strategy payloads (short date range for speed):

  1. Runs the BACKTEST path:
         services.algotest_job.execute_algotest_job(payload)
     -> result['trades'] (list of records) -> trades_df
     -> result['summary'] (the backtest summary dict)

  2. Computes the OPTIMIZER per-combo summary from the SAME trades_df:
         services.optimizer.excel_builder.compute_xlsx_summary_metrics(
             trades_df, result['summary'], patchwise=False, filter_segments=None)
     This is exactly how the optimizer's inline finalize calls it
     (parallel.py:421 `_cmetrics(trades_df, flat_summary, ..., patchwise=False,
     filter_segments=...)`).

  3. DIFFS the backtest summary against the optimizer summary for every
     OVERLAPPING key (equal? differ? by how many decimals for floats?), and
     lists keys present in only one of the two separately.

  4. Compares the tradesheet column-set the two paths would emit. The optimizer
     per-combo CSV is written by result_store.write_combo_tradesheet(), which is
     literally `trades_df.to_csv(...)` on the SAME trades_df -- so the columns
     are identical by construction. The harness confirms this and prints the
     column list so a future refactor that changes columns is caught.

  5. Prints a per-payload PASS/FAIL banner (PASS = zero diverging overlapping
     summary fields) with a table of every diverging field.

Each payload is wrapped in try/except; a failure (missing data, etc.) prints a
short traceback summary and the harness moves on. It ALWAYS exits 0 -- it is a
report, not a test gate.

HOW TO RUN (inside the backend/worker container, which has data + DB + deps):

    docker exec algotest-worker-backtests python /app/tools/parity_harness.py

(any backend/worker container works, e.g. algotest-backend). Optionally pass a
substring to run only matching payloads:

    docker exec algotest-worker-backtests python /app/tools/parity_harness.py straddle
"""

from __future__ import annotations

import os
import sys
import math
import traceback
from typing import Any, Dict, List, Optional, Tuple

# The backend package lives at /app inside the container; make its modules
# (services.*, base, etc.) importable exactly as the worker does.
for _p in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402


# ---------------------------------------------------------------------------
# Representative strategy payloads.
#
# Short 2024 windows (NIFTY, weekly) so each backtest is fast. Field names match
# the real app payload schema inferred from routers/backtest.py (_normalize_*),
# services/algotest_job.py (execute_algotest_job / _normalize_request /
# _resolve_effective_request), services/engine_rust.py (per-leg keys), and the
# reference payload (formerly backend/verify_both.py, removed as dead code).
#
# Leg schema (from engine_rust.py):
#   segment="OPTIONS", option_type="CE"/"PE", position="BUY"/"SELL",
#   lots:int, expiry="WEEKLY", strike_interval:int,
#   strike_selection={type:"strike_type", strike_type:"ATM"|"ITM1"|"OTM2"...}
#     (or {type:"pct_of_atm", pct_of_atm:float, direction:"ITM"|"OTM"}),
#   optional stopLoss={mode:"pct"|"points", value:float}.
# ---------------------------------------------------------------------------

_COMMON = {
    "index": "NIFTY",
    "from_date": "2024-01-01",
    "to_date": "2024-06-30",
    "strategy_type": "positional",
    "underlying": "cash",
    "expiry_type": "WEEKLY",
    "entry_dte": 1,
    "exit_dte": 1,
    "slippage_pct": 0,
    "charges_enabled": False,
    "square_off_mode": "partial",
    "rollover_toggle": True,
    "no_cache": True,  # bypass Redis so we always exercise the live engine path
}


def _leg(option_type: str, position: str, strike_type: str = "ATM",
         stop_loss: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    leg = {
        "segment": "OPTIONS",
        "option_type": option_type,
        "position": position,
        "lots": 1,
        "expiry": "WEEKLY",
        "strike_interval": 50,
        "strike_selection": {"type": "strike_type", "strike_type": strike_type},
    }
    if stop_loss:
        leg["stopLoss"] = stop_loss
    return leg


PAYLOADS: List[Tuple[str, Dict[str, Any]]] = [
    (
        "single_leg_CE_SELL_ATM_weekly",
        {**_COMMON, "legs": [_leg("CE", "SELL", "ATM")]},
    ),
    (
        "short_straddle_CE_PE_SELL_ATM_weekly",
        {**_COMMON, "legs": [_leg("CE", "SELL", "ATM"), _leg("PE", "SELL", "ATM")]},
    ),
    (
        "short_straddle_with_perleg_SL_30pct",
        {
            **_COMMON,
            "legs": [
                _leg("CE", "SELL", "ATM", stop_loss={"mode": "pct", "value": 30}),
                _leg("PE", "SELL", "ATM", stop_loss={"mode": "pct", "value": 30}),
            ],
        },
    ),
]


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------

def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _decimals_agree(a: float, b: float) -> Optional[int]:
    """Largest N (0..12) such that round(a,N) == round(b,N). None if they differ
    even at 0 decimals. Used to characterise a float mismatch."""
    if a == b:
        return 12
    for n in range(12, -1, -1):
        try:
            if round(float(a), n) == round(float(b), n):
                return n
        except (ValueError, OverflowError):
            return None
    return None


def _values_equal(a: Any, b: Any) -> Tuple[bool, str]:
    """Return (equal, note). Exact for ints/str/bool; for floats exact-equal is
    the target (summary fields are rounded before storage) but we note the
    decimal at which they agree when they differ."""
    if a is None and b is None:
        return True, ""
    if _is_number(a) and _is_number(b):
        af, bf = float(a), float(b)
        if math.isnan(af) and math.isnan(bf):
            return True, "both NaN"
        if af == bf:
            return True, ""
        n = _decimals_agree(af, bf)
        if n is None:
            return False, "differ at all decimals"
        return False, f"agree to {n} dp, diff={af - bf:+.10g}"
    # Fallback: string-compare for everything else (dates, labels, etc.)
    if a == b:
        return True, ""
    return False, "unequal (non-numeric)"


def _fmt(v: Any) -> str:
    if _is_number(v):
        return f"{v:.10g}"
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "..."


# ---------------------------------------------------------------------------
# Per-payload run
# ---------------------------------------------------------------------------

def run_one(name: str, payload: Dict[str, Any]) -> None:
    bar = "=" * 78
    print("\n" + bar)
    print(f"PAYLOAD: {name}")
    print(bar)
    print(f"  index={payload['index']}  range={payload['from_date']}..{payload['to_date']}  "
          f"legs={len(payload.get('legs', []))}")

    # --- 1) BACKTEST ---
    from services.algotest_job import execute_algotest_job
    result = execute_algotest_job(dict(payload))  # copy: callee mutates
    if not isinstance(result, dict):
        print("  [FAIL] execute_algotest_job did not return a dict.")
        return

    trades = result.get("trades") or []
    bt_summary = result.get("summary") or {}
    trades_df = pd.DataFrame(trades)
    print(f"  backtest: status={result.get('status')}  trades={len(trades)}  "
          f"summary_keys={len(bt_summary)}")

    if trades_df.empty:
        print("  [WARN] Backtest produced 0 trades in this window -- summary diff "
              "will be trivial. Check that market data covers the range.")

    # --- 2) OPTIMIZER per-combo summary from the SAME trades_df ---
    # Fed exactly as parallel.py:421 does: raw engine trades_df + the summary
    # dict, patchwise=False, filter_segments=None (no named/uploaded filter).
    #
    # NOTE on MAE/MFE enrichment: the optimizer does NOT run _compute_mae_mfe_batch
    # before compute_xlsx_summary_metrics -- that batch join happens only inside
    # write_combo_xlsx (result_store.py:519) for the XLSX build. The trades_df
    # handed to _cmetrics is the raw engine output, which -- because
    # BACKTEST_INCLUDE_MAE_MFE=1 -- already carries the engine-computed MAE/MFE
    # columns. The backtest result['trades'] carries those same engine columns,
    # so feeding it directly is apples-to-apples for the SUMMARY BUILDER.
    from services.optimizer.excel_builder import compute_xlsx_summary_metrics
    optim_summary = compute_xlsx_summary_metrics(
        trades_df, bt_summary, patchwise=False, filter_segments=None,
    )
    print(f"  optim_summary_keys={len(optim_summary)}")

    # --- 3) DIFF the two summaries ---
    bt_keys = set(bt_summary.keys())
    op_keys = set(optim_summary.keys())
    overlap = sorted(bt_keys & op_keys)
    only_bt = sorted(bt_keys - op_keys)
    only_op = sorted(op_keys - bt_keys)

    matches: List[str] = []
    diverging: List[Tuple[str, Any, Any, str]] = []
    for k in overlap:
        eq, note = _values_equal(bt_summary[k], optim_summary[k])
        if eq:
            matches.append(k)
        else:
            diverging.append((k, bt_summary[k], optim_summary[k], note))

    passed = len(diverging) == 0
    banner = "PASS" if passed else "FAIL"
    print("\n  " + "-" * 74)
    print(f"  RESULT: [{banner}]  overlapping={len(overlap)}  "
          f"matching={len(matches)}  diverging={len(diverging)}")
    print("  " + "-" * 74)

    if diverging:
        kw = max(len(k) for k, *_ in diverging)
        kw = max(kw, len("FIELD"))
        print(f"  {'FIELD'.ljust(kw)}  {'BACKTEST':>20}  {'OPTIMIZER':>20}  NOTE")
        for k, bv, ov, note in diverging:
            print(f"  {k.ljust(kw)}  {_fmt(bv):>20}  {_fmt(ov):>20}  {note}")

    if only_bt:
        print(f"\n  Keys only in BACKTEST summary ({len(only_bt)}): "
              + ", ".join(only_bt))
    if only_op:
        print(f"  Keys only in OPTIMIZER summary ({len(only_op)}): "
              + ", ".join(only_op))

    # --- 4) Tradesheet column comparison ---
    # result_store.write_combo_tradesheet(job_id, label, trades_df) is just
    # trades_df.to_csv(index=False) on this exact trades_df, so the optimizer
    # per-combo CSV has these exact columns. We assert that here and print them
    # so any future column drift shows up in the baseline.
    cols = list(trades_df.columns)
    print(f"\n  Tradesheet columns ({len(cols)}): {cols}")
    print("  (Optimizer per-combo CSV = trades_df.to_csv on this same frame -> "
          "identical column-set by construction.)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    selected = [(n, p) for (n, p) in PAYLOADS if (only is None or only in n)]

    print("#" * 78)
    print("# BACKTEST <-> OPTIMIZER PER-COMBO PARITY HARNESS")
    print("# (report-only baseline for the unify-the-builder refactor; "
          "changes nothing)")
    print(f"# payloads to run: {len(selected)}"
          + (f"  (filter='{only}')" if only else ""))
    print("#" * 78)

    if not selected:
        print(f"No payloads match filter '{only}'. "
              f"Available: {[n for n, _ in PAYLOADS]}")
        return 0

    for name, payload in selected:
        try:
            run_one(name, payload)
        except Exception as exc:  # noqa: BLE001 -- report, never crash the run
            print(f"\n  [ERROR] payload '{name}' raised {type(exc).__name__}: {exc}")
            tb = traceback.format_exc().strip().splitlines()
            # Keep the traceback summary short (last few frames) to avoid dumping.
            print("  traceback (tail):")
            for line in tb[-8:]:
                print("    " + line)

    print("\n" + "#" * 78)
    print("# Done. This is a baseline report -- exit 0 regardless of PASS/FAIL.")
    print("#" * 78)
    return 0


if __name__ == "__main__":
    # Always exit 0: this is a report, not a gate.
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[HARNESS ERROR] {type(exc).__name__}: {exc}")
        traceback.print_exc()
    sys.exit(0)
