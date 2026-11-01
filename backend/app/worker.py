"""Celery app. Pipeline tasks (parse, extract, ...) get added here in later phases.

Run with: celery -A app.worker worker --loglevel=info
"""

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("lexintake", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,  # a task is only removed from the queue once it finishes
    task_reject_on_worker_lost=True,  # so a crashed worker's task gets retried, not lost
    worker_prefetch_multiplier=1,  # long tasks: don't let one worker hoard the queue
)


@celery_app.task(name="lexintake.ping")
def ping() -> str:
    return "pong"
