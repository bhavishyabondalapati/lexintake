"""Putting work on the Celery queue.

Tasks are sent BY NAME, so the api image never imports the parsing code (or Docling).
"""

import uuid

from app.worker import PARSE_TASK, celery_app


def enqueue_parse(document_id: uuid.UUID) -> None:
    celery_app.send_task(PARSE_TASK, args=[str(document_id)])
