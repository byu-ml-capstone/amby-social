"""add sprint report

Revision ID: e5c1a7d3b902
Revises: b7e2d4f1a9c3
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "e5c1a7d3b902"
down_revision = "b7e2d4f1a9c3"
branch_labels = None
depends_on = None

TEXT = sqlmodel.sql.sqltypes.AutoString(length=8000)


def upgrade() -> None:
    report = op.create_table(
        "sprint_report",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sprint_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("goal_summary", TEXT, nullable=False),
        sa.Column("completed_summary", TEXT, nullable=False),
        sa.Column("blockers_and_rollovers", TEXT, nullable=False),
        sa.Column("velocity_summary", TEXT, nullable=False),
        sa.Column("faculty_notes", TEXT, nullable=False),
        sa.Column(
            "generation_status",
            sa.Enum("PENDING", "OK", "FAILED", name="reportgenerationstatus"),
            nullable=False,
        ),
        sa.Column(
            "generated_by",
            sa.Enum("SERVER", "MCP_CLIENT", "HUMAN", name="reportauthor"),
            nullable=True,
        ),
        sa.Column("is_finalized", sa.Boolean(), nullable=False),
        sa.Column("finalized_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["sprint_id"], ["sprint.id"]),
        sa.ForeignKeyConstraint(["finalized_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sprint_report_sprint_id", "sprint_report", ["sprint_id"], unique=True)

    # Sprints closed before this migration get an empty draft, as if close had made one.
    closed = op.get_bind().execute(sa.text("SELECT id FROM sprint WHERE status = 'CLOSED'"))
    now = datetime.now(UTC)
    rows = [
        {
            "id": str(uuid.uuid4()),
            "sprint_id": sprint_id,
            "goal_summary": "",
            "completed_summary": "",
            "blockers_and_rollovers": "",
            "velocity_summary": "",
            "faculty_notes": "",
            "generation_status": "PENDING",
            "is_finalized": False,
            "created_at": now,
        }
        for (sprint_id,) in closed
    ]
    if rows:
        op.bulk_insert(report, rows)


def downgrade() -> None:
    op.drop_index("ix_sprint_report_sprint_id", table_name="sprint_report")
    op.drop_table("sprint_report")
