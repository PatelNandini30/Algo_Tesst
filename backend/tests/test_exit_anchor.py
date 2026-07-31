import unittest

import pandas as pd

from backend.services.algotest_job import _anchor_sorted
from backend.services.trade_anchor import anchor_row, exit_anchor_row, apply_exit_anchor_exclusion


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

        fixed = apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
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

        fixed = apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
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
        fixed = apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
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
        fixed = apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
        self.assertEqual(fixed.loc[0, "Exit Reason"], "EXPIRY")

    def test_missing_columns_are_a_no_op(self):
        trades_df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": pd.Timestamp("2025-01-01"), "Entry Spot": 100.0},
        ])
        aggregated = pd.DataFrame([{"Trade": 1, "Entry Date": pd.Timestamp("2025-01-01")}])
        sorted_df = _anchor_sorted(trades_df)
        # No "Exit Reason" column at all -- must not raise, must return as-is.
        result = apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)
        pd.testing.assert_frame_equal(result, aggregated)

    def test_multi_trade_frame_no_cross_trade_leakage(self):
        """3 trades in ONE frame, covering all three code paths at once, to
        catch a multi-group indexing/merge/concat bug that single-trade
        tests can't see:
          Trade 1: no LEG_FILTER_END row anywhere -> untouched.
          Trade 2: one truncated leg among several -> exit comes from survivors.
          Trade 3: every leg truncated -> falls back to the full row set.
        """
        trades_df = pd.DataFrame([
            # Trade 1 -- ordinary, no truncation.
            {"Trade": 1, "Leg": 1, "Entry Date": "2025-02-01", "Exit Date": "2025-02-15",
             "Entry Spot": 200.0, "Exit Spot": 210.0, "Spot P&L": 10.0,
             "CE P&L": 4.0, "PE P&L": 0.0, "FUT P&L": 0.0, "Exit Reason": "EXPIRY"},
            {"Trade": 1, "Leg": 2, "Entry Date": "2025-02-01", "Exit Date": "2025-02-15",
             "Entry Spot": 200.0, "Exit Spot": 210.0, "Spot P&L": 10.0,
             "CE P&L": 0.0, "PE P&L": 2.0, "FUT P&L": 0.0, "Exit Reason": "EXPIRY"},
            # Trade 2 -- anchor leg (Leg 1, later entry) truncated; Leg 2 survives.
            {"Trade": 2, "Leg": 1, "Entry Date": "2025-01-01", "Exit Date": "2025-01-03",
             "Entry Spot": 100.0, "Exit Spot": 101.0, "Spot P&L": 1.0,
             "CE P&L": 5.0, "PE P&L": 0.0, "FUT P&L": 0.0, "Exit Reason": "LEG_FILTER_END"},
            {"Trade": 2, "Leg": 2, "Entry Date": "2024-12-20", "Exit Date": "2025-01-10",
             "Entry Spot": 95.0, "Exit Spot": 108.0, "Spot P&L": 13.0,
             "CE P&L": 0.0, "PE P&L": 3.0, "FUT P&L": 0.0, "Exit Reason": "EXPIRY"},
            # Trade 3 -- every leg truncated -> fallback to full set (Leg 1 anchor).
            {"Trade": 3, "Leg": 1, "Entry Date": "2025-03-01", "Exit Date": "2025-03-05",
             "Entry Spot": 300.0, "Exit Spot": 301.0, "Spot P&L": 1.0,
             "CE P&L": 6.0, "PE P&L": 0.0, "FUT P&L": 0.0, "Exit Reason": "LEG_FILTER_END"},
            {"Trade": 3, "Leg": 2, "Entry Date": "2025-02-25", "Exit Date": "2025-03-02",
             "Entry Spot": 295.0, "Exit Spot": 297.0, "Spot P&L": 2.0,
             "CE P&L": 0.0, "PE P&L": 1.0, "FUT P&L": 0.0, "Exit Reason": "LEG_FILTER_END"},
        ])
        for c in ("Entry Date", "Exit Date"):
            trades_df[c] = pd.to_datetime(trades_df[c])

        aggregated, sorted_df = self._aggregate(trades_df)
        input_row_count = len(aggregated)
        input_columns = list(aggregated.columns)
        input_trade_order = aggregated["Trade"].tolist()

        fixed = apply_exit_anchor_exclusion(aggregated.copy(), sorted_df)

        # Shape/order guarantees: no row lost/duplicated, no column reordered.
        self.assertEqual(len(fixed), input_row_count)
        self.assertEqual(list(fixed.columns), input_columns)
        self.assertEqual(fixed["Trade"].tolist(), input_trade_order)

        by_trade = fixed.set_index("Trade")

        # Trade 1: no LEG_FILTER_END row -> untouched vs. the pre-fix value.
        pre_fix_by_trade = aggregated.set_index("Trade")
        self.assertEqual(by_trade.loc[1, "Exit Date"], pre_fix_by_trade.loc[1, "Exit Date"])
        self.assertEqual(by_trade.loc[1, "Exit Reason"], pre_fix_by_trade.loc[1, "Exit Reason"])
        self.assertEqual(by_trade.loc[1, "Exit Date"], pd.Timestamp("2025-02-15"))
        self.assertEqual(by_trade.loc[1, "Exit Reason"], "EXPIRY")

        # Trade 2: truncated anchor leg excluded -> surviving Leg 2's exit.
        self.assertEqual(by_trade.loc[2, "Exit Date"], pd.Timestamp("2025-01-10"))
        self.assertEqual(by_trade.loc[2, "Exit Reason"], "EXPIRY")

        # Trade 3: every leg truncated -> fallback to full-set anchor (Leg 1,
        # the later entry), never null.
        self.assertFalse(pd.isna(by_trade.loc[3, "Exit Date"]))
        self.assertEqual(by_trade.loc[3, "Exit Date"], pd.Timestamp("2025-03-05"))
        self.assertEqual(by_trade.loc[3, "Exit Reason"], "LEG_FILTER_END")


class TestKeyMismatchFailsLoud(unittest.TestCase):
    """Deferred-5: `.map(...).fillna(...)` silently reverted to the un-fixed
    value when a Trade key did not match -- hiding exactly the class of
    trade-id renumbering bug this correction exists to survive.
    """

    def test_trade_missing_from_sorted_df_raises(self):
        sorted_df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": "2025-06-02",
             "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
            {"Trade": 1, "Leg": 2, "Entry Date": "2025-06-02",
             "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
        ])
        # Trade 2 exists only in the aggregated frame -- the keys diverge.
        aggregated = pd.DataFrame([
            {"Trade": 1, "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
            {"Trade": 2, "Exit Date": "2025-06-30", "Exit Reason": "EXPIRY"},
        ])
        with self.assertRaises(RuntimeError) as ctx:
            apply_exit_anchor_exclusion(aggregated, sorted_df)
        self.assertIn("Trade keys diverge", str(ctx.exception))

    def test_matching_keys_still_pass(self):
        sorted_df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": "2025-06-02",
             "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
            {"Trade": 1, "Leg": 2, "Entry Date": "2025-06-02",
             "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
        ])
        aggregated = pd.DataFrame([
            {"Trade": 1, "Exit Date": "2025-06-05", "Exit Reason": "LEG_FILTER_END"},
        ])
        out = apply_exit_anchor_exclusion(aggregated, sorted_df)
        self.assertEqual(out.loc[0, "Exit Date"], "2025-06-26")


class TestOptimBacktestRowOrderParity(unittest.TestCase):
    """I3: apply_exit_anchor_exclusion re-applies "first" on whatever row order
    it is given, so the optimizer must feed it the SAME order the backtest does
    or the two can report a different Exit Date on a truncated trade.
    """

    # Leg 2 enters LATER (it is the anchor) but is listed second; leg 1 is the
    # carried leg. Raw order and anchor_sorted order disagree, so a caller that
    # passes RAW picks leg 1's exit and a caller that sorts picks leg 2's.
    ROWS = [
        {"Trade": 1, "Leg": 1, "Entry Date": "2025-06-02",
         "Exit Date": "2025-06-20", "Exit Reason": "EXPIRY"},
        {"Trade": 1, "Leg": 2, "Entry Date": "2025-06-10",
         "Exit Date": "2025-06-26", "Exit Reason": "EXPIRY"},
        {"Trade": 1, "Leg": 3, "Entry Date": "2025-06-10",
         "Exit Date": "2025-06-12", "Exit Reason": "LEG_FILTER_END"},
    ]

    def test_raw_and_anchor_sorted_orders_would_disagree(self):
        # Guards the premise: if these ever stop differing the test below is
        # vacuous and this one fails, saying so.
        df = pd.DataFrame(self.ROWS)
        agg = pd.DataFrame([{"Trade": 1, "Exit Date": "x", "Exit Reason": "y"}])
        raw = apply_exit_anchor_exclusion(agg.copy(), df)
        srt = apply_exit_anchor_exclusion(agg.copy(), _anchor_sorted(df))
        self.assertNotEqual(raw.loc[0, "Exit Date"], srt.loc[0, "Exit Date"])

    def test_optimizer_runner_passes_anchor_sorted(self):
        # Source-text assertion: running the optimizer needs market data, which
        # this suite must never touch (it narrows the shared feather).
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "services", "optimizer", "runner.py",
        )
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("apply_exit_anchor_exclusion(aggregated, anchor_sorted(df))", src)
        self.assertNotIn("apply_exit_anchor_exclusion(aggregated, df)", src)

    def test_anchor_sorted_is_shared_not_duplicated(self):
        # Not assertIs: this repo imports services.* under two module paths
        # ("services.X" inside backend, "backend.services.X" in tests), so the
        # same source function is two objects. Identity of ORIGIN is the point.
        from backend.services.trade_anchor import anchor_sorted
        self.assertEqual(_anchor_sorted.__name__, "anchor_sorted")
        self.assertTrue(_anchor_sorted.__module__.endswith("services.trade_anchor"))
        self.assertEqual(_anchor_sorted.__doc__, anchor_sorted.__doc__)


class TestCacheVersionCoversTheNewModules(unittest.TestCase):
    """I4: a fix confined to leg_filter.py or trade_anchor.py must invalidate
    the Redis result cache, or stale tradesheets are served.
    """

    def test_both_modules_are_hashed(self):
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "services", "backtest_cache.py",
        )
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        i_start = src.index("hash_paths = [")
        block = src[i_start:src.index("]", i_start)]
        self.assertIn("'leg_filter.py'", block)
        self.assertIn("'trade_anchor.py'", block)


if __name__ == "__main__":
    unittest.main()
