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


class TestFuturesPathMasked(unittest.TestCase):
    """Futures rows are priced inside their builders and never reach
    apply_leg_filters, so the mask must appear in both futures builders.

    Both builders funnel through the shared _apply_leg_filter_mask helper
    (extracted so the two call sites can't drift apart) — these tests key on
    that call, not on leg_segments/leg_window directly, since the helper is
    now the only place those two names appear in either builder.
    """

    def _slice(self, src, fn_name):
        start = src.index("def %s(" % fn_name)
        nxt = src.index("\ndef ", start + 1)
        return src[start:nxt]

    def test_build_futures_specs_applies_the_mask(self):
        body = self._slice(_source(), "_build_futures_specs")
        self.assertIn("_apply_leg_filter_mask(", body)

    def test_fixed_entry_futures_specs_applies_the_mask(self):
        body = self._slice(_source(), "_build_fixed_entry_futures_specs")
        self.assertIn("_apply_leg_filter_mask(", body)

    def test_mask_precedes_pricing_in_build_futures_specs_rolled_branch(self):
        # The rolled-hold branch prices via _fut_price(index, entry_date, ...).
        # This literal is unique inside _build_futures_specs's own body (a
        # second, unrelated occurrence lives in a different function further
        # down the file, outside this slice).
        body = self._slice(_source(), "_build_futures_specs")
        i_price = body.index("_fut_price(index, entry_date")
        i_mask = body.rindex("_apply_leg_filter_mask(", 0, i_price)
        self.assertGreaterEqual(i_mask, 0, "mask call not found before rolled-branch pricing")

    def test_mask_precedes_pricing_in_build_futures_specs_nonrolled_branch(self):
        # The non-rolled branch prices via _resolve_futures_pnl_native(...).
        # Its FIRST occurrence in the function body is this branch's call (a
        # second occurrence, in the re-entry loop further down, is out of
        # scope for this assertion).
        body = self._slice(_source(), "_build_futures_specs")
        i_price_rolled = body.index("_fut_price(index, entry_date")
        i_price_nonrolled = body.index("_resolve_futures_pnl_native(")
        i_mask_nonrolled = body.rindex("_apply_leg_filter_mask(", 0, i_price_nonrolled)
        # Must be the SECOND mask call (after the rolled branch's), not a
        # false-positive match on the rolled branch's own mask call — this is
        # exactly the gap the old single-`.index()` test missed.
        self.assertGreater(i_mask_nonrolled, i_price_rolled,
                            "non-rolled branch's own mask call not found — "
                            "test would pass even if this branch's mask were "
                            "moved after its pricing call")

    def test_mask_precedes_pricing_in_build_fixed_entry_futures_specs(self):
        body = self._slice(_source(), "_build_fixed_entry_futures_specs")
        i_price = body.index("_resolve_futures_pnl_native(")
        i_mask = body.rindex("_apply_leg_filter_mask(", 0, i_price)
        self.assertGreaterEqual(i_mask, 0, "mask call not found before pricing")


if __name__ == "__main__":
    unittest.main()
