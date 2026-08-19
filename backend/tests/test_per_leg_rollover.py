"""Per-leg rollover — Python injection glue (`_inject_per_leg_rollover_inputs`).

Verifies that the opt-in flag stamps each leg with its OWN cadence expiry list
(and, for a yearly leg, its pinned cycles + exit_dte=0), and that the flag being
OFF is a strict no-op. The Rust union scheduler itself is covered by the Rust
unit test `per_leg_union_carry_across_foreign_boundary`.

DB/native are stubbed — this exercises only the Python glue.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import services.engine_rust as er  # noqa: E402


class TestPerLegRolloverInjection(unittest.TestCase):
    def setUp(self):
        # Stub the two DB-touching helpers with deterministic fixtures.
        self._orig_expiry_list = er._expiry_date_list
        self._orig_resolve = er.resolve_expiry_inputs

        def fake_expiry_list(index, expiry_type, from_date, to_date):
            if expiry_type == "weekly":
                return ["2026-02-05", "2026-02-12", "2026-02-19", "2026-02-26"]
            if expiry_type == "monthly":
                return ["2026-02-26", "2026-03-26"]
            return []

        def fake_resolve(index, payload, from_date, to_date, trading_days):
            # One yearly cycle: hold Dec-26, T-n exit snapped to a trading day.
            return (["2026-12-31"], [{"contract": "2026-12-31", "start": "2026-01-01", "end": "2026-11-25"}])

        er._expiry_date_list = fake_expiry_list
        er.resolve_expiry_inputs = fake_resolve

    def tearDown(self):
        er._expiry_date_list = self._orig_expiry_list
        er.resolve_expiry_inputs = self._orig_resolve

    def _payload(self, per_leg):
        return {
            "per_leg_rollover": per_leg,
            "index": "NIFTY",
            "from_date": "2026-02-02",
            "to_date": "2026-03-27",
            "exit_dte": 1,
            "legs": [
                {"expiry": "WEEKLY", "option_type": "CE", "exit_dte": 1},
                {"expiry": "MONTHLY", "option_type": "PE", "exit_dte": 7},
                {"expiry": "YEARLY", "option_type": "CE", "yearly_exit_months_before": 1},
            ],
        }

    def test_off_is_noop(self):
        p = self._payload(per_leg=False)
        er._inject_per_leg_rollover_inputs(p, [])
        for leg in p["legs"]:
            self.assertNotIn("_rollover_expiries", leg)
            self.assertNotIn("_rollover_cycles", leg)

    def test_weekly_and_monthly_legs_get_own_lists(self):
        p = self._payload(per_leg=True)
        er._inject_per_leg_rollover_inputs(p, [])
        wk, mo, yr = p["legs"]
        self.assertEqual(wk["_rollover_expiries"], ["2026-02-05", "2026-02-12", "2026-02-19", "2026-02-26"])
        self.assertIsNone(wk["_rollover_cycles"])
        self.assertEqual(wk["exit_dte"], 1)
        self.assertEqual(mo["_rollover_expiries"], ["2026-02-26", "2026-03-26"])
        self.assertEqual(mo["exit_dte"], 7)

    def test_yearly_leg_gets_cycles_and_zero_exit_dte(self):
        p = self._payload(per_leg=True)
        er._inject_per_leg_rollover_inputs(p, [])
        yr = p["legs"][2]
        # Yearly leg rolls at each cycle's T-n exit; those become its "expiries".
        self.assertEqual(yr["_rollover_expiries"], ["2026-11-25"])
        self.assertEqual(yr["_rollover_cycles"], [{"contract": "2026-12-31", "start": "2026-01-01", "end": "2026-11-25"}])
        self.assertEqual(yr["exit_dte"], 0)


if __name__ == "__main__":
    unittest.main()
