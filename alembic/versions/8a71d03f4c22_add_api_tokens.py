"""add api tokens

Revision ID: 8a71d03f4c22
Revises: 6c25b12d1a91
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "8a71d03f4c22"
down_revision: str | Sequence[str] | None = "6c25b12d1a91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_token",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("label", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("prefix", sqlmodel.sql.sqltypes.AutoString(length=12), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_token_user_id", "api_token", ["user_id"], unique=False)
    op.create_index("ix_api_token_token_hash", "api_token", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_token_token_hash", table_name="api_token")
    op.drop_index("ix_api_token_user_id", table_name="api_token")
    op.drop_table("api_token")
