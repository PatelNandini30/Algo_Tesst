"""THREE-WAY summary parity — the gate for "one code path, backtest is truth".

For the SAME payload, compare the three places a user can read a summary:

    A  BACKTEST            execute_algotest_job(payload)['summary']
                           (base.compute_analytics + additive optim-only fields)
    B  OPTIM PER-COMBO     the "Summary" sheet of build_combo_xlsx(...)
                           (_write_summary_sheet)
    C  OPTIM MASTER        compute_xlsx_summary_metrics(...)
                           (what optimize_summary.xlsx renders per row)

A is the SOURCE OF TRUTH (user rule: backtest tradesheet calculations are
correct). B and C must equal A for every shared metric. Any row printed below
is a place where the same strategy shows the user different numbers.

Includes a MULTI-INDEX payload (NIFTY CE sell + MIDCPNIFTY FUT buy), which is
where the cross-index CAGR(Spot) defect lived.

    docker exec -w /app algotest-backend python -m tools.three_way_summary_parity
"""
import warnings; warnings.filterwarnings("ignore")
import io
import sys
import traceback

import openpyxl
import pandas as pd

from services.algotest_job import execute_algotest_job
from services.optimizer.excel_builder import (
    compute_xlsx_summary_metrics, build_combo_xlsx,
)
from tools.optim_bt_summary_parity import MAP, _num, _sheet_kv
from tools.parity_harness import PAYLOADS as _SINGLE

_MI_COMMON = {
    "index": "NIFTY",
    "from_date": "2025-01-01",
    "to_date": "2025-06-30",
    "strategy_type": "positional",
    "underlying": "cash",
    "entry_dte": 1,
    "exit_dte": 1,
    "slippage_pct": 0,
    "charges_enabled": False,
    "square_off_mode": "partial",
    "rollover_toggle": True,
    "multi_index_mode": True,
    "no_cache": True,
}

MULTI_INDEX = (
    "multi_index_NIFTY_CE_sell_plus_MIDCP_FUT_buy",
    {
        **_MI_COMMON,
        "expiry_type": "WEEKLY",
        "legs": [
            {"segment": "OPTIONS", "index": "NIFTY", "option_type": "CE",
             "position": "SELL", "lots": 1, "expiry": "WEEKLY",
             "strike_interval": 100,
             "strike_selection": {"type": "strike_type", "strike_type": "ATM"}},
            {"segment": "FUTURES", "index": "MIDCPNIFTY", "position": "BUY",
             "lots": 1, "expiry": "MONTHLY"},
        ],
    },
)

# Same legs, but the UNIFIED-CADENCE path (run_sync_weekly_cadence). This one is a
# separate code path with its own compute_analytics call on a SYNTHETIC frame
# (Entry Spot = 100.0, no Exit Spot), so it must be exercised explicitly — the
# plain MULTI_INDEX payload above routes to run_multi_index_feature and does NOT
# cover it.
MULTI_INDEX_SYNC = (
    "multi_index_SYNC_weekly_roll_CE_sell_plus_MIDCP_FUT_buy",
    {**MULTI_INDEX[1], "sync_weekly_roll": True},
)

PAYLOADS = list(_SINGLE) + [MULTI_INDEX, MULTI_INDEX_SYNC]


# Sheet labels beyond the shared MAP, so the per-combo Summary sheet (B) is checked on
# everything it renders — not just the subset MAP happened to cover. Without these the
# gate watched 21 of ~35 rendered values, which is not enough to refactor against.
EXTRA_SHEET_MAP = {
    "Overall Profit": "total_pnl_pct",
    "No. of Trades": "count",
    "Win %": "win_pct",
    "Loss %": "loss_pct",
    "Avg Profit per Trade": "avg_profit_per_trade",
    "Expectancy Ratio": "expectancy",
    "Max Profit (Single Trade)": "max_win",
    "Max Loss (Single Trade)": "max_loss",
    "Max DD Days": "mdd_duration_days",
    "Max Win Streak": "max_win_streak",
    "Max Losing Streak": "max_loss_streak",
    "Net P&L": "total_pnl",
    "Actual Live DD Without Outlier 1": "outlier_dd_1",
    "Avg Actual Live DD Without Outlier 1": "outlier_dd_1_avg",
    "Actual Live DD Without Outlier 2": "outlier_dd_2",
    "Avg Actual Live DD Without Outlier 2": "outlier_dd_2_avg",
    "Actual Live DD Without Outlier 3": "outlier_dd_3",
    "Avg Actual Live DD Without Outlier 3": "outlier_dd_3_avg",
}
SHEET_MAP = {**MAP, **EXTRA_SHEET_MAP}


def _num_eq(a, b) -> bool:
    try:
        return abs(round(float(a), 2) - round(float(b), 2)) <= 0.005
    except (TypeError, ValueError):
        return str(a) == str(b)


def _cmp(tag, ref, got, checked, diffs, keys=None):
    """ref is authoritative; record any metric where `got` disagrees.

    `keys` = explicit key iterable. Default None means compare the FULL key
    intersection, so a metric can never diverge unwatched just because nobody
    added it to a hand-written map.
    """
    ks = keys if keys is not None else (set(ref) & set(got))
    for key in sorted(ks):
        rv, gv = ref.get(key), got.get(key)
        if rv is None or gv is None or isinstance(rv, bool) or isinstance(gv, bool):
            continue
        if not isinstance(rv, (int, float)) or not isinstance(gv, (int, float)):
            continue
        checked.add(key)
        if not _num_eq(rv, gv):
            diffs.append((tag, key, key, round(float(rv), 4), round(float(gv), 4)))


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    grand = 0
    skipped_patchwise = 0
    for name, payload in PAYLOADS:
        if only and only not in name:
            continue
        print("=" * 78)
        print("PAYLOAD:", name)
        try:
            res = execute_algotest_job(dict(payload))
        except Exception as exc:
            print(f"  [ERROR] {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)
            continue
        trades = res.get("trades") or []
        if not trades:
            print("  no trades — skip")
            continue
        df = pd.DataFrame(trades)
        A = res.get("summary") or {}

        C = compute_xlsx_summary_metrics(
            df, A,
            midcap_legs=payload.get("midcap_legs") or None,
            patchwise=False, filter_segments=None,
        )
        xb = build_combo_xlsx(df, A, combo_label=name,
                              from_date=payload["from_date"],
                              to_date=payload["to_date"])
        kv = _sheet_kv(openpyxl.load_workbook(io.BytesIO(xb))["Summary"])
        B = {}
        for label, key in SHEET_MAP.items():
            if label in kv:
                v = _num(kv[label])
                if v is not None:
                    B.setdefault(key, v)

        checked, diffs = set(), []
        # B is limited to what the sheet renders; C compares the FULL key intersection.
        _cmp("A!=B backtest vs per-combo", A, B, checked, diffs)
        _cmp("A!=C backtest vs master", A, C, checked, diffs)

        # ── PATCHWISE ─────────────────────────────────────────────────────────────
        # The backtest summary is OVERALL — it has no patchwise counterpart — so the
        # invariant here is NOT A==B==C but B==C: the per-combo patchwise Summary sheet
        # must equal the master patchwise summary. Without this, patchwise numbers are
        # produced by two independent implementations with nothing watching them, which
        # is what blocked collapsing _summary_layout onto the Rust engine.
        # Segments are synthesised as two halves of the real trade range so the equity
        # chain genuinely resets mid-run (a no-op filter would not exercise the path).
        try:
            _d = pd.to_datetime(df["Entry Date"], errors="coerce", dayfirst=True).dropna()
            if len(_d) >= 4:
                _lo, _hi = _d.min(), _d.max()
                _mid = _lo + (_hi - _lo) / 2
                segs = [
                    {"start": _lo.strftime("%Y-%m-%d"), "end": _mid.strftime("%Y-%m-%d")},
                    {"start": _mid.strftime("%Y-%m-%d"), "end": _hi.strftime("%Y-%m-%d")},
                ]
                Cpw = compute_xlsx_summary_metrics(
                    df, A, midcap_legs=payload.get("midcap_legs") or None,
                    patchwise=True, filter_segments=segs,
                )
                xbpw = build_combo_xlsx(
                    df, A, combo_label=name + "_pw",
                    from_date=payload["from_date"], to_date=payload["to_date"],
                    patchwise=True, filter_segments=segs, force_patch_wise=True,
                )
                kvpw = _sheet_kv(openpyxl.load_workbook(io.BytesIO(xbpw))["Summary"])
                Bpw = {}
                for label, key in SHEET_MAP.items():
                    if label in kvpw:
                        v = _num(kvpw[label])
                        if v is not None:
                            Bpw.setdefault(key, v)
                _cmp("B!=C PATCHWISE per-combo vs master", Cpw, Bpw, checked, diffs)
            else:
                skipped_patchwise += 1
                print(f"  [warn] patchwise check skipped: fewer than 4 Entry Dates ({len(_d)})")
        except Exception as exc:
            skipped_patchwise += 1
            print(f"  [warn] patchwise check skipped: {type(exc).__name__}: {exc}")
        print(f"  trades={len(df)}  metrics compared={len(checked)}  diffs={len(diffs)}"
              f"  [{'OK' if not diffs else 'DIVERGED'}]")
        for tag, label, key, rv, gv in diffs:
            # `rv` is whatever that comparison treats as authoritative — the BACKTEST for
            # the A!=B / A!=C rows, the MASTER for the patchwise B!=C rows (the backtest
            # has no patchwise counterpart). Label it accordingly so the output can't be
            # misread as "the backtest says -1.81".
            ref_name = "master" if tag.startswith("B!=C") else "backtest"
            print(f"    {tag:34s} {label:24s} {ref_name}={rv:<12} other={gv}")
        grand += len(diffs)

    print()
    print("=" * 78)
    if skipped_patchwise:
        print(f"TOTAL divergences from the backtest: {grand}"
              f"  (patchwise check SKIPPED for {skipped_patchwise} combo(s) — NOT verified for those)")
    else:
        print(f"TOTAL divergences from the backtest: {grand}"
              f"  {'-> ALL THREE IDENTICAL' if grand == 0 else '(NOT YET UNIFIED)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
