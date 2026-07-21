"""
Task: lot-quantity scaling — Midcap100 overlay (services/midcap_overlay.py).

Prior to this fix, compute_midcap_legs() carried `lots` from the leg config
but never multiplied anything by it (docstring literally said "carried but
not scaled (open item)"). That was harmless while every other P&L path in
this branch was 1x, but once the NIFTY side started scaling Net P&L and
MAE/MFE by lots (Task 2/2b/2c/7), the Midcap side became the ONLY unscaled
leg feeding `Combined Net P&L` and the Net MAE 1/2 cross-pairing
(backend/native/src/summary_metrics.rs:399, nm1 = Midcap MFE + Σ NIFTY MAE),
silently corrupting Combined Live DD / Final MAE at lots > 1.

These tests use an injected fake close/OHLC lookup (no DB/Rust/network),
mirroring backend/tests/test_midcap_overlay.py's fixtures but kept
self-contained here.
"""
import unittest

from services.midcap_overlay import compute_midcap_legs


class FakeLookup:
    """Close-only lookup (spot mode / spot-adjustment scan)."""
    def __init__(self, series):
        self._series = dict(series)
        self._sorted = sorted(self._series)

    def close(self, iso_date):
        return self._series.get(iso_date)

    def closes_in_range(self, start_iso, end_iso):
        return [(d, self._series[d]) for d in self._sorted if start_iso < d <= end_iso]


class OHLCFakeLookup:
    """OHLC lookup so the Midcap MAE/MFE excursion scan (_leg_mae_mfe) can be
    exercised without DB/Rust."""
    def __init__(self, ohlc):
        # ohlc: {iso_date: (open, high, low, close)}
        self._o = dict(ohlc)
        self._sorted = sorted(self._o)

    def close(self, iso_date):
        v = self._o.get(iso_date)
        return v[3] if v else None

    def closes_in_range(self, start_iso, end_iso):
        return [(d, self._o[d][3]) for d in self._sorted if start_iso < d <= end_iso]

    def ohlc_in_range(self, start_iso, end_iso):
        return [(d, self._o[d][1], self._o[d][2]) for d in self._sorted if start_iso <= d <= end_iso]


# Same sample-workbook fixture as test_midcap_overlay.py:
#   entry 2019-02-28 close 16721.10, exit 2019-03-28 close 18083.45.
SERIES = {
    "2019-02-28": 16721.10,
    "2019-03-05": 16850.00,
    "2019-03-15": 17400.00,
    "2019-03-28": 18083.45,
}

ROW = {
    "trade_id": 1,
    "entry_date": "28-02-2019",
    "exit_date": "28-03-2019",
    "nifty_pnl": -603.25,
    "nifty_pnl_pct": -5.589529766,
}

# Same OHLC fixture as test_midcap_overlay.py's TestMidcapMaeMfeMatchesWorkbook
# (f_entry = 17806.25 * (1 + 0.5%/mo * 2/30); documented MFE=1.7932 / MAE=0.2231
# at lots=1, hypothetical BUY 0.5%/month).
OHLC = {
    "2019-03-26": (17664.6, 17818.5, 17645.0, 17806.25),   # entry day (excluded from scan)
    "2019-03-27": (17881.8, 17991.65, 17848.95, 17898.5),
    "2019-03-28": (17954.8, 18131.6, 17929.7, 18083.45),   # exit day
}
OHLC_ROW = {
    "trade_id": 1, "entry_date": "26-03-2019", "exit_date": "28-03-2019",
    "nifty_pnl": 3.25, "nifty_pnl_pct": 0.0283,
}


def _run(row, legs, sa=None, lookup=None):
    return compute_midcap_legs(
        [dict(row)], midcap_legs=legs, midcap_spot_adjustment=sa,
        lookup=lookup or FakeLookup(SERIES),
    )


class TestLotsOneIsNoOp(unittest.TestCase):
    """lots=1 must reproduce the documented sample-workbook numbers exactly
    (byte-identical to the pre-fix, pre-lots-field behaviour)."""

    def test_hypothetical_buy_lots_1_matches_workbook(self):
        out = _run(ROW, [{"midcap_mode": "hypothetical", "position": "buy",
                          "cost_pct_per_month": 0.5, "lots": 1}])
        r = out["results"][0]
        self.assertAlmostEqual(r["Midcap Leg P&L"], 1284.32, places=1)
        self.assertAlmostEqual(r["Midcap Leg P&L %"], 7.6808, places=2)
        self.assertAlmostEqual(r["Combined Net P&L"], 681.07, places=1)
        self.assertAlmostEqual(r["Combined Net P&L %"], 2.0913, places=2)

    def test_missing_lots_key_defaults_to_one(self):
        explicit = _run(ROW, [{"midcap_mode": "hypothetical", "position": "buy",
                               "cost_pct_per_month": 0.5, "lots": 1}])
        implicit = _run(ROW, [{"midcap_mode": "hypothetical", "position": "buy",
                               "cost_pct_per_month": 0.5}])  # no "lots" key at all
        self.assertEqual(explicit["results"][0], implicit["results"][0])

    def test_mae_mfe_lots_1_matches_workbook(self):
        out = _run(OHLC_ROW,
                    [{"midcap_mode": "hypothetical", "position": "buy",
                      "cost_pct_per_month": 0.5, "lots": 1}],
                    lookup=OHLCFakeLookup(OHLC))
        r = out["results"][0]
        self.assertAlmostEqual(r["Midcap MFE"], 1.7932, places=3)
        self.assertAlmostEqual(r["Midcap MAE"], 0.2231, places=3)


class TestLegPnlScalesByLots(unittest.TestCase):
    """Midcap leg P&L (and its % counterpart) must scale linearly by the
    leg's own lots — not by lots^2, and Combined Net P&L must be the SUM of
    the (already-scaled) NIFTY + Midcap sides, not re-multiplied again."""

    def _leg(self, lots):
        return [{"midcap_mode": "hypothetical", "position": "buy",
                 "cost_pct_per_month": 0.5, "lots": lots}]

    def test_leg_pnl_scales_linearly(self):
        r1 = _run(ROW, self._leg(1))["results"][0]
        r3 = _run(ROW, self._leg(3))["results"][0]
        self.assertAlmostEqual(r3["Midcap Leg P&L"], r1["Midcap Leg P&L"] * 3, places=2)
        self.assertAlmostEqual(r3["Midcap Leg P&L %"], r1["Midcap Leg P&L %"] * 3, places=2)
        # Explicitly rule out lots^2 (would be *9, not *3).
        self.assertNotAlmostEqual(r3["Midcap Leg P&L"], r1["Midcap Leg P&L"] * 9, places=2)

    def test_combined_net_pnl_is_sum_of_scaled_parts_not_double_scaled(self):
        r1 = _run(ROW, self._leg(1))["results"][0]
        r3 = _run(ROW, self._leg(3))["results"][0]
        nifty_pnl = ROW["nifty_pnl"]
        # Combined Net P&L at 3 lots must equal nifty_pnl (unchanged, NIFTY side
        # scaling lives in the base backtest, not this file) + 3x the 1-lot
        # Midcap leg P&L — i.e. Combined = nifty + lots*leg_1lot, never
        # lots*(nifty + leg_1lot) and never lots^2*leg_1lot.
        expected = nifty_pnl + 3 * r1["Midcap Leg P&L"]
        self.assertAlmostEqual(r3["Combined Net P&L"], expected, places=2)
        not_double_scaled = nifty_pnl * 3 + 3 * r1["Midcap Leg P&L"]
        self.assertNotAlmostEqual(r3["Combined Net P&L"], not_double_scaled, places=2)

    def test_summary_accumulators_sum_already_scaled_rows(self):
        # Two trade rows sharing the same lots=3 leg: the summary sums must be
        # 3x the per-row totals summed at lots=1, not re-scaled again.
        rows = [dict(ROW, trade_id=1), dict(ROW, trade_id=2, entry_date="28-02-2019", exit_date="28-03-2019")]
        out1 = compute_midcap_legs(rows, midcap_legs=self._leg(1), lookup=FakeLookup(SERIES))
        out3 = compute_midcap_legs(rows, midcap_legs=self._leg(3), lookup=FakeLookup(SERIES))
        self.assertAlmostEqual(out3["summary"]["midcap_leg_pnl_sum"],
                                out1["summary"]["midcap_leg_pnl_sum"] * 3, places=2)
        self.assertAlmostEqual(out3["summary"]["combined_pnl_sum"],
                                sum(_f_nifty(r) for r in rows) + out1["summary"]["midcap_leg_pnl_sum"] * 3,
                                places=2)


class TestMaeMfeScalesByLots(unittest.TestCase):
    """Midcap MAE/MFE must scale linearly by the leg's own lots so they stay
    commensurate with the already lot-scaled NIFTY MAE/MFE that
    summary_metrics.rs:399 pairs them with (Net MAE 1 = Midcap MFE + NIFTY
    MAE, Net MAE 2 = Midcap MAE + NIFTY MFE)."""

    def test_mae_mfe_scale_linearly_not_quadratically(self):
        base = _run(OHLC_ROW,
                    [{"midcap_mode": "hypothetical", "position": "buy",
                      "cost_pct_per_month": 0.5, "lots": 1}],
                    lookup=OHLCFakeLookup(OHLC))["results"][0]
        scaled = _run(OHLC_ROW,
                      [{"midcap_mode": "hypothetical", "position": "buy",
                        "cost_pct_per_month": 0.5, "lots": 4}],
                      lookup=OHLCFakeLookup(OHLC))["results"][0]
        self.assertAlmostEqual(scaled["Midcap MFE"], base["Midcap MFE"] * 4, places=3)
        self.assertAlmostEqual(scaled["Midcap MAE"], base["Midcap MAE"] * 4, places=3)
        self.assertNotAlmostEqual(scaled["Midcap MFE"], base["Midcap MFE"] * 16, places=3)


class TestNoCrossLegContamination(unittest.TestCase):
    """Two Midcap legs on the same row with DIFFERENT lots — a 2x/3x ratio
    spread — must combine additively, each scaled by its OWN lots only.
    Rules out both lots^2 and one leg's lots multiplier leaking onto the
    other leg's contribution."""

    def test_two_legs_different_lots_combine_additively(self):
        leg_a_1lot = _run(ROW, [{"midcap_mode": "spot", "position": "buy", "lots": 1}])["results"][0]
        leg_b_1lot = _run(ROW, [{"midcap_mode": "spot", "position": "sell", "lots": 1}])["results"][0]

        combo = _run(ROW, [
            {"midcap_mode": "spot", "position": "buy", "lots": 2},
            {"midcap_mode": "spot", "position": "sell", "lots": 3},
        ])["results"][0]

        expected_leg_pnl = 2 * leg_a_1lot["Midcap Leg P&L"] + 3 * leg_b_1lot["Midcap Leg P&L"]
        self.assertAlmostEqual(combo["Midcap Leg P&L"], expected_leg_pnl, places=2)
        # Sanity: leg_a and leg_b at 1 lot are mirror images (buy vs sell of the
        # same raw spot move), so this is a non-degenerate additive check.
        self.assertAlmostEqual(leg_a_1lot["Midcap Leg P&L"], -leg_b_1lot["Midcap Leg P&L"], places=6)


def _f_nifty(row):
    return row["nifty_pnl"]


if __name__ == "__main__":
    unittest.main()
