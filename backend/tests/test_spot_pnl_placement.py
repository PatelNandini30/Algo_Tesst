import unittest


def _row(trade_id, leg_id, entry_spot, exit_spot, **kw):
    r = {
        "trade_id": trade_id, "leg_id": leg_id,
        "entry_spot": entry_spot, "exit_spot": exit_spot,
        "option_type": "CE", "position": "SELL",
        "entry_price": 100.0, "exit_price": 90.0,
        "entry_date": "2019-11-21", "exit_date": "2019-11-28",
        "expiry": "2019-11-28", "strike": 12000.0, "lots": 1, "lot_size": 75,
        "net_pnl": 10.0,
    }
    r.update(kw)
    return r


class TestSpotPnlPlacement(unittest.TestCase):
    """A trade-level quantity must land on the lowest leg that EXISTS.

    Leg 1 can be absent: an individual per-leg filter file removes that leg
    from the trade (see docs/superpowers/specs/2026-07-31-per-leg-filter-design.md).
    """

    def _spot_by_leg(self, rows):
        from backend.services.engine_rust import priced_to_tradesheet_records
        out = priced_to_tradesheet_records(rows, {}, 75)
        return {(r["Trade"], r["Leg"]): r["Spot P&L"] for r in out}

    def test_normal_trade_still_reports_on_leg_1(self):
        got = self._spot_by_leg([
            _row(1, 1, 11968.40, 12151.15),
            _row(1, 2, 11968.40, 12151.15),
        ])
        self.assertEqual(got[("1", 1)], 182.75)
        self.assertEqual(got[("1", 2)], "")

    def test_missing_leg_1_reports_on_leg_2(self):
        got = self._spot_by_leg([
            _row(2, 2, 12151.15, 12018.40),
        ])
        self.assertEqual(got[("2", 2)], -132.75)

    def test_missing_legs_1_and_2_reports_on_leg_3(self):
        got = self._spot_by_leg([
            _row(3, 3, 12018.40, 11971.80),
            _row(3, 4, 12018.40, 11971.80),
        ])
        self.assertEqual(got[("3", 3)], -46.60)
        self.assertEqual(got[("3", 4)], "")

    def test_exactly_one_row_per_trade_carries_a_value(self):
        """The invariant every downstream SUM depends on."""
        rows = [
            _row(1, 1, 100.0, 110.0), _row(1, 2, 100.0, 110.0),
            _row(2, 2, 110.0, 105.0), _row(2, 3, 110.0, 105.0),
            _row(3, 3, 105.0, 120.0),
            # trade 5: futures primary + re-entry row, SAME leg_id (see
            # _build_futures_specs :1623-1624 / :1733-1734) plus a higher leg.
            _row(5, 2, 100.0, 105.0), _row(5, 2, 105.0, 108.0), _row(5, 3, 100.0, 108.0),
        ]
        from backend.services.engine_rust import priced_to_tradesheet_records
        out = priced_to_tradesheet_records(rows, {}, 75)
        for tid in ("1", "2", "3", "5"):
            carried = [r for r in out
                       if r["Trade"] == tid and r["Spot P&L"] != ""]
            self.assertEqual(len(carried), 1, f"trade {tid}")

    def test_duplicate_leg_id_same_trade_first_row_wins(self):
        """Futures primary + re-entry row can share trade_id AND leg_id.

        Both would match the lowest-leg check; only the FIRST in input order
        may carry the value, or the trade's Spot P&L doubles when summed.
        """
        rows = [
            _row(6, 1, 100.0, 110.0),  # primary: 10.0
            _row(6, 1, 110.0, 108.0),  # re-entry: -2.0, same leg_id
        ]
        from backend.services.engine_rust import priced_to_tradesheet_records
        out = priced_to_tradesheet_records(rows, {}, 75)
        carried = [r for r in out if r["Trade"] == "6" and r["Spot P&L"] != ""]
        self.assertEqual(len(carried), 1)
        self.assertEqual(carried[0]["Spot P&L"], 10.0)

    def test_duplicated_lowest_leg_with_higher_leg(self):
        """Duplicated lowest leg (2, 2) alongside a higher leg (3)."""
        rows = [
            _row(7, 2, 100.0, 106.0),
            _row(7, 2, 106.0, 109.0),
            _row(7, 3, 100.0, 109.0),
        ]
        from backend.services.engine_rust import priced_to_tradesheet_records
        out = priced_to_tradesheet_records(rows, {}, 75)
        carried = [r for r in out if r["Trade"] == "7" and r["Spot P&L"] != ""]
        self.assertEqual(len(carried), 1)
        self.assertEqual(carried[0]["Leg"], 2)
        self.assertEqual(carried[0]["Spot P&L"], 6.0)

    def test_per_leg_ordering_does_not_matter(self):
        """Rows may arrive in any order; the lowest leg still wins."""
        got = self._spot_by_leg([
            _row(4, 3, 100.0, 120.0),
            _row(4, 2, 100.0, 120.0),
        ])
        self.assertEqual(got[("4", 2)], 20.0)
        self.assertEqual(got[("4", 3)], "")


import pandas as pd


class TestSpotPnlAggregation(unittest.TestCase):
    """The aggregate must find the carrying row wherever it sorts."""

    def _agg(self, df):
        from backend.services.algotest_job import _anchor_sorted
        from backend.services.trade_anchor import spot_first_non_empty
        # Mirror the real aggregation for just this column.
        return (_anchor_sorted(df)
                .groupby("Trade", as_index=False)
                .agg({"Spot P&L": spot_first_non_empty}))

    def test_carried_yearly_leg_order_still_finds_the_value(self):
        """Leg 2 enters LATER, so _anchor_sorted puts it first; leg 1 carries
        the value. Positional "first" returns "" here — the pre-existing bug."""
        df = pd.DataFrame([
            {"Trade": 1, "Leg": 1, "Entry Date": pd.Timestamp("2019-01-01"),
             "Spot P&L": 182.75},
            {"Trade": 1, "Leg": 2, "Entry Date": pd.Timestamp("2019-11-21"),
             "Spot P&L": ""},
        ])
        self.assertEqual(self._agg(df)["Spot P&L"].iloc[0], 182.75)

    def test_value_on_leg_2_when_leg_1_is_absent(self):
        df = pd.DataFrame([
            {"Trade": 2, "Leg": 2, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": -132.75},
            {"Trade": 2, "Leg": 3, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": ""},
        ])
        self.assertEqual(self._agg(df)["Spot P&L"].iloc[0], -132.75)

    def test_all_blank_stays_blank(self):
        df = pd.DataFrame([
            {"Trade": 3, "Leg": 1, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": ""},
        ])
        self.assertIn(self._agg(df)["Spot P&L"].iloc[0], ("", None))

    def test_zero_spot_pnl_survives(self):
        """A flat close-to-close move is a legitimate 0.0, not a blank.
        Must FAIL if the helper is ever rewritten with a truthiness check."""
        df = pd.DataFrame([
            {"Trade": 4, "Leg": 1, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": 0.0},
            {"Trade": 4, "Leg": 2, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": ""},
        ])
        self.assertEqual(self._agg(df)["Spot P&L"].iloc[0], 0.0)

    def test_negative_spot_pnl_survives(self):
        df = pd.DataFrame([
            {"Trade": 5, "Leg": 1, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": -132.75},
            {"Trade": 5, "Leg": 2, "Entry Date": pd.Timestamp("2019-11-28"),
             "Spot P&L": ""},
        ])
        self.assertEqual(self._agg(df)["Spot P&L"].iloc[0], -132.75)

    def test_pd_na_does_not_raise(self):
        from backend.services.trade_anchor import spot_first_non_empty
        result = spot_first_non_empty([pd.NA, 182.75])
        self.assertEqual(result, 182.75)


class TestSpotPnlOptimizerSum(unittest.TestCase):
    """Step 1 of the audit: metrics.per_leg_pnl sums the whole column with no
    Leg == 1 filter, so it is placement-independent PROVIDED exactly one row
    per trade carries a value (guaranteed by engine_rust.py — see the classes
    above). This pins that sum behaves identically whether the carrying row
    is leg 1 or a higher leg (leg 1 filtered out of the trade)."""

    def test_sum_unaffected_by_which_leg_carries_the_value(self):
        from backend.services.optimizer.metrics import per_leg_pnl

        df_leg1 = pd.DataFrame([
            {"Entry Spot": 100.0, "Spot P&L": 10.0, "Call P&L": 5.0, "Put P&L": 0.0},
            {"Entry Spot": 100.0, "Spot P&L": "", "Call P&L": 0.0, "Put P&L": 3.0},
        ])
        df_leg2 = pd.DataFrame([
            {"Entry Spot": 100.0, "Spot P&L": "", "Call P&L": 5.0, "Put P&L": 0.0},
            {"Entry Spot": 100.0, "Spot P&L": 10.0, "Call P&L": 0.0, "Put P&L": 3.0},
        ])
        self.assertEqual(
            per_leg_pnl(df_leg1)["long_spot_pnl"],
            per_leg_pnl(df_leg2)["long_spot_pnl"],
        )
        self.assertEqual(per_leg_pnl(df_leg1)["long_spot_pnl"], 10.0)


class TestMultiIndexSpotPnlAggregation(unittest.TestCase):
    """Step 8 follow-up: multi_index_feature.py's `combined = pd.concat(group_frames)`
    groupby("Trade").agg(...) sites have NO leg-order sort before them (unlike
    algotest_job.py's _anchor_sorted or engine_rust.py's trade/leg sort) — the
    per-group tradesheet arrives in whatever order Rust's simulate_trades_batch
    returned. Plain "first" can therefore pick a blank row exactly like the bug
    that made optimizer/runner.py need spot_first_non_empty. This pins the
    aggregation shape used at multi_index_feature.py:1856/2348/3337/3810."""

    def _agg_spec_shape(self, df, spot_agg):
        return df.groupby("Trade", as_index=False).agg({
            "Entry Spot": "first", "Spot P&L": spot_agg,
            "CE P&L": "sum", "PE P&L": "sum",
        })

    def test_carrying_row_not_first_breaks_plain_first(self):
        """Control: proves the bug is real against plain 'first', matching the
        shape actually used before this fix."""
        df = pd.DataFrame([
            {"Trade": 1, "Entry Spot": 100.0, "Spot P&L": "", "CE P&L": 5.0, "PE P&L": 0.0},
            {"Trade": 1, "Entry Spot": 100.0, "Spot P&L": -132.75, "CE P&L": 0.0, "PE P&L": 3.0},
        ])
        got = self._agg_spec_shape(df, "first")["Spot P&L"].iloc[0]
        self.assertNotEqual(got, -132.75, "plain 'first' should (wrongly) miss the value")
        self.assertEqual(got, "")

    def test_carrying_row_not_first_spot_first_non_empty_still_finds_it(self):
        from backend.services.trade_anchor import spot_first_non_empty
        df = pd.DataFrame([
            {"Trade": 1, "Entry Spot": 100.0, "Spot P&L": "", "CE P&L": 5.0, "PE P&L": 0.0},
            {"Trade": 1, "Entry Spot": 100.0, "Spot P&L": -132.75, "CE P&L": 0.0, "PE P&L": 3.0},
        ])
        got = self._agg_spec_shape(df, spot_first_non_empty)["Spot P&L"].iloc[0]
        self.assertEqual(got, -132.75)


if __name__ == "__main__":
    unittest.main()
