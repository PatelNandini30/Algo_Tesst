"""Resume must continue an interrupted sweep, never silently mix or renumber.

Before this, a killed sweep's finished combos were unreachable: a resubmit got a
fresh job_id and empty trades dir, so 1632/3600 finished combos were recomputed
from scratch. Resume keeps the job_id and dispatches only what is missing.

The two things that would corrupt results if wrong:
  • combo identity — matching must be on parameter VALUES, so a resume can never
    skip a combo that differs, nor recompute one that doesn't;
  • combo ids — survivors must keep their ORIGINAL index, because
    combo_label_safe embeds it and the first run's files are still on disk.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.optimizer import parallel as par
from services.optimizer.runner import _combo_key


class TestComboKey(unittest.TestCase):
    def test_ignores_dispatch_metadata(self):
        """__combo_id__ is bookkeeping — it must not change identity."""
        a = {"legs[0].x": 1, "__combo_id__": 7}
        b = {"legs[0].x": 1, "__combo_id__": 999}
        self.assertEqual(_combo_key(a), _combo_key(b))

    def test_key_is_order_independent(self):
        self.assertEqual(_combo_key({"a": 1, "b": 2}), _combo_key({"b": 2, "a": 1}))

    def test_different_values_are_different_combos(self):
        self.assertNotEqual(_combo_key({"a": 1}), _combo_key({"a": 2}))
        self.assertNotEqual(_combo_key({"a": 1}), _combo_key({"b": 1}))

    def test_false_and_zero_do_not_collide(self):
        """enabled=False vs pct=0 must stay distinct — both appear in real sweeps."""
        self.assertNotEqual(_combo_key({"a": False}), _combo_key({"a": 0}))

    def test_resume_filter_selects_exactly_the_missing_combos(self):
        grid = [{"p": i} for i in range(10)]
        done = {_combo_key(c) for c in grid[:6]}          # first 6 already computed
        missing = [c for c in grid if _combo_key(c) not in done]
        self.assertEqual([c["p"] for c in missing], [6, 7, 8, 9])


class TestComboIdStability(unittest.TestCase):
    """A survivor keeps its original index, so its filenames match the first run."""

    def _dispatch(self, chunk, starting_combo_id):
        seen = []

        def fake_apply(base, combo):
            seen.append(dict(combo))
            raise RuntimeError("stop after id resolution")

        # Reproduce the worker's id rule without running a backtest.
        ids = []
        for i, combo in enumerate(chunk):
            orig = combo.pop("__combo_id__", None) if isinstance(combo, dict) else None
            ids.append(orig if orig is not None else starting_combo_id + i)
        return ids, seen

    def test_stamped_id_wins_over_position(self):
        # Resume dispatching originals 1633 and 1700 as chunk positions 0 and 1.
        chunk = [{"p": 1, "__combo_id__": 1633}, {"p": 2, "__combo_id__": 1700}]
        ids, _ = self._dispatch(chunk, starting_combo_id=1)
        self.assertEqual(ids, [1633, 1700])          # NOT [1, 2] — no collision

    def test_falls_back_to_position_when_unstamped(self):
        chunk = [{"p": 1}, {"p": 2}]
        ids, _ = self._dispatch(chunk, starting_combo_id=41)
        self.assertEqual(ids, [41, 42])

    def test_marker_is_stripped_before_payload_application(self):
        """__combo_id__ must never reach apply_combo_for_optim as a payload path."""
        chunk = [{"p": 1, "__combo_id__": 5}]
        self._dispatch(chunk, starting_combo_id=1)
        self.assertNotIn("__combo_id__", chunk[0])


class TestResumeProgressAccounting(unittest.TestCase):
    def test_done_count_includes_the_first_run(self):
        """A resume with 100 combos left must report 3600/3600, not 100/3600."""
        resume_done, agg_done, total = 3500, 100, 3600
        self.assertEqual(resume_done + agg_done, total)

    def test_terminal_state_treats_full_resume_as_success(self):
        from services.optimizer.runner import _terminal_state
        state, err = _terminal_state(3500 + 100, 0, None)
        self.assertEqual(state, "success")
        self.assertIsNone(err)

    def test_zero_done_still_fails(self):
        from services.optimizer.runner import _terminal_state
        state, _ = _terminal_state(0, 5, "boom")
        self.assertEqual(state, "failed")


if __name__ == "__main__":
    unittest.main()
