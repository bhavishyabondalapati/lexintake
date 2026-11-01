"""Intake: the shared path for every new file, whether it was uploaded or dropped in the inbox.

check it is a PDF -> hash it -> new? store blob + row + audit, then queue parsing.
                              -> seen before? audit the duplicate and return the existing row.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db import session_scope
from app.models import Document, Status
from app.status import audit
from app.storage.blobs import BlobStore, sha256_file

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
# The PDF spec lets a file have a little junk before the header; readers look in the first 1 KB.
MAGIC_WINDOW = 1024


class IntakeRejected(Exception):
    def __init__(self, reason: str, status_code: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass
class IntakeResult:
    document: Document
    duplicate: bool


def check_pdf(path: Path, max_bytes: int) -> int:
    """Return the file size, or raise IntakeRejected if it is too big or not a PDF."""
    size = path.stat().st_size
    if size > max_bytes:
        raise IntakeRejected(f"file is {size} bytes, the limit is {max_bytes}", 413)
    with open(path, "rb") as f:
        head = f.read(MAGIC_WINDOW)
    if PDF_MAGIC not in head:
        raise IntakeRejected("not a PDF (no %PDF- header)", 415)
    return size


def ingest_file(
    path: Path,
    filename: str,
    *,
    source: str,
    actor: str,
    blobs: BlobStore,
    enqueue: Callable[[uuid.UUID], None],
    max_bytes: int,
) -> IntakeResult:
    size = check_pdf(path, max_bytes)
    sha256 = sha256_file(path)

    with session_scope() as session:
        # ON CONFLICT DO NOTHING makes two simultaneous uploads of the same file safe: exactly one
        # INSERT wins, the other gets no row back and takes the duplicate path below.
        new_id = session.execute(
            insert(Document)
            .values(
                id=uuid.uuid4(),
                sha256=sha256,
                filename=filename,
                size_bytes=size,
                mime="application/pdf",
                source=source,
                status=Status.RECEIVED,
                retry_count=0,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
            .returning(Document.id)
        ).scalar_one_or_none()

        if new_id is None:
            existing = session.scalars(select(Document).where(Document.sha256 == sha256)).one()
            audit(
                session,
                existing,
                actor,
                {"event": "duplicate_received", "filename": filename, "source": source},
                from_status=existing.status,
                to_status=existing.status,
            )
            return IntakeResult(existing, duplicate=True)

        # Stored before the commit: if storing fails, the row is rolled back too. (A commit failing
        # after this leaves an orphan blob, which is harmless: the next upload reuses it.)
        blobs.put(path, sha256)
        doc = session.get_one(Document, new_id)
        audit(
            session,
            doc,
            actor,
            {"event": "received", "filename": filename, "source": source},
            from_status=None,
            to_status=Status.RECEIVED,
        )

    # Only queue once the row is committed, or the worker could look for a row that isn't there.
    try:
        enqueue(doc.id)
    except Exception:
        # The row stays `received`; `python -m app.cli requeue` picks it up later.
        log.exception("could not queue parsing for %s", doc.id)
    return IntakeResult(doc, duplicate=False)
