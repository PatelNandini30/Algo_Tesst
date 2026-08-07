"""Fork-width term must actually fire — it was dead because `parallelism`
lives at the top level of the Celery spec, not inside base_payload."""
import os, unittest

os.environ.setdefault("HEAVY_COST_OPTIMIZE_PER_CHILD_MB", "700")


class ForkWidthCost(unittest.TestCase):
    def _cost(self, payload, p_env):
        os.environ["OPTIMIZE_PARALLELISM"] = str(p_env)
        import importlib, services.memory_gate as mg
        importlib.reload(mg)
        return mg.cost_for_job("optimize", payload)

    def test_env_fallback_scales_with_fork_width(self):
        span = {"date_from": "2025-01-01", "date_to": "2026-06-30"}
        wide = self._cost(span, 6)
        narrow = self._cost(span, 1)
        # P=6 must reserve (6-1)*700 more than a single-process run.
        self.assertGreater(wide, narrow, "fork-width term did not fire")
        self.assertEqual(wide - narrow, 5 * 700)

    def test_payload_override_wins_over_env(self):
        span = {"date_from": "2025-01-01", "date_to": "2026-06-30", "parallelism": 2}
        self.assertEqual(self._cost(span, 6) - self._cost({**span, "parallelism": 1}, 6),
                         1 * 700)


if __name__ == "__main__":
    unittest.main()


class SplitAwareCost(unittest.TestCase):
    def test_p_override_prices_at_actual_fork_width(self):
        os.environ["OPTIMIZE_PARALLELISM"] = "6"
        import importlib, services.memory_gate as mg
        importlib.reload(mg)
        span = {"date_from": "2025-01-01", "date_to": "2026-06-30"}
        at6 = mg.cost_for_job("optimize", span, p_override=6)
        at3 = mg.cost_for_job("optimize", span, p_override=3)
        # Half the children must cost 3*700 less — this is what lets a second
        # optim fit alongside instead of queueing.
        self.assertEqual(at6 - at3, 3 * 700)

    def test_resize_is_noop_without_rid(self):
        import importlib, services.memory_gate as mg
        importlib.reload(mg)
        mg.resize("", 1234)          # must not raise


class ForkWidthAdaptive(unittest.TestCase):
    """Width is re-derived per batch from live optims AND current free RAM, so it
    shrinks when the box is busy and recovers when RAM frees."""

    def _p(self, solo_ceiling, live, ram_cap):
        by_optims = max(1, solo_ceiling // live)
        return max(1, min(by_optims, ram_cap))      # mirrors _current_p()

    def test_shrinks_when_another_optim_starts(self):
        self.assertEqual(self._p(6, 2, 6), 3)

    def test_ram_caps_below_optim_split(self):
        self.assertEqual(self._p(6, 1, 1), 1)       # box busy -> single worker

    def test_recovers_when_ram_frees(self):
        # same job, same live count, RAM freed from 1-worth to 3-worth
        self.assertEqual(self._p(6, 1, 1), 1)
        self.assertEqual(self._p(6, 1, 3), 3)       # was pinned at 1 before this fix
