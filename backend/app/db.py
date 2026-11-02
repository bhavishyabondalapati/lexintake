"""Database engine and sessions (SQLAlchemy 2, sync, psycopg 3 driver)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def sqlalchemy_url(url: str) -> str:
    """Use the psycopg 3 driver for plain postgresql:// URLs (SQLAlchemy defaults to psycopg2)."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


@lru_cache
def get_engine() -> Engine:
    return create_engine(sqlalchemy_url(get_settings().database_url), pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session that commits on success and rolls back on any exception."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with session_scope() as session:
        yield session


def _lock_key(document_id: uuid.UUID) -> int:
    """Advisory locks take a 64-bit integer; use the first 8 bytes of the uuid."""
    return int.from_bytes(document_id.bytes[:8], "big", signed=True)


@contextmanager
def document_lock(document_id: uuid.UUID) -> Iterator[bool]:
    """A Postgres advisory lock on one document: yields False if another worker holds it.

    Parsing and extraction share it, so one document is never worked on twice at once.
    """
    with get_engine().connect() as conn:
        got_lock = conn.scalar(select(func.pg_try_advisory_lock(_lock_key(document_id))))
        conn.commit()  # a session-level lock outlives the transaction; don't sit in one
        try:
            yield bool(got_lock)
        finally:
            if got_lock:
                conn.scalar(select(func.pg_advisory_unlock(_lock_key(document_id))))
                conn.commit()
