"""REGRESSION: per-leg spot-adjustment anchor must be re-based only at the
leg's OWN filter-segment starts, never a sibling leg's.

Bug: in run_rust_engine_pipeline the per-leg spot-adjustment mark re-based
EVERY per-leg-SA leg at the STRATEGY-GLOBAL filter-segment starts
(_mark_seg_starts). With Individual Filter each leg carries its own
filter_segments, so a sibling leg's patch boundary wrongly moved this leg's 2%
spot-adj anchor -> the threshold fired on different dates -> different P&L.

Invariant: a monthly PE leg's total P&L must be INDEPENDENT of a sibling weekly
leg's filter. We run the monthly leg beside a weekly CE leg twice (weekly with
vs without its own filter) and require the monthly PE total to match.

Needs the warm NIFTY cache + engine, so it SKIPS on a bare checkout. Opt-in to
keep it out of `unittest discover` (runs 3 full backtests).
Run it on its own:
  docker compose exec -T -e RUN_PERLEG_SA_ANCHOR=1 -w /app worker-backtests \\
      python -m unittest tests.test_perleg_spotadj_anchor
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

FROM, TO = "2019-08-01", "2020-12-31"
_MONTHLY = {
    "expiry": "MONTHLY", "option_type": "PE", "position": "BUY",
    "strike_selection": {"type": "ATM"}, "lots": 1, "exit_dte": 0,
    "spot_adjustment": {"enabled": True, "direction": "both", "pct": 2, "units": "percent"},
}
_WEEKLY = {
    "expiry": "WEEKLY", "option_type": "CE", "position": "SELL",
    "strike_selection": {"type": "ATM"}, "lots": 1, "exit_dte": 0,
    "spot_adjustment": {"enabled": True, "direction": "rise", "pct": 2, "units": "percent"},
}
_WEEKLY_FILT = dict(_WEEKLY, filter_segments=[
    ["2019-08-01", "2019-11-05"], ["2019-11-06", "2020-06-20"], ["2020-06-21", "2020-12-31"],
])


@unittest.skipUnless(
    os.environ.get("RUN_PERLEG_SA_ANCHOR") == "1",
    "opt-in: runs full backtests against the warm cache. Run on its own:\n"
    "  docker compose exec -T -e RUN_PERLEG_SA_ANCHOR=1 -w /app worker-backtests \\\n"
    "      python -m unittest tests.test_perleg_spotadj_anchor",
)
class TestPerLegSpotAdjAnchor(unittest.TestCase):
    def _monthly_pe_total(self, legs):
        from services.algotest_job import execute_algotest_job
        req = {"index": "NIFTY", "from_date": FROM, "to_date": TO, "entry_dte": 0,
               "exit_dte": 0, "no_cache": True, "per_leg_rollover": True,
               "legs": [dict(l) for l in legs]}
        res = execute_algotest_job(req)
        if res.get("status") != "success" or not res.get("trades"):
            self.skipTest(f"engine/data unavailable: {res.get('status')}")
        # Sibling is a CE leg, so PE P&L isolates the monthly leg.
        return round(sum(float(t.get("PE P&L") or 0) for t in res["trades"]), 2)

    def test_monthly_leg_total_independent_of_sibling_filter(self):
        no_filt = self._monthly_pe_total([_WEEKLY, _MONTHLY])
        with_filt = self._monthly_pe_total([_WEEKLY_FILT, _MONTHLY])
        self.assertAlmostEqual(
            no_filt, with_filt, places=2,
            msg=f"sibling weekly filter leaked into monthly-leg spot-adj anchor: "
                f"no_filter={no_filt} with_filter={with_filt}",
        )


if __name__ == "__main__":
    unittest.main()
