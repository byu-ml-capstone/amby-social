"""add project chat webhooks

Revision ID: e4b9c52d8fa1
Revises: d93f7a21c4e8
Create Date: 2026-09-17
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "e4b9c52d8fa1"
down_revision = "d93f7a21c4e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_chat_webhook",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum("NONE", "TEAMS", "SLACK", "DISCORD", name="webhooktype"),
            nullable=False,
        ),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "provider IN ('SLACK', 'TEAMS', 'DISCORD')",
            name="ck_chat_webhook_provider",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "provider", name="uq_project_chat_webhook_provider"
        ),
    )
    op.create_index(
        "ix_project_chat_webhook_project_id",
        "project_chat_webhook",
        ["project_id"],
        unique=False,
    )

    connection = op.get_bind()
    configured_rows = connection.execute(
        sa.text(
            """
            SELECT id AS project_id, webhook_type AS provider, webhook_url AS url
            FROM project
            WHERE webhook_type != 'NONE' AND webhook_url IS NOT NULL
            ORDER BY id
            """
        )
    ).mappings().all()
    copied_at = datetime.now(UTC).replace(tzinfo=None)
    for row in configured_rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO project_chat_webhook
                    (id, project_id, provider, url, created_at, updated_at)
                VALUES
                    (:id, :project_id, :provider, :url, :created_at, :updated_at)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "project_id": row["project_id"],
                "provider": row["provider"],
                "url": row["url"],
                "created_at": copied_at,
                "updated_at": copied_at,
            },
        )

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("webhook_url")
        batch_op.drop_column("webhook_type")


def downgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "webhook_type",
                sa.Enum("NONE", "TEAMS", "SLACK", "DISCORD", name="webhooktype"),
                nullable=False,
                server_default="NONE",
            )
        )
        batch_op.add_column(
            sa.Column(
                "webhook_url",
                sqlmodel.sql.sqltypes.AutoString(length=500),
                nullable=True,
            )
        )

    connection = op.get_bind()
    configured_rows = connection.execute(
        sa.text(
            """
            SELECT project_id, provider, url
            FROM project_chat_webhook
            ORDER BY project_id,
                CASE provider
                    WHEN 'SLACK' THEN 0
                    WHEN 'TEAMS' THEN 1
                    WHEN 'DISCORD' THEN 2
                END
            """
        )
    ).mappings().all()
    restored_projects: set[str] = set()
    for row in configured_rows:
        if row["project_id"] in restored_projects:
            continue
        restored_projects.add(row["project_id"])
        connection.execute(
            sa.text(
                """
                UPDATE project
                SET webhook_type = :provider, webhook_url = :url
                WHERE id = :project_id
                """
            ),
            row,
        )

    op.drop_index(
        "ix_project_chat_webhook_project_id", table_name="project_chat_webhook"
    )
    op.drop_table("project_chat_webhook")
