"""add API token scope and expiry

Revision ID: f2a1b3c4d5e6
Revises: d39e6a0c1432
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2a1b3c4d5e6"
down_revision: str | Sequence[str] | None = "d39e6a0c1432"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_token",
        sa.Column("scope", sa.String(length=10), nullable=False, server_default="read"),
    )
    op.add_column("api_token", sa.Column("expires_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE api_token SET expires_at = datetime('now', '+90 days')")
    with op.batch_alter_table("api_token") as batch_op:
        batch_op.alter_column("expires_at", existing_type=sa.DateTime(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("api_token") as batch_op:
        batch_op.drop_column("expires_at")
        batch_op.drop_column("scope")
