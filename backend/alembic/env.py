"""Alembic entry point: runs migrations against DATABASE_URL (or an engine passed in by tests)."""

from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context
from app.config import get_settings
from app.db import sqlalchemy_url
from app.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def run_migrations() -> None:
    # Tests pass their own connection; everything else uses DATABASE_URL.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(sqlalchemy_url(get_settings().database_url))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
