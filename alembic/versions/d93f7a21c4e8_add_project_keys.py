"""add project keys

Revision ID: d93f7a21c4e8
Revises: c41d8e7f2a10
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "d93f7a21c4e8"
down_revision: str | Sequence[str] | None = "c41d8e7f2a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    key_type = sqlmodel.sql.sqltypes.AutoString(length=10)
    op.add_column("project", sa.Column("key", key_type, nullable=True))

    connection = op.get_bind()
    used: set[str] = set()
    projects = connection.execute(
        sa.text("SELECT id, name FROM project ORDER BY created_at, id")
    ).mappings()
    for project in projects:
        token = next(iter(project["name"].split()), "")
        base = "".join(c for c in token.upper() if c.isascii() and c.isalnum())[:3] or "PRJ"
        suffix = 1
        while True:
            key = base if suffix == 1 else f"{base[: 10 - len(str(suffix))]}{suffix}"
            if key not in used:
                break
            suffix += 1
        used.add(key)
        connection.execute(
            sa.text("UPDATE project SET key = :key WHERE id = :id"),
            {"key": key, "id": project["id"]},
        )

    op.create_index("ix_project_key", "project", ["key"], unique=True)
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.alter_column("key", existing_type=key_type, nullable=False)


def downgrade() -> None:
    op.drop_index("ix_project_key", table_name="project")
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("key")
