from alembic import command
from alembic.config import Config

from tests.conftest import ALEMBIC_INI


def test_models_and_migrations_are_in_sync() -> None:
    """Fails if someone changes a model without generating the matching migration."""
    command.check(Config(str(ALEMBIC_INI)))
