"""add global usage counters

Revision ID: 72c07d6f29f0
Revises: 2cde5664c537
Create Date: 2026-09-23 18:49:27.573248

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '72c07d6f29f0'
down_revision: Union[str, Sequence[str], None] = '2cde5664c537'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "global_usage_counters",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("llm_calls", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("day"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("global_usage_counters")
