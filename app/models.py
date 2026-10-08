import uuid
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def api_token_expiry() -> datetime:
    return utcnow() + timedelta(days=90)


def new_id() -> str:
    return str(uuid.uuid4())


class Role(StrEnum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"


class WebhookType(StrEnum):
    NONE = "NONE"
    TEAMS = "TEAMS"
    SLACK = "SLACK"
    DISCORD = "DISCORD"


class GitHubArtifactKind(StrEnum):
    PULL_REQUEST = "PULL_REQUEST"
    COMMIT = "COMMIT"


class GitHubArtifactState(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"


class GitHubReviewState(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class GitHubCIState(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    NONE = "NONE"


class TicketType(StrEnum):
    STORY = "STORY"
    BUG = "BUG"
    DEMO_REQUEST = "DEMO_REQUEST"
    TASK = "TASK"


class TicketStatus(StrEnum):
    BACKLOG = "BACKLOG"
    SELECTED = "SELECTED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class SprintStatus(StrEnum):
    PLANNING = "PLANNING"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class Priority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class GmailConnectionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    NEEDS_REAUTH = "NEEDS_REAUTH"


class AttachmentSource(StrEnum):
    UPLOAD = "UPLOAD"
    GMAIL = "GMAIL"


class User(SQLModel, table=True):
    __tablename__ = "user"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(max_length=50)
    email: str = Field(max_length=255, unique=True, index=True)
    password_hash: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=utcnow)
    deleted_at: datetime | None = None


class ApiToken(SQLModel, table=True):
    __tablename__ = "api_token"

    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    label: str = Field(max_length=100)
    prefix: str = Field(max_length=12)
    token_hash: str = Field(max_length=64, unique=True, index=True)
    scope: str = Field(default="read", max_length=10)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(default_factory=api_token_expiry)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class Project(SQLModel, table=True):
    __tablename__ = "project"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(max_length=100)
    slug: str = Field(max_length=50, unique=True, index=True)
    key: str = Field(max_length=10, unique=True, index=True)
    next_ticket_number: int = Field(default=1)
    created_at: datetime = Field(default_factory=utcnow)


class ProjectChatWebhook(SQLModel, table=True):
    __tablename__ = "project_chat_webhook"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", name="uq_project_chat_webhook_provider"),
        CheckConstraint(
            "provider IN ('SLACK', 'TEAMS', 'DISCORD')",
            name="ck_chat_webhook_provider",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    provider: WebhookType
    url: str = Field(max_length=500)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectMember(SQLModel, table=True):
    __tablename__ = "project_member"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_member_project_user"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    role: Role = Field(default=Role.MEMBER)
    joined_at: datetime = Field(default_factory=utcnow)


class Ticket(SQLModel, table=True):
    __tablename__ = "ticket"
    __table_args__ = (
        UniqueConstraint("project_id", "ticket_number", name="uq_ticket_project_number"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    ticket_number: int = Field(index=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    title: str = Field(max_length=255)
    description: str = Field(default="", max_length=20000)
    type: TicketType = Field(default=TicketType.TASK)
    status: TicketStatus = Field(default=TicketStatus.BACKLOG, index=True)
    priority: Priority = Field(default=Priority.MEDIUM)
    backlog_rank: int = Field(default=0, index=True)
    story_points: int | None = Field(default=None)
    due_date: date | None = Field(default=None, index=True)
    sprint_id: str | None = Field(default=None, foreign_key="sprint.id", index=True)
    first_sprint_entered_at: datetime | None = Field(default=None)
    delayed_days: int | None = Field(default=None)
    rollover_count: int = Field(default=0)
    creator_id: str = Field(foreign_key="user.id")
    assignee_id: str | None = Field(default=None, foreign_key="user.id", index=True)
    resolution_notes: str | None = Field(default=None)
    blocked_reason: str | None = Field(default=None, max_length=500)
    completed_at: datetime | None = Field(default=None)
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class TicketComment(SQLModel, table=True):
    __tablename__ = "ticket_comment"

    id: str = Field(default_factory=new_id, primary_key=True)
    ticket_id: str = Field(foreign_key="ticket.id", index=True)
    author_id: str = Field(foreign_key="user.id", index=True)
    body: str = Field(max_length=5000)
    mentioned_user_ids: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime | None = None


class Sprint(SQLModel, table=True):
    __tablename__ = "sprint"
    __table_args__ = (
        CheckConstraint("end_date > start_date", name="ck_sprint_end_after_start"),
        Index(
            "uq_sprint_active_project",
            "project_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "uq_sprint_planning_project",
            "project_id",
            unique=True,
            sqlite_where=text("status = 'PLANNING'"),
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    name: str = Field(max_length=100)
    goal: str = Field(max_length=2000)
    status: SprintStatus = Field(default=SprintStatus.PLANNING, index=True)
    start_date: date
    end_date: date
    committed_points: int | None = None
    completed_points: int | None = None
    goal_achieved: bool | None = None
    review_notes: str | None = Field(default=None, max_length=4000)
    closed_at: datetime | None = None


class SprintTicketHistory(SQLModel, table=True):
    __tablename__ = "sprint_ticket_history"
    __table_args__ = (UniqueConstraint("sprint_id", "ticket_id"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    sprint_id: str = Field(foreign_key="sprint.id", index=True)
    ticket_id: str = Field(foreign_key="ticket.id", index=True)
    status_at_close: TicketStatus
    story_points_at_close: int | None = None
    was_completed: bool
    recorded_at: datetime = Field(default_factory=utcnow)


class GitHubInstallation(SQLModel, table=True):
    __tablename__ = "github_installation"

    id: str = Field(default_factory=new_id, primary_key=True)
    github_installation_id: int = Field(unique=True)
    account_id: int
    account_login: str = Field(max_length=255)
    connected_by_id: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectGitHubConnection(SQLModel, table=True):
    __tablename__ = "project_github_connection"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "installation_id",
            name="uq_project_github_connection_installation",
        ),
    )

    project_id: str = Field(foreign_key="project.id", primary_key=True)
    installation_id: str = Field(foreign_key="github_installation.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class GitHubConnectState(SQLModel, table=True):
    __tablename__ = "github_connect_state"

    id: str = Field(max_length=64, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    pending_installation_id: int | None = None
    expires_at: datetime
    consumed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ProjectGitHubRepository(SQLModel, table=True):
    __tablename__ = "project_github_repository"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "github_repository_id",
            name="uq_project_github_repository",
        ),
        ForeignKeyConstraint(
            ["project_id", "installation_id"],
            [
                "project_github_connection.project_id",
                "project_github_connection.installation_id",
            ],
            name="fk_project_github_repository_connection",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    installation_id: str = Field(foreign_key="github_installation.id", index=True)
    github_repository_id: int = Field(index=True)
    full_name: str = Field(max_length=255)
    html_url: str = Field(max_length=500)
    default_branch: str = Field(max_length=255)
    active: bool = Field(default=True)
    disconnected_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class GitHubArtifact(SQLModel, table=True):
    __tablename__ = "github_artifact"
    __table_args__ = (
        UniqueConstraint(
            "repository_connection_id",
            "kind",
            "external_id",
            name="uq_github_artifact_external",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    repository_connection_id: str = Field(foreign_key="project_github_repository.id", index=True)
    kind: GitHubArtifactKind
    external_id: str = Field(max_length=255)
    number: int | None = None
    title: str = Field(max_length=500)
    html_url: str = Field(max_length=500)
    author_login: str = Field(max_length=255)
    state: GitHubArtifactState | None = None
    review_state: GitHubReviewState | None = None
    ci_state: GitHubCIState = Field(default=GitHubCIState.NONE)
    head_sha: str | None = Field(default=None, max_length=64)
    occurred_at: datetime
    updated_at: datetime = Field(default_factory=utcnow)


class TicketGitLink(SQLModel, table=True):
    __tablename__ = "ticket_git_link"

    ticket_id: str = Field(foreign_key="ticket.id", primary_key=True)
    artifact_id: str = Field(foreign_key="github_artifact.id", primary_key=True)
    created_at: datetime = Field(default_factory=utcnow)


class IntegrationDelivery(SQLModel, table=True):
    __tablename__ = "integration_delivery"
    __table_args__ = (
        UniqueConstraint("provider", "delivery_id", name="uq_integration_delivery_provider"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    provider: str = Field(default="GITHUB", max_length=20)
    delivery_id: str = Field(max_length=255)
    event_type: str = Field(max_length=100)
    received_at: datetime = Field(default_factory=utcnow)


class GmailConnection(SQLModel, table=True):
    __tablename__ = "gmail_connection"

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", unique=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    google_email: str = Field(max_length=255)
    refresh_token_enc: str
    label_mapping: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    history_id: str = Field(max_length=32)
    status: GmailConnectionStatus = Field(default=GmailConnectionStatus.ACTIVE)
    last_synced_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=utcnow)


class TicketAttachment(SQLModel, table=True):
    __tablename__ = "ticket_attachment"

    id: str = Field(default_factory=new_id, primary_key=True)
    ticket_id: str = Field(foreign_key="ticket.id", index=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    filename: str = Field(max_length=255)  # display only; the file on disk is named by id
    content_type: str = Field(max_length=100)
    size_bytes: int
    is_image: bool = False
    source: AttachmentSource = Field(default=AttachmentSource.UPLOAD)
    uploaded_by: str = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)
