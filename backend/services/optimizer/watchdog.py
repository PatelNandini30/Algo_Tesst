"""
Optimize-job watchdog — auto-cancels genuinely STUCK optimize jobs.

Why this exists
---------------
An optimize task that hangs in a single long step (e.g. the per-combo patchwise
pre-build over 1000+ combos) does NOT get caught by Celery's built-in
`task_time_limit`: that step ran ~6 hours on 2026-07-04 and was never killed,
its Redis status stayed "running", and it blocked the whole optimize queue while
pinning the worker at ~400% CPU.

Design
------
- Every optimize job writes a progress heartbeat (`last_progress_at` in
  `optim:{id}:meta`) on EVERY combo completion and EVERY finalization step
  (see services/optimizer/result_store.py + runner.py). So the heartbeat is a
  faithful "is it actually making progress" signal — it freezes ONLY when a
  single step hangs, never while the job is still moving.
- This watchdog runs as a daemon thread inside the optimize worker (started from
  the `worker_ready` signal, guarded by OPTIMIZE_WATCHDOG=1 so it runs on the
  optimize worker only). Every OPTIMIZE_WATCHDOG_INTERVAL seconds it inspects the
  worker's own actively-running optimize tasks and, for any whose heartbeat has
  not advanced for more than OPTIMIZE_STUCK_SECONDS, it force-cancels it
  (revoke + SIGKILL), marks it failed, and releases its memory-gate slot.

This never kills a job that is still progressing — only a hang.
"""
import os
import socket
import threading
import time
import logging

logger = logging.getLogger(__name__)

# 2.5h default: comfortably longer than any legitimate finalization step (which
# is minutes), short enough that a real hang frees the queue the same afternoon.
STUCK_SECONDS = int(os.environ.get("OPTIMIZE_STUCK_SECONDS", "9000"))
INTERVAL = int(os.environ.get("OPTIMIZE_WATCHDOG_INTERVAL", "300"))
_TASK_NAME = "worker.tasks.run_optimize_job"
_started = False
_lock = threading.Lock()


def _local_worker_name() -> str:
    # Celery names a prefork worker "celery@<hostname>"; matching this scopes the
    # scan to THIS container's tasks (inspect().active() returns all workers).
    return f"celery@{socket.gethostname()}"


def _scan_once() -> None:
    from worker.celery import celery_app
    from services.optimizer import result_store
    from services import memory_gate

    try:
        insp = celery_app.control.inspect(timeout=8, destination=[_local_worker_name()])
        active_map = insp.active() or {}
    except Exception as exc:
        logger.debug("[WATCHDOG] inspect failed: %s", exc)
        return

    now = time.time()
    for _worker, tasks in (active_map or {}).items():
        for t in tasks or []:
            if t.get("name") != _TASK_NAME:
                continue
            tid = t.get("id")
            if not tid:
                continue
            meta = result_store.get_meta(tid) or {}
            # Only running jobs are candidates. A job blocked in the memory gate
            # has no meta yet (init_job runs after the gate) → skipped, correct.
            if meta.get("status") != "running":
                continue
            last = (
                meta.get("last_progress_at")
                or meta.get("started_at")
                or t.get("time_start")
                or now
            )
            try:
                stale = now - float(last)
            except (TypeError, ValueError):
                continue
            if stale <= STUCK_SECONDS:
                continue

            phase = meta.get("phase") or "?"
            logger.warning(
                "[WATCHDOG] optimize job %s STUCK — no progress for %ds (>%ds) in "
                "phase=%s; force-cancelling",
                tid[:8], int(stale), STUCK_SECONDS, phase,
            )
            # 1) Kill the worker process running it (SIGKILL — the hung native
            #    step ignores SIGTERM; billiard respawns a fresh child).
            try:
                celery_app.control.revoke(tid, terminate=True, signal="SIGKILL")
            except Exception as exc:
                logger.warning("[WATCHDOG] revoke failed for %s: %s", tid[:8], exc)
            # 2) Flip status so the UI/download stop showing it as running.
            try:
                result_store.mark_complete(
                    tid,
                    error=(
                        f"auto-cancelled by watchdog: no progress for {int(stale)}s "
                        f"(> {STUCK_SECONDS}s) in phase '{phase}'"
                    ),
                )
            except Exception as exc:
                logger.warning("[WATCHDOG] mark_complete failed for %s: %s", tid[:8], exc)
            # 3) Free its memory-gate slot so the next queued job can start.
            try:
                memory_gate.release(tid)
            except Exception as exc:
                logger.debug("[WATCHDOG] gate release failed for %s: %s", tid[:8], exc)


def _loop() -> None:
    logger.info(
        "[WATCHDOG] started — cancels optimize jobs with no progress for >%ds, "
        "checking every %ds", STUCK_SECONDS, INTERVAL,
    )
    while True:
        try:
            _scan_once()
        except Exception as exc:  # never let the watchdog thread die
            logger.warning("[WATCHDOG] scan error: %s", exc)
        time.sleep(INTERVAL)


def start() -> None:
    """Start the watchdog daemon thread once (idempotent)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="optimize-watchdog", daemon=True).start()
