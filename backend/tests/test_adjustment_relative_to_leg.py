import unittest

try:  # in-container layout (/app == backend/) vs. repo-root layout
    from services.engine_rust import _apply_adjustment_relative_to_leg
except ModuleNotFoundError:  # pragma: no cover
    from backend.services.engine_rust import _apply_adjustment_relative_to_leg


class TestAdjustmentRelativeToLeg(unittest.TestCase):
    def test_inherits_ref_breaches(self):
        legs = [
            {
                "option_type": "CE", "expiry": "weekly",
                "_spot_adj_breaches": ["2020-01-10", "2020-02-05"],
                "_spot_adj_direction": "rise", "_spot_adj_pct": 2.0,
            },
            {
                "option_type": "PE", "expiry": "monthly", "_spot_adj_breaches": [],
                "adjustment_relative_to_leg": {"enabled": True, "ref_leg": 1},
            },
        ]
        _apply_adjustment_relative_to_leg(legs)
        self.assertEqual(legs[1]["_spot_adj_breaches"], ["2020-01-10", "2020-02-05"])
        self.assertEqual(legs[1]["_adj_rel_ref"], 1)
        # inherits ref semantics for labelling
        self.assertEqual(legs[1]["_spot_adj_direction"], "rise")
        self.assertEqual(legs[1]["_spot_adj_pct"], 2.0)

    def test_union_with_own(self):
        legs = [
            {"_spot_adj_breaches": ["2020-01-10"]},
            {
                "_spot_adj_breaches": ["2020-03-01"],
                "adjustment_relative_to_leg": {"enabled": True, "ref_leg": 1},
            },
        ]
        _apply_adjustment_relative_to_leg(legs)
        self.assertEqual(legs[1]["_spot_adj_breaches"], ["2020-01-10", "2020-03-01"])

    def test_off_is_noop(self):
        legs = [
            {"_spot_adj_breaches": ["2020-01-10"]},
            {"_spot_adj_breaches": []},
        ]
        _apply_adjustment_relative_to_leg(legs)
        self.assertEqual(legs[1]["_spot_adj_breaches"], [])
        self.assertNotIn("_adj_rel_ref", legs[1])

    def test_chain_transitive(self):
        legs = [
            {"_spot_adj_breaches": ["2020-01-10"]},
            {
                "_spot_adj_breaches": ["2020-02-01"],
                "adjustment_relative_to_leg": {"enabled": True, "ref_leg": 1},
            },
            {
                "_spot_adj_breaches": [],
                "adjustment_relative_to_leg": {"enabled": True, "ref_leg": 2},
            },
        ]
        _apply_adjustment_relative_to_leg(legs)
        # leg3 inherits leg2's already-extended (leg1 ∪ leg2) set
        self.assertEqual(legs[2]["_spot_adj_breaches"], ["2020-01-10", "2020-02-01"])

    def test_ref_must_be_earlier(self):
        legs = [
            {
                "_spot_adj_breaches": [],
                "adjustment_relative_to_leg": {"enabled": True, "ref_leg": 2},
            },
            {"_spot_adj_breaches": []},
        ]
        with self.assertRaises(ValueError):
            _apply_adjustment_relative_to_leg(legs)

    def test_ref_self_raises(self):
        legs = [
            {
                "_spot_adj_breaches": [],
                "adjustment_relative_to_leg": {"enabled": True, "ref_leg": 1},
            },
        ]
        with self.assertRaises(ValueError):
            _apply_adjustment_relative_to_leg(legs)


if __name__ == "__main__":
    unittest.main()
