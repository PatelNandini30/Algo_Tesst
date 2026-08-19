"""Did every CONFIGURED leg actually trade, in every trade?

multi_index_feature drops a leg it cannot resolve (e.g. a strike mode that has no
answer for that index/expiry), logs a WARNING, and lets the run finish normally.
Nothing in the summary or the tradesheet says a configured leg is missing, so an
"NIFTY + MIDCPNIFTY" result can be NIFTY-only for most of its trades while
reporting full stats.

The tradesheet has no per-row symbol column — `Index` is the leg NUMBER and
`Group Index` is the index name — so participation is measured by Leg number.

    LEGORDER_JOB=<job_id> python -m tools.leg_participation_check
"""
import collections
import os

from services.algotest_job import execute_algotest_job
from services.optimizer import result_store as rs


def main() -> int:
    job = os.getenv("LEGORDER_JOB") or ""
    bp = dict((rs.get_meta(job) or {}).get("base_payload") or {})
    if not bp:
        raise SystemExit("set LEGORDER_JOB to a job whose meta is still in Redis")
    for k in ("start_date", "date_from", "from_date"):
        if k in bp:
            bp[k] = os.getenv("BT_FROM", "2024-01-01")
    for k in ("end_date", "date_to", "to_date"):
        if k in bp:
            bp[k] = os.getenv("BT_TO", "2024-06-30")
    # The filter is stripped so a window outside the filter segments cannot be
    # mistaken for a dropped leg (it produced a false positive exactly that way).
    bp.pop("filter_segments", None)
    bp.pop("str_filter", None)
    bp["filter"] = None
    bp["filter_config"] = None

    legs = bp.get("legs") or []
    rows = execute_algotest_job(bp).get("trades") or []
    trades = {r.get("Trade") for r in rows}
    by_leg = collections.Counter(r.get("Leg") for r in rows)

    print("trades: %d | leg rows: %d" % (len(trades), len(rows)))
    bad = 0
    for i, l in enumerate(legs, 1):
        got = by_leg.get(i, 0)
        pct = (100.0 * got / len(trades)) if trades else 0.0
        flag = "OK" if got == len(trades) else "*** MISSING in %d trade(s)" % (len(trades) - got)
        bad += 0 if got == len(trades) else 1
        print("  Leg%d %-11s %-8s %-4s  rows=%-4d (%5.1f%% of trades)  %s"
              % (i, l.get("symbol") or l.get("index"), l.get("segment"),
                 l.get("option_type") or "FUT", got, pct, flag))
    if bad:
        print("\nVERDICT: %d configured leg(s) did not trade in every trade, yet the run "
              "reports a normal successful backtest with full stats." % bad)
    else:
        print("\nVERDICT: every configured leg traded in every trade.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
