import ast
import os
import unittest

_ENGINE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services", "engine_rust.py",
)


def _source():
    with open(_ENGINE, "r", encoding="utf-8") as fh:
        return fh.read()


class TestLegFilterWiring(unittest.TestCase):
    """The mask must be applied, and applied in the right place.

    Grep-based order checks are deliberate: the only alternative is a full
    engine run, which needs market data and would narrow the shared feather.
    """

    def test_apply_leg_filters_is_called(self):
        self.assertIn("apply_leg_filters(specs, payload.get(\"legs\")", _source())

    def test_runs_after_fixed_rollover_strike_and_before_pricing(self):
        src = _source()
        i_fixed = src.index("_apply_fixed_rollover_strike(specs, payload")
        i_mask = src.index("apply_leg_filters(specs, payload.get(\"legs\")")
        i_price = src.index("algotest_native.simulate_trades_batch(specs)")
        self.assertLess(i_fixed, i_mask, "mask must not disturb strike epochs")
        self.assertLess(i_mask, i_price, "mask must be applied before pricing")

    def test_runs_before_the_return_specs_only_early_exit(self):
        src = _source()
        i_mask = src.index("apply_leg_filters(specs, payload.get(\"legs\")")
        i_ret = src.index("if return_specs_only:")
        self.assertLess(i_mask, i_ret, "multi-index FUSED path must be masked too")

    def test_engine_module_parses(self):
        ast.parse(_source())


class TestLegFilterEndReason(unittest.TestCase):
    def test_reason_is_protected_from_the_patch_tagger(self):
        self.assertIn("\"LEG_FILTER_END\",", _source())
        src = _source()
        i_set = src.index("_FILTER_END_SKIP_REASONS")
        i_reason = src.index("\"LEG_FILTER_END\",")
        self.assertLess(i_reason - i_set, 500,
                        "LEG_FILTER_END must be inside _FILTER_END_SKIP_REASONS")

    def test_keys_are_captured_before_pricing(self):
        src = _source()
        self.assertLess(src.index("_leg_filter_end_keys: set = {"),
                        src.index("algotest_native.simulate_trades_batch(specs)"))

    def test_stamped_after_the_patch_tagger(self):
        src = _source()
        self.assertLess(
            src.index("_apply_filter_end_last_per_patch(final_priced"),
            src.index("if _leg_filter_end_keys:"),
        )


if __name__ == "__main__":
    unittest.main()
