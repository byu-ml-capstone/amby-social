"""add user soft delete

Revision ID: 5d0b32a81e77
Revises: 42f6c8e1ad30
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5d0b32a81e77"
down_revision: str | Sequence[str] | None = "42f6c8e1ad30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "deleted_at")
