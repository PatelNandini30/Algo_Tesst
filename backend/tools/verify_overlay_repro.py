"""Faithful repro of the user's multi-index overlay-SA config (WITH a filter so
windows are multi-day). Read-only; does not touch any feather.
Run: docker compose exec -T -w /app -e PYTHONPATH=/app worker-backtests python tools/verify_overlay_repro.py
"""
import json
from services.algotest_job import execute_algotest_job


def _leg(index, opt, sa_dir, sa_pct):
    return {
        "segment": "OPTIONS", "index": index, "option_type": opt, "position": "SELL",
        "lots": 1, "expiry": "MONTHLY", "strike_interval": 100,
        "entry_dte": 30, "exit_dte": 1,
        "strike_selection": {"type": "STRADDLE_WIDTH", "strike_type": "ATM",
                             "strike_interval": 100, "straddle_multiplier": 0.5,
                             "straddle_direction": "+"},
        "straddle_multiplier": 0.5, "straddle_direction": "+",
        "rollover_strike_mode": "fresh", "slippage_pct": 1,
        "spot_adjustment": {"enabled": True, "direction": sa_dir, "pct": sa_pct, "units": "percent"},
    }


def _payload():
    return {
        "index": "NIFTY", "from_date": "2024-11-22", "to_date": "2025-06-30",
        "square_off_mode": "partial", "slippage_pct": 1, "charges_enabled": False,
        "multi_index_mode": True, "sync_weekly_roll": True,
        "entry_dte": 30, "exit_dte": 1, "rollover_min_days_to_expiry": 0,
        # One patch over the whole range -> filter-driven entry + monthly rollovers inside.
        "filter_config": "custom",
        "filter_segments": [{"start": "2024-11-22", "end": "2025-06-30"}],
        "legs": [_leg("NIFTY", "CE", "rise", 1.0), _leg("MIDCPNIFTY", "PE", "fall", 0.5)],
    }


def g(t, *names):
    for n in names:
        if n in t and t[n] not in (None, ""):
            return t[n]
    return ""


res = execute_algotest_job(_payload())
meta = res.get("meta", {}) or {}
trades = res.get("trades", []) or []
print("status:", res.get("status"), "| meta.fused:", meta.get("fused"),
      "| indices:", meta.get("indices"), "| n_rows:", len(trades))
nifty_sa = mcn_sa = multiday = 0
for t in trades:
    idx = g(t, "Index", "Group Index", "Symbol", "index")
    typ = g(t, "Type", "option_type")
    reason = str(g(t, "Exit Reason", "exit_reason"))
    ein = str(g(t, "Entry Date", "entry_date"))[:10]
    eout = str(g(t, "Exit Date", "exit_date"))[:10]
    if ein and eout and ein != eout:
        multiday += 1
    if "SPOT_ADJ" in reason and typ == "CE":
        nifty_sa += 1
    if "SPOT_ADJ" in reason and typ == "PE":
        mcn_sa += 1
    print(json.dumps({"Leg": g(t, "Leg"), "Idx?": idx, "Type": typ,
                      "Strike": g(t, "Strike"), "In": ein, "Out": eout, "Reason": reason}))
print("\nSUMMARY: multiday_windows=%d  NIFTY_SPOT_ADJ_rows=%d  MCN_SPOT_ADJ_rows=%d" %
      (multiday, nifty_sa, mcn_sa))
print("EXPECT (bug reproduced representatively): multiday>0, NIFTY_SPOT_ADJ>0, MCN_SPOT_ADJ==0")
