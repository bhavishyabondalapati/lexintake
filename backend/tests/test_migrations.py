import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from tests.conftest import BACKEND

pytestmark = pytest.mark.db


def test_migrations_create_the_ingestion_tables(db):
    assert {"documents", "text_blocks", "audit_log"} <= set(inspect(db).get_table_names())


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
