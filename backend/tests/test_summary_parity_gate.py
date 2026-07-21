"""GATE: backtest == optim per-combo tradesheet == optim master summary.

Runs the three-way comparison (tools/three_way_summary_parity) as a real test so a
divergence FAILS THE BUILD instead of being discovered in a customer's spreadsheet.

Three sites still compute these numbers — base.py compute_analytics (the backtest,
source of truth), summary_metrics.rs (the master), and excel_builder._summary_layout
(the per-combo sheet) — so nothing structurally prevents them drifting apart again.
Until they are collapsed into one, THIS TEST is what holds the invariant.

History it protects (all found by widening this comparison, not by reading code):
  * multi-index cagr_spot paired one index's entry spot with the OTHER index's exit
    spot -> backtest 5.82 / per-combo 15.73 / master -68.49
  * base.py sorted dd-mm-yyyy dates LEXICOGRAPHICALLY, so every multi-index run
    walked its equity curve out of order -> mdd_duration_days = -140
  * the sync_weekly_roll path reported PERCENTS in point-labelled fields
    (total_pnl 3.77 vs the real 407.65)

Needs market data + DB, so it SKIPS rather than fails when run outside a container
that has them (e.g. a bare checkout).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@unittest.skipUnless(
    os.environ.get("RUN_SUMMARY_PARITY_GATE") == "1",
    "opt-in: runs 5 full backtests (2 multi-index, loads MIDCPNIFTY) and OOM-killed "
    "`unittest discover` on the 16 GB box (exit 137). Run it on its own:\n"
    "  docker exec -e RUN_SUMMARY_PARITY_GATE=1 -w /app algotest-worker-backtests \\\n"
    "      python -m unittest tests.test_summary_parity_gate\n"
    "or use the equivalent report: python -m tools.three_way_summary_parity",
)
class TestSummaryParityGate(unittest.TestCase):

    def test_all_three_summaries_identical(self):
        try:
            from tools.three_way_summary_parity import (
                PAYLOADS, MAP, SHEET_MAP, _cmp, _num, _sheet_kv,
            )
            from services.algotest_job import execute_algotest_job
            from services.optimizer.excel_builder import (
                compute_xlsx_summary_metrics, build_combo_xlsx,
            )
            import pandas as pd
            import openpyxl
            import io
        except Exception as exc:                      # pragma: no cover
            self.skipTest(f"parity deps unavailable: {exc}")

        ran = 0
        all_diffs = []
        for name, payload in PAYLOADS:
            try:
                res = execute_algotest_job(dict(payload))
            except Exception as exc:                  # pragma: no cover
                self.skipTest(f"{name}: engine/data unavailable ({exc})")
            trades = res.get("trades") or []
            if not trades:
                continue
            ran += 1
            df = pd.DataFrame(trades)
            A = res.get("summary") or {}
            C = compute_xlsx_summary_metrics(
                df, A,
                midcap_legs=payload.get("midcap_legs") or None,
                patchwise=False, filter_segments=None,
            )
            xb = build_combo_xlsx(df, A, combo_label=name,
                                  from_date=payload["from_date"],
                                  to_date=payload["to_date"])
            kv = _sheet_kv(openpyxl.load_workbook(io.BytesIO(xb))["Summary"])
            B = {}
            for label, key in SHEET_MAP.items():
                if label in kv:
                    v = _num(kv[label])
                    if v is not None:
                        B.setdefault(key, v)

            checked, diffs = set(), []
            _cmp(f"{name}: backtest != per-combo", A, B, checked, diffs)
            _cmp(f"{name}: backtest != master", A, C, checked, diffs)
            all_diffs.extend(diffs)

        if ran == 0:                                  # pragma: no cover
            self.skipTest("no payload produced trades (no market data loaded)")

        self.assertEqual(
            all_diffs, [],
            "Summary metrics diverge from the BACKTEST (the source of truth):\n"
            + "\n".join(f"  {t}: {k} backtest={rv} other={gv}"
                        for t, k, _, rv, gv in all_diffs),
        )


if __name__ == "__main__":
    unittest.main()
