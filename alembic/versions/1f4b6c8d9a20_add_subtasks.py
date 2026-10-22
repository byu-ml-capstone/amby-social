"""add subtasks

Revision ID: 1f4b6c8d9a20
Revises: f6a2b4c8d901
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "1f4b6c8d9a20"
down_revision: str | Sequence[str] | None = "f6a2b4c8d901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subtask",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ticket_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(length=5000), nullable=False),
        sa.Column(
            "status",
            sa.Enum("TODO", "IN_PROGRESS", "DONE", name="subtaskstatus"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_subtask_title_not_blank"),
        sa.ForeignKeyConstraint(["ticket_id"], ["ticket.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subtask_ticket_id", "subtask", ["ticket_id"], unique=False)
    op.create_index("ix_subtask_status", "subtask", ["status"], unique=False)
    op.create_index("ix_subtask_position", "subtask", ["position"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_subtask_position", table_name="subtask")
    op.drop_index("ix_subtask_status", table_name="subtask")
    op.drop_index("ix_subtask_ticket_id", table_name="subtask")
    op.drop_table("subtask")
