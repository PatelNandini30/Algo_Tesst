import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.optimizer.excel_builder import _aggregate_trades  # noqa: E402


class TestOptimizerExcelBuilder(unittest.TestCase):
    def test_cumulative_peak_dd_follow_visible_trade_formula_order(self):
        rows = [
            {
                "Trade": 1,
                "Leg": 1,
                "Entry Date": "2019-02-20",
                "Entry Spot": 10735.45,
                "Net P&L": -124.15,
                "MAE": -1.1988,
            },
            {
                "Trade": 2,
                "Leg": 1,
                "Entry Date": "2019-02-27",
                "Entry Spot": 10806.65,
                "Net P&L": 0,
                "MAE": 6.9143,
            },
            {
                "Trade": 3,
                "Leg": 1,
                "Entry Date": "2019-03-06",
                "Entry Spot": 11053,
                "Net P&L": 0,
                "MAE": 4.9204,
            },
            {
                "Trade": 4,
                "Leg": 1,
                "Entry Date": "2019-03-19",
                "Entry Spot": 11532.4,
                "Net P&L": 187,
                "MAE": -0.2437,
            },
            {
                "Trade": 5,
                "Leg": 1,
                "Entry Date": "2019-04-10",
                "Entry Spot": 11584.3,
                "Net P&L": -153.7,
                "MAE": -1.4692,
            },
            {
                "Trade": 99,
                "Leg": 1,
                "Entry Date": "2019-03-12",
                "Entry Spot": 11250,
                "Net P&L": 112.5,
                "MAE": -0.5,
            },
        ]

        tm, _ = _aggregate_trades(rows)

        sorted_rows = sorted(rows, key=lambda r: (r["Entry Date"], r["Trade"], r["Leg"]))
        expected = []
        cumulative = 100.0
        peak = 100.0
        for row in sorted_rows:
            pct = row["Net P&L"] / row["Entry Spot"] * 100.0
            cumulative *= 1.0 + pct / 100.0
            peak = max(peak, cumulative)
            dd = cumulative - peak if peak > cumulative else ""
            pct_dd = dd / peak if dd != "" else 0
            expected.append((str(row["Trade"]), cumulative, peak, dd, pct_dd))

        for trade_id, cum, peak, dd, pct_dd in expected:
            got = tm[trade_id]
            self.assertAlmostEqual(got["cumulative"], cum)
            self.assertAlmostEqual(got["peak"], peak)
            if dd == "":
                self.assertEqual(got["dd"], "")
            else:
                self.assertAlmostEqual(got["dd"], dd)
            self.assertAlmostEqual(got["pctDd"], pct_dd)


if __name__ == "__main__":
    unittest.main()
