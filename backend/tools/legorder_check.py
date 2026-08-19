"""Run the SAME strategy twice with the legs in different orders and diff the stats.

Leg ORDER must never change a statistic (services/trade_anchor.py). This is the
runnable check for that invariant on the real engine, not on synthetic rows.

    python -m tools.legorder_check
"""
from services.algotest_job import execute_algotest_job
from services.optimizer import result_store as rs

import os
# Override with LEGORDER_JOB to test another strategy shape. The multi-index
# NIFTY + MIDCPNIFTY case is the one the user reported: swapping which index
# leads must not move a single statistic.
REF_JOB = os.getenv("LEGORDER_JOB", "788dbf1f-898d-472f-a35a-850576b44ace")
WATCH = ("total_pnl", "count", "max_dd_pct", "cagr_options", "car_mdd",
         "actual_live_dd_max", "actual_live_dd_avg", "net_pnl")


def _payload(order):
    bp = dict((rs.get_meta(REF_JOB) or {}).get("base_payload") or {})
    for k in ("start_date", "date_from", "from_date"):
        if k in bp:
            bp[k] = "2024-01-01"
    for k in ("end_date", "date_to", "to_date"):
        if k in bp:
            bp[k] = "2024-06-30"
    bp.pop("filter_segments", None)
    bp.pop("str_filter", None)
    bp["filter"] = None
    bp["filter_config"] = None
    legs = list(bp.get("legs") or [])
    bp["legs"] = [legs[i] for i in order]
    return bp


def main() -> int:
    n = len((rs.get_meta(REF_JOB) or {}).get("base_payload", {}).get("legs") or [])
    forward, reverse = list(range(n)), list(reversed(range(n)))
    bp = (rs.get_meta(REF_JOB) or {}).get("base_payload") or {}
    print("legs (forward order):")
    for i, l in enumerate(bp.get("legs") or [], 1):
        print("   L%d %s %s %s %s" % (i, l.get("symbol") or l.get("index"),
              l.get("segment"), l.get("option_type"), l.get("expiry")))
    a = execute_algotest_job(_payload(forward)).get("summary") or {}
    b = execute_algotest_job(_payload(reverse)).get("summary") or {}
    bad = 0
    for k in WATCH:
        if k not in a and k not in b:
            continue
        same = a.get(k) == b.get(k)
        bad += 0 if same else 1
        print("  %-20s forward=%-16s reversed=%-16s %s"
              % (k, a.get(k), b.get(k), "OK" if same else "*** DIFFERS ***"))
    print("VERDICT:", "ORDER-INVARIANT" if not bad else f"{bad} STAT(S) DEPEND ON LEG ORDER")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
