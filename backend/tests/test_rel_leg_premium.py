"""Relative to Leg Premium strike mode.

Drives the REAL post-pass (`_apply_rel_leg_premium_to_specs`) against a stub
`algotest_native` installed in sys.modules — so this suite NEVER touches the
shared NIFTY feather. (Loading a real symbol here would narrow the cache and
zero out unrelated backtests; see the standing feather-truncation rule.)

The stub mirrors the two calls the post-pass makes:
  * get_strikes_for_date        -> the option chain, [(strike, close)]
  * get_option_price_tradeable  -> None for an untraded contract
"""
import sys
import types
import unittest


# ── Stub native extension, installed BEFORE engine_rust is imported ──────────
GAP = 50.0
# 11-Aug-2022 CE chain: premium falls as strike rises. 17650 is deliberately
# UNTRADED (stale close far above the curve) to prove the tradeable filter bites,
# and 17575 is deliberately OFF-GRID (not a multiple of 50) and priced right at
# the 32.80 target to prove the strike-gap filter bites.
_CE_CHAIN = {
    17400.0: 160.00, 17450.0: 132.00, 17500.0: 108.00, 17550.0: 86.00,
    17575.0: 33.00, 17600.0: 25.85, 17650.0: 900.00, 17700.0: 14.20,
    17750.0: 9.10, 17800.0: 5.40,
}
_UNTRADED = {17650.0}
# 25-Aug-2022 PE chain — only the ref leg's own strike is ever priced from it.
_PE_CHAIN = {17150.0: 98.40}


class _StubNative(types.ModuleType):
    def get_strikes_for_date(self, date, index, expiry, opt_type):
        chain = _CE_CHAIN if opt_type.upper() == "CE" else _PE_CHAIN
        return [(k, v) for k, v in sorted(chain.items())]

    def get_option_price(self, date, index, strike, opt_type, expiry):
        # The plain close — returned even for a zero-turnover contract, exactly
        # like every leg's real fill. RELPREM reads the REFERENCE premium this way.
        chain = _CE_CHAIN if opt_type.upper() == "CE" else _PE_CHAIN
        return chain.get(float(strike))

    def get_option_price_tradeable(self, date, index, strike, opt_type, expiry):
        chain = _CE_CHAIN if opt_type.upper() == "CE" else _PE_CHAIN
        if float(strike) in _UNTRADED:
            return None
        return chain.get(float(strike))

    def is_loaded(self):
        return True


sys.modules["algotest_native"] = _StubNative("algotest_native")

from services import engine_rust as E  # noqa: E402


# engine_rust imports algotest_native lazily (per call, from sys.modules), so
# re-assert THIS module's stub before its tests run and restore afterwards —
# otherwise a sibling test module's stub (last import wins the slot) leaks in
# when the suites run together.
_prev_native = None


_prev_get_expiry = None


def _stub_get_expiry_dates(symbol, expiry_type="weekly", from_date=None, to_date=None):
    """Deterministic offline expiry calendar for the divisor count: every Thursday
    in [from_date, to_date] for a weekly cadence, the last Thursday of each month
    for a monthly cadence. Aug-2022 → 4 weekly expiries (4/11/18/25), the count
    the full-cycle divisor must produce. Keeps the suite off the real DB/feather.
    """
    from datetime import date, timedelta
    s, e = date.fromisoformat(from_date), date.fromisoformat(to_date)
    thursdays = []
    d = s
    while d <= e:
        if d.weekday() == 3:
            thursdays.append(d.isoformat())
        d += timedelta(days=1)
    if str(expiry_type).lower().startswith("month"):
        by_month = {}
        for iso in thursdays:
            dd = date.fromisoformat(iso)
            by_month[(dd.year, dd.month)] = iso     # last Thursday wins
        return list(by_month.values())
    return thursdays


def setUpModule():
    global _prev_native, _prev_get_expiry
    _prev_native = sys.modules.get("algotest_native")
    sys.modules["algotest_native"] = _StubNative("algotest_native")
    import base
    _prev_get_expiry = base.get_expiry_dates
    base.get_expiry_dates = _stub_get_expiry_dates
    E._RELPREM_WINDOW_CACHE.clear()                  # drop any real-DB entry


def tearDownModule():
    if _prev_native is not None:
        sys.modules["algotest_native"] = _prev_native
    else:
        sys.modules.pop("algotest_native", None)
    if _prev_get_expiry is not None:
        import base
        base.get_expiry_dates = _prev_get_expiry
    E._RELPREM_WINDOW_CACHE.clear()


def _specs(child_lots=1, ref_lots=1, entry="2022-08-04"):
    """Two spec rows for one trade: leg 2 = monthly PE ref, leg 3 = weekly CE child.

    Leg 3's strike is the ATM placeholder (17400) the swap leaves behind, which
    is exactly what the post-pass expects to overwrite.
    """
    common = {"trade_id": 1, "index": "NIFTY", "entry_date": entry, "exit_date": "2022-08-11"}
    return [
        dict(common, leg_id=2, expiry="2022-08-25", strike=17150.0, option_type="PE"),
        dict(common, leg_id=3, expiry="2022-08-11", strike=17400.0, option_type="CE"),
    ]


def _payload(child_lots=1, ref_lots=1):
    return {
        "index": "NIFTY",
        "expiry_type": "WEEKLY",
        "legs": [
            {"segment": "OPTIONS", "expiry": "WEEKLY", "lots": 1,
             "strike_selection": {"type": "strike_type", "strike_type": "ATM"}},
            {"segment": "OPTIONS", "expiry": "MONTHLY", "lots": ref_lots,
             "strike_selection": {"type": "closest_premium", "premium": 100}},
            {"segment": "OPTIONS", "expiry": "WEEKLY", "lots": child_lots,
             "strike_selection": {"type": "rel_leg_premium", "ref_leg": 2}},
        ],
    }


def _run(payload, specs):
    """Post-pass, returning the child spec. Divisor N comes from the stubbed
    expiry calendar (_stub_get_expiry_dates), so this stays offline."""
    out = E._apply_rel_leg_premium_to_specs(
        specs, payload, E._relprem_legs(payload), 65)
    return next((s for s in out if s["leg_id"] == 3), None)


class TestDivisor(unittest.TestCase):
    def test_divisor_is_ref_full_cycle_not_weeks_remaining(self):
        """N = child-cadence expiries in the REFERENCE contract's OWN calendar
        cycle (Aug-2022 monthly ref -> 4 August weeklies), from the real expiry
        calendar. A property of the contract, NOT of the entry date.
        """
        child = _run(_payload(), _specs())
        self.assertAlmostEqual(child["rel_leg_premium_n"], 4.0, places=6)
        self.assertAlmostEqual(child["rel_leg_premium_target"], 98.40 / 4.0, places=4)

    def test_divisor_independent_of_entry_date(self):
        """The bug this fixes: N must NOT shrink as the entry nears expiry. An
        early and a late entry in the SAME ref cycle get the SAME N (the full
        cycle), so the child strike no longer drifts from far-OTM to ATM."""
        n_early = _run(_payload(), _specs(entry="2022-08-04"))["rel_leg_premium_n"]
        n_late = _run(_payload(), _specs(entry="2022-08-18"))["rel_leg_premium_n"]
        self.assertAlmostEqual(n_early, 4.0, places=6)
        self.assertAlmostEqual(n_late, 4.0, places=6)
        self.assertEqual(n_early, n_late)

    def test_child_cadence_sets_what_is_counted(self):
        """N counts CHILD-cadence expiries inside the ref's cycle: a weekly child
        -> 4 (the August weeklies); a monthly child -> 1 (the one August monthly)."""
        weekly = _run(_payload(), _specs())                        # leg 3 = WEEKLY
        self.assertAlmostEqual(weekly["rel_leg_premium_n"], 4.0, places=6)
        p = _payload()
        p["legs"][2]["expiry"] = "MONTHLY"                         # child now monthly
        monthly = _run(p, _specs())
        self.assertAlmostEqual(monthly["rel_leg_premium_n"], 1.0, places=6)

    def test_ref_read_survives_zero_turnover_ref(self):
        """The reference is often a long-dated deep-OTM leg with no turnover.

        Its close still exists (that's what it fills at), so RELPREM must read it
        via get_option_price and resolve — not abort to the ATM placeholder.
        Regression for the 67/151 ATM-fallback bug.
        """
        _UNTRADED.add(17150.0)                       # ref PE now zero-turnover
        try:
            child = _run(_payload(), _specs())
        finally:
            _UNTRADED.discard(17150.0)
        self.assertIsNotNone(child)                  # resolved, not dropped
        self.assertEqual(child["strike"], 17600.0)   # same pick as the traded case
        self.assertAlmostEqual(child["rel_leg_premium_target"], 98.40 / 4.0, places=4)


class TestLots(unittest.TestCase):
    def test_equal_lots_cancel(self):
        a = _run(_payload(ref_lots=1, child_lots=1), _specs())
        b = _run(_payload(ref_lots=2, child_lots=2), _specs())
        self.assertAlmostEqual(a["rel_leg_premium_target"], b["rel_leg_premium_target"])

    def test_money_is_conserved(self):
        """The child's N cycles must collect what the ref collects once."""
        LOT_SIZE = 65
        for ref_lots, child_lots in ((1, 1), (2, 1), (1, 2), (3, 2)):
            with self.subTest(ref=ref_lots, child=child_lots):
                child = _run(_payload(ref_lots=ref_lots, child_lots=child_lots), _specs())
                n = child["rel_leg_premium_n"]
                ref_money = 98.40 * LOT_SIZE * ref_lots
                child_money = child["rel_leg_premium_target"] * LOT_SIZE * child_lots * n
                self.assertAlmostEqual(ref_money, child_money, places=6)

    def test_ref_lots_pull_strike_toward_atm(self):
        """More lots on the ref raises the target, so a richer strike wins."""
        one = _run(_payload(ref_lots=1), _specs())
        three = _run(_payload(ref_lots=3), _specs())
        # N=4.0. 1 lot -> target 24.60 -> 17600 @ 25.85 (1.25 off).
        #        3 lots -> target 73.80 -> 17550 @ 86.00 (12.20 off, beats 17600's 47.95).
        self.assertEqual(one["strike"], 17600.0)
        self.assertEqual(three["strike"], 17550.0)
        self.assertLess(three["strike"], one["strike"])     # closer to ATM


class TestChainFilters(unittest.TestCase):
    def test_off_grid_strike_is_never_selected(self):
        """17575 @ 33.00 is off-grid; the 24.60 target must skip it for a grid strike."""
        specs = _specs()
        child = _run(_payload(), specs)
        self.assertNotEqual(child["strike"], 17575.0)

    def test_untraded_strike_is_never_selected(self):
        """17650's stale 900.00 close must not be treated as a real premium."""
        payload = _payload()
        payload["legs"][2]["strike_selection"]["ref_leg"] = 2
        child = _run(payload, _specs())
        self.assertNotEqual(child["strike"], 17650.0)

    def test_picks_nearest_qualifying_premium(self):
        """N=4.0, target 24.60 -> 17600 @ 25.85 is nearest on the filtered grid chain."""
        child = _run(_payload(), _specs())
        self.assertEqual(child["strike"], 17600.0)
        # Derived strikes must not be reported as forced liquidity shifts.
        self.assertEqual(child["requested_strike"], child["strike"])


class TestSymmetry(unittest.TestCase):
    def test_shorter_ref_multiplies_instead_of_dividing(self):
        """Weekly ref under a monthly child: N becomes a multiplier, not a divisor."""
        payload = _payload()
        payload["legs"][1]["expiry"] = "WEEKLY"
        payload["legs"][2]["expiry"] = "MONTHLY"
        specs = _specs()
        specs[0]["expiry"] = "2022-08-11"   # ref is now the near weekly
        specs[1]["expiry"] = "2022-08-25"   # child is the monthly
        # Ref priced off the weekly CE chain at its 17150 strike is absent, so
        # give the ref a strike the chain knows.
        specs[0]["strike"] = 17400.0
        specs[0]["option_type"] = "CE"
        child = _run(payload, specs)
        self.assertIsNotNone(child)
        self.assertGreater(child["rel_leg_premium_target"], 160.00)


class TestValidation(unittest.TestCase):
    def test_forward_reference_is_rejected(self):
        payload = _payload()
        payload["legs"][2]["strike_selection"]["ref_leg"] = 3   # itself
        with self.assertRaises(ValueError):
            E._validate_relprem(payload, E._relprem_legs(payload))

    def test_missing_ref_is_rejected(self):
        payload = _payload()
        payload["legs"][2]["strike_selection"]["ref_leg"] = 9
        with self.assertRaises(ValueError):
            E._validate_relprem(payload, E._relprem_legs(payload))

    def test_futures_ref_is_rejected(self):
        payload = _payload()
        payload["legs"][1]["segment"] = "FUTURES"
        with self.assertRaises(ValueError):
            E._validate_relprem(payload, E._relprem_legs(payload))

    def test_reentry_is_rejected(self):
        """Re-entry re-resolves the leg from the ATM placeholder — must not run."""
        payload = _payload()
        payload["legs"][2]["reEntryOnSL"] = {"mode": "RE_ASAP", "count": 3}
        with self.assertRaises(ValueError):
            E._validate_relprem(payload, E._relprem_legs(payload))

    def test_spot_adjustment_is_rejected(self):
        payload = _payload()
        payload["spot_adjustment_enabled"] = True
        with self.assertRaises(ValueError):
            E._validate_relprem(payload, E._relprem_legs(payload))

    def test_cross_index_is_rejected(self):
        payload = _payload()
        payload["legs"][2]["index"] = "MIDCPNIFTY"
        with self.assertRaises(ValueError):
            E._validate_relprem(payload, E._relprem_legs(payload))

    def test_child_leg_dropped_when_ref_has_no_spec(self):
        specs = [s for s in _specs() if s["leg_id"] == 3]
        out = E._apply_rel_leg_premium_to_specs(
            specs, _payload(), E._relprem_legs(_payload()), 65)
        self.assertEqual(out, [])


class TestInertWhenUnused(unittest.TestCase):
    def test_no_relprem_leg_means_no_change(self):
        payload = _payload()
        payload["legs"][2]["strike_selection"] = {"type": "strike_type", "strike_type": "ATM"}
        self.assertEqual(E._relprem_legs(payload), {})

    def test_placeholder_swap_leaves_original_payload_untouched(self):
        payload = _payload()
        swapped = E._payload_with_relprem_placeholder(payload, E._relprem_legs(payload))
        self.assertEqual(payload["legs"][2]["strike_selection"]["type"], "rel_leg_premium")
        self.assertEqual(swapped["legs"][2]["strike_selection"]["type"], "strike_type")


if __name__ == "__main__":
    unittest.main()
