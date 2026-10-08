"""add backlog rank

Revision ID: b17c4e8a9210
Revises: 7a9c2d1e4f80
"""

from alembic import op
import sqlalchemy as sa

revision = "b17c4e8a9210"
down_revision = "7a9c2d1e4f80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ticket", sa.Column("backlog_rank", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute("UPDATE ticket SET backlog_rank = ticket_number")
    op.create_index("ix_ticket_backlog_rank", "ticket", ["backlog_rank"])


def downgrade() -> None:
    op.drop_index("ix_ticket_backlog_rank", table_name="ticket")
    op.drop_column("ticket", "backlog_rank")
