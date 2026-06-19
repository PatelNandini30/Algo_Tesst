"""Tests for services.optimizer.metrics."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd  # noqa: E402

from services.optimizer.metrics import (  # noqa: E402
    actual_live_dd,
    car_mdd_live,
    compute_optim_metrics,
    leg_pct_no_outliers,
    outlier_stripped_live_dd,
    per_leg_pnl,
    roi_vs_spot,
)


def _trades(rows):
    return pd.DataFrame(rows)


class TestPerLegPnL(unittest.TestCase):
    def test_ce_pe_spot_sums(self):
        df = _trades(
            [
                {"Entry Spot": 100, "Exit Spot": 102, "Call P&L": 5, "Put P&L": -2, "Spot P&L": 2},
                {"Entry Spot": 100, "Exit Spot": 105, "Call P&L": 10, "Put P&L": 0, "Spot P&L": 5},
            ]
        )
        out = per_leg_pnl(df)
        self.assertEqual(out["ce_pnl_total"], 15)
        self.assertEqual(out["pe_pnl_total"], -2)
        self.assertEqual(out["long_spot_pnl"], 7)
        # 15 / 100 * 100 = 15.0
        self.assertEqual(out["ce_pnl_pct"], 15.0)
        self.assertEqual(out["pe_pnl_pct"], -2.0)
        self.assertEqual(out["long_spot_pnl_pct"], 7.0)


class TestROIVsSpot(unittest.TestCase):
    def test_basic_ratio(self):
        self.assertAlmostEqual(roi_vs_spot({"total_pnl": 50, "spot_change": 100}), 50.0)

    def test_zero_spot_change(self):
        self.assertEqual(roi_vs_spot({"total_pnl": 50, "spot_change": 0}), 0.0)


class TestLiveDD(unittest.TestCase):
    def test_uses_lowest_nav_minus_prev_peak(self):
        # Research-verified rule: Live DD is measured against the PREVIOUS trade's
        # peak (AX = AW - AV_prev), not the trade's own peak. The first trade's
        # prior peak is seeded at 100.
        df = _trades(
            [
                {"Lowest NAV During Trade": 100, "Peak": 100},  # live = 100-100 = 0
                {"Lowest NAV During Trade": 98, "Peak": 101},   # live = 98-100  = -2
                {"Lowest NAV During Trade": 99, "Peak": 102},   # live = 99-101  = -2
            ]
        )
        out = actual_live_dd(df)
        self.assertEqual(out["actual_live_dd_max"], -2.0)
        # Avg = (0 + -2 + -2) / 3 = -1.3333
        self.assertEqual(out["actual_live_dd_avg"], -1.3333)

    def test_car_mdd_live(self):
        # (cagr/100) / |dd| — dd is in NAV points (e.g. -4.0 = 4 points)
        self.assertAlmostEqual(car_mdd_live({"cagr_options": 12}, -4.0), 0.03)
        self.assertEqual(car_mdd_live({"cagr_options": 12}, 0.0), 0.0)


class TestOutlierStripped(unittest.TestCase):
    def test_rebuilds_path_after_outlier_removal(self):
        df = _trades(
            [
                {"Trade": 1, "Entry Date": "01-01-2024", "Net P&L %": -24.94, "Final MAE": -5.40},
                {"Trade": 2, "Entry Date": "02-01-2024", "Net P&L %": -0.32,  "Final MAE": -28.50},
                {"Trade": 3, "Entry Date": "03-01-2024", "Net P&L %": 18.38,   "Final MAE": -15.70},
                {"Trade": 4, "Entry Date": "04-01-2024", "Net P&L %": -12.80,  "Final MAE": -26.89},
                {"Trade": 5, "Entry Date": "05-01-2024", "Net P&L %": -8.74,   "Final MAE": -2.10},
            ]
        )
        out = outlier_stripped_live_dd(df)
        # Revised research rule: each stripped trade (incl. the first) anchors its
        # low to prev_cum*(1+FinalMAE%) and measures Live DD against the PREVIOUS
        # trade's peak. Strip top1+bot1 → remaining T2,T4,T5:
        #   T2: 100*(1-.2850)=71.50 vs prevPk 100 → -28.50
        #   T4: 99.68*(1-.2689)=72.87 vs prevPk 100 → -27.13
        #   T5: 86.92*(1-.0210)=85.09 vs prevPk 100 → -14.91
        self.assertEqual(out["positive_outlier_1"], 18.38)
        self.assertEqual(out["negative_outlier_1"], -24.94)
        self.assertEqual(out["outlier_dd_1"], -28.5)
        self.assertEqual(out["outlier_dd_1_avg"], -23.5067)
        self.assertEqual(out["outlier_dd_2"], -2.1)
        self.assertEqual(out["outlier_dd_2_avg"], -2.1)
        self.assertEqual(out["outlier_dd_3"], 0.0)
        self.assertEqual(out["outlier_dd_3_avg"], 0.0)


class TestLegPctNoOutliers(unittest.TestCase):
    def test_drops_largest_positive_ce(self):
        df = _trades(
            [
                {"Entry Spot": 100, "Exit Spot": 100, "Call P&L": 10, "Put P&L": 0},
                {"Entry Spot": 100, "Exit Spot": 100, "Call P&L": 5, "Put P&L": 0},
                {"Entry Spot": 100, "Exit Spot": 100, "Call P&L": 1, "Put P&L": 0},
            ]
        )
        out = leg_pct_no_outliers(df)
        # Drop the +10 outlier → remaining 5+1 = 6 / 100 * 100 = 6.0
        self.assertEqual(out["ce_pnl_pct_no_outlier_1"], 6.0)
        # Drop top 2 (+10, +5) → 1 / 100 * 100 = 1.0
        self.assertEqual(out["ce_pnl_pct_no_outlier_2"], 1.0)


class TestComputeBundle(unittest.TestCase):
    def test_returns_all_expected_keys(self):
        df = _trades(
            [
                {
                    "Entry Spot": 100,
                    "Exit Spot": 102,
                    "Call P&L": 5,
                    "Put P&L": -1,
                    "Spot P&L": 2,
                    "Net P&L %": 0.5,
                    "Lowest NAV During Trade": 99,
                    "Peak": 100,
                }
            ]
        )
        summary = {"total_pnl": 4, "spot_change": 2, "cagr_options": 10}
        out = compute_optim_metrics(df, summary)
        for key in (
            "ce_pnl_total",
            "pe_pnl_total",
            "long_spot_pnl",
            "roi_vs_spot",
            "actual_live_dd_max",
            "actual_live_dd_avg",
            "car_mdd_live",
            "positive_outlier_1",
            "negative_outlier_1",
            "outlier_dd_1",
            "outlier_dd_2",
            "outlier_dd_3",
            "ce_pe_pnl_pct_without_top_1_outliers",
            "ce_pnl_pct_no_outlier_1",
            "pe_pnl_pct_no_outlier_1",
            "cagr_midcap",
        ):
            self.assertIn(key, out, f"missing key {key}")


if __name__ == "__main__":
    unittest.main()
