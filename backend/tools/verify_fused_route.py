"""TEMP verification harness for the multi-index non-yearly overlay-SA fused route.
Runs the engine in-process (fresh code), NOT via the stale Celery worker.
Removed after review. Does not write/rebuild any feather.
"""
import json
import sys

from services.algotest_job import execute_algotest_job


def _leg(index, opt, position, sa_dir, sa_pct):
    return {
        "segment": "OPTIONS",
        "index": index,
        "option_type": opt,
        "position": position,
        "lots": 1,
        "expiry": "MONTHLY",
        "strike_interval": 50 if index == "NIFTY" else 25,
        "entry_dte": 1,
        "exit_dte": 1,
        "strike_selection": {
            "type": "STRADDLE_WIDTH",
            "strike_type": "ATM",
            "strike_interval": 50 if index == "NIFTY" else 25,
            "straddle_multiplier": 0.5,
            "straddle_direction": "+",
        },
        "straddle_multiplier": 0.5,
        "straddle_direction": "+",
        "spot_adjustment": {"enabled": True, "direction": sa_dir, "pct": sa_pct, "units": "percent"},
    }


def _payload(mcn_sa_enabled):
    l1 = _leg("NIFTY", "CE", "SELL", "rise", 1.0)
    l2 = _leg("MIDCPNIFTY", "PE", "SELL", "fall", 0.5)
    if not mcn_sa_enabled:
        l2["spot_adjustment"] = {"enabled": False}
    return {
        "index": "NIFTY",
        "from_date": "2024-01-01",
        "to_date": "2024-12-31",
        "square_off_mode": "partial",
        "slippage_pct": 0,
        "multi_index_mode": True,
        "sync_weekly_roll": True,
        "entry_dte": 1,
        "exit_dte": 1,
        "legs": [l1, l2],
    }


def run(tag, mcn_sa):
    res = execute_algotest_job(_payload(mcn_sa))
    meta = res.get("meta", {}) or {}
    trades = res.get("trades", []) or []
    print(f"=== {tag} ===")
    print("status:", res.get("status"))
    print("meta.fused:", meta.get("fused"))
    print("meta.indices:", meta.get("indices"))
    print("meta.cadence:", meta.get("cadence"), "cadence_index:", meta.get("cadence_index"))
    print("n_trade_rows:", len(trades))
    if trades:
        tnums = sorted({int(t.get("Trade") or 0) for t in trades})
        print("distinct trades:", len(tnums), "range:", tnums[0], "..", tnums[-1])
    return res


def dump_mcn_sa(res):
    trades = res.get("trades", []) or []
    # rows where MCN leg breached on its own -0.5% move
    print("--- MCN SPOT_ADJ_FALL rows (MCN's own breach) ---")
    n = 0
    for t in trades:
        er = str(t.get("Exit Reason") or "")
        gi = str(t.get("Group Index") or "")
        if "MIDCPNIFTY" in er and "SPOT_ADJ_FALL" in er and gi == "MIDCPNIFTY":
            print(json.dumps({
                "Trade": t.get("Trade"), "Group Index": gi, "Type": t.get("Type"),
                "Strike": t.get("Strike"), "Entry Date": t.get("Entry Date"),
                "Exit Date": t.get("Exit Date"), "Exit Reason": er,
            }))
            n += 1
            if n >= 4:
                break
    if not n:
        print("NONE")


def dump_coexit(res, direction_leg_index, tag):
    """Find a trade whose exit reason is a spot-adj breach driven by direction_leg_index;
    show both legs' rows for that Trade# to prove co-exit + which re-strikes."""
    trades = res.get("trades", []) or []
    by_trade = {}
    for t in trades:
        by_trade.setdefault(int(t.get("Trade") or 0), []).append(t)
    print(f"--- CO-EXIT proof ({tag}) ---")
    shown = 0
    for tn in sorted(by_trade):
        rows = by_trade[tn]
        ers = {str(r.get("Exit Reason") or "") for r in rows}
        joined = " | ".join(ers)
        if "SPOT_ADJ" in joined and direction_leg_index in joined:
            # need a NEXT trade to compare strikes for re-strike proof
            for r in rows:
                print(json.dumps({
                    "Trade": r.get("Trade"), "Group Index": r.get("Group Index"),
                    "Type": r.get("Type"), "Strike": r.get("Strike"),
                    "Entry Date": r.get("Entry Date"), "Exit Date": r.get("Exit Date"),
                    "Exit Reason": r.get("Exit Reason"),
                }))
            # show the immediately following trade's strikes (re-strike proof)
            nxt = [x for x in sorted(by_trade) if x > tn]
            if nxt:
                print("  next trade rows (strikes after re-anchor):")
                for r in by_trade[nxt[0]]:
                    print("   ", json.dumps({
                        "Trade": r.get("Trade"), "Group Index": r.get("Group Index"),
                        "Type": r.get("Type"), "Strike": r.get("Strike"),
                        "Entry Date": r.get("Entry Date"),
                    }))
            shown += 1
            if shown >= 1:
                break
    if not shown:
        print("no co-exit trade driven by", direction_leg_index)


if __name__ == "__main__":
    r_on = run("FUSED CONFIG (MCN SA enabled)", True)
    dump_mcn_sa(r_on)
    dump_coexit(r_on, "NIFTY", "NIFTY-rise breach")
    dump_coexit(r_on, "MIDCPNIFTY", "MCN-fall breach")
    print()
    r_off = run("NON-REGRESSION (MCN SA disabled)", False)
