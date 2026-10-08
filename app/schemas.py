from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import (
    AttachmentSource,
    GitHubArtifactKind,
    GitHubArtifactState,
    GitHubCIState,
    GitHubReviewState,
    Priority,
    Role,
    SprintStatus,
    TicketStatus,
    TicketType,
)
from app.services import DESCRIPTION_MAX_LENGTH, TITLE_MAX_LENGTH

STORY_POINTS = Literal[1, 2, 3, 5, 8, 13]


def _check_password_byte_length(password: str) -> str:
    # Pydantic's max_length counts characters, not bytes. bcrypt hashes UTF-8
    # bytes and raises an uncaught ValueError past 72 of them, so a multi-byte
    # passphrase (e.g. Korean, 3 bytes/char) can pass the character limit and
    # still crash bcrypt. Catch it here as a 422 instead.
    if len(password.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes")
    return password


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    _check_password_bytes = field_validator("password")(_check_password_byte_length)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)

    _check_password_bytes = field_validator("password")(_check_password_byte_length)


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    created_at: datetime


class TokenCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    scope: Literal["read", "write"] = "read"

    @field_validator("label")
    @classmethod
    def _strip_and_reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        return value


class TokenOut(BaseModel):
    id: str
    label: str
    prefix: str
    scope: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenIssued(TokenOut):
    token: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip_and_reject_blank(cls, value: str | None) -> str | None:
        # min_length=1 only counts the raw input, so "   " passes Pydantic but
        # strips to "". update_project has no slug re-derivation to catch this
        # the way create_project's empty-slug check does (Ruling R23).
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("name must not be blank")
        return value


class ProjectOut(BaseModel):
    id: str
    name: str
    slug: str
    key: str
    created_at: datetime
    role: str | None = None


class MemberAdd(BaseModel):
    email: EmailStr
    role: Role = Role.MEMBER


class MemberUpdate(BaseModel):
    role: Role


class MemberOut(BaseModel):
    user_id: str
    name: str
    email: str
    role: Role
    joined_at: datetime


class TicketCreate(BaseModel):
    slug: str
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    description: str = Field(default="", max_length=DESCRIPTION_MAX_LENGTH)
    type: TicketType = TicketType.TASK
    priority: Priority = Priority.MEDIUM
    story_points: STORY_POINTS | None = None
    due_date: date | None = None
    sprint_id: str | None = None
    assignee_id: str | None = None
    meta: dict = Field(default_factory=dict)


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticket_number: int
    project_id: str
    title: str
    description: str
    type: TicketType
    status: TicketStatus
    priority: Priority
    story_points: int | None
    due_date: date | None
    sprint_id: str | None
    creator_id: str
    assignee_id: str | None
    resolution_notes: str | None
    blocked_reason: str | None
    completed_at: datetime | None
    meta: dict
    created_at: datetime


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    is_image: bool
    source: AttachmentSource
    created_at: datetime


class DevelopmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    repository_full_name: str
    kind: GitHubArtifactKind
    number: int | None
    title: str
    html_url: str | None
    author_login: str
    state: GitHubArtifactState | None
    review_state: GitHubReviewState | None
    ci_state: GitHubCIState
    occurred_at: datetime


class TicketDetailOut(TicketOut):
    attachments: list[AttachmentOut] = []
    branch_command: str | None = None
    development: list[DevelopmentOut] = []


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    type: TicketType | None = None
    priority: Priority | None = None
    story_points: STORY_POINTS | None = None
    due_date: date | None = None
    sprint_id: str | None = None
    assignee_id: str | None = None
    resolution_notes: str | None = None
    blocked_reason: str | None = Field(default=None, max_length=500)
    meta: dict | None = None


class TicketPage(BaseModel):
    items: list[TicketOut]
    next_cursor: int | None = None


class StatusUpdate(BaseModel):
    status: TicketStatus
    resolution_notes: str | None = None


def _strip_sprint_text(value: str | None) -> str | None:
    if value is not None:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
    return value


class SprintCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=2000)
    start_date: date
    end_date: date

    _strip_text = field_validator("name", "goal")(_strip_sprint_text)

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


class SprintUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    goal: str | None = Field(default=None, min_length=1, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None
    status: Literal[SprintStatus.ACTIVE] | None = None

    _strip_text = field_validator("name", "goal")(_strip_sprint_text)

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date <= self.start_date
        ):
            raise ValueError("end_date must be after start_date")
        return self


class SprintClose(BaseModel):
    next_sprint_id: str


class SprintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    goal: str
    status: SprintStatus
    start_date: date
    end_date: date
    committed_points: int | None
    completed_points: int | None
    closed_at: datetime | None


class SprintHistoryOut(BaseModel):
    ticket_id: str
    ticket_number: int
    title: str
    status_at_close: TicketStatus
    story_points_at_close: int | None
    was_completed: bool
