"""add ticket attachments

Revision ID: b7e2d4f1a9c3
Revises: a3f7c9e2b815
"""

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "b7e2d4f1a9c3"
down_revision = "a3f7c9e2b815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_attachment",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ticket_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("filename", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("content_type", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("is_image", sa.Boolean(), nullable=False),
        sa.Column("source", sa.Enum("UPLOAD", "GMAIL", name="attachmentsource"), nullable=False),
        sa.Column("uploaded_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["ticket.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["uploaded_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_attachment_ticket_id", "ticket_attachment", ["ticket_id"], unique=False
    )
    op.create_index(
        "ix_ticket_attachment_project_id", "ticket_attachment", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_attachment_project_id", table_name="ticket_attachment")
    op.drop_index("ix_ticket_attachment_ticket_id", table_name="ticket_attachment")
    op.drop_table("ticket_attachment")
