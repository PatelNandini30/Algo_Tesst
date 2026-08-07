"""A P=1 start must stay adaptive.

Regression: run_parallel returned early whenever parallelism<=1, so a sweep that
launched while the box was momentarily RAM-tight (MemAvailable 1459MB vs the
1500MB floor) ran its ENTIRE life serial — 2160 combos at P=1 with 4.1GB free —
because the early return never reached the batch loop that re-measures width.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.optimizer import parallel as par


class TestAdaptiveP1Climb(unittest.TestCase):
    def _run(self, combos, caps, solo_ceiling=6, waiting=0, live=1):
        """Run run_parallel with the billiard pool faked out; `caps` feeds width.

        The pool is mocked rather than executed: every batch forks now (p==1
        included), and forking for real inside the test container hangs.
        `calls` records the chunk each simulated worker received.
        """
        calls = []
        seq = list(caps)

        def fake_cap(requested, live_optims=1):
            return seq.pop(0) if seq else caps[-1]

        def fake_apply_async(func, args=(), kwds=None):
            chunk = list(args[2])
            calls.append(chunk)
            res = mock.Mock()
            res.get.return_value = {"done": len(chunk), "failures": 0, "error": None}
            return res

        with mock.patch.object(par, "cap_parallelism_for_live_ram", fake_cap), \
             mock.patch.dict(os.environ, {"OPTIMIZE_BATCH_PER_WORKER": "40"}), \
             mock.patch("services.memory_gate.waiting_count",
                        return_value=waiting, create=True), \
             mock.patch("services.optimizer.result_store.active_optim_count",
                        return_value=live, create=True), \
             mock.patch("billiard.get_context") as gc:
            gc.return_value.Pool.return_value.apply_async = fake_apply_async
            agg = par.run_parallel(
                job_id="t", base_payload={}, combos=combos,
                objective_name="total_pnl", parallelism=1,
                solo_ceiling=solo_ceiling, node_id="local",
            )
        return agg, calls

    def test_p1_start_batches_instead_of_one_serial_run(self):
        """P=1 + adaptive → batch loop (40/40/10), not one 90-combo serial call."""
        combos = [{"i": i} for i in range(90)]
        agg, calls = self._run(combos, caps=[1])
        self.assertEqual([len(c) for c in calls], [40, 40, 10])
        self.assertEqual(agg["done"], 90)
        self.assertEqual(agg["failures"], 0)
        # every combo ran exactly once, in order
        self.assertEqual([c["i"] for batch in calls for c in batch], list(range(90)))

    def test_p1_batch_forks_a_single_child(self):
        """p==1 must FORK one child, never run in the parent.

        Running the batch in-process initialises Rust/rayon state in the parent,
        and the next batch's fork then inherits it without its threads — the
        children hang before their first combo. Every batch must fork from a
        pristine parent.
        """
        combos = [{"i": i} for i in range(10)]
        with mock.patch.object(par, "_worker_entrypoint") as we, \
             mock.patch.object(par, "cap_parallelism_for_live_ram", return_value=1), \
             mock.patch("services.memory_gate.waiting_count", return_value=0, create=True), \
             mock.patch("services.optimizer.result_store.active_optim_count",
                        return_value=1, create=True), \
             mock.patch("billiard.get_context") as gc:
            pool = gc.return_value.Pool.return_value
            pool.apply_async.return_value.get.return_value = {
                "done": 10, "failures": 0, "error": None}
            agg = par.run_parallel(
                job_id="t", base_payload={}, combos=combos,
                objective_name="total_pnl", parallelism=1,
                solo_ceiling=6, node_id="local",
            )
        gc.return_value.Pool.assert_called_with(processes=1)
        we.assert_not_called()          # never executed in the parent
        self.assertEqual(agg["done"], 10)

    def test_solo_ceiling_zero_keeps_old_terminal_serial_path(self):
        """Opted out of adaptive width → one in-process call, exactly as before.

        This path never forks afterwards, so running in the parent is safe here;
        it is only unsafe when a later batch forks (see test_p1_batch_forks_a_
        single_child).
        """
        combos = [{"i": i} for i in range(90)]
        with mock.patch.object(par, "_worker_entrypoint") as we, \
             mock.patch("billiard.get_context") as gc:
            we.return_value = {"done": 90, "failures": 0, "error": None}
            agg = par.run_parallel(
                job_id="t", base_payload={}, combos=combos,
                objective_name="total_pnl", parallelism=1,
                solo_ceiling=0, node_id="local",
            )
        self.assertEqual(we.call_count, 1)
        self.assertEqual(len(we.call_args[0][2]), 90)   # one chunk, all combos
        gc.return_value.Pool.assert_not_called()
        self.assertEqual(agg["done"], 90)


class TestWaiterNarrowsWidth(unittest.TestCase):
    """A job blocked on the memory gate must make a running sweep give width back.

    These exercise split_width directly — driving run_parallel at P>1 would fork
    real billiard children, which is not what's under test.
    """

    def _width(self, waiting, live=1, ceiling=6, kind="backtest"):
        """`waiting` is returned only for `kind`; other kinds count as 0."""
        def fake_waiting(k=None, node_id=None):
            return waiting if k == kind else 0

        with mock.patch.object(par, "cap_parallelism_for_live_ram",
                               side_effect=lambda req, live_optims=1: req), \
             mock.patch("services.memory_gate.waiting_count",
                        side_effect=fake_waiting, create=True), \
             mock.patch("services.optimizer.result_store.active_optim_count",
                        return_value=live, create=True):
            return par.split_width(ceiling, 1, "local")

    def test_queued_optim_is_not_double_counted(self):
        """An optimize-kind waiter is already inside active_optim_count().

        Counting it again made one queued sweep worth two claimants, so the
        running job narrowed twice as hard as the box required.
        """
        # live=2 already includes the queued optim; its waiter entry must add 0.
        self.assertEqual(self._width(waiting=1, live=2, kind="optimize"), 3)

    def test_no_claimants_keeps_full_width(self):
        self.assertEqual(self._width(waiting=0), 6)

    def test_blocked_backtest_halves_the_split(self):
        # Without counting the waiter this stayed 6, and the backtest sat behind
        # the sweep's reservation for the whole run.
        self.assertEqual(self._width(waiting=1), 3)

    def test_second_optim_and_a_waiter_narrow_further(self):
        self.assertEqual(self._width(waiting=1, live=2), 2)

    def test_width_never_drops_below_one(self):
        self.assertEqual(self._width(waiting=99), 1)

    def test_climbs_back_when_waiter_clears(self):
        self.assertEqual(self._width(waiting=2), 2)
        self.assertEqual(self._width(waiting=0), 6)

    def test_ram_cap_still_wins(self):
        with mock.patch.object(par, "cap_parallelism_for_live_ram", return_value=1), \
             mock.patch("services.memory_gate.waiting_count", return_value=0, create=True), \
             mock.patch("services.optimizer.result_store.active_optim_count",
                        return_value=1, create=True):
            self.assertEqual(par.split_width(6, 1, "local"), 1)

    def test_opted_out_ceiling_returns_fixed_parallelism(self):
        self.assertEqual(par.split_width(0, 4, "local"), 4)


class TestWaitingRegistry(unittest.TestCase):
    """waiting_count must ignore expired entries and never raise."""

    def test_counts_live_entries_by_kind_and_drops_expired(self):
        from services import memory_gate as mg
        import time as _t
        now = _t.time()
        fake = {
            b"a": ("backtest:%f" % (now + 30)).encode(),
            b"b": ("backtest:%f" % (now - 5)).encode(),    # expired
            b"c": ("optimize:%f" % (now + 30)).encode(),
        }
        with mock.patch.object(mg, "_redis") as r:
            r.return_value.hgetall.return_value = fake
            self.assertEqual(mg.waiting_count(), 2)
            self.assertEqual(mg.waiting_count("backtest"), 1)
            self.assertEqual(mg.waiting_count("optimize"), 1)

    def test_redis_error_returns_zero(self):
        from services import memory_gate as mg
        with mock.patch.object(mg, "_redis", side_effect=RuntimeError("down")):
            self.assertEqual(mg.waiting_count(), 0)


if __name__ == "__main__":
    unittest.main()


class TestMemoryPressureBrake(unittest.TestCase):
    """A thrashing box must narrow the fork width, however much MemAvailable claims.

    MemAvailable counts reclaimable page cache as free, so it reads healthy while
    the machine swaps itself to a standstill — which is how a 24 GB-swap episode
    ended in a cgroup OOM kill that wedged a sweep. PSI `full avg10` measures the
    stall directly.
    """

    def _cap(self, psi_line, avail_mb=12000, requested=12):
        import builtins
        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if str(path) == "/proc/pressure/memory":
                import io
                return io.StringIO(psi_line)
            return real_open(path, *a, **kw)

        with mock.patch.object(par, "_live_available_mb", return_value=avail_mb), \
             mock.patch.dict(os.environ, {"OPTIMIZE_MEM_PRESSURE_MAX_PCT": "8",
                                          "HEAVY_GATE_LIVE_RAM_FLOOR_MB": "2500",
                                          "OPTIMIZE_WORKER_PRIVATE_MB": "300"}), \
             mock.patch.object(builtins, "open", fake_open):
            return par.cap_parallelism_for_live_ram(requested, 1)

    def test_calm_box_keeps_full_width(self):
        self.assertEqual(self._cap("some avg10=0.01\nfull avg10=0.01 avg60=0.02\n"), 12)

    def test_thrashing_box_halves_width(self):
        # 10.34% is the value observed during the real swap-full incident.
        self.assertEqual(self._cap("some avg10=20\nfull avg10=10.34 avg60=9.0\n"), 6)

    def test_pressure_brake_never_returns_zero(self):
        self.assertGreaterEqual(self._cap("full avg10=99.0\n", requested=1), 1)

    def test_missing_psi_is_not_fatal(self):
        with mock.patch.object(par, "_live_available_mb", return_value=12000):
            self.assertGreaterEqual(par.cap_parallelism_for_live_ram(4, 1), 1)
