"""TEMP inspector for the multi-index non-yearly overlay-SA fused route.
Dumps real row keys / exit reasons so we don't rely on guessed field names.
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
        "entry_dte": 30,
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
        "entry_dte": 30,
        "exit_dte": 1,
        "legs": [l1, l2],
    }


def dump_all(tag, mcn_sa):
    res = execute_algotest_job(_payload(mcn_sa))
    meta = res.get("meta", {}) or {}
    trades = res.get("trades", []) or []
    print(f"\n===== {tag} =====", flush=True)
    print("status:", res.get("status"), "| meta.fused:", meta.get("fused"),
          "| indices:", meta.get("indices"), "| cadence:", meta.get("cadence"),
          "| n_rows:", len(trades), flush=True)
    if trades:
        print("ROW KEYS:", sorted(trades[0].keys()), flush=True)
    # pick the fields that actually exist
    def g(t, *names):
        for n in names:
            if n in t and t[n] not in (None, ""):
                return t[n]
        return ""
    print("--- ALL ROWS ---", flush=True)
    for t in trades:
        print(json.dumps({
            "Trade": g(t, "Trade", "Trade #", "trade_no"),
            "Idx": g(t, "Group Index", "Index", "index", "Symbol", "symbol"),
            "Leg": g(t, "Leg", "Leg #", "leg_no"),
            "Type": g(t, "Type", "Option Type", "option_type"),
            "Strike": g(t, "Strike", "strike"),
            "In": g(t, "Entry Date", "entry_date"),
            "Out": g(t, "Exit Date", "exit_date"),
            "Reason": g(t, "Exit Reason", "exit_reason", "ExitReason"),
        }), flush=True)
    return res


if __name__ == "__main__":
    dump_all("FUSED (MCN SA enabled)", True)
    dump_all("NON-REGRESSION (MCN SA disabled)", False)
    print("\nDONE", flush=True)
