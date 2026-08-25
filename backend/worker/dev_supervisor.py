#!/usr/bin/env python3
"""
Idle-aware hot-reload supervisor for a Celery worker.

Runs the Celery worker as a child process and watches the bind-mounted source
(`/app/**/*.py`). When a file changes it restarts the worker so the NEW code is
picked up — but ONLY while the worker is idle (no active/reserved/scheduled
tasks). A job that is actively running is never disturbed; the restart is
deferred until it finishes. Because the code is bind-mounted, this needs NO
Docker image rebuild — you just save your edit and the next run uses new code.

Wiring: set as the container command. Configure via env:
  RELOAD_CHILD_CMD    full Celery command to run (required), e.g.
                      "celery -A worker.celery worker --queues=optimize -c 3 ..."
  RELOAD_WATCH_DIR    directory to watch (default /app)
  RELOAD_POLL_SECONDS mtime poll interval (default 3)
  RELOAD_ENABLE       "1" to enable reload; "0" runs the child with no watching
                      (default 1)

Signals: SIGTERM/SIGINT are forwarded to the child's process group so
`docker stop` shuts the worker down cleanly, then the supervisor exits.
"""
import os
import shlex
import signal
import socket
import subprocess
import sys
import time

WATCH_DIR = os.environ.get("RELOAD_WATCH_DIR", "/app")
POLL = float(os.environ.get("RELOAD_POLL_SECONDS", "3"))
ENABLE = os.environ.get("RELOAD_ENABLE", "1").strip() in ("1", "true", "True")
CHILD_CMD = os.environ.get("RELOAD_CHILD_CMD", "").strip()

_proc = None            # current child (subprocess.Popen)
_shutting_down = False


def _log(msg: str) -> None:
    print(f"[RELOAD] {msg}", flush=True)


def _launch():
    # start_new_session=True => child is a process-group leader, so we can signal
    # the whole Celery process tree (pool children included) as a group.
    return subprocess.Popen(shlex.split(CHILD_CMD), start_new_session=True)


def _signal_child(sig: int) -> None:
    if _proc and _proc.poll() is None:
        try:
            os.killpg(os.getpgid(_proc.pid), sig)
        except ProcessLookupError:
            pass


def _stop_child(timeout: float = 40.0) -> None:
    """Warm-shutdown the child (SIGTERM). Idle => returns almost immediately."""
    if not _proc or _proc.poll() is not None:
        return
    _signal_child(signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _proc.poll() is not None:
            return
        time.sleep(0.3)
    _log("child did not stop in time — SIGKILL")
    _signal_child(signal.SIGKILL)
    try:
        _proc.wait(timeout=10)
    except Exception:
        pass


def _handle_term(signum, _frame):
    global _shutting_down
    _shutting_down = True
    _log(f"received signal {signum} — shutting down child")
    _stop_child()
    sys.exit(0)


def _max_mtime() -> float:
    latest = 0.0
    for root, _dirs, files in os.walk(WATCH_DIR):
        # skip caches/venvs to keep the poll cheap
        if "__pycache__" in root or "/.git" in root or "/node_modules" in root:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                m = os.stat(os.path.join(root, f)).st_mtime
                if m > latest:
                    latest = m
            except OSError:
                pass
    return latest


import re as _re


def _watched_queues() -> list:
    """Queue name(s) this worker consumes, parsed from the Celery command
    (--queues=X / --queues X / -Q X). Used to see undelivered work still
    sitting in the broker that the worker hasn't reserved yet."""
    m = _re.search(r"(?:--queues[=\s]+|-Q[=\s]+)([\w,]+)", CHILD_CMD)
    return [q for q in m.group(1).split(",") if q] if m else []


def _queue_pending() -> bool:
    """True if any queue this worker consumes has messages WAITING in the broker
    but not yet reserved by the worker. Celery-on-Redis stores each queue as a
    Redis list keyed by the queue name, so LLEN is the undelivered depth. This
    is the gap that dropped a job: a submitted-but-not-yet-started optim is
    invisible to inspect.active/reserved, so the reload grabbed it mid-restart
    and SIGTERM'd it. On any error we can't prove the queue is empty → treat as
    pending so we never restart into a queued job."""
    qs = _watched_queues()
    if not qs:
        return False
    try:
        import redis
        c = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        return any(int(c.llen(q) or 0) > 0 for q in qs)
    except Exception as exc:
        _log(f"queue-depth check failed ({exc}) — treating as PENDING (won't restart)")
        return True


def _nested_pool_busy() -> bool:
    """True while a task-owned subprocess still belongs to the worker group.

    Celery's normal prefork children are direct children of the Celery main
    process.  Optimizer batch workers are one level deeper; if their Celery
    parent is OOM-killed they become orphans but retain the same process group.
    Celery inspect can then report "idle" while those children are still
    computing, and restarting closes their result pipes (BrokenPipeError).
    """
    if not _proc or _proc.poll() is not None:
        return False
    try:
        main_pid = int(_proc.pid)
        worker_pgid = os.getpgid(main_pid)
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            if pid == main_pid:
                continue
            try:
                # /proc/PID/stat: pid (comm) state ppid pgrp ...; split after
                # the final ')' because comm itself may contain spaces.
                raw = open(f"/proc/{pid}/stat", "r", encoding="utf-8").read()
                fields = raw[raw.rfind(")") + 2:].split()
                state, ppid, pgrp = fields[0], int(fields[1]), int(fields[2])
            except (OSError, ValueError, IndexError):
                continue
            if state != "Z" and pgrp == worker_pgid and ppid != main_pid:
                return True
    except (OSError, ValueError):
        # Uncertainty must defer reload, matching the broker/inspect checks.
        return True
    return False


def _worker_idle() -> bool:
    """True only when THIS container's worker has no active/reserved/scheduled
    tasks AND nothing waiting in its queue(s). On persistent uncertainty returns
    False (never restart while unsure). Retries briefly so a transient
    broker/DNS blip doesn't wrongly block reload."""
    # A nested optimizer child may outlive an OOM-killed Celery task parent.
    # Never restart until it exits and closes its own side of the batch pipe.
    if _nested_pool_busy():
        return False
    # Broker-side pending work (submitted, not yet reserved) — checked first so a
    # queued optim is never caught in a restart.
    if _queue_pending():
        return False
    from worker.celery import celery_app
    local = f"celery@{socket.gethostname()}"
    last_exc = None
    for attempt in range(3):
        try:
            insp = celery_app.control.inspect(timeout=6, destination=[local])
            active = insp.active()
            # active() returns None when NO worker replied (broker down or worker
            # not ready) — that is NOT "idle"; treat as busy and retry.
            if active is None:
                raise RuntimeError("no reply from worker (broker down or booting)")
            for probe in (lambda: active, insp.reserved, insp.scheduled):
                data = probe() or {}
                for _w, tasks in data.items():
                    if tasks:
                        return False
            return True
        except Exception as exc:
            last_exc = exc
            time.sleep(1.0)
    _log(f"idle-check failed after retries ({last_exc}) — treating as BUSY (won't restart)")
    return False


def main() -> int:
    global _proc
    if not CHILD_CMD:
        _log("RELOAD_CHILD_CMD is empty — nothing to run")
        return 2

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    _log(f"launching: {CHILD_CMD}")
    _proc = _launch()
    _child_started = time.time()

    if not ENABLE:
        _log("reload disabled (RELOAD_ENABLE=0) — running child without watching")
        return _proc.wait()

    _log(f"watching {WATCH_DIR}/**/*.py every {POLL}s; restart only when idle")
    last_seen = _max_mtime()
    dirty = False
    crash_streak = 0

    while not _shutting_down:
        # Relaunch if the child died on its own (crash, or max-memory recycle).
        # Back off on a CRASH LOOP (e.g. broker unreachable, or a bad edit that
        # fails to import) so we don't hot-spin relaunching a dying process.
        if _proc.poll() is not None:
            if _shutting_down:
                break
            ran_for = time.time() - _child_started
            if ran_for < 15:
                crash_streak += 1
                backoff = min(60.0, 2.0 ** crash_streak)
                _log(f"child exited fast (rc={_proc.returncode}, ran {ran_for:.1f}s) "
                     f"— crash streak {crash_streak}, backing off {backoff:.0f}s")
                # Sleep in small slices so SIGTERM stays responsive.
                _slept = 0.0
                while _slept < backoff and not _shutting_down:
                    time.sleep(0.5)
                    _slept += 0.5
                if _shutting_down:
                    break
            else:
                crash_streak = 0  # it ran a healthy while; a clean recycle
                _log(f"child exited (rc={_proc.returncode}) — relaunching")
            _proc = _launch()
            _child_started = time.time()
            last_seen = _max_mtime()
            dirty = False

        time.sleep(POLL)

        cur = _max_mtime()
        if cur > last_seen:
            last_seen = cur
            if not dirty:
                _log("code change detected — will restart worker when idle")
            dirty = True

        if dirty and _worker_idle():
            _log("worker idle — restarting to load new code")
            _stop_child()
            _proc = _launch()
            _child_started = time.time()
            crash_streak = 0
            last_seen = _max_mtime()
            dirty = False
            _log("worker restarted with new code")

    return 0


if __name__ == "__main__":
    sys.exit(main())
