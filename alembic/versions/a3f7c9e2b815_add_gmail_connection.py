"""add gmail connection

Revision ID: a3f7c9e2b815
Revises: f2a1b3c4d5e6
"""

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "a3f7c9e2b815"
down_revision = "f2a1b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_connection",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("google_email", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("refresh_token_enc", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("label_mapping", sa.JSON(), nullable=False),
        sa.Column("history_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "NEEDS_REAUTH", name="gmailconnectionstatus"),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index("ix_gmail_connection_user_id", "gmail_connection", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_gmail_connection_user_id", table_name="gmail_connection")
    op.drop_table("gmail_connection")
