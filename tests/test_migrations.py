from collections.abc import Iterator

from alembic import command
from alembic.config import Config

from tests.conftest import ALEMBIC_INI


def test_models_and_migrations_are_in_sync(migrated_database: Iterator[None]) -> None:
    """Fails if someone changes a model without generating the matching migration.

    The `migrated_database` argument is what makes this a database test: `alembic check` talks to
    the server, so it must be skipped (or failed, in CI) when there is none. The argument itself
    is unused.
    """
    command.check(Config(str(ALEMBIC_INI)))
