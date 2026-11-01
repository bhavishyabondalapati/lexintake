"""Database tables. Change these only together with a new Alembic migration (see alembic/)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.extraction.schemas import DocType, Subtype


class Status(StrEnum):
    RECEIVED = "received"
    PARSING = "parsing"
    PARSED = "parsed"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"  # every check passed (Phase 4 adds the playbook check after this)
    NEEDS_REVIEW = "needs_review"  # a check failed; the reasons are in extractions.review_reasons
    FAILED = "failed"


class BlockSource(StrEnum):
    TEXT_LAYER = "text_layer"  # text the PDF already contained
    OCR = "ocr"  # text read from a page image


class LlmStep(StrEnum):
    CLASSIFY = "classify"
    EXTRACT = "extract"


class LlmOutcome(StrEnum):
    OK = "ok"
    INVALID_OUTPUT = "invalid_output"  # not valid JSON for the schema, or cut off at max_tokens
    REFUSED = "refused"  # stop_reason "refusal"
    API_ERROR = "api_error"


class IntakeSource(StrEnum):
    UPLOAD = "upload"
    WATCHER = "watcher"


def _one_of(column: str, enum: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(m.value) for m in enum)})"


class Base(DeclarativeBase):
    # Predictable constraint names, so later migrations can refer to them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        # TEXT + CHECK instead of a Postgres ENUM: adding a status later is a one-line migration.
        CheckConstraint(_one_of("status", Status), name="status_valid"),
        CheckConstraint(_one_of("source", IntakeSource), name="source_valid"),
        CheckConstraint(
            f"doc_type IS NULL OR {_one_of('doc_type', DocType)}", name="doc_type_valid"
        ),
        CheckConstraint(
            f"doc_subtype IS NULL OR {_one_of('doc_subtype', Subtype)}", name="doc_subtype_valid"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default=Status.RECEIVED)
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer)
    # Copied from the latest extraction, so the review queue can filter without a join.
    doc_type: Mapped[str | None] = mapped_column(String(30))
    doc_subtype: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    blocks: Mapped[list["TextBlock"]] = relationship(
        back_populates="document", order_by="(TextBlock.page, TextBlock.seq)"
    )


class TextBlock(Base):
    __tablename__ = "text_blocks"
    __table_args__ = (
        CheckConstraint(_one_of("source", BlockSource), name="source_valid"),
        Index(None, "document_id", "page", "seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page: Mapped[int] = mapped_column(Integer)  # 1-based
    seq: Mapped[int] = mapped_column(Integer)  # reading order within the document
    # Bounding box in PDF points with a TOP-LEFT origin (y grows downwards), like a screen.
    x0: Mapped[float] = mapped_column(Float)
    y0: Mapped[float] = mapped_column(Float)
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(40))  # Docling's label: text, section_header, ...
    source: Mapped[str] = mapped_column(String(20))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)  # 0..1, only for OCR blocks

    document: Mapped[Document] = relationship(back_populates="blocks")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(30))  # None when the doc is created
    to_status: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(60))  # e.g. api:upload, watcher, worker:parse
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Extraction(Base):
    """The latest classification + extraction of a document. Replaced (never appended) when the
    document is extracted again; the audit log keeps the history."""

    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True
    )
    doc_type: Mapped[str] = mapped_column(String(30))
    doc_subtype: Mapped[str | None] = mapped_column(String(30))
    # {confidence, page, quote, verification...}
    classification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # {field: {value, page, quote, confidence, status, match_score, block_ids, flags}}
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # [{code, field, detail}]: why the document went to needs_review (empty when extracted)
    review_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    prompt_version: Mapped[str] = mapped_column(String(20))
    schema_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmCall(Base):
    """One API call, including failed attempts (they cost money too). Cost per document and per
    model is a GROUP BY on this table."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(_one_of("step", LlmStep), name="step_valid"),
        CheckConstraint(_one_of("outcome", LlmOutcome), name="outcome_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # one `app.cli extract` run
    step: Mapped[str] = mapped_column(String(20))
    attempt: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(100))  # as reported by the API
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)  # uncached input
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)  # includes thinking
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    stop_reason: Mapped[str | None] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
