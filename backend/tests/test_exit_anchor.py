import unittest

import pandas as pd

from backend.services.algotest_job import _apply_exit_anchor_exclusion, _anchor_sorted
from backend.services.trade_anchor import anchor_row, exit_anchor_row


class TestExitAnchorRow(unittest.TestCase):
    def test_picks_the_latest_exit(self):
        rows = [
            {"Leg": 1, "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
            {"Leg": 2, "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
        ]
        self.assertEqual(exit_anchor_row(rows)["Exit Reason"], "EXPIRY")

    def test_leg_order_does_not_change_the_answer(self):
        rows = [
            {"Leg": 2, "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
            {"Leg": 1, "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
        ]
        self.assertEqual(exit_anchor_row(rows)["Exit Date"], "2025-06-26")

    def test_ties_break_on_lowest_leg(self):
        rows = [
            {"Leg": 2, "Exit Date": "2025-06-26", "Exit Reason": "B"},
            {"Leg": 1, "Exit Date": "2025-06-26", "Exit Reason": "A"},
        ]
        self.assertEqual(exit_anchor_row(rows)["Exit Reason"], "A")

    def test_legs_exiting_together_is_unchanged_behaviour(self):
        rows = [
            {"Leg": 1, "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
            {"Leg": 2, "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
        ]
        self.assertEqual(exit_anchor_row(rows), rows[0])

    def test_empty_is_none(self):
        self.assertIsNone(exit_anchor_row([]))

    def test_excluded_row_fallback_when_every_leg_truncated(self):
        # Every leg of the trade was truncated by its own filter file. The
        # caller's exclusion step leaves nothing, so per the documented
        # contract it must fall back to the full, unfiltered row set rather
        # than calling exit_anchor_row([]) and losing the Exit Date.
        rows = [
            {"Leg": 1, "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
            {"Leg": 2, "Exit Date": "2025-06-10", "Exit Reason": "LEG_FILTER_END"},
        ]
        candidates = [r for r in rows if "LEG_FILTER_END" not in str(r.get("Exit Reason", ""))]
        self.assertEqual(candidates, [])
        fallback = candidates or rows
        result = exit_anchor_row(fallback)
        self.assertIsNotNone(result)
        self.assertEqual(result["Exit Date"], "2025-06-10")

    def test_no_truncation_matches_pre_existing_anchor_first(self):
        # Ordinary strategy: legs enter and exit together, no LEG_FILTER_END
        # anywhere. The old code took whichever row `_anchor_sorted` put
        # first (entry-anchor: latest Entry Date, ties lowest Leg). Since
        # nothing here is truncated, exit_anchor_row on the same rows must
        # agree with that pre-existing pick.
        rows = [
            {"Leg": 2, "Entry Date": "2025-06-01", "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
            {"Leg": 1, "Entry Date": "2025-06-01", "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
        ]
        old_first = anchor_row(rows)
        self.assertEqual(exit_anchor_row(rows), old_first)


class TestApplyExitAnchorExclusion(unittest.TestCase):
    """Exercises the actual code path wired into algotest_job.py's per-trade
    aggregation, on synthetic frames only (no engine/backtest run)."""

    def _aggregate(self, trades_df):
        sorted_df = _anchor_sorted(trades_df)
        aggregated = sorted_df.groupby("Trade", as_index=False).agg({
            "Entry Date": "first",
            "Exit Date": "first",
            "Entry Spot": "first",
            "Exit Spot": "first",
            "Spot P&L": "first",
            "CE P&L": "sum",
            "PE P&L": "sum",
            "FUT P&L": "sum",
            "Exit Reason": "first",
        })
        return aggregated, sorted_df

    def test_byte_identical_when_no_leg_filter_end(self):
        """The single most important property: a synthetic multi-leg trade
        with DIFFERING exit dates and no LEG_FILTER_END row anywhere must
        aggregate to exactly what the old `.agg({"Exit Date": "first", ...})`
        alone produced -- constructed here explicitly, not via the new code."""
        trades_df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": "2025-01-01", "Exit Date": "2025-01-10",
             "Entry Spot": 100.0, "Exit Spot": 110.0, "Spot P&L": 10.0,
             "CE P&L": 5.0, "PE P&L": 0.0, "FUT P&L": 0.0, "Exit Reason": "TARGET"},
            {"Trade": 1, "Leg": 2, "Entry Date": "2024-12-20", "Exit Date": "2025-01-05",
             "Entry Spot": 95.0, "Exit Spot": 105.0, "Spot P&L": 10.0,
             "CE P&L": 0.0, "PE P&L": 3.0, "FUT P&L": 0.0, "Exit Reason": "EXPIRY"},
        ])
        for c in ("Entry Date", "Exit Date"):
            trades_df[c] = pd.to_datetime(trades_df[c])

        aggregated, sorted_df = self._aggregate(trades_df)
        # Expected value from the OLD rule, computed independently: "first"
        # on the _anchor_sorted order (latest Entry Date wins => Leg 1).
        expected_exit_date = pd.Timestamp("2025-01-10")
        expected_exit_reason = "TARGET"
        self.assertEqual(aggregated.loc[0, "Exit Date"], expected_exit_date)
        self.assertEqual(aggregated.loc[0, "Exit Reason"], expected_exit_reason)

        fixed = _apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
        self.assertEqual(fixed.loc[0, "Exit Date"], expected_exit_date)
        self.assertEqual(fixed.loc[0, "Exit Reason"], expected_exit_reason)

    def test_truncated_anchor_leg_no_longer_hijacks_exit_date(self):
        """Leg 1 (the entry-anchor, since it entered later) was truncated by
        its own filter file and exits early. Without the fix, "first" would
        report 2025-01-03/LEG_FILTER_END as the trade's exit. With the fix,
        the trade's exit comes from the surviving Leg 2."""
        trades_df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": "2025-01-01", "Exit Date": "2025-01-03",
             "Entry Spot": 100.0, "Exit Spot": 101.0, "Spot P&L": 1.0,
             "CE P&L": 5.0, "PE P&L": 0.0, "FUT P&L": 0.0, "Exit Reason": "LEG_FILTER_END"},
            {"Trade": 1, "Leg": 2, "Entry Date": "2024-12-20", "Exit Date": "2025-01-10",
             "Entry Spot": 95.0, "Exit Spot": 108.0, "Spot P&L": 13.0,
             "CE P&L": 0.0, "PE P&L": 3.0, "FUT P&L": 0.0, "Exit Reason": "EXPIRY"},
        ])
        for c in ("Entry Date", "Exit Date"):
            trades_df[c] = pd.to_datetime(trades_df[c])

        aggregated, sorted_df = self._aggregate(trades_df)
        # Confirm the OLD behaviour would indeed have been hijacked, so this
        # test is actually exercising the bug the task exists to fix.
        self.assertEqual(aggregated.loc[0, "Exit Reason"], "LEG_FILTER_END")

        fixed = _apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
        self.assertEqual(fixed.loc[0, "Exit Date"], pd.Timestamp("2025-01-10"))
        self.assertEqual(fixed.loc[0, "Exit Reason"], "EXPIRY")
        # Every other column must be untouched by the fix.
        self.assertEqual(fixed.loc[0, "Entry Date"], aggregated.loc[0, "Entry Date"])
        self.assertEqual(fixed.loc[0, "CE P&L"], aggregated.loc[0, "CE P&L"])
        self.assertEqual(fixed.loc[0, "PE P&L"], aggregated.loc[0, "PE P&L"])

    def test_every_leg_truncated_falls_back_to_full_set(self):
        trades_df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": "2025-01-01", "Exit Date": "2025-01-03",
             "Entry Spot": 100.0, "Exit Spot": 101.0, "Spot P&L": 1.0,
             "CE P&L": 5.0, "PE P&L": 0.0, "FUT P&L": 0.0, "Exit Reason": "LEG_FILTER_END"},
            {"Trade": 1, "Leg": 2, "Entry Date": "2024-12-20", "Exit Date": "2025-01-02",
             "Entry Spot": 95.0, "Exit Spot": 96.0, "Spot P&L": 1.0,
             "CE P&L": 0.0, "PE P&L": 3.0, "FUT P&L": 0.0, "Exit Reason": "LEG_FILTER_END"},
        ])
        for c in ("Entry Date", "Exit Date"):
            trades_df[c] = pd.to_datetime(trades_df[c])

        aggregated, sorted_df = self._aggregate(trades_df)
        fixed = _apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
        # Falls back to the full set's anchor pick (Leg 1, latest entry) --
        # never null.
        self.assertFalse(pd.isna(fixed.loc[0, "Exit Date"]))
        self.assertEqual(fixed.loc[0, "Exit Date"], pd.Timestamp("2025-01-03"))
        self.assertEqual(fixed.loc[0, "Exit Reason"], "LEG_FILTER_END")

    def test_combined_exit_reason_substring_is_detected(self):
        """Exit Reason may be a combined "+"-joined string. Substring match,
        never equality."""
        trades_df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": "2025-01-01", "Exit Date": "2025-01-03",
             "Entry Spot": 100.0, "Exit Spot": 101.0, "Spot P&L": 1.0,
             "CE P&L": 5.0, "PE P&L": 0.0, "FUT P&L": 0.0, "Exit Reason": "STOP_LOSS+LEG_FILTER_END"},
            {"Trade": 1, "Leg": 2, "Entry Date": "2024-12-20", "Exit Date": "2025-01-10",
             "Entry Spot": 95.0, "Exit Spot": 108.0, "Spot P&L": 13.0,
             "CE P&L": 0.0, "PE P&L": 3.0, "FUT P&L": 0.0, "Exit Reason": "EXPIRY"},
        ])
        for c in ("Entry Date", "Exit Date"):
            trades_df[c] = pd.to_datetime(trades_df[c])

        aggregated, sorted_df = self._aggregate(trades_df)
        fixed = _apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
        self.assertEqual(fixed.loc[0, "Exit Reason"], "EXPIRY")

    def test_missing_columns_are_a_no_op(self):
        trades_df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": pd.Timestamp("2025-01-01"), "Entry Spot": 100.0},
        ])
        aggregated = pd.DataFrame([{"Trade": 1, "Entry Date": pd.Timestamp("2025-01-01")}])
        sorted_df = _anchor_sorted(trades_df)
        # No "Exit Reason" column at all -- must not raise, must return as-is.
        result = _apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
        pd.testing.assert_frame_equal(result, aggregated)


if __name__ == "__main__":
    unittest.main()
