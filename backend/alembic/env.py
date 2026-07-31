"""Alembic environment.

The database URL comes from the application settings rather than alembic.ini, so
migrations run against whatever DATABASE_URL points at -- the local SQLite file
in development, Postgres in deployment -- with no second place to configure it
and no credentials committed to the repo.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import get_settings
from app.db import Base, UtcDateTime

# Importing the models is what populates Base.metadata. Without it, autogenerate
# would compare the database against an empty schema and propose dropping
# every table.
from app import models  # noqa: F401  isort: skip

config = context.config

# A caller that has already chosen a database wins -- that is how the drift test
# points migrations at a throwaway file instead of the real one.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context) -> str | bool:
    """Render our own column types with the import they need.

    Autogenerate otherwise writes `app.db.UtcDateTime()` into the migration
    without importing it, and the file fails at run time with a NameError --
    which only shows up when the migration is applied, not when it is written.
    """
    if type_ == "type" and isinstance(obj, UtcDateTime):
        autogen_context.imports.add("from app.db import UtcDateTime")
        return "UtcDateTime()"
    return False


def run_migrations_offline() -> None:
    """Emit SQL to stdout rather than running it, for review before deploying."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things, so changes are applied by
            # rebuilding the table. Harmless on Postgres, which means one
            # migration file runs on both.
            render_as_batch=True,
            render_item=render_item,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
