"""Tests for services.optimizer.param_expander."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.optimizer.param_expander import (  # noqa: E402
    apply_combo,
    count_combinations,
    effective_combo_count,
    expand_param_specs,
    get_by_path,
)


class TestExpandValues(unittest.TestCase):
    def test_integer_range_step_1(self):
        specs = [{"path": "entry_dte", "kind": "range", "min": 1, "max": 5, "step": 1}]
        combos = list(expand_param_specs(specs))
        self.assertEqual([c["entry_dte"] for c in combos], [1, 2, 3, 4, 5])
        self.assertTrue(all(isinstance(c["entry_dte"], int) for c in combos))

    def test_float_range(self):
        specs = [{"path": "slip", "kind": "range", "min": 0.1, "max": 0.5, "step": 0.1}]
        combos = list(expand_param_specs(specs))
        vals = [c["slip"] for c in combos]
        self.assertEqual(vals, [0.1, 0.2, 0.3, 0.4, 0.5])

    def test_values_list(self):
        specs = [
            {"path": "x.y", "kind": "values", "values": [10, 20, 30]},
        ]
        self.assertEqual([c["x.y"] for c in expand_param_specs(specs)], [10, 20, 30])

    def test_enum(self):
        specs = [
            {
                "path": "legs[0].strike_selection.strike_type",
                "kind": "enum",
                "values": ["ATM", "ITM1", "OTM1"],
            }
        ]
        out = [c["legs[0].strike_selection.strike_type"] for c in expand_param_specs(specs)]
        self.assertEqual(out, ["ATM", "ITM1", "OTM1"])

    def test_invalid_step_zero(self):
        specs = [{"path": "x", "kind": "range", "min": 0, "max": 5, "step": 0}]
        with self.assertRaises(ValueError):
            list(expand_param_specs(specs))

    def test_max_lt_min(self):
        specs = [{"path": "x", "kind": "range", "min": 5, "max": 1, "step": 1}]
        with self.assertRaises(ValueError):
            list(expand_param_specs(specs))


class TestCartesianProduct(unittest.TestCase):
    def test_two_params_cartesian(self):
        specs = [
            {"path": "a", "kind": "range", "min": 1, "max": 2, "step": 1},
            {"path": "b", "kind": "range", "min": 10, "max": 30, "step": 10},
        ]
        combos = list(expand_param_specs(specs))
        self.assertEqual(len(combos), 6)  # 2 × 3
        # First spec varies SLOWEST (outermost) per itertools.product
        self.assertEqual(combos[0], {"a": 1, "b": 10})
        self.assertEqual(combos[1], {"a": 1, "b": 20})
        self.assertEqual(combos[2], {"a": 1, "b": 30})
        self.assertEqual(combos[3], {"a": 2, "b": 10})
        # No duplicates
        seen = {(c["a"], c["b"]) for c in combos}
        self.assertEqual(len(seen), 6)

    def test_three_params_cartesian(self):
        specs = [
            {"path": "a", "kind": "values", "values": [1, 2]},
            {"path": "b", "kind": "values", "values": ["x", "y"]},
            {"path": "c", "kind": "values", "values": [True, False]},
        ]
        self.assertEqual(count_combinations(specs), 8)
        self.assertEqual(len(list(expand_param_specs(specs))), 8)

    def test_count_matches_expand(self):
        specs = [
            {"path": "a", "kind": "range", "min": 0, "max": 10, "step": 2},  # 6 vals
            {"path": "b", "kind": "values", "values": [-1, 0, 1]},  # 3 vals
        ]
        self.assertEqual(count_combinations(specs), 18)
        self.assertEqual(len(list(expand_param_specs(specs))), 18)

    def test_empty_specs_yields_one_empty_combo(self):
        self.assertEqual(list(expand_param_specs([])), [{}])
        self.assertEqual(count_combinations([]), 1)


class TestGatedParamCollapse(unittest.TestCase):
    """When spot_adjustment_enabled is swept OFF, its dependent params
    (pct/direction) must NOT multiply out into duplicate combos, while the ON
    branch keeps the full sweep."""

    def _specs(self):
        return [
            {"path": "legs[0].strike_selection.value", "kind": "range",
             "min": -3, "max": 3, "step": 0.5},                 # 13 strikes
            {"path": "spot_adjustment_pct", "kind": "range",
             "min": 1, "max": 2, "step": 1},                    # 2 pct
            {"path": "spot_adjustment_direction", "kind": "enum",
             "values": ["rise", "fall", "both"]},               # 3 dirs
            {"path": "spot_adjustment_enabled", "kind": "enum",
             "values": [False, True]},                          # gate
        ]

    def test_no_duplicate_no_adjustment_combos(self):
        specs = self._specs()
        combos = list(expand_param_specs(specs))
        # Raw product would be 13*2*3*2 = 156. Effective = 13 (OFF, one per
        # strike) + 78 (ON, 13*2*3) = 91.
        self.assertEqual(count_combinations(specs), 156)
        self.assertEqual(len(combos), 91)
        self.assertEqual(effective_combo_count(specs), 91)

        off = [c for c in combos if c.get("spot_adjustment_enabled") is False]
        on = [c for c in combos if c.get("spot_adjustment_enabled") is True]
        # OFF branch: exactly one per strike, with pct/direction dropped.
        self.assertEqual(len(off), 13)
        for c in off:
            self.assertNotIn("spot_adjustment_pct", c)
            self.assertNotIn("spot_adjustment_direction", c)
        self.assertEqual(len({c["legs[0].strike_selection.value"] for c in off}), 13)
        # ON branch: full pct×direction sweep retained, no collapsing.
        self.assertEqual(len(on), 78)
        for c in on:
            self.assertIn("spot_adjustment_pct", c)
            self.assertIn("spot_adjustment_direction", c)
        # No duplicate combos overall.
        ident = {tuple(sorted(c.items())) for c in combos}
        self.assertEqual(len(ident), len(combos))

    def test_no_gate_swept_is_unchanged(self):
        # Without the enabled toggle in the sweep, behaviour is the plain
        # cartesian product (no pruning, zero overhead path).
        specs = [
            {"path": "spot_adjustment_pct", "kind": "range",
             "min": 1, "max": 2, "step": 1},
            {"path": "spot_adjustment_direction", "kind": "enum",
             "values": ["rise", "fall", "both"]},
        ]
        self.assertEqual(count_combinations(specs), 6)
        self.assertEqual(effective_combo_count(specs), 6)
        self.assertEqual(len(list(expand_param_specs(specs))), 6)


class TestPathOverrides(unittest.TestCase):
    def test_nested_dict_set(self):
        payload = {"legs": [{"stopLoss": {"value": 10}}]}
        combo = {"legs[0].stopLoss.value": 25}
        out = apply_combo(payload, combo)
        self.assertEqual(out["legs"][0]["stopLoss"]["value"], 25)
        # Original unchanged (deep copy)
        self.assertEqual(payload["legs"][0]["stopLoss"]["value"], 10)

    def test_top_level_set(self):
        payload = {"entry_dte": 1}
        out = apply_combo(payload, {"entry_dte": 7})
        self.assertEqual(out["entry_dte"], 7)
        self.assertEqual(payload["entry_dte"], 1)

    def test_multiple_paths(self):
        payload = {"legs": [{"stopLoss": {"value": 0}, "tp": {"value": 0}}]}
        combo = {"legs[0].stopLoss.value": 30, "legs[0].tp.value": 60}
        out = apply_combo(payload, combo)
        self.assertEqual(out["legs"][0]["stopLoss"]["value"], 30)
        self.assertEqual(out["legs"][0]["tp"]["value"], 60)

    def test_get_by_path(self):
        payload = {"legs": [{"stopLoss": {"value": 25}}]}
        self.assertEqual(get_by_path(payload, "legs[0].stopLoss.value"), 25)
        self.assertIsNone(get_by_path(payload, "legs[0].missing.value"))
        self.assertEqual(get_by_path(payload, "x.y", default="N/A"), "N/A")

    def test_create_missing_path(self):
        payload = {}
        out = apply_combo(payload, {"legs[0].stopLoss.value": 30})
        self.assertEqual(out["legs"][0]["stopLoss"]["value"], 30)


if __name__ == "__main__":
    unittest.main()
