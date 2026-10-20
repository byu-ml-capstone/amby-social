"""Add project start date and allow multiple planning sprints.

Revision ID: f6a2b4c8d901
Revises: e5c1a7d3b902
"""

import sqlalchemy as sa

from alembic import op

revision = "f6a2b4c8d901"
down_revision = "e5c1a7d3b902"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("project", sa.Column("start_date", sa.Date(), nullable=True))
    op.execute("UPDATE project SET start_date = DATE(created_at)")
    with op.batch_alter_table("project") as batch_op:
        batch_op.alter_column("start_date", existing_type=sa.Date(), nullable=False)
    op.drop_index("uq_sprint_planning_project", table_name="sprint")


def downgrade():
    op.create_index(
        "uq_sprint_planning_project",
        "sprint",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status = 'PLANNING'"),
    )
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("start_date")
