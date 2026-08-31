# backend/tests/test_quarterly_expiry.py
"""QUARTERLY expiry = YEARLY pinned to Mar/Jun/Sep/Dec. Purely additive: it must
normalize to the existing YEARLY path (strategy-level AND per-leg), leave a
_quarterly display hint, and be a strict no-op for every non-quarterly payload."""
import unittest

try:  # in-container (/app) vs repo-root layout
    from services.algotest_job import _apply_quarterly_expiry, _QUARTERLY_ROLL_MONTHS
    from services.optimizer.combo_labeler import _expiry_label
except ModuleNotFoundError:  # pragma: no cover
    from backend.services.algotest_job import _apply_quarterly_expiry, _QUARTERLY_ROLL_MONTHS
    from backend.services.optimizer.combo_labeler import _expiry_label


class TestQuarterlyNormalization(unittest.TestCase):
    def test_strategy_level_maps_to_yearly(self):
        p = {"expiry_type": "QUARTERLY", "legs": [{"expiry": "YEARLY"}]}
        _apply_quarterly_expiry(p)
        self.assertEqual(p["expiry_type"], "YEARLY")
        self.assertTrue(p["_quarterly"])
        self.assertEqual(p["yearly_roll_months"], _QUARTERLY_ROLL_MONTHS)
        self.assertIs(p["rollover_toggle"], True)

    def test_per_leg_maps_to_yearly(self):
        p = {"expiry_type": "WEEKLY", "legs": [
            {"expiry": "WEEKLY"},
            {"expiry": "QUARTERLY"},
        ]}
        _apply_quarterly_expiry(p)
        # strategy expiry untouched (mixed per-leg run)
        self.assertEqual(p["expiry_type"], "WEEKLY")
        self.assertNotIn("_quarterly", p)
        self.assertEqual(p["legs"][0]["expiry"], "WEEKLY")
        self.assertEqual(p["legs"][1]["expiry"], "YEARLY")
        self.assertTrue(p["legs"][1]["_quarterly"])
        self.assertEqual(p["legs"][1]["yearly_roll_months"], _QUARTERLY_ROLL_MONTHS)

    def test_forces_canonical_params_over_frontend_values(self):
        # The frontend's yearly controls inject roll_months=['12'] and
        # rollover_toggle=False for the quarterly basis; quarterly must force the
        # definitional Mar/Jun/Sep/Dec + pinning ON regardless.
        p = {"expiry_type": "QUARTERLY",
             "yearly_roll_months": ["12"], "rollover_toggle": False, "legs": []}
        _apply_quarterly_expiry(p)
        self.assertEqual(p["yearly_roll_months"], _QUARTERLY_ROLL_MONTHS)
        self.assertIs(p["rollover_toggle"], True)

    def test_noop_for_non_quarterly(self):
        for et in ("WEEKLY", "MONTHLY", "YEARLY", "NEXT_WEEKLY"):
            p = {"expiry_type": et, "legs": [{"expiry": et}]}
            before = {k: (list(v) if isinstance(v, list) else v) for k, v in p.items()}
            _apply_quarterly_expiry(p)
            self.assertEqual(p["expiry_type"], before["expiry_type"])
            self.assertNotIn("_quarterly", p)
            self.assertNotIn("yearly_roll_months", p)


class TestQuarterlyLabel(unittest.TestCase):
    def test_label_says_quarterly(self):
        # After normalization expiry_type is YEARLY; the _quarterly hint drives display.
        self.assertEqual(
            _expiry_label({"expiry_type": "YEARLY", "_quarterly": True,
                           "rollover_cadence": "monthly"}),
            "Quarterly_Monthly")
        self.assertEqual(
            _expiry_label({"expiry_type": "YEARLY", "_quarterly": True}),
            "Quarterly")

    def test_yearly_label_unchanged(self):
        self.assertEqual(
            _expiry_label({"expiry_type": "YEARLY", "rollover_cadence": "monthly"}),
            "Yearly_Monthly")
        self.assertEqual(_expiry_label({"expiry_type": "YEARLY"}), "Yearly")


if __name__ == "__main__":
    unittest.main()
