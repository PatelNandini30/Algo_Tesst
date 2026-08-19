"""Smoke-run one backtest through the engine, in-process.

Deliberately NOT via the API: services/maintenance.py gates only the submit
endpoints, so calling execute_algotest_job directly is the documented way to
test locally while the maintenance lock keeps every other machine out.

    python -m tools.smoke_backtest
"""
import time

from services.algotest_job import execute_algotest_job
from services.optimizer import result_store as rs

REF_JOB = "788dbf1f-898d-472f-a35a-850576b44ace"


def main() -> int:
    bp = dict((rs.get_meta(REF_JOB) or {}).get("base_payload") or {})
    if not bp:
        raise SystemExit("reference payload unavailable")
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

    t = time.time()
    res = execute_algotest_job(bp)
    print("elapsed %.1fs" % (time.time() - t))
    trades = res.get("trades") or res.get("tradesheet") or []
    summary = res.get("summary") or {}
    print("trades:", len(trades))
    for k in ("total_pnl", "count", "max_dd_pct", "cagr_options", "car_mdd", "net_pnl"):
        if k in summary:
            print("  %-14s %s" % (k, summary[k]))
    if not trades:
        # A 0-trade result is exactly the failure mode audit finding #6 describes:
        # a refusal reported as a clean, empty backtest. Say so loudly.
        print("WARNING: 0 trades — verify this is genuine and not a silent engine refusal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
