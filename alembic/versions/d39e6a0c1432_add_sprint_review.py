"""add sprint review

Revision ID: d39e6a0c1432
Revises: c28d5f9b0321
"""

from alembic import op
import sqlalchemy as sa

revision = "d39e6a0c1432"
down_revision = "c28d5f9b0321"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sprint", sa.Column("goal_achieved", sa.Boolean(), nullable=True))
    op.add_column("sprint", sa.Column("review_notes", sa.String(length=4000), nullable=True))


def downgrade() -> None:
    op.drop_column("sprint", "review_notes")
    op.drop_column("sprint", "goal_achieved")
