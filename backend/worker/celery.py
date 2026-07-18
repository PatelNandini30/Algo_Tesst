"""
Celery worker configuration for background tasks.
"""
import os
from celery import Celery

# Get Redis URL from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Create Celery app
celery_app = Celery(
    'algotest',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['worker.tasks']
)

# Configure Celery
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    result_extended=True,
    result_expires=86400,
    timezone='Asia/Kolkata',
    enable_utc=True,
    task_track_started=True,
    # Time limits are a LAST-RESORT backstop only. The primary "not stuck"
    # mechanism is the heartbeat watchdog (services/optimizer/watchdog.py), which
    # cancels a job whose progress heartbeat freezes for >OPTIMIZE_STUCK_SECONDS
    # (~2.5h) WITHOUT killing a job that is still actively progressing. These
    # flat limits deliberately sit ABOVE any legitimate long run (a genuinely
    # active exhaustive sweep may take hours) so they never cut off real work —
    # they exist only to guarantee nothing can run truly forever if the watchdog
    # itself is somehow unavailable. Note the 4.5h flat limit did NOT catch the
    # 6h finalization hang on 2026-07-04 (the hung native step escaped billiard's
    # timer), which is exactly why the watchdog was added.
    task_time_limit=28800,  # 8 hours hard kill (backstop)
    task_soft_time_limit=27000,  # 7.5 hours soft limit (backstop)
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,   # recycle workers less frequently now that memory is bounded
    # Raised from 2800000 → 5000000 on 2026-05-25: the feather grew to 706 MB
    # (FUTURES port), the Rust cache expanded to ~3.5 GB, and the prior 2.8 GB
    # cap was killing optimize tasks mid-flight at ~3 GB RSS during cache load.
    # CLI --max-memory-per-child must match (currently 5000000 on all workers).
    worker_max_memory_per_child=5000000,
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=3,
    task_routes={
        'worker.tasks.run_backtest_task': {'queue': 'backtests'},
        'worker.tasks.run_algotest_job': {'queue': 'backtests'},
        'worker.tasks.warm_backtest_cache_task': {'queue': 'backtests'},
        # Fast queue is selected explicitly by routers/backtest.py for short ranges.
        'worker.tasks.run_optimize_job': {'queue': 'optimize'},
        'worker.tasks.load_data_task': {'queue': 'uploads'},
        'worker.tasks.migrate_csv_task': {'queue': 'uploads'},
    },
)


@celery_app.task(bind=True)
def test_task(self, x, y):
    """Test task to verify Celery is working."""
    return x + y


from celery.signals import worker_ready as _worker_ready, worker_process_init as _worker_process_init


@_worker_process_init.connect
def _reset_db_pool_after_fork(**_kwargs):
    """Dispose the shared SQLAlchemy engine's pooled connections right after
    THIS worker process forks from Celery's prefork MainProcess.

    database.py's engine is a module-level singleton created once at import
    time — if the MainProcess ever opened a connection before forking (or a
    sibling fork did), every forked child inherits the same underlying DB
    socket. Two processes reading/writing that one socket concurrently
    desyncs the Postgres wire protocol: a query in one process can receive
    bytes meant for a different process's query. Seen in practice as a
    corrupted _db_option_min_date() result (a literal "1" instead of a date,
    crashing an optimize job) and as get_expiry_dates() silently returning
    zero rows (every combo in that job priced trades=0 with no error at all —
    the more dangerous case since it looks like success).

    Deliberately calls .dispose() on the SAME engine object every module
    holds (database.engine, base.py's db_engine, etc. are all the same
    instance — `from X import engine` binds a reference, not a copy) rather
    than database.reset_engine(), which only replaces its own private
    module-level variable and would leave every already-imported reference
    (base.py's db_engine, etc.) pointing at the stale, still-shared pool.
    dispose() mutates the existing Engine's pool in place, so every one of
    those references transparently gets fresh, non-shared connections."""
    try:
        from database import engine as _shared_engine
        _shared_engine.dispose()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[DB POOL] post-fork dispose failed: %s", exc)


@_worker_ready.connect
def _start_optimize_watchdog(**_kwargs):
    """Start the stuck-job watchdog when THIS worker boots — but only on the
    optimize worker (OPTIMIZE_WATCHDOG=1 in its compose env). Backtest/upload
    workers and the backend (which merely imports this app to enqueue) never
    start it. Fires once per worker process, in the MainProcess only."""
    if os.environ.get("OPTIMIZE_WATCHDOG", "").strip() not in ("1", "true", "True"):
        return
    try:
        from services.optimizer import watchdog
        watchdog.start()
    except Exception as exc:  # never block worker startup on the watchdog
        import logging
        logging.getLogger(__name__).warning("[WATCHDOG] failed to start: %s", exc)


def _start_node_heartbeat():
    """LAN remote-worker self-registration (see services/node_registry.py,
    remote-worker/). Only runs when NODE_IP is set — local/default worker
    containers are unaffected and are never registered as a 'node'."""
    node_ip = os.environ.get("NODE_IP", "").strip()
    if not node_ip:
        return
    import socket
    import threading
    import time as _time

    cpu_count = os.cpu_count() or 1
    try:
        ram_mb = int(os.environ.get("NODE_RAM_MB", "0"))
        if not ram_mb:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        ram_mb = int(line.split()[1]) // 1024
                        break
    except Exception:
        ram_mb = 0
    hostname = socket.gethostname()
    interval = int(os.environ.get("NODE_HEARTBEAT_SECONDS", "15"))
    try:
        from services.code_version import compute_code_version
        version = compute_code_version()
    except Exception:
        version = ""

    def _loop():
        from services import node_registry
        while True:
            try:
                node_registry.heartbeat(node_ip, cpu_count, ram_mb, hostname, version=version)
            except Exception:
                pass
            _time.sleep(interval)

    threading.Thread(target=_loop, name="node-heartbeat", daemon=True).start()


_start_node_heartbeat()
