"""add sprint workflow

Revision ID: 6c25b12d1a91
Revises: 89398913f6cb
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "6c25b12d1a91"
down_revision: str | Sequence[str] | None = "89398913f6cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sprint",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("goal", sqlmodel.sql.sqltypes.AutoString(length=2000), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PLANNING", "ACTIVE", "CLOSED", name="sprintstatus"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("committed_points", sa.Integer(), nullable=True),
        sa.Column("completed_points", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("end_date > start_date", name="ck_sprint_end_after_start"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sprint_project_id", "sprint", ["project_id"], unique=False)
    op.create_index("ix_sprint_status", "sprint", ["status"], unique=False)
    op.create_index(
        "uq_sprint_active_project",
        "sprint",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "uq_sprint_planning_project",
        "sprint",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status = 'PLANNING'"),
    )

    with op.batch_alter_table("ticket", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("sprint_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(sa.Column("first_sprint_entered_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("delayed_days", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("rollover_count", sa.Integer(), server_default=sa.text("0"), nullable=False)
        )
        batch_op.create_foreign_key("fk_ticket_sprint_id_sprint", "sprint", ["sprint_id"], ["id"])
        batch_op.create_index("ix_ticket_sprint_id", ["sprint_id"], unique=False)

    op.create_table(
        "sprint_ticket_history",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sprint_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ticket_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "status_at_close",
            sa.Enum("BACKLOG", "SELECTED", "IN_PROGRESS", "DONE", name="ticketstatus"),
            nullable=False,
        ),
        sa.Column("story_points_at_close", sa.Integer(), nullable=True),
        sa.Column("was_completed", sa.Boolean(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["sprint_id"], ["sprint.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["ticket.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sprint_id", "ticket_id"),
    )
    op.create_index(
        "ix_sprint_ticket_history_sprint_id",
        "sprint_ticket_history",
        ["sprint_id"],
        unique=False,
    )
    op.create_index(
        "ix_sprint_ticket_history_ticket_id",
        "sprint_ticket_history",
        ["ticket_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sprint_ticket_history_ticket_id", table_name="sprint_ticket_history")
    op.drop_index("ix_sprint_ticket_history_sprint_id", table_name="sprint_ticket_history")
    op.drop_table("sprint_ticket_history")

    with op.batch_alter_table("ticket", schema=None) as batch_op:
        batch_op.drop_index("ix_ticket_sprint_id")
        batch_op.drop_constraint("fk_ticket_sprint_id_sprint", type_="foreignkey")
        batch_op.drop_column("rollover_count")
        batch_op.drop_column("delayed_days")
        batch_op.drop_column("first_sprint_entered_at")
        batch_op.drop_column("sprint_id")

    op.drop_index("uq_sprint_planning_project", table_name="sprint")
    op.drop_index("uq_sprint_active_project", table_name="sprint")
    op.drop_index("ix_sprint_status", table_name="sprint")
    op.drop_index("ix_sprint_project_id", table_name="sprint")
    op.drop_table("sprint")
