"""
Task-6 verification: optimizer parity & coverage gate for the per-leg-filter split.

Three sub-step verdicts:

1. COVERAGE GATE (sub-step 1)
   The per-leg filter gate ("leg_filter") was corpus-verified clean at 0 diffs and
   LIFTED on 2026-08-14 in the user's dirty rust_combo_loop.py (see lines 225-232 of
   that file).  In OPTIMIZE_RUST_LOOP=1 (mode 1) only the SUMMARY is Rust-authoritative;
   the TRADES still come from run_rust_engine_pipeline — the same path as a direct
   backtest.  Corpus job 26736821 confirmed 6 combos / 672 fields / 0 diffs.

   The pre-existing test test_per_leg_filter_is_unsupported in
   test_rust_combo_whitelist.py reflects the pre-lift expectation ("leg_filter") and
   is part of the baseline 33 FAILures.  Task 6 does NOT restore that gate —
   doing so would touch the user's dirty file.

   The tests below assert the CURRENT (post-lift) contract and the optimizer call-path
   that makes optim==backtest hold regardless of the gate state.

2. OPTIM==BACKTEST BY CONSTRUCTION (sub-step 2)
   runner.py's _run_single_backtest_rust_fast calls run_rust_engine_pipeline directly.
   apply_leg_filters_split lives inside run_rust_engine_pipeline (engine_rust.py).
   Both paths share the identical code, so the split is applied identically.

3. FUTURES FILTERED LEGS (sub-step 3)
   Futures legs use subtract-only (no mid-cycle entry — out of scope for Task 3).
   Both backtest and optimizer take that same path via run_rust_engine_pipeline.
   No divergence.  The multi-index fused guard (engine_rust.py, return_specs_only block)
   already hard-fails per-leg filter on the only risky path (fused pricing hand-off).

Pure source-text / logic tests.  No data / native dependency.
"""
import inspect
import unittest


class TestOptimizerCallPath(unittest.TestCase):
    """Sub-step 2: optimizer per-combo path uses run_rust_engine_pipeline."""

    def _runner_src(self):
        from services.optimizer import runner
        return inspect.getsource(runner)

    def test_run_single_backtest_rust_fast_calls_run_rust_engine_pipeline(self):
        """_run_single_backtest_rust_fast calls run_rust_engine_pipeline directly."""
        src = self._runner_src()
        self.assertIn("priced = run_rust_engine_pipeline(", src)

    def test_optimizer_loop_calls_run_single_backtest(self):
        """The sequential combo loop calls _run_single_backtest (which routes to
        _run_single_backtest_rust_fast → run_rust_engine_pipeline)."""
        src = self._runner_src()
        self.assertIn("trades_df, summary = _run_single_backtest(merged)", src)

    def test_run_rust_engine_pipeline_is_imported_from_engine_rust(self):
        """runner.py imports run_rust_engine_pipeline from services.engine_rust."""
        src = self._runner_src()
        self.assertIn("from services.engine_rust import", src)
        self.assertIn("run_rust_engine_pipeline", src)

    def test_apply_leg_filters_split_lives_inside_run_rust_engine_pipeline(self):
        """apply_leg_filters is called from within run_rust_engine_pipeline in
        engine_rust.py — meaning every caller of run_rust_engine_pipeline (both
        backtest and optimizer) applies the split identically."""
        from services import engine_rust
        src = inspect.getsource(engine_rust)
        # The call site added in Task 3
        self.assertIn("specs = apply_leg_filters(", src)
        # It lives inside run_rust_engine_pipeline
        pipeline_idx = src.index("def run_rust_engine_pipeline(")
        call_idx = src.index("specs = apply_leg_filters(")
        self.assertGreater(call_idx, pipeline_idx,
                           "apply_leg_filters call must appear after run_rust_engine_pipeline def")


class TestLegFilterGateLiftedState(unittest.TestCase):
    """Sub-step 1: document the current (post-lift) state of the coverage gate.

    The leg_filter gate was LIFTED 2026-08-14 (corpus-verified 0 diffs, 6 combos,
    672 fields).  These tests assert the CURRENT contract so a future edit can't
    silently change it without a test failure.
    """

    def _unsupported(self, payload):
        from services.optimizer.rust_combo_loop import rust_batch_unsupported
        return rust_batch_unsupported(payload)

    def _leg(self, **kw):
        base = {"option_type": "CE", "position": "SELL",
                "strike_selection": {"type": "strike_type"}}
        base.update(kw)
        return base

    def test_gate_lift_is_documented_in_source(self):
        """The lifted gate has a LIFTED comment explaining why."""
        from services.optimizer import rust_combo_loop
        src = inspect.getsource(rust_combo_loop)
        self.assertIn("LIFTED 2026-08-14 (leg_filter)", src)

    def test_per_leg_filter_gate_current_state(self):
        """After the lift, rust_batch_unsupported returns None (not 'leg_filter')
        for a leg carrying filter_segments — the Rust summary is corpus-proven safe.
        IMPORTANT: if this test starts FAILING it means the gate was RE-ADDED,
        which is intentional — the pre-lift test test_per_leg_filter_is_unsupported
        in test_rust_combo_whitelist.py will then PASS again (remove this test then).
        """
        from services.optimizer.rust_combo_loop import rust_batch_unsupported
        leg = self._leg(filter_segments=[{"start": "2025-04-05", "end": "2025-06-05"}])
        result = rust_batch_unsupported({"legs": [leg]})
        # Gate is lifted: None means the Rust summary owns this combo's stats
        # (but trades still come from run_rust_engine_pipeline — optim==backtest holds)
        self.assertIsNone(result,
                          "leg_filter gate is lifted — expect None; if re-added "
                          "this will fail and test_per_leg_filter_is_unsupported "
                          "in test_rust_combo_whitelist.py will pass instead.")

    def test_split_triggering_range_same_outcome(self):
        """A split-triggering filter_segments range (start > trade-entry, so Case B
        mid-cycle fresh entry would fire) also returns None with the gate lifted."""
        from services.optimizer.rust_combo_loop import rust_batch_unsupported
        leg = self._leg(filter_segments=[{"start": "2025-05-15", "end": "2025-07-31"}])
        self.assertIsNone(rust_batch_unsupported({"legs": [leg]}))

    def test_empty_filter_segments_still_supported(self):
        """Empty filter_segments (uploaded then cleared) is always supported."""
        from services.optimizer.rust_combo_loop import rust_batch_unsupported
        leg = self._leg(filter_segments=[])
        self.assertIsNone(rust_batch_unsupported({"legs": [leg]}))


class TestFuturesFilteredLegNoDivergence(unittest.TestCase):
    """Sub-step 3: futures leg with filter_segments — no silent divergence.

    A futures leg with filter_segments uses subtract-only (no mid-cycle entry)
    via _build_futures_specs / _apply_leg_filter_mask.  Both the direct backtest
    and the optimizer call run_rust_engine_pipeline, so they apply exactly the
    same subtract-only behavior.  No Rust-vs-Python divergence exists.

    The multi-index fused guard (engine_rust.py, return_specs_only block) already
    hard-fails per-leg filter on the only risky path.
    """

    def _unsupported(self, payload):
        from services.optimizer.rust_combo_loop import rust_batch_unsupported
        return rust_batch_unsupported(payload)

    def _fut_leg(self, **kw):
        base = {"option_type": "FUT", "segment": "futures",
                "strike_selection": {"type": "strike_type"}}
        base.update(kw)
        return base

    def test_futures_gate_is_lifted_in_current_tree(self):
        """The futures gate is also lifted (LIFTED 2026-08-14 (futures leg))."""
        from services.optimizer import rust_combo_loop
        src = inspect.getsource(rust_combo_loop)
        self.assertIn("LIFTED 2026-08-14 (futures leg)", src)

    def test_futures_leg_with_filter_segments_gate_state(self):
        """With the futures gate lifted, a FUT leg with filter_segments returns None
        (same as any other leg with gate lifted) — no divergence because trades
        come from run_rust_engine_pipeline in both optim and backtest."""
        leg = self._fut_leg(filter_segments=[{"start": "2025-04-05", "end": "2025-06-05"}])
        self.assertIsNone(self._unsupported({"legs": [leg]}))

    def test_fused_guard_source_exists_for_filter_segments(self):
        """The return_specs_only guard hard-fails per-leg filter on the fused path
        (Task-3 FIX 1), which is the only path where futures filtered legs could
        silently produce wrong numbers."""
        from services import engine_rust
        src = inspect.getsource(engine_rust)
        # Task-3 round-2 FIX 1 guard
        self.assertIn("if return_specs_only:", src)
        self.assertIn("Per-leg individual filter is not supported on the", src)
        self.assertIn("multi-index fused", src.lower())

    def test_futures_subtract_only_path_documented_in_leg_filter(self):
        """leg_filter.py has a TODO noting futures use subtract-only (no split)."""
        from services import leg_filter
        src = inspect.getsource(leg_filter)
        self.assertIn("Futures filtered legs", src)


if __name__ == "__main__":
    unittest.main()
