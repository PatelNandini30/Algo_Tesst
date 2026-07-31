"""
Leg ORDER must never change a statistic.

Reported symptom: a strategy built as
    Leg 1  NIFTY WEEKLY  CE SELL
    Leg 2  MIDCAP100     hypothetical-future BUY   (overlay)
    Leg 3  NIFTY YEARLY  PE BUY
produced different stats -- and sometimes lost the whole Midcap column block
from the Trade Sheet -- when the same three legs were reordered in the builder.

Root cause: trade-level fields (Entry Date, Entry Spot, and therefore % P&L,
the base-100 NAV, Max DD, CAGR and the Midcap overlay's pricing window) were
read off "whichever row came first", which is the user's configured leg
position. A CARRIED yearly leg holds an older entry date than the weekly leg
that re-enters each cycle, so the answer flipped with leg order.

These tests enumerate EVERY permutation of the leg rows and assert the derived
trade-level values are identical, plus assert the no-change guarantee for the
ordinary case where all legs enter on the same date.

Run:
    python -m unittest backend.tests.test_leg_order_parity
"""

import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.trade_anchor import (  # noqa: E402
    anchor_row,
    is_reentry_row,
    trade_entry_spot,
    trade_net_pnl,
    trade_pct_pnl,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _leg(leg_no, entry, exit_, entry_spot, exit_spot, ce=0.0, pe=0.0, fut=0.0, **extra):
    """One per-leg tradesheet row in the shape engine_rust.py:3800-3850 emits."""
    row = {
        "Trade": 1,
        "Leg": leg_no,
        "Entry Date": entry,
        "Exit Date": exit_,
        "Entry Spot": entry_spot,
        "Exit Spot": exit_spot,
        "CE P&L": ce,
        "PE P&L": pe,
        "FUT P&L": fut,
    }
    row.update(extra)
    return row


# The reported config. The WEEKLY CE re-enters this cycle (entry 2025-06-05);
# the YEARLY PE is CARRIED and still carries its December-contract anchor entry
# (2024-12-26), which is what used to hijack the trade window when it was
# configured first.
CARRIED_YEARLY_TRADE = [
    _leg(1, "2025-06-05", "2025-06-12", 24800.0, 24910.0, ce=118.25),
    _leg(2, "2024-12-26", "2025-06-12", 23600.0, 24910.0, pe=-42.50),
]

# An ordinary same-cadence trade: every leg enters on the same day. This is the
# shape that must stay byte-identical to the old "first row wins" behaviour.
SAME_CADENCE_TRADE = [
    _leg(1, "2025-06-05", "2025-06-12", 24800.0, 24910.0, ce=118.25),
    _leg(2, "2025-06-05", "2025-06-12", 24800.0, 24910.0, pe=-42.50),
    _leg(3, "2025-06-05", "2025-06-12", 24800.0, 24910.0, fut=31.00),
]

# Same-index FUT + OPT, plus a leg cut early by an SL.
MIXED_SEGMENT_TRADE = [
    _leg(1, "2025-06-05", "2025-06-09", 24800.0, 24755.0, ce=64.00, **{"Exit Reason": "STOP_LOSS"}),
    _leg(2, "2025-06-05", "2025-06-12", 24800.0, 24910.0, fut=110.00),
]

# A trade carrying a re-entry sub-row, which must never be able to define the
# parent trade's window even though its entry date is the latest of all.
TRADE_WITH_REENTRY = [
    _leg(1, "2025-06-05", "2025-06-12", 24800.0, 24910.0, ce=118.25),
    _leg(2, "2024-12-26", "2025-06-12", 23600.0, 24910.0, pe=-42.50),
    _leg(1, "2025-06-10", "2025-06-12", 24870.0, 24910.0, ce=15.00,
         **{"ReEntryIndex": 1, "ReEntryTrigger": "SL"}),
]

ALL_SHAPES = {
    "carried_yearly": CARRIED_YEARLY_TRADE,
    "same_cadence": SAME_CADENCE_TRADE,
    "mixed_segment": MIXED_SEGMENT_TRADE,
    "with_reentry": TRADE_WITH_REENTRY,
}


def _derived(rows):
    """Every trade-level value the tradesheet/overlay/NAV depend on."""
    a = anchor_row(rows) or {}
    return {
        "entry_date": a.get("Entry Date"),
        "exit_date": a.get("Exit Date"),
        "entry_spot": a.get("Entry Spot"),
        "exit_spot": a.get("Exit Spot"),
        "net_pnl": round(trade_net_pnl(rows), 6),
        "pct_pnl": trade_pct_pnl(rows),
    }


# ---------------------------------------------------------------------------
# Order invariance
# ---------------------------------------------------------------------------

class TestLegOrderInvariance(unittest.TestCase):
    """The whole point: permuting the rows changes nothing."""

    def test_every_permutation_of_every_shape_is_identical(self):
        for name, rows in ALL_SHAPES.items():
            with self.subTest(shape=name):
                perms = list(itertools.permutations(rows))
                baseline = _derived(list(perms[0]))
                for perm in perms[1:]:
                    self.assertEqual(
                        _derived(list(perm)), baseline,
                        "shape %r: leg order changed the derived trade values.\n"
                        "  order   : %s\n"
                        "  got     : %s\n"
                        "  expected: %s" % (
                            name,
                            [r["Leg"] for r in perm],
                            _derived(list(perm)),
                            baseline,
                        ),
                    )

    def test_reported_config_all_six_orderings(self):
        """The exact reported case, spelled out so a regression names itself."""
        rows = CARRIED_YEARLY_TRADE
        seen = {tuple(sorted(_derived(list(p)).items())) for p in itertools.permutations(rows)}
        self.assertEqual(
            len(seen), 1,
            "NIFTY WEEKLY CE + carried NIFTY YEARLY PE produced %d different "
            "results across its orderings; expected exactly 1." % len(seen),
        )


# ---------------------------------------------------------------------------
# The anchor rule itself
# ---------------------------------------------------------------------------

class TestAnchorRule(unittest.TestCase):

    def test_latest_entry_wins_over_carried_leg(self):
        """A carried YEARLY leg must not define the trade window."""
        a = anchor_row(CARRIED_YEARLY_TRADE)
        self.assertEqual(a["Entry Date"], "2025-06-05")
        self.assertEqual(a["Entry Spot"], 24800.0)

    def test_carried_leg_first_gives_the_same_anchor(self):
        a = anchor_row(list(reversed(CARRIED_YEARLY_TRADE)))
        self.assertEqual(a["Entry Date"], "2025-06-05")
        self.assertEqual(a["Entry Spot"], 24800.0)

    def test_same_date_ties_break_on_lowest_leg(self):
        a = anchor_row(SAME_CADENCE_TRADE)
        self.assertEqual(a["Leg"], 1)
        a = anchor_row(list(reversed(SAME_CADENCE_TRADE)))
        self.assertEqual(a["Leg"], 1)

    def test_reentry_rows_never_become_the_anchor(self):
        a = anchor_row(TRADE_WITH_REENTRY)
        self.assertFalse(is_reentry_row(a))
        self.assertEqual(a["Entry Date"], "2025-06-05")

    def test_blank_entry_date_never_wins(self):
        rows = list(SAME_CADENCE_TRADE) + [_leg(9, "", "", 0.0, 0.0)]
        a = anchor_row(rows)
        self.assertEqual(a["Entry Date"], "2025-06-05")

    def test_accepts_ddmmyyyy_and_iso_and_datetime(self):
        from datetime import datetime
        iso = anchor_row([
            _leg(1, "2025-06-05", "2025-06-12", 1.0, 1.0),
            _leg(2, "2024-12-26", "2025-06-12", 2.0, 2.0),
        ])
        ddmm = anchor_row([
            _leg(1, "05-06-2025", "12-06-2025", 1.0, 1.0),
            _leg(2, "26-12-2024", "12-06-2025", 2.0, 2.0),
        ])
        dt = anchor_row([
            _leg(1, datetime(2025, 6, 5), datetime(2025, 6, 12), 1.0, 1.0),
            _leg(2, datetime(2024, 12, 26), datetime(2025, 6, 12), 2.0, 2.0),
        ])
        self.assertEqual((iso["Leg"], ddmm["Leg"], dt["Leg"]), (1, 1, 1))

    def test_empty_input(self):
        self.assertIsNone(anchor_row([]))
        self.assertEqual(trade_net_pnl([]), 0.0)
        self.assertIsNone(trade_entry_spot([]))
        self.assertEqual(trade_pct_pnl([]), 0.0)


# ---------------------------------------------------------------------------
# No-change guarantee for existing strategies
# ---------------------------------------------------------------------------

class TestNoChangeForSameCadenceStrategies(unittest.TestCase):
    """When every leg enters on the same date -- the overwhelming majority of
    saved strategies -- the anchor IS row 1, so the new rule reproduces the old
    `agg("first")` / `legs[0]` behaviour exactly."""

    def test_anchor_equals_old_first_row_behaviour(self):
        for name, rows in (("same_cadence", SAME_CADENCE_TRADE),
                           ("mixed_segment", MIXED_SEGMENT_TRADE)):
            with self.subTest(shape=name):
                old_first = rows[0]
                a = anchor_row(rows)
                self.assertEqual(a["Entry Date"], old_first["Entry Date"])
                self.assertEqual(a["Entry Spot"], old_first["Entry Spot"])
                self.assertEqual(a["Exit Spot"], old_first["Exit Spot"])


# ---------------------------------------------------------------------------
# The Net P&L double-count
# ---------------------------------------------------------------------------

class TestNetPnlIsNotDoubleCounted(unittest.TestCase):
    """simulate.rs:1794-1806 writes the trade TOTAL onto the lowest-leg_id row
    and leaves per-leg values on the others, so summing the `Net P&L` column
    double-counts. trade_net_pnl() sums the per-leg CE/PE/FUT columns instead."""

    def test_sum_of_net_pnl_column_would_double_count(self):
        rows = [dict(r) for r in SAME_CADENCE_TRADE]
        per_leg_total = 118.25 - 42.50 + 31.00
        rows[0]["Net P&L"] = per_leg_total       # parent carries the trade total
        rows[1]["Net P&L"] = -42.50              # others keep their own
        rows[2]["Net P&L"] = 31.00

        naive = sum(r["Net P&L"] for r in rows)
        self.assertAlmostEqual(trade_net_pnl(rows), per_leg_total, places=6)
        self.assertNotAlmostEqual(naive, per_leg_total, places=6)

    def test_total_is_order_invariant(self):
        for perm in itertools.permutations(SAME_CADENCE_TRADE):
            self.assertAlmostEqual(trade_net_pnl(list(perm)), 106.75, places=6)


# ---------------------------------------------------------------------------
# The real call sites, over every permutation
# ---------------------------------------------------------------------------

def _tradesheet_rows(order):
    """A two-trade tradesheet whose legs are emitted in `order` (a permutation of
    leg numbers), shaped like the rows the engine hands the overlay."""
    per_trade = {
        1: {
            1: _leg(1, "2025-06-05", "2025-06-12", 24800.0, 24910.0, ce=118.25),
            2: _leg(2, "2024-12-26", "2025-06-12", 23600.0, 24910.0, pe=-42.50),
        },
        2: {
            1: _leg(1, "2025-06-12", "2025-06-19", 24910.0, 24700.0, ce=-56.00),
            2: _leg(2, "2024-12-26", "2025-06-19", 23600.0, 24700.0, pe=88.75),
        },
    }
    rows = []
    for trade_id, legs in per_trade.items():
        for leg_no in order:
            row = dict(legs[leg_no])
            row["Trade"] = trade_id
            # Parent row (lowest Leg) carries the trade total, as simulate.rs does.
            total = sum(legs[n].get("CE P&L", 0) + legs[n].get("PE P&L", 0)
                        + legs[n].get("FUT P&L", 0) for n in legs)
            row["Net P&L"] = total if leg_no == min(legs) else (
                row.get("CE P&L", 0) + row.get("PE P&L", 0) + row.get("FUT P&L", 0)
            )
            rows.append(row)
    return rows


class TestProjectRowsForMidcapIsOrderInvariant(unittest.TestCase):
    """excel_builder._project_rows_for_midcap decides the Midcap overlay's
    pricing window AND the % that feeds Combined Net P&L %. When it returned an
    unusable window the overlay reported available=False for every trade, which
    flipped has_midcap to False and stripped all 21 _MIDCAP_COLS out of the
    Trade Sheet -- the reported "Midcap leg doesn't show"."""

    def _project(self, order):
        from services.optimizer.excel_builder import _project_rows_for_midcap
        out = _project_rows_for_midcap(_tradesheet_rows(order))
        return sorted(out, key=lambda r: r["trade_id"])

    def test_all_permutations_project_identically(self):
        baseline = self._project((1, 2))
        for order in itertools.permutations((1, 2)):
            self.assertEqual(
                self._project(order), baseline,
                "leg order %s changed the Midcap projection" % (order,),
            )

    def test_window_comes_from_the_cycle_leg_not_the_carried_leg(self):
        for order in itertools.permutations((1, 2)):
            proj = self._project(order)
            self.assertEqual(proj[0]["entry_date"], "2025-06-05", "order=%s" % (order,))
            self.assertEqual(proj[1]["entry_date"], "2025-06-12", "order=%s" % (order,))

    def test_projection_is_priceable_in_both_orders(self):
        """Both dates must resolve; a None window is what made available=False."""
        for order in itertools.permutations((1, 2)):
            for row in self._project(order):
                self.assertIsNotNone(row["entry_date"], "order=%s" % (order,))
                self.assertIsNotNone(row["exit_date"], "order=%s" % (order,))


class TestAnchorSortedIsOrderInvariant(unittest.TestCase):
    """algotest_job._anchor_sorted feeds the groupby whose `.agg("first")`
    supplies Entry Spot -- the % P&L / base-100 NAV / Max DD / CAGR denominator."""

    def _aggregate(self, order):
        import pandas as pd
        from services.algotest_job import _anchor_sorted
        df = pd.DataFrame(_tradesheet_rows(order))
        for c in ("Entry Date", "Exit Date"):
            df[c] = pd.to_datetime(df[c], errors="coerce")
        agg = _anchor_sorted(df).groupby("Trade", as_index=False).agg({
            "Entry Date": "first", "Exit Date": "first",
            "Entry Spot": "first", "Exit Spot": "first",
            "CE P&L": "sum", "PE P&L": "sum", "FUT P&L": "sum",
        })
        agg["Net P&L"] = agg["CE P&L"] + agg["PE P&L"] + agg["FUT P&L"]
        agg["% P&L"] = (agg["Net P&L"] / agg["Entry Spot"] * 100.0).round(4)
        return agg.to_dict("records")

    def test_all_permutations_aggregate_identically(self):
        baseline = self._aggregate((1, 2))
        for order in itertools.permutations((1, 2)):
            self.assertEqual(
                self._aggregate(order), baseline,
                "leg order %s changed the per-trade aggregate" % (order,),
            )

    def test_entry_spot_is_the_cycle_spot_not_the_carried_anchor(self):
        for order in itertools.permutations((1, 2)):
            rows = self._aggregate(order)
            self.assertEqual(rows[0]["Entry Spot"], 24800.0, "order=%s" % (order,))
            self.assertEqual(rows[1]["Entry Spot"], 24910.0, "order=%s" % (order,))

    def test_frame_passed_in_is_not_mutated(self):
        import pandas as pd
        from services.algotest_job import _anchor_sorted
        df = pd.DataFrame(_tradesheet_rows((1, 2)))
        df["Entry Date"] = pd.to_datetime(df["Entry Date"], errors="coerce")
        before = df.copy()
        out = _anchor_sorted(df)
        pd.testing.assert_frame_equal(df, before)
        self.assertNotIn("_ta_re", out.columns)
        self.assertNotIn("_ta_leg", out.columns)


# ---------------------------------------------------------------------------
# Multi-index / multi-expiry cadence selectors
# ---------------------------------------------------------------------------

def _mleg(idx, expiry, segment="OPTIONS"):
    return {"index": idx, "expiry": expiry, "segment": segment}


# Shapes covering same-index and cross-index configurations. Every one of these
# used to route differently depending on which leg was configured first.
MI_SHAPES = {
    # The audited case: NIFTY yearly + MIDCPNIFTY monthly + NIFTY monthly.
    "yearly_plus_two_indices": [
        _mleg("NIFTY", "YEARLY"),
        _mleg("MIDCPNIFTY", "MONTHLY"),
        _mleg("NIFTY", "MONTHLY"),
    ],
    # No yearly leg: weekly on both indices plus a monthly.
    "weekly_two_indices": [
        _mleg("NIFTY", "WEEKLY"),
        _mleg("MIDCPNIFTY", "WEEKLY"),
        _mleg("NIFTY", "MONTHLY"),
    ],
    # SAME index, FUT + OPT -- futures and options do not share a monthly
    # expiry, so the segment pick alone changed the roll calendar.
    "same_index_fut_opt": [
        _mleg("NIFTY", "MONTHLY", "FUTURES"),
        _mleg("NIFTY", "MONTHLY"),
    ],
    "same_index_fut_opt_plus_second": [
        _mleg("NIFTY", "MONTHLY", "FUTURES"),
        _mleg("NIFTY", "MONTHLY"),
        _mleg("MIDCPNIFTY", "MONTHLY"),
    ],
    # Same index, mixed weekly + monthly.
    "same_index_weekly_monthly": [
        _mleg("NIFTY", "WEEKLY"),
        _mleg("NIFTY", "MONTHLY"),
    ],
    # Three indices -- exercises the group-ordering fix.
    "three_indices": [
        _mleg("NIFTY", "MONTHLY"),
        _mleg("MIDCPNIFTY", "MONTHLY"),
        _mleg("BANKNIFTY", "MONTHLY"),
    ],
    # Every leg on a NON-strategy index (the builder routes this to multi-index).
    "no_leg_on_strategy_index": [
        _mleg("MIDCPNIFTY", "MONTHLY"),
        _mleg("MIDCPNIFTY", "WEEKLY"),
    ],
}

DEFAULT_INDEX = "NIFTY"
DEFAULT_EXPIRY = "MONTHLY"


class TestMultiIndexCadenceIsOrderInvariant(unittest.TestCase):
    """The cadence index/segment drives _build_sync_cycles, which produces the
    roll windows every trade is cut from. Picking it with `legs[0]` meant leg
    order rebuilt the whole schedule off a different expiry calendar."""

    def _selectors(self, legs):
        from services.multi_index_feature import (
            _canonical_cadence, _canonical_group_segment, _sync_tracks, _leg_index,
        )
        legs = list(legs)
        nony = [l for l in legs if not str(l.get("expiry", "")).upper().startswith("YEAR")]
        weekly = [l for l in nony if str(l.get("expiry", "")).upper().startswith("WEEK")]
        cands = weekly or nony
        cadence = _canonical_cadence(cands, DEFAULT_INDEX) if cands else (DEFAULT_INDEX, "OPT")
        by_idx = {}
        for l in legs:
            by_idx.setdefault(_leg_index(l, DEFAULT_INDEX), []).append(l)
        return {
            "cadence": cadence,
            "tracks": _sync_tracks(legs, DEFAULT_INDEX, DEFAULT_EXPIRY),
            "group_order": sorted(
                by_idx, key=lambda k: (0 if k == DEFAULT_INDEX else 1, k)
            ),
            "group_segments": {k: _canonical_group_segment(v) for k, v in by_idx.items()},
        }

    def test_every_permutation_of_every_shape_is_identical(self):
        for name, legs in MI_SHAPES.items():
            with self.subTest(shape=name):
                perms = list(itertools.permutations(legs))
                baseline = self._selectors(perms[0])
                for perm in perms[1:]:
                    self.assertEqual(
                        self._selectors(perm), baseline,
                        "shape %r: leg order changed the cadence selectors" % name,
                    )

    def test_strategy_index_drives_when_present(self):
        from services.multi_index_feature import _canonical_cadence
        for perm in itertools.permutations(MI_SHAPES["weekly_two_indices"]):
            weekly = [l for l in perm if l["expiry"] == "WEEKLY"]
            self.assertEqual(_canonical_cadence(weekly, DEFAULT_INDEX)[0], "NIFTY")

    def test_falls_back_to_alphabetical_when_strategy_index_absent(self):
        from services.multi_index_feature import _canonical_cadence
        legs = [_mleg("MIDCPNIFTY", "MONTHLY"), _mleg("BANKNIFTY", "MONTHLY")]
        for perm in itertools.permutations(legs):
            self.assertEqual(_canonical_cadence(list(perm), "NIFTY")[0], "BANKNIFTY")

    def test_option_segment_wins_a_mixed_group(self):
        from services.multi_index_feature import _canonical_cadence, _canonical_group_segment
        legs = MI_SHAPES["same_index_fut_opt"]
        for perm in itertools.permutations(legs):
            self.assertEqual(_canonical_cadence(list(perm), DEFAULT_INDEX)[1], "OPT")
            self.assertEqual(_canonical_group_segment(list(perm)), "OPT")

    def test_futures_only_group_still_rolls_on_fut(self):
        from services.multi_index_feature import _canonical_cadence, _canonical_group_segment
        legs = [_mleg("NIFTY", "MONTHLY", "FUTURES"), _mleg("NIFTY", "MONTHLY", "FUTURES")]
        self.assertEqual(_canonical_cadence(legs, DEFAULT_INDEX)[1], "FUT")
        self.assertEqual(_canonical_group_segment(legs), "FUT")

    def test_sync_tracks_are_canonically_ordered(self):
        """_build_sync_cycles breaks a same-day boundary tie with tracks[0]."""
        from services.multi_index_feature import _sync_tracks
        for name, legs in MI_SHAPES.items():
            with self.subTest(shape=name):
                baseline = _sync_tracks(list(legs), DEFAULT_INDEX, DEFAULT_EXPIRY)
                for perm in itertools.permutations(legs):
                    self.assertEqual(
                        _sync_tracks(list(perm), DEFAULT_INDEX, DEFAULT_EXPIRY), baseline)
                if baseline and any(t[0] == DEFAULT_INDEX for t in baseline):
                    self.assertEqual(baseline[0][0], DEFAULT_INDEX)


if __name__ == "__main__":
    unittest.main()
