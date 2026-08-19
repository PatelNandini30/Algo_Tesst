# backend/tests/test_yearly_contract_schedule.py
"""Tests for _yearly_schedule_row (Task 1) and the strike-gap override
in _build_fixed_entry_specs (Task 2).

Stub `algotest_native` is installed before engine_rust is imported so the
test never touches the shared NIFTY feather cache.  `get_option_status`
returns "tradeable" for every strike so _validate_or_shift_strike_python
accepts the computed ATM strike without a real data lookup.
"""
import sys
import types
import unittest


class _StubNative(types.ModuleType):
    def get_option_status(self, date, index, strike, opt_type, expiry):
        return "tradeable"

    def is_loaded(self):
        return True


sys.modules["algotest_native"] = _StubNative("algotest_native")

from services import engine_rust as E  # noqa: E402


# engine_rust imports algotest_native lazily (per call, from sys.modules), so
# re-assert THIS module's stub before its tests run and restore afterwards —
# otherwise a sibling test module's stub (last import wins the slot) leaks in
# when the suites run together.
_prev_native = None


def setUpModule():
    global _prev_native
    _prev_native = sys.modules.get("algotest_native")
    sys.modules["algotest_native"] = _StubNative("algotest_native")


def tearDownModule():
    if _prev_native is not None:
        sys.modules["algotest_native"] = _prev_native
    else:
        sys.modules.pop("algotest_native", None)


SCHED = [
    {"contract": "2022", "strike_gap": 500,  "spot_adj_pct": 500},
    {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000},
]


class TestResolver(unittest.TestCase):
    def test_row_by_december_year(self):
        leg = {"yearly_contract_schedule": SCHED}
        self.assertEqual(E._yearly_schedule_row(leg, "2023-12-30"),
                         {"strike_gap": 1000.0, "spot_adj_pct": 1000.0, "spot_adj_unit": None})
        self.assertEqual(E._yearly_schedule_row(leg, "2022-12-30"),
                         {"strike_gap": 500.0, "spot_adj_pct": 500.0, "spot_adj_unit": None})

    def test_before_first_row_returns_none(self):
        # A contract earlier than every row → base fallback (None).
        self.assertIsNone(E._yearly_schedule_row({"yearly_contract_schedule": SCHED}, "2020-12-31"))

    def test_sticky_forward_fill(self):
        # SCHED = 2022→500, 2023→1000. Sticky: later contracts inherit the
        # newest row <= their year.
        leg = {"yearly_contract_schedule": SCHED}
        self.assertEqual(E._yearly_schedule_row(leg, "2024-12-27")["spot_adj_pct"], 1000.0)
        self.assertEqual(E._yearly_schedule_row(leg, "2022-12-30")["spot_adj_pct"], 500.0)

    def test_no_schedule_returns_none(self):
        self.assertIsNone(E._yearly_schedule_row({}, "2023-12-30"))

    def test_duplicate_year_rejected(self):
        p = {"legs": [{"yearly_contract_schedule": [
            {"contract": "2023", "strike_gap": 500, "spot_adj_pct": 500},
            {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000}]}]}
        with self.assertRaises(ValueError):
            E._validate_yearly_schedule(p)


class TestPerRowSpotAdjUnit(unittest.TestCase):
    """Per-row %/points unit for the spot-adjustment value."""

    def test_resolver_reads_points_unit(self):
        leg = {"yearly_contract_schedule": [
            {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000, "spot_adj_unit": "points"}]}
        self.assertEqual(E._yearly_schedule_row(leg, "2023-12-30")["spot_adj_unit"], "points")

    def test_resolver_reads_percent_unit(self):
        leg = {"yearly_contract_schedule": [
            {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 2, "spot_adj_unit": "percent"}]}
        self.assertEqual(E._yearly_schedule_row(leg, "2023-12-30")["spot_adj_unit"], "percent")

    def test_resolver_unit_absent_is_none(self):
        # None => caller falls back to the leg's own units (backward-compat).
        self.assertIsNone(E._yearly_schedule_row({"yearly_contract_schedule": SCHED}, "2023-12-30")["spot_adj_unit"])

    def test_norm_variants(self):
        self.assertEqual(E._norm_sa_unit("%"), "percent")
        self.assertEqual(E._norm_sa_unit("pts"), "points")
        self.assertEqual(E._norm_sa_unit("PERCENT"), "percent")
        self.assertIsNone(E._norm_sa_unit(""))
        self.assertIsNone(E._norm_sa_unit("banana"))

    def test_validator_rejects_bad_unit(self):
        p = {"legs": [{"yearly_contract_schedule": [
            {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000, "spot_adj_unit": "banana"}]}]}
        with self.assertRaises(ValueError):
            E._validate_yearly_schedule(p)

    def test_validator_accepts_good_unit_and_absent(self):
        # points, percent, and absent all validate cleanly.
        p = {"legs": [{"yearly_contract_schedule": [
            {"contract": "2022", "strike_gap": 500, "spot_adj_pct": 500, "spot_adj_unit": "points"},
            {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 2, "spot_adj_unit": "percent"},
            {"contract": "2024", "strike_gap": 1000, "spot_adj_pct": 1000}]}]}
        E._validate_yearly_schedule(p)  # must not raise


# ---------------------------------------------------------------------------
# Task 2: strike_interval in specs follows yearly_contract_schedule
# ---------------------------------------------------------------------------
# Minimal calendar (monthly cadence, two December contracts):
#   2022-01-27 and 2023-01-26 are the two monthly expiries.
#   Dec-2022 cycle: start=2022-01-01, end=2022-12-30, contract=2022-12-30
#   Dec-2023 cycle: start=2022-12-30, end=2023-12-29, contract=2023-12-29
#
# With rollover_toggle=True the builder chains trades until segment end, so:
#   Seg-1 (2022-01-01..2022-12-30) -> trades with contract=2022-12-30 → gap=500
#   Seg-2 (2022-12-30..2023-12-29) -> trades with contract=2023-12-29 → gap=1000
# Without the override all specs show strike_interval=50 (NIFTY default).

_YEARLY_CYCLES = [
    {"contract": "2022-12-30", "start": "2022-01-01", "end": "2022-12-30"},
    {"contract": "2023-12-29", "start": "2022-12-30", "end": "2023-12-29"},
]
_CADENCE_EXPIRIES = ["2022-01-27", "2023-01-26"]
_TRADING_DAYS = [
    "2022-01-25", "2022-01-26", "2022-01-27",
    "2022-12-29", "2022-12-30",
    "2023-01-24", "2023-01-25", "2023-01-26",
]
_SPOT = {d: 18000.0 for d in _TRADING_DAYS}
_SEGS = [("2022-01-01", "2022-12-30"), ("2022-12-30", "2023-12-29")]

_SCHED_T2 = [
    {"contract": "2022", "strike_gap": 500,  "spot_adj_pct": 500},
    {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000},
]


def _base_payload(sched=_SCHED_T2):
    return {
        "index": "NIFTY",
        "expiry_type": "YEARLY",
        "rollover_cadence": "monthly",
        "rollover_toggle": True,
        "exit_dte": 0,
        "legs": [
            {
                "segment": "OPTIONS",
                "expiry": "YEARLY",
                "option_type": "CE",
                "position": "SELL",
                "lots": 1,
                "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
                **({"yearly_contract_schedule": sched} if sched is not None else {}),
            }
        ],
        "yearly_cycles": _YEARLY_CYCLES,
    }


def _run(payload):
    return E._build_fixed_entry_specs(
        payload, _CADENCE_EXPIRIES, _TRADING_DAYS, _SPOT, 50, _SEGS
    )


class TestStrikeGapOverride(unittest.TestCase):
    """Task 2: _build_fixed_entry_specs must use yearly_contract_schedule gap."""

    def test_scheduled_gap_applied_for_2022_contract(self):
        """All trades pinned to 2022-12-30 should carry strike_interval=500."""
        specs = _run(_base_payload())
        self.assertIsNotNone(specs, "_build_fixed_entry_specs returned None (premium mode?)")
        trades_2022 = [s for s in specs if str(s.get("expiry", "")).startswith("2022")]
        self.assertTrue(trades_2022, "Expected at least one trade under the 2022 December contract")
        for s in trades_2022:
            self.assertEqual(
                s["strike_interval"], 500.0,
                f"trade {s['trade_id']} (expiry {s['expiry']}) has interval {s['strike_interval']}, want 500",
            )

    def test_scheduled_gap_applied_for_2023_contract(self):
        """All trades pinned to 2023-12-29 should carry strike_interval=1000."""
        specs = _run(_base_payload())
        self.assertIsNotNone(specs, "_build_fixed_entry_specs returned None (premium mode?)")
        trades_2023 = [s for s in specs if str(s.get("expiry", "")).startswith("2023")]
        self.assertTrue(trades_2023, "Expected at least one trade under the 2023 December contract")
        for s in trades_2023:
            self.assertEqual(
                s["strike_interval"], 1000.0,
                f"trade {s['trade_id']} (expiry {s['expiry']}) has interval {s['strike_interval']}, want 1000",
            )

    def test_no_schedule_keeps_default_interval(self):
        """Without yearly_contract_schedule the NIFTY default (50) is preserved."""
        specs = _run(_base_payload(sched=None))
        self.assertIsNotNone(specs)
        self.assertTrue(specs, "Expected trades to be produced")
        for s in specs:
            self.assertEqual(
                s["strike_interval"], 50.0,
                f"trade {s['trade_id']} has interval {s['strike_interval']}, want 50 (default)",
            )


# ---------------------------------------------------------------------------
# Task 3: spot-adjustment trigger follows per-contract schedule
# ---------------------------------------------------------------------------
# Verifies that _yearly_schedule_row returns the correct per-contract pct, and
# that _compute_spot_adjustment_trigger fires at the right threshold when that
# pct is used — proving the two halves of the override compose correctly.
#
# The schedule: Dec-2022 → spot_adj_pct=500 (points), Dec-2023 → 1000 (points).
# A spot_by_date path that rises +750 (above 500, below 1000):
#   · Dec-2022 contract → should fire (500 ≤ 750)
#   · Dec-2023 contract → should NOT fire (1000 > 750)
# Falling back to the leg's BASE pct (50 pts) always fires on the +750 path —
# so these two assertions can only both pass when the schedule override is live.

_SCHED_T3 = [
    {"contract": "2022", "strike_gap": 500,  "spot_adj_pct": 500},
    {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000},
]
_YEARLY_LEG_T3 = {
    "segment": "OPTIONS",
    "expiry": "YEARLY",
    "option_type": "PE",
    "position": "SELL",
    "lots": 1,
    "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
    "spot_adjustment": {"enabled": True, "pct": 50, "direction": "rise", "units": "points"},
    "yearly_contract_schedule": _SCHED_T3,
}
_TDAYS_T3 = ["2022-06-01", "2022-06-02", "2022-06-03", "2022-06-04", "2022-06-05"]
# Spot rises by 750 pts from 18000 → 18750 on day 3.
_SPOT_T3 = {
    "2022-06-01": 18000.0,
    "2022-06-02": 18200.0,
    "2022-06-03": 18750.0,
    "2022-06-04": 18800.0,
    "2022-06-05": 18850.0,
}


class TestSpotAdjPctSchedule(unittest.TestCase):
    """Task 3: _yearly_schedule_row resolution + _compute_spot_adjustment_trigger."""

    def _resolved_pct(self, contract_iso):
        """Simulate the override logic added to the cascade at the four sites."""
        base_pct = _YEARLY_LEG_T3["spot_adjustment"]["pct"]  # 50
        row = E._yearly_schedule_row(_YEARLY_LEG_T3, contract_iso)
        if row is None:
            return base_pct
        pct = row["spot_adj_pct"]
        # units == "points" for this leg → no percent clamp
        return pct

    def test_pct_chosen_for_2022_contract(self):
        """Dec-2022 contract → schedule row gives pct=500."""
        pct = self._resolved_pct("2022-12-30")
        self.assertEqual(pct, 500.0)

    def test_pct_chosen_for_2023_contract(self):
        """Dec-2023 contract → schedule row gives pct=1000."""
        pct = self._resolved_pct("2023-12-29")
        self.assertEqual(pct, 1000.0)

    def test_fallback_to_base_pct_before_first_row(self):
        """A contract BEFORE the first schedule row → base pct (50)."""
        pct = self._resolved_pct("2021-12-31")
        self.assertEqual(pct, 50.0)

    def test_sticky_inherits_latest_row(self):
        """Sticky: Dec-2024 (no own row) inherits the 2023 row (1000)."""
        self.assertEqual(self._resolved_pct("2024-12-27"), 1000.0)
        self.assertEqual(self._resolved_pct("2030-12-27"), 1000.0)

    def test_trigger_fires_at_500_for_2022_contract(self):
        """Dec-2022 (pct=500 pts): spot rises +750 → trigger fires."""
        pct = self._resolved_pct("2022-12-30")
        trig = E._compute_spot_adjustment_trigger(
            "2022-06-01", 18000.0, "2022-06-05",
            "rise", pct, "points", _TDAYS_T3, _SPOT_T3,
        )
        self.assertIsNotNone(trig, "Expected trigger for Dec-2022 (500pt threshold, +750 rise)")
        self.assertEqual(trig, "2022-06-03")

    def test_trigger_silent_at_1000_for_2023_contract(self):
        """Dec-2023 (pct=1000 pts): spot rises only +750 → no trigger."""
        pct = self._resolved_pct("2023-12-29")
        trig = E._compute_spot_adjustment_trigger(
            "2022-06-01", 18000.0, "2022-06-05",
            "rise", pct, "points", _TDAYS_T3, _SPOT_T3,
        )
        self.assertIsNone(trig, "Expected no trigger for Dec-2023 (1000pt threshold, only +750 rise)")

    def test_base_pct_fires_at_50_before_first_row(self):
        """Contract before the first row (pct=50 pts): same +750 rise → still fires."""
        pct = self._resolved_pct("2021-12-31")
        trig = E._compute_spot_adjustment_trigger(
            "2022-06-01", 18000.0, "2022-06-05",
            "rise", pct, "points", _TDAYS_T3, _SPOT_T3,
        )
        self.assertIsNotNone(trig, "Base pct=50 should fire on +750 rise")


# ---------------------------------------------------------------------------
# Task 4: _stamp_yearly_roll_reason annotates first row of each new contract
# ---------------------------------------------------------------------------

_SCHED_T4 = [
    {"contract": "2022", "strike_gap": 500,  "spot_adj_pct": 500},
    {"contract": "2023", "strike_gap": 1000, "spot_adj_pct": 1000},
]
_LEG_T4 = {
    "segment": "OPTIONS",
    "expiry": "YEARLY",
    "option_type": "CE",
    "position": "SELL",
    "lots": 1,
    "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
    "yearly_contract_schedule": _SCHED_T4,
}


def _make_row(trade, entry_date, expiry, shift_reason=""):
    return {
        "Trade": trade,
        "Leg": 1,
        "Entry Date": entry_date,
        "Exit Date": entry_date,
        "Expiry": expiry,
        "Strike Shift Reason": shift_reason,
    }


class TestStampYearlyRollReason(unittest.TestCase):
    """Task 4: _stamp_yearly_roll_reason stamps YEARLY_ROLL on first new-contract row."""

    def _run(self, rows):
        import copy
        rows = copy.deepcopy(rows)
        E._stamp_yearly_roll_reason(rows, [_LEG_T4])
        return rows

    def test_first_dec2023_row_gets_note(self):
        rows = [
            _make_row("T1", "2022-03-01", "2022-12-30"),
            _make_row("T2", "2022-06-01", "2022-12-30"),
            _make_row("T3", "2023-01-05", "2023-12-29"),  # first 2023 row
            _make_row("T4", "2023-06-01", "2023-12-29"),
        ]
        out = self._run(rows)
        self.assertIn("YEARLY_ROLL → Dec-2023 (gap 1000, adj 1000)", out[2]["Strike Shift Reason"])

    def test_later_dec2023_row_does_not_get_note(self):
        rows = [
            _make_row("T1", "2022-03-01", "2022-12-30"),
            _make_row("T2", "2023-01-05", "2023-12-29"),
            _make_row("T3", "2023-06-01", "2023-12-29"),
        ]
        out = self._run(rows)
        self.assertNotIn("YEARLY_ROLL", out[2]["Strike Shift Reason"])

    def test_inherited_contract_gets_nothing(self):
        # Sticky: Dec-2024 inherits the 2023 row → effective unchanged → no note.
        rows = [
            _make_row("T1", "2022-03-01", "2022-12-30"),
            _make_row("T2", "2023-01-05", "2023-12-29"),
            _make_row("T3", "2024-01-05", "2024-12-27"),
        ]
        out = self._run(rows)
        self.assertEqual(out[2]["Strike Shift Reason"], "")

    def test_later_override_restamps(self):
        # Schedule 2022→500, 2025→1000: Dec-2023/2024 inherit 500 (no note),
        # Dec-2025 overrides to 1000 (note fires again).
        leg = {"segment": "OPTIONS", "expiry": "YEARLY", "option_type": "CE",
               "position": "SELL", "lots": 1,
               "yearly_contract_schedule": [
                   {"contract": "2022", "strike_gap": 500, "spot_adj_pct": 500},
                   {"contract": "2025", "strike_gap": 1000, "spot_adj_pct": 1000}]}
        rows = [
            _make_row("T1", "2022-03-01", "2022-12-30"),   # 500 → note
            _make_row("T2", "2023-01-05", "2023-12-29"),   # inherit 500 → none
            _make_row("T3", "2024-01-05", "2024-12-27"),   # inherit 500 → none
            _make_row("T4", "2025-01-05", "2025-12-26"),   # 1000 → note
        ]
        import copy
        rows = copy.deepcopy(rows)
        E._stamp_yearly_roll_reason(rows, [leg])
        self.assertIn("YEARLY_ROLL → Dec-2022 (gap 500, adj 500)", rows[0]["Strike Shift Reason"])
        self.assertEqual(rows[1]["Strike Shift Reason"], "")
        self.assertEqual(rows[2]["Strike Shift Reason"], "")
        self.assertIn("YEARLY_ROLL → Dec-2025 (gap 1000, adj 1000)", rows[3]["Strike Shift Reason"])

    def test_joins_existing_reason_with_plus(self):
        rows = [
            _make_row("T1", "2022-03-01", "2022-12-30"),
            _make_row("T2", "2023-01-05", "2023-12-29", shift_reason="18000→18500 (zero turnover, 1 step toward ATM)"),
        ]
        out = self._run(rows)
        reason = out[1]["Strike Shift Reason"]
        self.assertIn("YEARLY_ROLL → Dec-2023 (gap 1000, adj 1000)", reason)
        self.assertIn("18000→18500", reason)
        self.assertIn(" + ", reason)

    def test_non_yearly_leg_untouched(self):
        weekly_leg = {"segment": "OPTIONS", "expiry": "WEEKLY", "option_type": "CE",
                      "position": "SELL", "lots": 1}
        rows = [_make_row("T1", "2023-01-05", "2023-01-05")]
        import copy
        rows = copy.deepcopy(rows)
        E._stamp_yearly_roll_reason(rows, [weekly_leg])
        self.assertEqual(rows[0]["Strike Shift Reason"], "")


# ---------------------------------------------------------------------------
# Task 5: parity guard — all hooks are no-ops when no schedule is configured
# ---------------------------------------------------------------------------

_LEG_NO_SCHED = {
    "segment": "OPTIONS",
    "expiry": "YEARLY",
    "option_type": "CE",
    "position": "SELL",
    "lots": 1,
    "strike_selection": {"type": "strike_type", "strike_type": "ATM"},
    # deliberately NO yearly_contract_schedule key
}
_LEG_EMPTY_SCHED = {**_LEG_NO_SCHED, "yearly_contract_schedule": []}
_LEG_NONE_SCHED = {**_LEG_NO_SCHED, "yearly_contract_schedule": None}
_LEG_WEEKLY_WITH_SCHED = {
    "segment": "OPTIONS",
    "expiry": "WEEKLY",
    "option_type": "CE",
    "position": "SELL",
    "lots": 1,
    "yearly_contract_schedule": _SCHED_T4,
}


class TestNoScheduleParity(unittest.TestCase):
    """Task 5: prove every new hook is a no-op when yearly_contract_schedule is absent."""

    # --- _yearly_schedule_row gates ---

    def test_resolver_no_key(self):
        """Missing key → None."""
        self.assertIsNone(E._yearly_schedule_row(_LEG_NO_SCHED, "2023-12-30"))

    def test_resolver_empty_list(self):
        """Empty list → None."""
        self.assertIsNone(E._yearly_schedule_row(_LEG_EMPTY_SCHED, "2023-12-30"))

    def test_resolver_none_value(self):
        """Explicit None → None."""
        self.assertIsNone(E._yearly_schedule_row(_LEG_NONE_SCHED, "2023-12-30"))

    def test_resolver_before_first_row_light(self):
        """Leg with a schedule but querying a year BEFORE the first row → None."""
        leg = {"yearly_contract_schedule": _SCHED_T4}
        self.assertIsNone(E._yearly_schedule_row(leg, "2020-12-30"))

    # --- _stamp_yearly_roll_reason is a no-op when no schedule ---

    def _rows_spanning_two_contracts(self):
        return [
            _make_row("T1", "2022-03-01", "2022-12-30"),
            _make_row("T2", "2022-09-01", "2022-12-30", shift_reason="pre-existing reason"),
            _make_row("T3", "2023-01-05", "2023-12-29"),
        ]

    def test_stamp_noop_no_key(self):
        """No schedule key → rows byte-identical after stamp."""
        import copy
        rows = self._rows_spanning_two_contracts()
        expected = copy.deepcopy(rows)
        E._stamp_yearly_roll_reason(rows, [_LEG_NO_SCHED])
        self.assertEqual(rows, expected)

    def test_stamp_noop_empty_list(self):
        """Empty schedule list → rows byte-identical after stamp."""
        import copy
        rows = self._rows_spanning_two_contracts()
        expected = copy.deepcopy(rows)
        E._stamp_yearly_roll_reason(rows, [_LEG_EMPTY_SCHED])
        self.assertEqual(rows, expected)

    def test_stamp_noop_none_value(self):
        """Explicit None schedule → rows byte-identical after stamp."""
        import copy
        rows = self._rows_spanning_two_contracts()
        expected = copy.deepcopy(rows)
        E._stamp_yearly_roll_reason(rows, [_LEG_NONE_SCHED])
        self.assertEqual(rows, expected)

    def test_stamp_noop_preserves_prepopulated_reason(self):
        """Pre-populated Strike Shift Reason must survive a no-op stamp."""
        import copy
        rows = self._rows_spanning_two_contracts()
        expected = copy.deepcopy(rows)
        E._stamp_yearly_roll_reason(rows, [_LEG_NO_SCHED])
        self.assertEqual(rows[1]["Strike Shift Reason"], expected[1]["Strike Shift Reason"])
        self.assertEqual(rows[1]["Strike Shift Reason"], "pre-existing reason")

    def test_stamp_noop_weekly_leg_with_schedule(self):
        """Non-YEARLY expiry leg with a schedule present → still a no-op."""
        import copy
        rows = self._rows_spanning_two_contracts()
        expected = copy.deepcopy(rows)
        E._stamp_yearly_roll_reason(rows, [_LEG_WEEKLY_WITH_SCHED])
        self.assertEqual(rows, expected)


if __name__ == "__main__":
    unittest.main()
