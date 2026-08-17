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
        src = _source()
        # Task 3 expanded the call to include spot_by_date and resolve_strike;
        # assert the core positional args are still threaded through.
        self.assertIn("apply_leg_filters(", src)
        self.assertIn("payload.get(\"legs\") or []", src)
        self.assertIn("trading_days,", src)

    def test_runs_after_fixed_rollover_strike_and_before_pricing(self):
        src = _source()
        i_fixed = src.index("_apply_fixed_rollover_strike(specs, payload")
        i_mask = src.index("specs = apply_leg_filters(")
        i_price = src.index("algotest_native.simulate_trades_batch(specs)")
        self.assertLess(i_fixed, i_mask, "mask must not disturb strike epochs")
        self.assertLess(i_mask, i_price, "mask must be applied before pricing")

    def test_runs_before_the_return_specs_only_early_exit(self):
        src = _source()
        i_mask = src.index("specs = apply_leg_filters(")
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
        self.assertLess(src.index("_leg_filter_end_keys: Dict[Tuple[str, int], Set[str]] = {}"),
                        src.index("algotest_native.simulate_trades_batch(specs)"))

    def test_stamped_after_the_patch_tagger(self):
        src = _source()
        self.assertLess(
            src.index("_apply_filter_end_last_per_patch(final_priced"),
            src.index("if not _is_leg_filter_ended(_row):"),
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


class TestSpotAdjGuard(unittest.TestCase):
    """A leg already ended by its OWN filter file must not be resurrected by
    the spot-adjustment cascade — neither given a later spot-adj exit date,
    nor re-entered into a spot-adj mini-trade.

    Two independent guards exist (exit-clamp site + re-entry synthesis
    site). A single `.index()`/`.rindex()` on the bare literal
    "in _leg_filter_end_keys:" is NOT enough to tell them apart from the
    pre-existing Task-4 tagging block at the end of the function (which also
    contains that literal and runs unconditionally) — so this asserts each
    guard by its distinctive surrounding code instead.
    """

    def test_exit_clamp_site_checks_the_guard_before_applying(self):
        src = _source()
        i_clamp_reason = src.index("_sa_clamp_reason = spot_adj_reasons.get")
        i_guard = src.index("_sa_leg_filter_ended", i_clamp_reason)
        i_apply = src.index("final_exit = spot_adj_clamp", i_guard)
        self.assertLess(i_clamp_reason, i_guard)
        self.assertLess(i_guard, i_apply,
                         "guard must be evaluated before the clamp is applied")

    def test_reentry_synthesis_skips_filter_ended_legs(self):
        src = _source()
        i_guard = src.index("if _leg_was_truncated(_sa_leg):")
        i_append = src.index("mini_specs.append(", i_guard)
        self.assertLess(i_guard, i_append,
                         "guard must precede the mini-trade append")


class TestTruncatedExitIsSnapped(unittest.TestCase):
    """C1: an uploaded window ending on a non-trading day must snap back.

    The rule itself is unit-tested in test_leg_filter.py; what has to be checked
    HERE is that the engine actually hands the trading-day list over -- the bug
    was a missing argument, not a wrong algorithm.
    """

    def test_options_post_pass_receives_trading_days(self):
        # Task 3 expanded the call to include spot_by_date and resolve_strike.
        # Verify trading_days is still passed (as the third positional arg).
        src = _source()
        self.assertIn("apply_leg_filters(", src)
        self.assertIn("trading_days,", src)
        self.assertIn("spot_by_date=spot_by_date", src)

    def test_both_paths_share_one_implementation(self):
        # The options post-pass and the futures helper must resolve the mask
        # through the SAME function, so the same file can never behave
        # differently on an option leg than on a futures leg.
        src = _source()
        i_def = src.index("def _apply_leg_filter_mask(")
        i_end = src.index("def _build_futures_specs(", i_def)
        body = src[i_def:i_end]
        self.assertIn("from services.leg_filter import resolve_leg_window", body)
        self.assertIn("return resolve_leg_window(leg, entry_date, exit_date, sorted_td)", body)
        # ...and no second, local re-implementation of the snap.
        self.assertNotIn("_last_trading_day_on_or_before(leg_exit", body)

    def test_engine_snap_helper_delegates_to_leg_filter(self):
        src = _source()
        i = src.index("def _last_trading_day_on_or_before(")
        body = src[i:i + 700]
        self.assertIn("from services.leg_filter import last_trading_day_on_or_before", body)


class TestUnsupportedPathsHardFail(unittest.TestCase):
    """C2 / I1: a path that cannot honour the mask must RAISE, never ignore it.

    Rust-only, no silent degradation. Each guard must sit OUTSIDE the try/except
    that wraps its builder, otherwise the except turns the hard-fail back into
    the silent fallback it is meant to replace.
    """

    def test_guard_helper_raises(self):
        src = _source()
        i = src.index("def _reject_leg_filter_unsupported(")
        body = src[i:src.index("def _build_futures_specs(", i)]
        self.assertIn("raise RuntimeError(", body)
        self.assertIn("leg_segments", body)

    def test_mixed_futures_next_weekly_is_guarded_before_the_try(self):
        src = _source()
        i_guard = src.index('_reject_leg_filter_unsupported(payload, "mixed FUTURES+NEXT_WEEKLY")')
        i_build = src.index("_build_mixed_futures_next_weekly(", i_guard)
        i_try = src.rindex("try:", 0, i_build)
        self.assertLess(i_guard, i_try, "guard must sit outside the swallowing try")

    def test_mixed_futures_options_is_guarded_before_the_try(self):
        src = _source()
        i_guard = src.index('_reject_leg_filter_unsupported(payload, "mixed FUTURES+OPTIONS')
        i_build = src.index("_build_mixed_futures_options(", i_guard)
        i_try = src.rindex("try:", 0, i_build)
        self.assertLess(i_guard, i_try, "guard must sit outside the swallowing try")

    def test_fused_multi_index_path_raises_on_a_truncated_leg(self):
        src = _source()
        i_ret = src.index("if return_specs_only:")
        body = src[i_ret:src.index("return specs", i_ret)]
        self.assertIn("if _leg_filter_end_keys:", body)
        self.assertIn("raise RuntimeError(", body)

    def test_fused_guard_runs_after_the_keys_are_built(self):
        src = _source()
        self.assertLess(
            src.index("_leg_filter_end_keys: Dict[Tuple[str, int], Set[str]] = {}"),
            src.index("if return_specs_only:"),
        )


class TestTagOnlyWhenTheBoundaryBound(unittest.TestCase):
    """Deferred-6: a row that exited EARLIER on SL/Target was not bound by the
    filter and must keep its own reason. "STOP_LOSS+LEG_FILTER_END" would
    wrongly drop a legitimate exit out of apply_exit_anchor_exclusion, which
    matches on `contains`.
    """

    def test_options_tagger_checks_the_realised_exit(self):
        src = _source()
        i = src.index("def _is_leg_filter_ended(")
        body = src[i:i + 900]
        self.assertIn("exit_override or row.get(\"exit_date\")", body)

    def test_futures_primary_row_checks_the_boundary(self):
        self.assertIn("if _leg_filter_end_row and fut_exit_date == _lf_bound:", _source())

    def test_futures_reentry_row_checks_the_boundary(self):
        self.assertIn("if _leg_filter_end_row and _re_exit_date == _lf_bound:", _source())

    def test_fixed_entry_futures_row_checks_the_boundary(self):
        self.assertIn("if _leg_filter_end_row and fut_exit_date == _fe_exit_date:", _source())

    def test_no_bare_truncation_tag_remains(self):
        # Every LEG_FILTER_END tag site must be conditioned on the realised exit.
        src = _source()
        self.assertNotIn("if _leg_filter_end_row:\n", src)


class TestKeysSurviveTradeIdRenumbering(unittest.TestCase):
    """I2: the marker must not be keyed on trade_id -- re-entry, bridge and
    spot-adj synthesis allocate fresh ids (and fresh entry dates), so those
    rows would fall out of the set and go untagged/unguarded.
    """

    def test_keys_do_not_contain_trade_id(self):
        src = _source()
        i = src.index("_leg_filter_end_keys: Dict[Tuple[str, int], Set[str]] = {}")
        body = src[i:src.index("def _is_leg_filter_ended(", i)]
        self.assertNotIn("trade_id", body)
        self.assertIn("_normalize_iso(_s.get(\"expiry\", \"\"))", body)

    def test_every_consumer_goes_through_the_shared_predicate(self):
        src = _source()
        # Three consumers: the clamp guard, the re-entry guard and the tagger.
        # 1 def + 2 uses (clamp guard, tagger). The re-entry-synthesis guard
        # deliberately uses the WIDER _leg_was_truncated -- see
        # TestReentryGuardTestsPresenceNotExitEquality below.
        self.assertEqual(src.count("_is_leg_filter_ended("), 3)
        self.assertEqual(src.count("_leg_was_truncated("), 2)  # 1 def + 1 use


class TestReentryGuardTestsPresenceNotExitEquality(unittest.TestCase):
    """The spot-adj RE-ENTRY-SYNTHESIS guard is a safety guard, not a label.

    Narrowing it to "the realised exit landed on the boundary" (correct for the
    three TAG sites) lets a leg that was truncated but exited EARLIER on its own
    SL/Target be resurrected into a spot-adjustment mini-trade. Mini-specs never
    pass back through apply_leg_filters, so that re-entry can hold PAST the
    leg's own window end -- the window violation this whole feature prevents.

    The sibling CLAMP guard is correctly left on the narrow predicate: applying
    or skipping the clamp only ever moves an exit earlier, so it cannot violate
    a window.
    """

    def _reentry_guard_line(self):
        src = _source()
        # Anchor on the mini-spec synthesis loop, which is unique to this site
        # and appears at neither the clamp guard nor any of the tag sites.
        i_loop = src.index("for _sa_leg in sorted(orig_legs_s")
        i_append = src.index("mini_specs.append(", i_loop)
        return src[i_loop:i_append]

    def test_guard_uses_the_wide_presence_predicate(self):
        body = self._reentry_guard_line()
        self.assertIn("if _leg_was_truncated(_sa_leg):", body)
        self.assertIn("continue", body)

    def test_guard_does_not_use_the_exit_equality_predicate(self):
        # This is the regression itself: _is_leg_filter_ended here would let an
        # SL-exited truncated leg through.
        self.assertNotIn("_is_leg_filter_ended", self._reentry_guard_line())

    def test_presence_predicate_ignores_the_rows_exit_date(self):
        src = _source()
        i = src.index("def _leg_was_truncated(")
        body = src[i:src.index("if return_specs_only:", i)]
        self.assertIn("return _leg_filter_bounds(row) is not None", body)
        self.assertNotIn("exit_date", body)

    def test_clamp_guard_keeps_the_narrow_predicate(self):
        # Explicitly pinned: this site must NOT be widened along with the other.
        self.assertIn("_sa_leg_filter_ended = _is_leg_filter_ended(leg, final_exit)",
                      _source())

    def test_the_two_predicates_are_distinct(self):
        src = _source()
        self.assertIn("def _is_leg_filter_ended(", src)
        self.assertIn("def _leg_was_truncated(", src)


if __name__ == "__main__":
    unittest.main()
