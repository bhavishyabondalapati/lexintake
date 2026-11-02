"""Celery app. Pipeline tasks register here; their code lives in app/tasks/.

Run with: celery -A app.worker worker -Q parse,celery --loglevel=info   (parse worker image)
          celery -A app.worker worker -Q extract --loglevel=info        (extractor, api image)
"""

from celery import Celery

from app.config import get_settings

PARSE_TASK = "lexintake.parse_document"
EXTRACT_TASK = "lexintake.extract_document"

settings = get_settings()

celery_app = Celery("lexintake", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,  # a task is only removed from the queue once it finishes
    task_reject_on_worker_lost=True,  # so a crashed worker's task gets retried, not lost
    worker_prefetch_multiplier=1,  # long tasks: don't let one worker hoard the queue
    # Parsing gets its own queue, consumed only by the worker image that has Docling. Extraction
    # mostly waits on the API, so it has its own queue and a small worker of its own.
    task_routes={PARSE_TASK: {"queue": "parse"}, EXTRACT_TASK: {"queue": "extract"}},
    task_ignore_result=True,  # results live in Postgres, not in Redis
    # Importing the task module doesn't import Docling (the parser loads lazily), so the api
    # image can import this file too.
    include=["app.tasks.parse", "app.tasks.extract"],
)


@celery_app.task(name="lexintake.ping", ignore_result=False)
def ping() -> str:
    return "pong"
