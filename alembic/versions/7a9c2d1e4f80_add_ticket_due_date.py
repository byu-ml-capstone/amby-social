"""add ticket due date

Revision ID: 7a9c2d1e4f80
Revises: 5d0b32a81e77
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7a9c2d1e4f80"
down_revision: str | Sequence[str] | None = "5d0b32a81e77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticket", sa.Column("due_date", sa.Date(), nullable=True))
    op.create_index("ix_ticket_due_date", "ticket", ["due_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ticket_due_date", table_name="ticket")
    op.drop_column("ticket", "due_date")
