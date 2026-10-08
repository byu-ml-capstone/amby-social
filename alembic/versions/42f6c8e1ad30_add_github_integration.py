"""add github integration

Revision ID: 42f6c8e1ad30
Revises: e4b9c52d8fa1
Create Date: 2026-09-17
"""

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "42f6c8e1ad30"
down_revision = "e4b9c52d8fa1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_installation",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("github_installation_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column(
            "account_login",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "connected_by_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["connected_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_installation_id"),
    )
    op.create_table(
        "github_connect_state",
        sa.Column(
            "id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False
        ),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("pending_installation_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_github_connect_state_project_id"),
        "github_connect_state",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_github_connect_state_user_id"),
        "github_connect_state",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "project_github_connection",
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "installation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["installation_id"], ["github_installation.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("project_id"),
        sa.UniqueConstraint(
            "project_id",
            "installation_id",
            name="uq_project_github_connection_installation",
        ),
    )
    op.create_index(
        op.f("ix_project_github_connection_installation_id"),
        "project_github_connection",
        ["installation_id"],
        unique=False,
    )
    op.create_table(
        "project_github_repository",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "installation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False
        ),
        sa.Column("github_repository_id", sa.Integer(), nullable=False),
        sa.Column(
            "full_name",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "html_url",
            sqlmodel.sql.sqltypes.AutoString(length=500),
            nullable=False,
        ),
        sa.Column(
            "default_branch",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "installation_id"],
            [
                "project_github_connection.project_id",
                "project_github_connection.installation_id",
            ],
            name="fk_project_github_repository_connection",
        ),
        sa.ForeignKeyConstraint(["installation_id"], ["github_installation.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "github_repository_id",
            name="uq_project_github_repository",
        ),
    )
    op.create_index(
        op.f("ix_project_github_repository_github_repository_id"),
        "project_github_repository",
        ["github_repository_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_github_repository_installation_id"),
        "project_github_repository",
        ["installation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_github_repository_project_id"),
        "project_github_repository",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "integration_delivery",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "provider",
            sqlmodel.sql.sqltypes.AutoString(length=20),
            nullable=False,
        ),
        sa.Column(
            "delivery_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sqlmodel.sql.sqltypes.AutoString(length=100),
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "delivery_id", name="uq_integration_delivery_provider"
        ),
    )
    op.create_table(
        "github_artifact",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "repository_connection_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum("PULL_REQUEST", "COMMIT", name="githubartifactkind"),
            nullable=False,
        ),
        sa.Column(
            "external_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column(
            "title", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False
        ),
        sa.Column(
            "html_url",
            sqlmodel.sql.sqltypes.AutoString(length=500),
            nullable=False,
        ),
        sa.Column(
            "author_login",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(
                "DRAFT",
                "OPEN",
                "MERGED",
                "CLOSED",
                name="githubartifactstate",
            ),
            nullable=True,
        ),
        sa.Column(
            "review_state",
            sa.Enum(
                "REVIEW_REQUIRED",
                "APPROVED",
                "CHANGES_REQUESTED",
                name="githubreviewstate",
            ),
            nullable=True,
        ),
        sa.Column(
            "ci_state",
            sa.Enum("PENDING", "PASSED", "FAILED", "NONE", name="githubcistate"),
            nullable=False,
        ),
        sa.Column(
            "head_sha",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["repository_connection_id"], ["project_github_repository.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_connection_id",
            "kind",
            "external_id",
            name="uq_github_artifact_external",
        ),
    )
    op.create_index(
        op.f("ix_github_artifact_repository_connection_id"),
        "github_artifact",
        ["repository_connection_id"],
        unique=False,
    )
    op.create_table(
        "ticket_git_link",
        sa.Column("ticket_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("artifact_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["github_artifact.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["ticket.id"]),
        sa.PrimaryKeyConstraint("ticket_id", "artifact_id"),
    )


def downgrade() -> None:
    op.drop_table("ticket_git_link")
    op.drop_index(
        op.f("ix_github_artifact_repository_connection_id"),
        table_name="github_artifact",
    )
    op.drop_table("github_artifact")
    op.drop_table("integration_delivery")
    op.drop_index(
        op.f("ix_project_github_repository_project_id"),
        table_name="project_github_repository",
    )
    op.drop_index(
        op.f("ix_project_github_repository_installation_id"),
        table_name="project_github_repository",
    )
    op.drop_index(
        op.f("ix_project_github_repository_github_repository_id"),
        table_name="project_github_repository",
    )
    op.drop_table("project_github_repository")
    op.drop_index(
        op.f("ix_project_github_connection_installation_id"),
        table_name="project_github_connection",
    )
    op.drop_table("project_github_connection")
    op.drop_index(
        op.f("ix_github_connect_state_user_id"),
        table_name="github_connect_state",
    )
    op.drop_index(
        op.f("ix_github_connect_state_project_id"),
        table_name="github_connect_state",
    )
    op.drop_table("github_connect_state")
    op.drop_table("github_installation")
