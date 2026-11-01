"""Shared test setup.

Tests that touch the database use the `db` fixture. It creates a fresh `lexintake_test`
database (never the dev one), runs the Alembic migrations on it, and empties every table after
each test. Postgres must be running: `make test` starts it (`docker compose up -d db`); CI uses
a service container. TEST_DATABASE_URL overrides the default address.
"""

import os
from pathlib import Path

import pytest
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from app import db as app_db
from app.config import get_settings
from app.models import Base

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _test_database_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    # Same credentials as the dev database (from the repo's .env), separate database name.
    env = {**dotenv_values(BACKEND.parent / ".env"), **os.environ}
    user = env.get("POSTGRES_USER", "lexintake")
    password = env.get("POSTGRES_PASSWORD", "change-me")
    port = env.get("DB_PORT", "5432")
    return f"postgresql://{user}:{password}@localhost:{port}/lexintake_test"


def _reset_caches() -> None:
    get_settings.cache_clear()
    app_db.get_engine.cache_clear()
    app_db.get_sessionmaker.cache_clear()


@pytest.fixture(scope="session")
def database_url() -> str:
    url = make_url(app_db.sqlalchemy_url(_test_database_url()))
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as e:
        pytest.exit(
            f"Postgres is not reachable at {url.render_as_string(hide_password=True)} ({e}).\n"
            "Start it with: docker compose up -d --wait db",
            returncode=1,
        )
    finally:
        admin.dispose()
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def migrated(database_url: str) -> str:
    os.environ["DATABASE_URL"] = database_url
    _reset_caches()
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    return database_url


@pytest.fixture
def db(migrated: str, monkeypatch):
    """A migrated, empty test database that the app code (session_scope etc.) talks to."""
    monkeypatch.setenv("DATABASE_URL", migrated)
    yield app_db.get_engine()
    with app_db.get_engine().begin() as conn:
        tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def make_pdf(tmp_path):
    """Write a small file with a PDF header (enough for intake, which only checks the header)."""

    def _make(content: bytes = b"hello", name: str = "doc.pdf") -> Path:
        path = tmp_path / name
        path.write_bytes(b"%PDF-1.7\n" + content + b"\n%%EOF\n")
        return path

    return _make
