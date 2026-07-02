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
    # Raised 2026-06-05: large optimize sweeps (1000s of combos at ~3s each
    # across 6 parallel workers) were hitting the soft cap and dying mid-run
    # with SoftTimeLimitExceeded. Sized for 6000+ combo exhaustive sweeps:
    # ~6000 combos / (6 workers / ~3s) ≈ 50-60 min compute + warmup + xlsx
    # writes, so 3h soft / 3.5h hard gives comfortable headroom for even
    # bigger runs. Time-only change — no memory/OOM impact (per-child RSS is
    # still bounded by worker_max_memory_per_child below).
    task_time_limit=16200,  # 4.5 hours hard kill
    task_soft_time_limit=14400,  # 4 hours soft limit
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
