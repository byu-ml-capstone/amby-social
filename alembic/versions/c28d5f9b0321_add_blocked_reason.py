"""add blocked reason

Revision ID: c28d5f9b0321
Revises: b17c4e8a9210
"""

from alembic import op
import sqlalchemy as sa

revision = "c28d5f9b0321"
down_revision = "b17c4e8a9210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ticket", sa.Column("blocked_reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("ticket", "blocked_reason")
