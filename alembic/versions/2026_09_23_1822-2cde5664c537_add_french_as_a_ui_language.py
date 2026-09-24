"""add french as a ui language

Revision ID: 2cde5664c537
Revises: 0f194dee9800
Create Date: 2026-09-23 18:22:19.011895

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2cde5664c537'
down_revision: Union[str, Sequence[str], None] = '0f194dee9800'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("ui_language", "users", type_="check")
    op.create_check_constraint(
        "ui_language", "users", "ui_language IN ('ca', 'es', 'en', 'fr')"
    )
    op.drop_constraint("ui_language_used", "texts", type_="check")
    op.create_check_constraint(
        "ui_language_used", "texts", "ui_language_used IN ('ca', 'es', 'en', 'fr')"
    )


def downgrade() -> None:
    """Downgrade schema.

    Any row already using 'fr' would violate the narrower constraint below and abort the
    migration; there is no data-safe way to downgrade once a user has picked French, short of
    deleting or reassigning those rows first.
    """
    op.drop_constraint("ui_language", "users", type_="check")
    op.create_check_constraint("ui_language", "users", "ui_language IN ('ca', 'es', 'en')")
    op.drop_constraint("ui_language_used", "texts", type_="check")
    op.create_check_constraint(
        "ui_language_used", "texts", "ui_language_used IN ('ca', 'es', 'en')"
    )
