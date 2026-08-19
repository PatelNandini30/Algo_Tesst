"""Layout of the merged optimizer WOW/MOM grid (`write_merged_wow_mom`).

Blocks must ALWAYS flow horizontally first — across the band, then wrap to a
band below inside the same strike group. Regression cover for the two bugs that
made a real download come out as two misaligned vertical stacks:
  1. combos whose Redis row was dropped by `_dedupe_by_label` lost their
     metadata and keyed on "No Adj" while the survivors keyed on
     "NoAdjustment" — one adjustment, two column groups;
  2. any param the combo label doesn't encode (SL, target, DTE…) collapsed
     every variant into one cell, which the collision handler stacked downward.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openpyxl import Workbook  # noqa: E402

from services.optimizer.wow_mom import (  # noqa: E402
    adj_label_from_combo_label,
    variant_labels,
    write_merged_wow_mom,
)


def _cleaned(seed: float):
    """Two trades in different years so every block has identical dimensions."""
    return [
        {"Expiry": "2024-01-25", "Exit Date": "2024-01-25",
         "% P&L": 1.0 + seed, "%DD": -2.0, "Actual Live DD": -1.5},
        {"Expiry": "2025-02-27", "Exit Date": "2025-02-27",
         "% P&L": 2.0 + seed, "%DD": -3.0, "Actual Live DD": -2.5},
    ]


def _combo(strike, adj, variant="", seed=0.0):
    return {
        "title": f"{strike} | {adj}" + (f" | {variant}" if variant else ""),
        "cleaned": _cleaned(seed),
        "has_midcap": False,
        "adj_key": adj,
        "adj_label": adj,
        "row_key": f"{strike}||",
        "variant_label": variant,
    }


def _blocks(ws):
    """{title: (row, col)} for every block header written on the sheet."""
    out = {}
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if isinstance(v, str) and " | " in v:
                out[v] = (cell.row, cell.column)
    return out


def _build(combos):
    wb = Workbook()
    wb.remove(wb.active)
    if not write_merged_wow_mom(wb, combos):
        raise AssertionError("write_merged_wow_mom returned False (no trades?)")
    return _blocks(wb["WOW Summary"]), _blocks(wb["MOM Summary"])


class TestMergedWowMomLayout(unittest.TestCase):

    def test_adjustments_get_one_fixed_column_each(self):
        """No Adj / Rise / Fall / Rise or Fall run left to right on one band."""
        adjs = ["No Adj", "Rise 1%", "Fall 1%", "Rise or Fall 1%"]
        wow, _ = _build([_combo("PE ATM", a, seed=i) for i, a in enumerate(adjs)])
        rows = {t: rc[0] for t, rc in wow.items()}
        cols = sorted(rc[1] for rc in wow.values())
        self.assertEqual(len(set(rows.values())), 1, "all four must share one band")
        self.assertEqual(len(set(cols)), 4, "all four must sit in distinct columns")
        # Ordered No Adj → Rise → Fall → Rise or Fall.
        by_col = sorted(wow.items(), key=lambda kv: kv[1][1])
        self.assertEqual([t.split(" | ")[1] for t, _ in by_col], adjs)

    def test_single_adjustment_variants_wrap_four_across(self):
        """One adjustment + six variants → 4 across, then 2 on the band below."""
        combos = [_combo("PE ATM", "No Adj", f"SL {v}", seed=i)
                  for i, v in enumerate((10, 20, 30, 40, 50, 60))]
        for sheet in _build(combos):
            bands = {}
            for title, (r, c) in sheet.items():
                bands.setdefault(r, []).append(c)
            self.assertEqual(len(bands), 2, "six variants must occupy two bands")
            widths = sorted(len(v) for v in bands.values())
            self.assertEqual(widths, [2, 4], "4 across, remainder wraps below")
            for cols in bands.values():
                self.assertEqual(len(set(cols)), len(cols), "no two blocks overlap")

    def test_variants_never_stack_inside_a_band(self):
        """Same strike + same adjustment: variants go across, not down."""
        combos = [_combo("PE ATM", "No Adj", f"SL {v}", seed=i)
                  for i, v in enumerate((10, 20, 30))]
        wow, mom = _build(combos)
        for sheet in (wow, mom):
            self.assertEqual(len(sheet), 3)
            self.assertEqual(len({rc[0] for rc in sheet.values()}), 1)
            self.assertEqual(len({rc[1] for rc in sheet.values()}), 3)

    def test_strike_groups_stack_below_each_other(self):
        """Each strike keeps its own band(s); strikes stack, columns realign."""
        combos = []
        for si, strike in enumerate(("PE ATM", "PE 1% OTM")):
            for vi, sl in enumerate((10, 20)):
                combos.append(_combo(strike, "No Adj", f"SL {sl}", seed=si * 10 + vi))
        wow, _ = _build(combos)
        rows_by_strike = {}
        for title, (r, c) in wow.items():
            rows_by_strike.setdefault(title.split(" | ")[0], []).append((r, c))
        self.assertEqual(len(rows_by_strike), 2)
        for placed in rows_by_strike.values():
            self.assertEqual(len({r for r, _ in placed}), 1, "one band per strike")
            self.assertEqual(len({c for _, c in placed}), 2, "variants side by side")
        # Columns line up across strike groups.
        atm, otm = rows_by_strike["PE ATM"], rows_by_strike["PE 1% OTM"]
        self.assertEqual(sorted(c for _, c in atm), sorted(c for _, c in otm))
        self.assertNotEqual({r for r, _ in atm}, {r for r, _ in otm})

    def test_no_collisions_keeps_original_single_column_grid(self):
        """One combo per (strike, adj) → the pre-existing layout, unchanged."""
        combos = [_combo(s, "No Adj", seed=i)
                  for i, s in enumerate(("PE ATM", "PE 1% OTM", "PE 2% OTM"))]
        wow, _ = _build(combos)
        self.assertEqual(len({rc[1] for rc in wow.values()}), 1, "single column")
        self.assertEqual(len({rc[0] for rc in wow.values()}), 3, "three rows")
        self.assertEqual(min(rc[1] for rc in wow.values()), 1, "starts at column A")

    def test_adjustment_columns_survive_multiple_variants(self):
        """4 adjustments x 2 variants → 4 fixed columns, 2 bands, aligned."""
        combos = []
        for vi, sl in enumerate((10, 20)):
            for ai, adj in enumerate(("No Adj", "Rise 1%", "Fall 1%", "Rise or Fall 1%")):
                combos.append(_combo("PE ATM", adj, f"SL {sl}", seed=vi * 4 + ai))
        wow, _ = _build(combos)
        bands = {}
        for title, (r, c) in wow.items():
            bands.setdefault(r, []).append((title.split(" | ")[1], c))
        self.assertEqual(len(bands), 2)
        col_of_adj = [dict(b) for b in bands.values()]
        self.assertEqual(col_of_adj[0], col_of_adj[1],
                         "an adjustment must keep the same column on every band")


    def test_identical_blocks_collapse_instead_of_stacking(self):
        """A gated sweep (direction varies while enabled=false) repeats a run —
        emit it once, not three stacked copies of the same numbers."""
        combos = [_combo("PE ATM", "No Adj", seed=0.0) for _ in range(3)]
        wow, mom = _build(combos)
        self.assertEqual(len(wow), 1)
        self.assertEqual(len(mom), 1)
        self.assertEqual(list(wow.values())[0], (1, 1))

    def test_different_results_are_kept_side_by_side(self):
        """Same cell, DIFFERENT numbers → two columns, never collapsed."""
        combos = [_combo("PE ATM", "No Adj", seed=0.0), _combo("PE ATM", "No Adj", seed=5.0)]
        wow, _ = _build(combos)
        self.assertEqual(len(wow), 2)
        self.assertEqual(len({rc[0] for rc in wow.values()}), 1)
        self.assertEqual(len({rc[1] for rc in wow.values()}), 2)

    def test_shared_leg_adjustment_does_not_break_ordering(self):
        """Every combo carries a constant 'Rise 1% (L1)'; only L2 is swept.

        Ordering must run No Adj → Rise → Fall → Rise or Fall on the DIFFERING
        segment — sorting the whole string ties on the shared 'Rise …' prefix
        and falls back to alphabetical, which puts Fall before Rise.
        """
        adjs = [
            "Rise 1% (L1)",
            "Rise 1% (L1) + Fall 1000pts (L2)",
            "Rise 1% (L1) + Rise 1000pts (L2)",
            "Rise 1% (L1) + Rise or Fall 1000pts (L2)",
        ]
        wow, _ = _build([_combo("PE ATM", a, seed=i) for i, a in enumerate(adjs)])
        by_col = [t for t, _ in sorted(wow.items(), key=lambda kv: kv[1][1])]
        self.assertEqual(
            [t.split(" | ", 1)[1] for t in by_col],
            [adjs[0], adjs[2], adjs[1], adjs[3]],
            "columns must read No Adj, Rise, Fall, Rise or Fall",
        )


class TestVariantLabels(unittest.TestCase):

    def test_only_varying_non_title_params_are_named(self):
        combos = {
            "a": {"legs[0].stopLoss.value": 10, "legs[0].expiry": "weekly",
                  "legs[0].strike_selection.value": 0, "exit_dte": 1},
            "b": {"legs[0].stopLoss.value": 20, "legs[0].expiry": "weekly",
                  "legs[0].strike_selection.value": 0, "exit_dte": 1},
        }
        labels = variant_labels(combos)
        self.assertEqual(labels["a"], "L1 SL 10")
        self.assertEqual(labels["b"], "L1 SL 20")

    def test_strike_expiry_shift_adjustment_are_never_named(self):
        combos = {
            "a": {"legs[0].strike_selection.value": 0, "legs[0].expiry": "weekly",
                  "spot_adjustment_pct": 1, "strike_shift_enabled": True},
            "b": {"legs[0].strike_selection.value": 100, "legs[0].expiry": "monthly",
                  "spot_adjustment_pct": 2, "strike_shift_enabled": False},
        }
        self.assertEqual(variant_labels(combos), {})

    def test_constant_params_are_dropped(self):
        combos = {"a": {"exit_dte": 1}, "b": {"exit_dte": 1}}
        self.assertEqual(variant_labels(combos), {})


class TestPerLegAdjustmentLabel(unittest.TestCase):

    def test_reads_per_leg_segment_from_combo_label(self):
        lbl = "NIFTY_M_PE_S_ATM_adj_rise_1pct_M_PE_B_RtL1_T-1_To_T-1"
        self.assertEqual(adj_label_from_combo_label(lbl), "Rise 1%")

    def test_direction_and_units(self):
        self.assertEqual(adj_label_from_combo_label("x_adj_fall_1000pts_y"),
                         "Fall 1000pts")
        self.assertEqual(adj_label_from_combo_label("x_adj_both_2pct_y"),
                         "Rise or Fall 2%")

    def test_multiple_legs_joined(self):
        self.assertEqual(adj_label_from_combo_label("x_adj_rise_1pct_y_adj_fall_1pct_z"),
                         "Rise 1% + Fall 1%")

    def test_absent_returns_empty(self):
        self.assertEqual(adj_label_from_combo_label("CE_ATM_Sell_NoAdjustment"), "")
        self.assertEqual(adj_label_from_combo_label(""), "")


if __name__ == "__main__":
    unittest.main()
