import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from tests.conftest import BACKEND

pytestmark = pytest.mark.db


def test_migrations_create_the_tables(db):
    tables = {"documents", "text_blocks", "audit_log", "extractions", "llm_calls"}
    assert tables <= set(inspect(db).get_table_names())


def test_downgrade_to_phase_2_keeps_documents_as_parsed(db):
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["configure_logger"] = False
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents (id, sha256, filename, size_bytes, mime, source, status,"
                " retry_count) VALUES (gen_random_uuid(), repeat('a', 64), 'x.pdf', 1,"
                " 'application/pdf', 'upload', 'needs_review', 0)"
            )
        )

    command.downgrade(config, "0001")
    with db.connect() as conn:
        assert conn.scalar(text("SELECT status FROM documents")) == "parsed"
    command.upgrade(config, "head")


def test_migrations_downgrade_and_upgrade_cleanly(db):
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["configure_logger"] = False

    command.downgrade(config, "base")
    assert "documents" not in inspect(db).get_table_names()

    command.upgrade(config, "head")
    assert "documents" in inspect(db).get_table_names()


def test_models_match_the_migrations(db):
    # Fails if someone changes app/models.py without writing a migration.
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["configure_logger"] = False
    command.check(config)
