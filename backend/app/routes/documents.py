"""Document endpoints: upload a PDF, look one up."""

import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.intake import IntakeRejected, ingest_file
from app.models import Document, IntakeSource, TextBlock
from app.queue import enqueue_parse
from app.storage.blobs import CHUNK, BlobStore, LocalBlobStore

router = APIRouter(prefix="/documents", tags=["documents"])

# Room for the multipart boundaries and headers around the file itself.
MULTIPART_OVERHEAD = 64 * 1024


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sha256: str
    filename: str
    size_bytes: int
    source: str
    status: str
    error: str | None
    retry_count: int
    page_count: int | None
    created_at: datetime
    updated_at: datetime


class UploadOut(DocumentOut):
    duplicate: bool


class DocumentDetail(DocumentOut):
    block_count: int


def get_blob_store(settings: Annotated[Settings, Depends(get_settings)]) -> BlobStore:
    return LocalBlobStore(settings.blob_dir)


def get_enqueue() -> Callable[[uuid.UUID], None]:
    return enqueue_parse


@router.post("", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    blobs: Annotated[BlobStore, Depends(get_blob_store)],
    enqueue: Annotated[Callable[[uuid.UUID], None], Depends(get_enqueue)],
) -> UploadOut:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    # Cheap early check from the header; the real check counts bytes below.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + MULTIPART_OVERHEAD:
        raise HTTPException(413, f"upload is larger than {settings.max_upload_mb} MB")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload.pdf"
        written = 0
        with open(path, "wb") as out:
            while chunk := file.file.read(CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(413, f"upload is larger than {settings.max_upload_mb} MB")
                out.write(chunk)
        try:
            result = ingest_file(
                path,
                Path(file.filename or "upload.pdf").name,
                source=IntakeSource.UPLOAD,
                actor="api:upload",
                blobs=blobs,
                enqueue=enqueue,
                max_bytes=max_bytes,
            )
        except IntakeRejected as e:
            raise HTTPException(e.status_code, e.reason) from e

    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    base = DocumentOut.model_validate(result.document).model_dump()
    return UploadOut(**base, duplicate=result.duplicate)


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]
) -> DocumentDetail:
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    count = session.scalar(
        select(func.count()).select_from(TextBlock).where(TextBlock.document_id == doc.id)
    )
    base = DocumentOut.model_validate(doc).model_dump()
    return DocumentDetail(**base, block_count=count or 0)
