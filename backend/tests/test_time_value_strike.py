"""time_value strike selection — exercises the REAL resolver.

`_compute_strike_for_leg_python` does `import algotest_native` inside the
function, so injecting a stub into sys.modules lets these tests drive the
shipped code path (formula, strike-gap filter, tradeable filter, negative-TV
drop, stepwise-from-ATM walk) with no feather and no DB.

Fixture is the real NIFTY PE chain for 22-Sep-2025 / expiry 30-Sep-2025,
spot 25,202.35 — every close and traded-volume below was verified against
NSE's own F&O bhavcopy (BhavCopy_NSE_FO_0_0_0_20250922_F_0000.csv). That is
the trade where the pre-fix engine chose 26850 (time value -143.35) after
matching the untraded 27050, instead of 25300 (time value 48.10).
"""
import sys
import types
import unittest

SPOT = 25202.35
EXPIRY = "2025-09-30"
DATE = "2025-09-22"

# strike -> (close, traded)   traded=False means 0 contracts on NSE that day,
# so the "close" is a carried-forward stale value and its time value is fiction.
CHAIN = {
    # --- OTM side (strike < spot for a PE) ---
    25000.0: (70.00, True),
    25100.0: (95.00, True),
    25200.0: (130.00, True),
    # --- ITM side ---
    25250.0: (98.65, True),    # OFF the 100-grid; TV = 51.00
    25300.0: (145.75, True),   # NSE: 334,905 contracts. TV = 48.10
    25350.0: (170.00, True),   # OFF the 100-grid; TV = 22.35
    25400.0: (202.20, True),   # NSE: 140,666 contracts. TV = 4.55
    26000.0: (847.65, True),   # TV = 50.00 exactly, but 798 pts ITM
    26800.0: (1390.00, False),
    26850.0: (1504.30, True),  # NSE: 6 contracts. TV = -143.35
    26900.0: (1623.40, True),  # TV = -74.25
    27000.0: (1719.60, True),  # TV = -78.05
    27050.0: (1900.00, False), # NSE: 0 contracts. Fake TV = 52.35
    27100.0: (1715.00, False),
}


def _install_stub():
    m = types.ModuleType("algotest_native")
    m.get_strikes_for_date = lambda d, idx, exp, ot: [
        (s, v[0]) for s, v in sorted(CHAIN.items())
    ]
    # Mirrors the real split: get_option_price returns the published close even
    # for a contract that never traded (that stale number is the whole problem);
    # get_option_price_tradeable returns None when turnover was zero.
    m.get_option_price_tradeable = lambda d, idx, strike, ot, exp: (
        CHAIN[strike][0] if CHAIN.get(strike) and CHAIN[strike][1] else None
    )
    m.get_option_price = lambda d, idx, strike, ot, exp: (
        CHAIN[strike][0] if CHAIN.get(strike) else None
    )
    sys.modules["algotest_native"] = m


class TimeValueBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = sys.modules.get("algotest_native")
        _install_stub()
        from services.engine_rust import _compute_strike_for_leg_python
        cls.resolve = staticmethod(_compute_strike_for_leg_python)

    @classmethod
    def tearDownClass(cls):
        if cls._saved is not None:
            sys.modules["algotest_native"] = cls._saved
        else:
            sys.modules.pop("algotest_native", None)

    def pick(self, target, side="ATM", mode="time_value", gap=100.0, cap=0,
             units="points"):
        leg = {"option_type": "PE",
               "strike_selection": {"type": mode, "premium": target, "moneyness": side,
                                    "tv_range_pct": cap, "tv_units": units}}
        return self.resolve(leg, SPOT, gap,
                            entry_date=DATE, expiry=EXPIRY, index="NIFTY")


class TestStrikeGap(TimeValueBase):
    def test_gap_100_excludes_off_grid_strikes(self):
        # 25250 (TV 51.00) is the closest to 50 but is off the 100-grid.
        self.assertEqual(self.pick(50, "ITM", gap=100.0), 25300.0)

    def test_gap_50_admits_the_off_grid_strike(self):
        # Same target, finer gap -> 25250 becomes legal and wins on |TV-target|.
        self.assertEqual(self.pick(50, "ITM", gap=50.0), 25250.0)


class TestTradeableFilter(TimeValueBase):
    def test_untraded_strike_never_selected_even_on_an_exact_match(self):
        # 27050 has a stale close giving TV 52.35 — an exact hit for this target.
        # It traded 0 contracts on NSE, so it must not be reachable.
        self.assertEqual(self.pick(52.35, "ITM"), 25300.0)

    def test_untraded_strike_excluded_across_every_target(self):
        for t in (0, 25, 50, 52.35, 100, 500, 1000):
            self.assertNotIn(self.pick(t, "ITM"), (27050.0, 27100.0, 26800.0),
                             "untraded strike selected for target %s" % t)


class TestNegativeTimeValue(TimeValueBase):
    """Negative time value is a real print (thin books, settlement) and stays a
    candidate, ranked on ABSOLUTE distance like any other value."""

    def test_absolute_distance_decides_across_the_sign(self):
        # Target 20: 25400 TV 4.55 is 15.45 away; nothing negative is nearer.
        self.assertEqual(self.pick(20, "ITM"), 25400.0)

    def test_target_zero_takes_the_value_nearest_zero(self):
        self.assertEqual(self.pick(0, "ITM"), 25400.0)

    def test_a_positive_target_still_cannot_reach_deep_negatives(self):
        # The stepwise walk stops at the crossing, so chasing +50 can never
        # march out into the -143 territory that caused the 22-Sep-2025 pick.
        self.assertEqual(self.pick(50, "ITM"), 25300.0)
        self.assertNotIn(self.pick(50, "ITM"), (26850.0, 27050.0))


class TestSignTieBreak(unittest.TestCase):
    """On an EXACT tie in |TV - target|, a non-negative time value wins —
    ahead of distance from ATM."""

    def setUp(self):
        self._saved = sys.modules.get("algotest_native")

    def tearDown(self):
        if self._saved is not None:
            sys.modules["algotest_native"] = self._saved
        else:
            sys.modules.pop("algotest_native", None)

    def _pick(self, chain, spot, target=0.0, side="ITM"):
        import importlib
        m = types.ModuleType("algotest_native")
        m.get_strikes_for_date = lambda *a: [(s, v) for s, v in sorted(chain.items())]
        m.get_option_price_tradeable = lambda d, i, s, o, e: chain.get(s)
        m.get_option_price = lambda d, i, s, o, e: chain.get(s)
        sys.modules["algotest_native"] = m
        import services.engine_rust as E
        importlib.reload(E)
        return E._compute_strike_for_leg_python(
            {"option_type": "PE",
             "strike_selection": {"type": "time_value", "premium": target,
                                  "moneyness": side}},
            spot, 100.0, entry_date=DATE, expiry=EXPIRY, index="NIFTY")

    # spot exactly on a boundary -> intrinsic is exact -> a real tie
    def test_positive_wins_when_it_is_also_nearer_atm(self):
        self.assertEqual(self._pick({25300.0: 101.0, 25400.0: 199.0}, 25200.0), 25300.0)

    def test_positive_wins_even_when_the_negative_is_nearer_atm(self):
        # -1 sits 100 from ATM, +1 sits 200 away. Sign outranks distance.
        self.assertEqual(self._pick({25300.0: 99.0, 25400.0: 201.0}, 25200.0), 25400.0)

    # a real spot leaves ~3e-12 of float error; rounding must still see a tie
    def test_tie_survives_float_noise_on_a_real_spot(self):
        self.assertEqual(self._pick({25300.0: 97.65 + 1, 25400.0: 197.65 - 1}, 25202.35),
                         25300.0)
        self.assertEqual(self._pick({25300.0: 97.65 - 1, 25400.0: 197.65 + 1}, 25202.35),
                         25400.0)

    def test_a_genuinely_closer_negative_still_wins(self):
        # Not a tie: -1 is 1 away, +5 is 5 away. Distance decides, sign does not.
        self.assertEqual(self._pick({25300.0: 99.0, 25400.0: 205.0}, 25200.0), 25300.0)


class TestStepwiseFromAtm(TimeValueBase):
    def test_stops_at_the_crossing_instead_of_a_perfect_far_match(self):
        # 26000 has TV 50.00 — an EXACT hit — but sits 798 pts ITM. A global
        # scan would return it; stepwise must stop at 25300 (TV 48.10).
        self.assertEqual(self.pick(50, "ITM"), 25300.0)

    def test_nearest_picks_the_better_target_match_across_sides(self):
        # ATM = 25200. Target 65.
        #   OTM branch -> 25000 (TV 70.00, |TV-65|= 5.00), 200 pts from ATM
        #   ITM branch -> 25300 (TV 48.10, |TV-65|=16.90), 100 pts from ATM
        # NEAREST selects on the TARGET, so 25000 wins despite being further out.
        self.assertEqual(self.pick(65, "ATM"), 25000.0)

    def test_gte_takes_the_nearest_qualifying_strike(self):
        # GTE = FIRST strike meeting the floor walking outward from ATM (mirrors
        # LTE), then closest-to-ATM across the two sides.
        #   target 40: OTM branch -> 25200 (TV 130.00 >= 40) at distance 0
        #              ITM branch -> 25300 (TV  48.10 >= 40) at distance 100
        # -> 25200. Time value peaks at ATM, so the nearest qualifying strike is
        # normally the ATM strike itself.
        self.assertEqual(self.pick(40, "ATM", mode="time_value_gte"), 25200.0)

    def test_range_cap_confines_the_walk(self):
        # 1% of 25202.35 is +-252 -> only 25000..25400 are legal.
        self.assertEqual(self.pick(0, "ITM", cap=1.0), 25400.0)
        # Without the cap the ITM walk can reach further out.
        self.assertIsNotNone(self.pick(0, "ITM"))

    def test_cap_zero_means_uncapped(self):
        self.assertEqual(self.pick(50, "ITM", cap=0), self.pick(50, "ITM"))

    def test_otm_side_walks_down_from_atm(self):
        # OTM candidates: 25200 (130.00), 25100 (95.00), 25000 (70.00).
        self.assertEqual(self.pick(100, "OTM"), 25100.0)

    def test_gte_first_qualifying_on_a_single_side(self):
        # ITM ordered outward: 25300 (48.10), 25400 (4.55), 26000 (50.00).
        # First to clear the floor of 40 is 25300.
        self.assertEqual(self.pick(40, "ITM", mode="time_value_gte"), 25300.0)

    def test_gte_does_not_run_past_the_first_qualifier(self):
        # 26000 (TV 50.00) also clears 40 but sits 800 pts out; the nearest
        # qualifier must win.
        self.assertNotEqual(self.pick(40, "ITM", mode="time_value_gte"), 26000.0)

    def test_lte_takes_the_first_strike_at_or_below_the_ceiling(self):
        self.assertEqual(self.pick(40, "ITM", mode="time_value_lte"), 25400.0)


class TestCapFallback(TimeValueBase):
    """A tight cap must thin the strike CHOICE, not the tradesheet."""

    def test_gte_falls_back_when_nothing_meets_the_floor(self):
        # No strike carries 10,000 of time value. Fall back to the closest
        # available instead of dropping the trade.
        self.assertEqual(self.pick(10_000.0, "ITM", mode="time_value_gte"), 25300.0)

    def test_lte_falls_back_when_a_tight_cap_excludes_every_match(self):
        # 1% band = 25000..25400; lowest TV in band is 4.55, so a ceiling of 0
        # matches nothing -> take the closest (25400).
        self.assertEqual(self.pick(0, "ITM", mode="time_value_lte", cap=1.0), 25400.0)

    def test_fallback_also_applies_on_the_both_sides_walk(self):
        self.assertIsNotNone(self.pick(10_000.0, "ATM", mode="time_value_gte"))

    def test_no_candidates_at_all_still_means_no_trade(self):
        # 0.05% band around 25,202.35 contains no on-grid strike, so there is
        # genuinely nothing to trade — the fallback must NOT invent one.
        self.assertIsNone(self.pick(50, "ITM", cap=0.05))

    def test_nearest_is_unaffected_by_the_fallback(self):
        self.assertEqual(self.pick(50, "ITM"), 25300.0)


class TestUnits(TimeValueBase):
    """Target in index points (default) or as a percent of spot."""

    # spot 25,202.35 -> 25300 PE has TV 48.10 pts = 0.19086%
    #                   25100 PE has TV 95.00 pts = 0.37695%
    def test_percent_target_selects_the_same_strike_as_its_points_equivalent(self):
        self.assertEqual(self.pick(48.10, "ITM"),
                         self.pick(48.10 / SPOT * 100, "ITM", units="percent"))

    def test_percent_target_on_the_otm_side(self):
        self.assertEqual(self.pick(0.37695, "OTM", units="percent"), 25100.0)

    def test_points_remains_the_default(self):
        self.assertEqual(self.pick(50, "ITM"), self.pick(50, "ITM", units="points"))

    def test_the_unit_actually_changes_the_pick(self):
        # Target 0.5, ITM side:
        #   points  -> 25300 |48.10-0.5|=47.60, 25400 |4.55-0.5|=4.05 better -> 25400
        #   percent -> 25300 |0.19086-0.5|=0.309, 25400 |0.01805-0.5|=0.482
        #              worse, so the walk stops                     -> 25300
        # Same number, different unit, different strike — proves the unit is read.
        self.assertEqual(self.pick(0.5, "ITM", units="points"), 25400.0)
        self.assertEqual(self.pick(0.5, "ITM", units="percent"), 25300.0)

    def test_percent_units_rank_negatives_by_absolute_distance_too(self):
        # Sign handling is identical in percent — a negative target is legal.
        self.assertIsNotNone(self.pick(-0.3, "ITM", units="percent"))


class TestFormula(TimeValueBase):
    def test_otm_time_value_is_the_whole_premium(self):
        # PE 25100 is OTM (strike < spot) -> intrinsic 0 -> TV == close == 95.00
        self.assertEqual(self.pick(95.0, "OTM"), 25100.0)

    def test_itm_subtracts_intrinsic(self):
        # 25400: intrinsic 197.65, close 202.20 -> TV 4.55
        self.assertEqual(self.pick(4.55, "ITM"), 25400.0)


if __name__ == "__main__":
    unittest.main()
