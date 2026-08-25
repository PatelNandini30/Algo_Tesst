"""Analyst verification of the MCN own-SA fix.
Run: docker compose exec -T -w /app -e PYTHONPATH=/app worker-backtests python tools/verify_overlay_sa.py
"""
import sys
from services.algotest_job import execute_algotest_job

def _leg(index, opt, sa_dir, sa_pct):
    return {
        "segment": "OPTIONS", "index": index, "option_type": opt, "position": "SELL",
        "lots": 1, "expiry": "MONTHLY", "strike_interval": 100,
        "entry_dte": 1, "exit_dte": 1, "rollover_min_days_to_expiry": 0,
        "strike_selection": {"type": "STRADDLE_WIDTH", "strike_type": "ATM",
                             "strike_interval": 100, "straddle_multiplier": 0.5,
                             "straddle_direction": "+"},
        "straddle_multiplier": 0.5, "straddle_direction": "+",
        "rollover_strike_mode": "fresh", "slippage_pct": 1,
        "spot_adjustment": {"enabled": True, "direction": sa_dir, "pct": sa_pct, "units": "percent"},
    }

payload = {
    "index": "NIFTY", "from_date": "2024-11-22", "to_date": "2025-06-25",
    "square_off_mode": "partial", "slippage_pct": 1, "charges_enabled": False,
    "multi_index_mode": True, "sync_weekly_roll": True,
    "entry_dte": 1, "exit_dte": 1, "rollover_min_days_to_expiry": 0,
    "filter_config": "custom",
    "filter_segments": [
        {"start": "2024-11-22", "end": "2025-01-07"},
        {"start": "2025-03-28", "end": "2025-06-25"},
    ],
    "legs": [_leg("NIFTY", "CE", "rise", 1.0), _leg("MIDCPNIFTY", "PE", "fall", 0.5)],
}

res = execute_algotest_job(payload)
trades = res.get("trades", []) or []

def g(t, *n):
    for x in n:
        if x in t and t[x] not in (None, ""):
            return t[x]
    return ""

print("=" * 80)
print("BACKTEST: NIFTY CE rise-1% + MCN PE fall-0.5% | T-1 | 2 filter patches")
print("status=%s  fused=%s  total_rows=%d" % (res.get("status"), res.get("meta",{}).get("fused"), len(trades)))
print("=" * 80)

by_trade = {}
for t in trades:
    tid = g(t, "Trade")
    by_trade.setdefault(tid, []).append(t)

mcn_sa = nifty_sa = 0
anomalies = []

for tid in sorted(by_trade, key=lambda x: str(x)):
    rows = by_trade[tid]
    print("\nTRADE %s" % tid)
    for r in rows:
        typ   = g(r, "Type", "option_type")
        strk  = g(r, "Strike")
        ein   = str(g(r, "Entry Date", "entry_date"))[:10]
        eout  = str(g(r, "Exit Date",  "exit_date"))[:10]
        espot = g(r, "Entry Spot")
        xspot = g(r, "Exit Spot")
        reason= str(g(r, "Exit Reason"))
        pnl   = g(r, "Net P&L")
        flag  = ""
        if "SPOT_ADJ" in reason:
            if typ == "PE": mcn_sa += 1
            else: nifty_sa += 1
            baseline = float(espot) if espot else 0.0
            trig_sp  = float(xspot) if xspot else 0.0
            if baseline > 0:
                pct = (trig_sp - baseline) / baseline * 100.0
                thr = -0.5 if typ == "PE" else 1.0
                ok  = (pct <= thr) if typ == "PE" else (pct >= thr)
                flag = "  OK spot %.0f->%.0f (%+.2f%% vs %+.1f%%)" % (baseline, trig_sp, pct, thr) if ok \
                       else "  ANOMALY: spot %.0f->%.0f (%+.2f%%) missed %.1f%%" % (baseline, trig_sp, pct, thr)
                if not ok: anomalies.append("T%s %s: %+.2f%% vs %.1f%%"%(tid,typ,pct,thr))
        print("  %s str=%-7s %s->%s espot=%-7s P&L=%-8s  %s%s" % (typ, strk, ein, eout, espot, pnl, reason, flag))

print("\n" + "=" * 80)
print("NIFTY SA rows: %d  |  MCN SA rows: %d  |  Anomalies: %d" % (nifty_sa, mcn_sa, len(anomalies)))
for a in anomalies: print("  ANOMALY:", a)
if mcn_sa > 0 and not anomalies: print("All MCN SA triggers verified - spot crossed threshold on every breach")
elif mcn_sa == 0: print("MCN SA never fired")
