"""The parse step: blob -> text blocks in Postgres.

Safe to run twice, or at the same time, on the same document:
- a document that is already `parsed` is left alone;
- a Postgres advisory lock lets only one worker parse a given document at once;
- the blocks are replaced (delete + insert) in the same transaction that marks it `parsed`.
"""

import logging
import time
import uuid

from celery.signals import worker_process_init
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import get_engine, session_scope
from app.models import Document, Status, TextBlock
from app.parsing import get_parser
from app.parsing.base import Parser
from app.status import transition
from app.storage.blobs import BlobStore, LocalBlobStore
from app.worker import PARSE_TASK, celery_app

log = logging.getLogger(__name__)

ACTOR = "worker:parse"
MAX_ERROR_CHARS = 2000


def _lock_key(document_id: uuid.UUID) -> int:
    """Advisory locks take a 64-bit integer; use the first 8 bytes of the uuid."""
    return int.from_bytes(document_id.bytes[:8], "big", signed=True)


def _fail(document_id: uuid.UUID, error: str, detail: dict) -> None:
    with session_scope() as session:
        doc = session.get_one(Document, document_id, with_for_update=True)
        doc.error = error[:MAX_ERROR_CHARS]
        transition(session, doc, Status.FAILED, ACTOR, {**detail, "error": doc.error})


def run_parse(document_id: uuid.UUID, parser: Parser, blobs: BlobStore, max_attempts: int) -> str:
    """Parse one document. Returns what happened (for logs and tests)."""
    with get_engine().connect() as lock_conn:
        got_lock = lock_conn.scalar(select(func.pg_try_advisory_lock(_lock_key(document_id))))
        lock_conn.commit()  # a session-level lock outlives the transaction; don't sit in one
        if not got_lock:
            return "locked"  # another worker is parsing this document right now
        try:
            return _parse_locked(document_id, parser, blobs, max_attempts)
        finally:
            lock_conn.scalar(select(func.pg_advisory_unlock(_lock_key(document_id))))
            lock_conn.commit()


def _parse_locked(
    document_id: uuid.UUID, parser: Parser, blobs: BlobStore, max_attempts: int
) -> str:
    # Step 1: claim the document.
    with session_scope() as session:
        doc = session.get(Document, document_id, with_for_update=True)
        if doc is None:
            log.warning("parse: document %s does not exist", document_id)
            return "missing"
        if doc.status == Status.PARSED:
            return "already_parsed"
        if doc.retry_count >= max_attempts:
            # Each attempt is counted before parsing starts, so a PDF that kills the worker
            # (out of memory, segfault) still uses up its attempts instead of looping forever.
            if doc.status != Status.FAILED:
                doc.error = f"gave up after {doc.retry_count} attempts"
                transition(session, doc, Status.FAILED, ACTOR, {"event": "gave_up"})
            return "gave_up"
        doc.retry_count += 1
        transition(session, doc, Status.PARSING, ACTOR, {"attempt": doc.retry_count})
        sha256 = doc.sha256

    # Step 2: parse, outside any transaction (this can take a while).
    started = time.monotonic()
    try:
        result = parser.parse(blobs.path(sha256))
    except OperationalError:
        raise  # database trouble: let Celery retry
    except Exception as e:
        log.exception("parse failed for %s", document_id)
        _fail(document_id, f"{type(e).__name__}: {e}", {"error_type": type(e).__name__})
        return "failed"
    seconds = round(time.monotonic() - started, 2)

    # Step 3: save the blocks and mark parsed, all or nothing.
    with session_scope() as session:
        doc = session.get_one(Document, document_id, with_for_update=True)
        session.execute(delete(TextBlock).where(TextBlock.document_id == document_id))
        if result.blocks:
            session.execute(
                insert(TextBlock),
                [
                    {
                        "document_id": document_id,
                        "page": b.page,
                        "seq": b.seq,
                        "x0": b.bbox[0],
                        "y0": b.bbox[1],
                        "x1": b.bbox[2],
                        "y1": b.bbox[3],
                        "text": b.text,
                        "label": b.label,
                        "source": b.source,
                        "ocr_confidence": b.ocr_confidence,
                    }
                    for b in result.blocks
                ],
            )
        doc.page_count = result.page_count
        # A partial parse still counts as parsed; the error tells Phase 3 to send it to review.
        problems = []
        if result.failed_pages:
            problems.append(f"pages {result.failed_pages} could not be parsed")
        problems += result.errors
        doc.error = ("partial: " + "; ".join(problems))[:MAX_ERROR_CHARS] if problems else None
        transition(
            session,
            doc,
            Status.PARSED,
            ACTOR,
            {
                "blocks": len(result.blocks),
                "pages": result.page_count,
                "ocr_pages": result.ocr_pages,
                "failed_pages": result.failed_pages,
                "errors": result.errors,
                "seconds": seconds,
            },
        )
    return "parsed"


@celery_app.task(
    name=PARSE_TASK,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=5,
)
def parse_document(document_id: str) -> str:
    settings = get_settings()
    return run_parse(
        uuid.UUID(document_id),
        get_parser(),
        LocalBlobStore(settings.blob_dir),
        settings.parse_max_attempts,
    )


@worker_process_init.connect
def load_models(**_: object) -> None:
    """Load the Docling models when a worker process starts, not on its first document."""
    get_parser().warm_up()
