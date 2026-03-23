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
    task_time_limit=3600,  # 1 hour max
    task_soft_time_limit=3000,  # 50 minutes soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,   # recycle workers to prevent Pandas/Polars memory fragmentation
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=3,
    task_routes={
        'worker.tasks.run_backtest_task': {'queue': 'backtests'},
        'worker.tasks.run_algotest_job': {'queue': 'backtests'},
        'worker.tasks.load_data_task': {'queue': 'uploads'},
        'worker.tasks.migrate_csv_task': {'queue': 'uploads'},
    },
)


@celery_app.task(bind=True)
def test_task(self, x, y):
    """Test task to verify Celery is working."""
    return x + y
