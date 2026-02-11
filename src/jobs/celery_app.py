"""
Celery application configuration for background job processing.
"""
from celery import Celery
import os

# Redis connection settings
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

# Celery broker and backend URLs
BROKER_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
BACKEND_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB + 1}"  # Use different DB for results

# Create Celery application
celery_app = Celery(
    "strategy_executor",
    broker=BROKER_URL,
    backend=BACKEND_URL,
    include=["src.jobs.tasks"]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max
    task_soft_time_limit=3300,  # 55 minutes soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    result_expires=86400,  # Results expire after 24 hours
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Task routes (optional - for future scaling)
celery_app.conf.task_routes = {
    "src.jobs.tasks.execute_strategy_async": {"queue": "strategy_execution"},
}
