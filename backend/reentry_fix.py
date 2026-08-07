"""Verify: leg-wise spot adjustment under FIXED entry now re-enters same day.

Defect: cascade at :6363 carried `and filter_entry_mode != "fixed"`, and the
per-leg block at :6616 reads a never-written map -> breach exited, nothing re-entered.
"""
import warnings; warnings.filterwarnings("ignore")
import collections, sys
from services.algotest_job import execute_algotest_job

MODE = sys.argv[1] if len(sys.argv) > 1 else "fixed"

def leg(ot, pos, pct, units):
    return {
        "segment": "OPTIONS", "index": "NIFTY", "option_type": ot, "position": pos,
        "lots": 1, "expiry": "WEEKLY", "strike_interval": 50,
        "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
        "spot_adjustment": {"enabled": True, "pct": pct, "units": units,
                            "direction": "rise"},
    }

payload = {
    "index": "NIFTY", "from_date": "2019-01-01", "to_date": "2020-06-30",
    "strategy_type": "positional", "underlying": "cash",
    "expiry_type": "WEEKLY", "entry_dte": 0, "exit_dte": 0,
    "rollover_toggle": True, "filter_entry_mode": MODE,
    "slippage_pct": 0, "charges_enabled": False, "square_off_mode": "partial",
    "no_cache": True,
    "legs": [leg("CE", "SELL", 1, "percent"), leg("PE", "BUY", 1000, "points")],
}

res = execute_algotest_job(dict(payload))
tr = res.get("trades") or []
by = collections.OrderedDict()
for t in tr:
    by.setdefault(t["Trade"], []).append(t)
import datetime
def _d(x):
    return datetime.datetime.strptime(x, "%d-%m-%Y")
ks = sorted(by, key=lambda k: (_d(by[k][0]["Entry Date"]), k))  # CHRONOLOGICAL
print("=" * 70)
print("entry mode: %s   trades: %d   legs: %d" % (MODE, len(ks), len(tr)))

sa = [k for k in ks if any("SPOT_ADJ" in (x.get("Exit Reason") or "") for x in by[k])]
same = miss = 0
gaps = []
for i, k in enumerate(ks[:-1]):
    if k not in sa:
        continue
    ex = by[k][0]["Exit Date"]
    nxt = by[ks[i + 1]][0]["Entry Date"]
    if nxt == ex:
        same += 1
    else:
        miss += 1
        gaps.append((k, ex, nxt))
print("  SPOT_ADJ exits            : %d" % len(sa))
print("  ...with same-day re-entry : %d" % same)
print("  ...with a GAP (the bug)   : %d" % miss)
for g in gaps[:8]:
    print("      trade %-4s exit %-11s next entry %-11s" % g)

# duplicate guard: no two trades may share the same entry date AND strike set
seen = collections.Counter()
for k in ks:
    sig = (by[k][0]["Entry Date"], tuple(sorted(x.get("Strike") for x in by[k])))
    seen[sig] += 1
dupes = [s for s, c in seen.items() if c > 1]
print("  duplicate (entry,strikes) : %d  %s" % (len(dupes), dupes[:3]))

print("\n  first 12 trades")
for k in ks[:12]:
    L = by[k]
    print("   %-4s %-11s -> %-11s  %s  %s" % (
        k, L[0]["Entry Date"], L[0]["Exit Date"],
        [x.get("Strike") for x in L],
        "|".join(sorted({x.get("Exit Reason", "") for x in L}))))
