"""Prove algotest_native.compute_summary_metrics is leg-order invariant.

Synthesizes ONE trade with two main legs (no re-entry flags) — a carried leg
with an OLDER Entry Date and a fresh leg with a NEWER Entry Date, same shape
as a carried-YEARLY + fresh-WEEKLY pair — and calls the real Rust summary
engine with the legs in both orders. Entry Spot (the %P&L/NAV denominator)
must be identical either way.

    python -m tools.rust_anchor_check
"""
import pandas as pd

from services.optimizer.excel_builder import compute_xlsx_summary_metrics


def _row(leg, entry_date, entry_spot, net_pnl):
    return {
        "Trade": 1, "Leg": leg, "B/S": "SELL", "Type": "CE",
        "Net P&L": net_pnl, "CE P&L": net_pnl, "PE P&L": 0.0, "FUT P&L": 0.0,
        "Entry Spot": entry_spot, "Exit Spot": entry_spot, "Spot P&L": 0.0,
        "MAE": 0.0, "MFE": 0.0,
        "Entry Date": entry_date, "Exit Date": "2024-02-01",
        "Exit Reason": "EXPIRY", "%DD": 0.0,
    }


def main() -> int:
    old_leg = _row(1, "2023-06-01", 20000.0, 100.0)   # carried, OLDER entry
    new_leg = _row(2, "2024-01-25", 22000.0, 50.0)    # fresh, NEWER entry

    for name, order in (("old-first (Leg1,Leg2)", [old_leg, new_leg]),
                         ("new-first (Leg2,Leg1)", [new_leg, old_leg])):
        df = pd.DataFrame(order)
        m = compute_xlsx_summary_metrics(df, {}, patchwise=False)
        print(f"  {name:24} entry_spot-derived cagr_options={m.get('cagr_options')} "
              f"max_dd_pct={m.get('max_dd_pct')}")

    df1 = pd.DataFrame([old_leg, new_leg])
    df2 = pd.DataFrame([new_leg, old_leg])
    m1 = compute_xlsx_summary_metrics(df1, {}, patchwise=False)
    m2 = compute_xlsx_summary_metrics(df2, {}, patchwise=False)
    diffs = {k: (m1.get(k), m2.get(k)) for k in m1 if m1.get(k) != m2.get(k)}
    if diffs:
        print("\nVERDICT: LEG-ORDER DEPENDENT —", diffs)
        return 1
    print("\nVERDICT: ORDER-INVARIANT (Rust engine)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
