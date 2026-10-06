"""index exercise attempts by user

Revision ID: 4b6c8d0e2f5a
Revises: 9d4e7a2b8c1f
Create Date: 2026-10-06 13:05:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4b6c8d0e2f5a'
down_revision: Union[str, Sequence[str], None] = '9d4e7a2b8c1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # /me/export filters attempts by user_id; every other user_id lookup column is indexed.
    op.create_index("ix_exercise_attempts_user_id", "exercise_attempts", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_exercise_attempts_user_id", table_name="exercise_attempts")
