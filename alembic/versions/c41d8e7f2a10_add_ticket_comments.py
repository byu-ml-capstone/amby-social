"""add ticket comments

Revision ID: c41d8e7f2a10
Revises: 8a71d03f4c22
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "c41d8e7f2a10"
down_revision: str | Sequence[str] | None = "8a71d03f4c22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ticket_comment",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ticket_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("author_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("body", sqlmodel.sql.sqltypes.AutoString(length=5000), nullable=False),
        sa.Column("mentioned_user_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["ticket.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_comment_author_id", "ticket_comment", ["author_id"], unique=False
    )
    op.create_index(
        "ix_ticket_comment_ticket_id", "ticket_comment", ["ticket_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_comment_ticket_id", table_name="ticket_comment")
    op.drop_index("ix_ticket_comment_author_id", table_name="ticket_comment")
    op.drop_table("ticket_comment")
