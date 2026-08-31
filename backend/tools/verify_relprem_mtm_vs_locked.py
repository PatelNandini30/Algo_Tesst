"""Diff rel_leg_premium child strikes between MTM ('premium') and Locked
('premium_locked') for a monthly-CE ref + weekly-PE child config.
Read-only. Run:
  docker compose exec -T -w /app -e PYTHONPATH=/app worker-backtests \
    python tools/verify_relprem_mtm_vs_locked.py
"""
from services.algotest_job import execute_algotest_job


def _payload(mode):
    return {
        "index": "NIFTY", "from_date": "2024-01-01", "to_date": "2024-05-31",
        "square_off_mode": "partial", "slippage_pct": 0, "charges_enabled": False,
        "entry_dte": 1, "exit_dte": 1, "rollover_min_days_to_expiry": 0,
        "filter_config": "custom",
        "filter_segments": [{"start": "2024-01-01", "end": "2024-05-31"}],
        "legs": [
            {"segment": "OPTIONS", "index": "NIFTY", "option_type": "CE",
             "position": "SELL", "lots": 1, "expiry": "MONTHLY",
             "strike_interval": 50, "entry_dte": 25, "exit_dte": 1,
             "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
             "rollover_strike_mode": "fresh"},
            {"segment": "OPTIONS", "index": "NIFTY", "option_type": "PE",
             "position": "SELL", "lots": 1, "expiry": "WEEKLY",
             "strike_interval": 50, "entry_dte": 1, "exit_dte": 1,
             "strike_selection": {"type": "rel_leg_premium", "ref_leg": 1,
                                  "rel_ref_mode": mode},
             "rollover_strike_mode": "fresh"},
        ],
    }


def g(t, *names):
    for n in names:
        if n in t and t[n] not in (None, ""):
            return t[n]
    return ""


def pe_strikes(mode):
    res = execute_algotest_job(_payload(mode))
    out = {}
    for t in res.get("trades", []) or []:
        if str(g(t, "Type", "option_type")).upper() != "PE":
            continue
        ein = str(g(t, "Entry Date", "entry_date"))[:10]
        out[ein] = g(t, "Strike", "strike")
    return res.get("status"), out


s1, mtm = pe_strikes("premium")
s2, locked = pe_strikes("premium_locked")
print(f"status mtm={s1} locked={s2}  PE rows: mtm={len(mtm)} locked={len(locked)}")
keys = sorted(set(mtm) | set(locked))
diffs = 0
for k in keys:
    a, b = mtm.get(k), locked.get(k)
    flag = "" if a == b else "  <-- DIFF"
    if a != b:
        diffs += 1
    print(f"{k}  mtm={a!s:>9}  locked={b!s:>9}{flag}")
print(f"\nTOTAL PE rows compared={len(keys)}  DIFFERING strikes={diffs}")
