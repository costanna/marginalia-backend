"""drop the never-used exercise source text column

Revision ID: 9d4e7a2b8c1f
Revises: 72c07d6f29f0
Create Date: 2026-10-06 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d4e7a2b8c1f'
down_revision: Union[str, Sequence[str], None] = '72c07d6f29f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nothing ever wrote or read this column (no endpoint, no schema, no service), so dropping
    # it loses no data by construction.
    op.drop_constraint("exercises_source_text_id_fkey", "exercises", type_="foreignkey")
    op.drop_column("exercises", "source_text_id")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("exercises", sa.Column("source_text_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "exercises_source_text_id_fkey",
        "exercises",
        "texts",
        ["source_text_id"],
        ["id"],
        ondelete="SET NULL",
    )
