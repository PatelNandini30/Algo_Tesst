"""
Unit tests for the Midcap100 cross-index overlay math (services/midcap_overlay).

Uses an injected fake close-lookup so the math is verified with no DB / Rust /
network. The headline assertion reproduces the user's sample workbook:
    entry 2019-02-28 close 16721.10, exit 2019-03-28 close 18083.45,
    hypothetical BUY @ 0.5%/month, NIFTY CE-Sell leg P&L -603.25
  → Midcap Spot P&L 1362.35, hypothetical leg 1284.32, combined ~681.07.
"""
import unittest

from services.midcap_overlay import compute_midcap_legs


class FakeLookup:
    def __init__(self, series):
        self._series = dict(series)
        self._sorted = sorted(self._series)

    def close(self, iso_date):
        return self._series.get(iso_date)

    def closes_in_range(self, start_iso, end_iso):
        return [(d, self._series[d]) for d in self._sorted if start_iso < d <= end_iso]

    def mae_mfe(self, entry_iso, exit_iso, entry_spot, position):
        # Treat high=low=close for the fake (no intraday) so MAE/MFE come from closes.
        pts = [self._series[d] for d in self._sorted if entry_iso <= d <= exit_iso]
        if not pts or not entry_spot:
            return (0.0, 0.0)
        mx, mn = max(pts), min(pts)
        if str(position).upper() == "SELL":
            return (round((entry_spot - mx) / entry_spot * 100, 4),
                    round((entry_spot - mn) / entry_spot * 100, 4))
        return (round((mn - entry_spot) / entry_spot * 100, 4),
                round((mx - entry_spot) / entry_spot * 100, 4))


SERIES = {
    "2019-02-28": 16721.10,
    "2019-03-05": 16850.00,   # +0.77% from entry (for spot-adj scan)
    "2019-03-15": 17400.00,
    "2019-03-28": 18083.45,
}

ROW = {
    "trade_id": 1,
    "entry_date": "28-02-2019",
    "exit_date": "28-03-2019",
    "nifty_pnl": -603.25,
    "nifty_pnl_pct": -5.589529766,  # -603.25 / 10792.5 * 100
}


class TestMidcapOverlay(unittest.TestCase):
    def _run(self, legs, sa=None):
        return compute_midcap_legs(
            [dict(ROW)],
            midcap_legs=legs,
            midcap_spot_adjustment=sa,
            lookup=FakeLookup(SERIES),
        )

    def test_hypothetical_buy_matches_sample(self):
        out = self._run([{"midcap_mode": "hypothetical", "position": "buy",
                          "cost_pct_per_month": 0.5, "lots": 1}])
        self.assertTrue(out["available"])
        r = out["results"][0]
        self.assertAlmostEqual(r["Midcap Entry Spot"], 16721.10, places=2)
        self.assertAlmostEqual(r["Midcap Exit Spot"], 18083.45, places=2)
        self.assertAlmostEqual(r["Midcap Spot P&L"], 1362.35, places=2)
        self.assertAlmostEqual(r["Midcap Spot P&L %"], 8.1475, places=2)
        self.assertEqual(r["Midcap No Of Days"], 28)
        self.assertAlmostEqual(r["Midcap Rollover Cost %"], 0.46667, places=3)
        self.assertAlmostEqual(r["Midcap Leg P&L"], 1284.32, places=1)
        self.assertAlmostEqual(r["Midcap Leg P&L %"], 7.6808, places=2)
        self.assertAlmostEqual(r["Combined Net P&L"], 681.07, places=1)
        self.assertAlmostEqual(r["Combined Net P&L %"], 2.0913, places=2)

    def test_spot_mode_buy(self):
        out = self._run([{"midcap_mode": "spot", "position": "buy", "lots": 1}])
        r = out["results"][0]
        self.assertAlmostEqual(r["Midcap Leg P&L"], 1362.35, places=2)
        self.assertAlmostEqual(r["Midcap Rollover Cost %"], 0.0, places=6)
        # combined = -603.25 + 1362.35
        self.assertAlmostEqual(r["Combined Net P&L"], 759.10, places=1)

    def test_sell_negation(self):
        out = self._run([{"midcap_mode": "hypothetical", "position": "sell",
                          "cost_pct_per_month": 0.5, "lots": 1}])
        r = out["results"][0]
        # short an index that rallied → loss, plus carry cost.
        self.assertLess(r["Midcap Leg P&L"], 0.0)
        self.assertAlmostEqual(r["Midcap Leg P&L"], -1362.35 - (0.005 * 28 / 30) * 16721.10, places=1)

    def test_spot_adjustment_rise_triggers_early_exit(self):
        # rise 0.5% target = 16721.10 * 1.005 = 16804.7; first breach 2019-03-05 (16850).
        out = self._run(
            [{"midcap_mode": "spot", "position": "buy", "lots": 1}],
            sa={"enabled": True, "direction": "rise", "pct": 0.5, "units": "percent"},
        )
        r = out["results"][0]
        self.assertEqual(r["Midcap Exit Date"], "05-03-2019")
        self.assertEqual(r["Midcap No Of Days"], 5)
        self.assertAlmostEqual(r["Midcap Exit Spot"], 16850.00, places=2)
        self.assertAlmostEqual(r["Midcap Spot P&L"], 128.90, places=2)

    def test_missing_date_flagged_not_crash(self):
        bad = dict(ROW)
        bad["entry_date"] = "01-01-1990"  # not in series
        out = compute_midcap_legs(
            [bad], midcap_legs=[{"midcap_mode": "spot", "position": "buy"}],
            lookup=FakeLookup(SERIES),
        )
        self.assertFalse(out["available"])
        self.assertFalse(out["results"][0]["available"])


class OHLCFakeLookup:
    """Fake lookup that also serves daily OHLC, so the Midcap MAE/MFE formula
    (which scans High/Low over (entry, exit]) can be verified with no DB/Rust."""
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


class TestMidcapMaeMfeMatchesWorkbook(unittest.TestCase):
    """Reproduces the reference workbook's Midcap MAE/MFE for trade 1
    (entry 26-Mar-2019, exit 28-Mar-2019, Hypo BUY @ 0.5%/mo):
      f_entry = 17806.25 * (1 + 0.5%/mo * 2/30) = 17812.185  (Hypo close on entry)
      scan (entry, exit] EXCLUDING the entry-day bar:
        27-Mar hypo_low  = 17848.95*(1+0.5%*1/30)  = 17851.925  -> min
        28-Mar hypo_high = 18131.6 (carry 0 at exit)            -> max
      MFE = (18131.6/17812.185 - 1)*100   = 1.7932 %
      MAE = (17851.925/17812.185 - 1)*100 = 0.2231 %  (positive: never went adverse)
    Workbook stores these as fractions (0.0179324 / 0.0022310); we emit percent."""
    OHLC = {
        "2019-03-26": (17664.6, 17818.5, 17645.0, 17806.25),   # entry day (excluded from scan)
        "2019-03-27": (17881.8, 17991.65, 17848.95, 17898.5),
        "2019-03-28": (17954.8, 18131.6, 17929.7, 18083.45),   # exit day
    }
    ROW = {"trade_id": 1, "entry_date": "26-03-2019", "exit_date": "28-03-2019",
           "nifty_pnl": 3.25, "nifty_pnl_pct": 0.0283}

    def test_midcap_mae_mfe(self):
        out = compute_midcap_legs(
            [dict(self.ROW)],
            midcap_legs=[{"midcap_mode": "hypothetical", "position": "buy",
                          "cost_pct_per_month": 0.5, "lots": 1}],
            midcap_spot_adjustment=None,
            lookup=OHLCFakeLookup(self.OHLC),
        )
        r = out["results"][0]
        self.assertAlmostEqual(r["Midcap MFE"], 1.7932, places=3)
        self.assertAlmostEqual(r["Midcap MAE"], 0.2231, places=3)

    def test_midcap_mae_mfe_sell_uses_same_carry_addition(self):
        out = compute_midcap_legs(
            [dict(self.ROW)],
            midcap_legs=[{"midcap_mode": "hypothetical", "position": "sell",
                          "cost_pct_per_month": 0.5, "lots": 1}],
            midcap_spot_adjustment=None,
            lookup=OHLCFakeLookup(self.OHLC),
        )
        r = out["results"][0]
        self.assertAlmostEqual(r["Midcap MFE"], -0.2231, places=3)
        self.assertAlmostEqual(r["Midcap MAE"], -1.7932, places=3)

    def test_entry_day_bar_excluded(self):
        # If the entry-day bar (low 17645) were included, MAE would be strongly
        # negative (~-0.94%). Excluding it yields the positive workbook value.
        out = compute_midcap_legs(
            [dict(self.ROW)],
            midcap_legs=[{"midcap_mode": "hypothetical", "position": "buy",
                          "cost_pct_per_month": 0.5}],
            lookup=OHLCFakeLookup(self.OHLC),
        )
        self.assertGreater(out["results"][0]["Midcap MAE"], 0.0)


class TestMidcapRustPythonParity(unittest.TestCase):
    """Rust (native compute_midcap_legs) must equal the Python reference, field
    for field, on real index_ohlc data. Skips if the native build lacks it."""

    ROWS = [
        {"trade_id": "1", "entry_date": "28-02-2019", "exit_date": "28-03-2019", "nifty_pnl": -603.25, "nifty_pnl_pct": -5.59},
        {"trade_id": "2", "entry_date": "26-09-2019", "exit_date": "03-10-2019", "nifty_pnl": 98.65, "nifty_pnl_pct": 0.85},
        {"trade_id": "3", "entry_date": "04-02-2021", "exit_date": "11-02-2021", "nifty_pnl": -119.65, "nifty_pnl_pct": -0.80},
        {"trade_id": "bad", "entry_date": "01-01-1990", "exit_date": "02-01-1990", "nifty_pnl": 0, "nifty_pnl_pct": 0},
    ]
    CASES = [
        ([{"midcap_mode": "hypothetical", "position": "buy", "cost_pct_per_month": 0.5, "lots": 1}], None),
        ([{"midcap_mode": "spot", "position": "sell", "lots": 1}], None),
        ([{"midcap_mode": "hypothetical", "position": "buy", "cost_pct_per_month": 0.5}],
         {"enabled": True, "direction": "rise", "pct": 0.5, "units": "percent"}),
    ]

    def test_rust_matches_python(self):
        from services import rust_fast_path, index_ohlc_store
        from services.midcap_overlay import compute_midcap_legs
        if not rust_fast_path.compute_midcap_legs_available():
            self.skipTest("native compute_midcap_legs not built yet")
        if not index_ohlc_store.ensure_index_ohlc_loaded("NIFTYMIDCAP100"):
            self.skipTest("index_ohlc not loaded (no DB/feather)")
        for legs, sa in self.CASES:
            py = compute_midcap_legs(self.ROWS, midcap_legs=legs, midcap_spot_adjustment=sa, symbol="NIFTYMIDCAP100")
            ru = rust_fast_path.compute_midcap_legs(self.ROWS, legs, sa, "NIFTYMIDCAP100")
            self.assertIsNotNone(ru, f"rust returned None (legs={legs})")
            self.assertEqual(py["available"], ru["available"], f"available differs (legs={legs})")
            self.assertEqual(len(py["results"]), len(ru["results"]))
            for pr, rr in zip(py["results"], ru["results"]):
                self.assertEqual(bool(pr.get("available")), bool(rr.get("available")),
                                 f"row available differs (legs={legs}, row={pr.get('trade_id')})")
                for k, v in pr.items():
                    if isinstance(v, bool) or v is None:
                        continue
                    if isinstance(v, (int, float)):
                        self.assertAlmostEqual(float(v), float(rr.get(k)), delta=0.001,
                                               msg=f"{k} differs (legs={legs}, row={pr.get('trade_id')})")
                    elif k == "Midcap Exit Date":
                        self.assertEqual(v, rr.get(k), f"exit date differs (legs={legs})")
            for k in ("midcap_leg_pnl_sum", "midcap_leg_pnl_pct_sum", "combined_pnl_sum", "combined_pnl_pct_sum"):
                self.assertAlmostEqual(float(py["summary"][k]), float(ru["summary"][k]), delta=0.001,
                                       msg=f"summary {k} differs (legs={legs})")


if __name__ == "__main__":
    unittest.main()
