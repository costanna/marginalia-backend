import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.models import Base

# psycopg's async mode cannot run on Windows' default ProactorEventLoop (local development only).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: by default fileConfig silences every logger that already
    # exists (the application's own included), which is wrong when Alembic runs in-process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The URL comes from the environment (never from alembic.ini). "%" is escaped because
# ConfigParser treats it as interpolation syntax and passwords may contain it.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

# Models imported above register their tables on Base.metadata, enabling autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to the database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # Same reason as the app engine: Neon's pooler (PgBouncer) has no prepared statements.
        connect_args={"prepare_threshold": None},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
