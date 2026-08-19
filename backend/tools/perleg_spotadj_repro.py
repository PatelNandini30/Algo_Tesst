"""Synthetic reproduction of the per-leg spot-adjustment anchor bug.

Invariant under test: with per_leg_rollover + Individual Filter, a leg's
spot-adjustment must re-base only at ITS OWN filter-segment starts. A SIBLING
leg's filter boundaries must NOT move this leg's spot-adj anchor.

We hold a monthly PE BUY ATM leg (own spot_adjustment=both 2%) constant and
run it beside a weekly CE SELL leg (own spot_adjustment=rise 2%) TWICE:
  X) weekly leg has NO filter
  Y) weekly leg carries its OWN filter_segments (boundaries distinct from any
     monthly boundary)
The monthly leg's total PE P&L (isolated because the sibling is a CE leg) must
be IDENTICAL in X and Y. Before the fix the weekly filter boundary re-based the
monthly leg's anchor -> mismatch. Uses the WARM cache (no build_cache).

Run: docker compose exec -T -w /app worker-backtests python tools/perleg_spotadj_repro.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from services.algotest_job import execute_algotest_job

FROM, TO = "2019-08-01", "2020-12-31"

MONTHLY_LEG = {
    "expiry": "MONTHLY", "option_type": "PE", "position": "BUY",
    "strike_selection": {"type": "ATM"}, "lots": 1, "exit_dte": 0,
    "spot_adjustment": {"enabled": True, "direction": "both", "pct": 2, "units": "percent"},
}
WEEKLY_BASE = {
    "expiry": "WEEKLY", "option_type": "CE", "position": "SELL",
    "strike_selection": {"type": "ATM"}, "lots": 1, "exit_dte": 0,
    "spot_adjustment": {"enabled": True, "direction": "rise", "pct": 2, "units": "percent"},
}
WEEKLY_FILT = dict(WEEKLY_BASE, filter_segments=[
    ["2019-08-01", "2019-11-05"], ["2019-11-06", "2020-06-20"], ["2020-06-21", "2020-12-31"],
])

def _run(legs):
    req = {"index": "NIFTY", "from_date": FROM, "to_date": TO, "entry_dte": 0,
           "exit_dte": 0, "no_cache": True, "per_leg_rollover": True,
           "legs": [dict(l) for l in legs]}
    res = execute_algotest_job(req)
    if res.get("status") != "success":
        raise SystemExit(f"job failed: {res.get('status')} {res.get('error')}")
    return res["trades"]

def _pe_total(trades):
    return round(sum(float(t.get("PE P&L") or 0) for t in trades), 2)

if __name__ == "__main__":
    x = _pe_total(_run([WEEKLY_BASE, MONTHLY_LEG]))
    y = _pe_total(_run([WEEKLY_FILT, MONTHLY_LEG]))
    print(f"monthly PE total, weekly NO filter  = {x}")
    print(f"monthly PE total, weekly WITH filter = {y}")
    print(f"delta = {round(y - x, 2)}  " + ("MATCH" if abs(y - x) < 0.01 else "MISMATCH"))
