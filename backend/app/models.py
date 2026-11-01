"""Database tables. Change these only together with a new Alembic migration (see alembic/)."""

import uuid
from datetime import datetime
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
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Status(StrEnum):
    RECEIVED = "received"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


class BlockSource(StrEnum):
    TEXT_LAYER = "text_layer"  # text the PDF already contained
    OCR = "ocr"  # text read from a page image


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
