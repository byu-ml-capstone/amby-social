import ipaddress
import json
import re
import socket
from collections.abc import Sequence
from datetime import date
from urllib.parse import urlparse

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import delete, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from app import attachments
from app.auth import hash_password
from app.config import settings
from app.models import (
    GitHubArtifact,
    GitHubConnectState,
    GitHubInstallation,
    GmailConnection,
    Priority,
    Project,
    ProjectChatWebhook,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    ProjectMember,
    Role,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketAttachment,
    TicketComment,
    TicketGitLink,
    TicketStatus,
    TicketType,
    User,
    WebhookType,
    utcnow,
)
from app.notifications import EVENT_TICKET_CREATED, EVENT_TICKET_DONE, schedule

META_MAX_BYTES = 8 * 1024
META_MAX_DEPTH = 3
TITLE_MAX_LENGTH = 255
DESCRIPTION_MAX_LENGTH = 20_000
VALID_STORY_POINTS = {1, 2, 3, 5, 8, 13}
NULLABLE_TICKET_FIELDS = {
    "story_points",
    "due_date",
    "sprint_id",
    "assignee_id",
    "resolution_notes",
    "blocked_reason",
}
NAME_MAX_LENGTH = 100
USER_NAME_MAX_LENGTH = 50
_EMAIL_TAKEN = "Email already registered"
_PLANNING_SPRINT_EXISTS = "A planning sprint already exists"
_ACTIVE_SPRINT_EXISTS = "An active sprint already exists"
_SPRINT_UPDATE_CONFLICT = "Sprint update conflict"
_SPRINT_CLOSE_CONFLICT = "Sprint close conflict"
_SQLITE_LOCK_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50].strip("-")


def _slug_conflict_detail(slug: str) -> str:
    return f"Slug already taken: {slug}"


def allocate_project_key(session: Session, name: str) -> str:
    token = next(iter(name.split()), "")
    base = "".join(c for c in token.upper() if c.isascii() and c.isalnum())[:3] or "PRJ"
    suffix = 1
    while True:
        key = base if suffix == 1 else f"{base[: 10 - len(str(suffix))]}{suffix}"
        if session.exec(select(Project.id).where(Project.key == key)).first() is None:
            return key
        suffix += 1


def register_user(session: Session, *, name: str, email: str, password: str) -> User:
    # Both registration routes use this service because the HTML form has no
    # Pydantic model in front of it. Keep the database-facing validation and
    # duplicate-email race handling in one place.
    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "name must not be blank")
    if len(clean_name) > USER_NAME_MAX_LENGTH:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"name must be at most {USER_NAME_MAX_LENGTH} characters",
        )
    normalized_email = email.lower()
    if session.exec(select(User).where(User.email == normalized_email)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, _EMAIL_TAKEN)
    user = User(
        name=clean_name,
        email=normalized_email,
        password_hash=hash_password(password),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        # The unique constraint resolves a concurrent check-then-insert race.
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _EMAIL_TAKEN) from None
    session.refresh(user)
    return user


def create_project(session: Session, name: str, user: User) -> Project:
    # Shared by the JSON route (app/routers/api_projects.py) and the dashboard
    # form (app/routers/web.py) so slug derivation, the collision pre-check, the
    # IntegrityError race guard (Ruling R16), and the creator-becomes-OWNER
    # membership insert exist in exactly one place. ProjectCreate already
    # enforces these name limits on the JSON path via Pydantic; the form route
    # has no schema in front of it (Ruling R36), so the check lives here where
    # both callers get it.
    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "name must not be blank")
    if len(clean_name) > NAME_MAX_LENGTH:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"name must be at most {NAME_MAX_LENGTH} characters",
        )
    slug = slugify(clean_name)
    if not slug:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Name yields an empty slug")
    if session.exec(select(Project).where(Project.slug == slug)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, _slug_conflict_detail(slug))
    key = allocate_project_key(session, clean_name)
    while True:
        project = Project(name=clean_name, slug=slug, key=key)
        session.add(project)
        try:
            session.flush()
            session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.OWNER))
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if "UNIQUE constraint failed: project.key" in str(exc.orig):
                key = allocate_project_key(session, clean_name)
                continue
            # Two concurrent project creations that derive the same slug can both
            # pass the pre-check above; the unique constraint on Project.slug
            # catches the loser here (Ruling R16).
            raise HTTPException(status.HTTP_409_CONFLICT, _slug_conflict_detail(slug)) from None
        session.refresh(project)
        return project


def update_project(session: Session, project: Project, **changes) -> Project:
    if "name" in changes:
        name = changes["name"]
        if name is None or not name.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "name must not be blank")
        if len(name.strip()) > NAME_MAX_LENGTH:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"name must be at most {NAME_MAX_LENGTH} characters",
            )
    for field, value in changes.items():
        setattr(project, field, value.strip() if field == "name" else value)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def validate_webhook_url(url: str) -> None:
    if len(url) > 500:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "webhook URL must be at most 500 characters",
        )
    if any(char == "\\" or char.isspace() or ord(char) <= 31 or ord(char) == 127 for char in url):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "webhook URL must be a safe https URL",
        )
    try:
        parsed = urlparse(url)
        valid = parsed.scheme == "https" and bool(parsed.netloc) and bool(parsed.hostname)
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "webhook URL must be a safe https URL",
        )
    addresses = []
    try:
        addresses.append(ipaddress.ip_address(parsed.hostname))
    except ValueError:
        try:
            addresses.append(ipaddress.ip_address(socket.inet_aton(parsed.hostname)))
        except OSError:
            try:
                resolved = socket.getaddrinfo(
                    parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
                )
                addresses.extend(
                    ipaddress.ip_address(item[4][0].split("%", 1)[0]) for item in resolved
                )
            except (OSError, ValueError):
                addresses = []
    if not addresses or any(
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "webhook URL must be a safe https URL",
        )
    hostname = parsed.hostname.lower().rstrip(".")
    if not any(
        hostname == allowed or hostname.endswith(f".{allowed}")
        for allowed in settings.webhook_allowed_hosts
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "webhook URL must use an approved webhook host",
        )


def chat_webhooks(session: Session, project_id: str) -> list[ProjectChatWebhook]:
    return session.exec(
        select(ProjectChatWebhook)
        .where(ProjectChatWebhook.project_id == project_id)
        .order_by(ProjectChatWebhook.provider)
    ).all()


def set_chat_webhook(
    session: Session, project: Project, provider: WebhookType, url: str
) -> ProjectChatWebhook:
    if provider not in (WebhookType.SLACK, WebhookType.TEAMS, WebhookType.DISCORD):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid chat provider")
    validate_webhook_url(url)
    webhook = session.exec(
        select(ProjectChatWebhook).where(
            ProjectChatWebhook.project_id == project.id,
            ProjectChatWebhook.provider == provider,
        )
    ).first()
    if webhook is None:
        webhook = ProjectChatWebhook(project_id=project.id, provider=provider, url=url)
    else:
        webhook.url = url
        webhook.updated_at = utcnow()
    session.add(webhook)
    session.commit()
    session.refresh(webhook)
    return webhook


def disconnect_chat_webhook(session: Session, project: Project, provider: WebhookType) -> None:
    webhook = session.exec(
        select(ProjectChatWebhook).where(
            ProjectChatWebhook.project_id == project.id,
            ProjectChatWebhook.provider == provider,
        )
    ).first()
    if webhook is not None:
        session.delete(webhook)
    session.commit()


def project_members(session: Session, project_id: str) -> list[tuple[ProjectMember, User]]:
    return session.exec(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.joined_at)
    ).all()


def _owner_count(session: Session, project_id: str) -> int:
    return len(
        session.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.role == Role.OWNER
            )
        ).all()
    )


def add_project_member(session: Session, project: Project, email: str, role: Role) -> ProjectMember:
    target = session.exec(select(User).where(User.email == email.lower())).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No registered user with that email")
    existing = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == target.id
        )
    ).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a member")
    member = ProjectMember(project_id=project.id, user_id=target.id, role=role)
    session.add(member)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a member") from None
    session.refresh(member)
    return member


def update_project_member(
    session: Session, project: Project, user_id: str, role: Role
) -> ProjectMember:
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user_id
        )
    ).first()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member")
    if member.role == Role.OWNER and role != Role.OWNER and _owner_count(session, project.id) == 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "A project must keep at least one OWNER")
    member.role = role
    session.add(member)
    session.commit()
    session.refresh(member)
    return member


def remove_project_member(session: Session, project: Project, user_id: str) -> None:
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user_id
        )
    ).first()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member")
    if member.role == Role.OWNER and _owner_count(session, project.id) == 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "A project must keep at least one OWNER")
    for ticket in session.exec(
        select(Ticket).where(Ticket.project_id == project.id, Ticket.assignee_id == user_id)
    ).all():
        ticket.assignee_id = None
        session.add(ticket)
    session.delete(member)
    session.commit()


def delete_ticket_record(session: Session, ticket: Ticket) -> None:
    if session.exec(
        select(SprintTicketHistory).where(SprintTicketHistory.ticket_id == ticket.id)
    ).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket belongs to closed sprint history")
    project_id = ticket.project_id
    attachment_ids = session.exec(
        select(TicketAttachment.id).where(TicketAttachment.ticket_id == ticket.id)
    ).all()
    session.execute(delete(TicketAttachment).where(TicketAttachment.ticket_id == ticket.id))
    session.execute(delete(TicketGitLink).where(TicketGitLink.ticket_id == ticket.id))
    session.execute(delete(TicketComment).where(TicketComment.ticket_id == ticket.id))
    session.delete(ticket)
    session.commit()
    for attachment_id in attachment_ids:  # after commit: a failure leaves an orphan file only
        attachments.delete(project_id, attachment_id)


def delete_project(session: Session, project: Project, confirm: str) -> None:
    if confirm != project.slug:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "confirm must equal the slug")
    project_id = project.id
    repository_ids = select(ProjectGitHubRepository.id).where(
        ProjectGitHubRepository.project_id == project.id
    )
    artifact_ids = select(GitHubArtifact.id).where(
        GitHubArtifact.repository_connection_id.in_(repository_ids)
    )
    installation_ids = session.exec(
        select(ProjectGitHubConnection.installation_id).where(
            ProjectGitHubConnection.project_id == project.id
        )
    ).all()
    session.execute(delete(GitHubConnectState).where(GitHubConnectState.project_id == project.id))
    session.execute(delete(TicketGitLink).where(TicketGitLink.artifact_id.in_(artifact_ids)))
    session.execute(
        delete(GitHubArtifact).where(GitHubArtifact.repository_connection_id.in_(repository_ids))
    )
    session.execute(
        delete(ProjectGitHubRepository).where(ProjectGitHubRepository.project_id == project.id)
    )
    session.execute(
        delete(ProjectGitHubConnection).where(ProjectGitHubConnection.project_id == project.id)
    )
    sprint_ids = select(Sprint.id).where(Sprint.project_id == project.id)
    session.execute(
        delete(SprintTicketHistory).where(SprintTicketHistory.sprint_id.in_(sprint_ids))
    )
    ticket_ids = select(Ticket.id).where(Ticket.project_id == project.id)
    session.execute(delete(TicketAttachment).where(TicketAttachment.project_id == project.id))
    session.execute(delete(TicketComment).where(TicketComment.ticket_id.in_(ticket_ids)))
    session.execute(delete(Ticket).where(Ticket.project_id == project.id))
    session.execute(delete(Sprint).where(Sprint.project_id == project.id))
    session.execute(delete(ProjectMember).where(ProjectMember.project_id == project.id))
    session.execute(delete(ProjectChatWebhook).where(ProjectChatWebhook.project_id == project.id))
    session.execute(delete(GmailConnection).where(GmailConnection.project_id == project.id))
    session.flush()
    session.delete(project)
    session.flush()
    for installation_id in installation_ids:
        if not session.exec(
            select(ProjectGitHubConnection.project_id).where(
                ProjectGitHubConnection.installation_id == installation_id
            )
        ).first():
            installation = session.get(GitHubInstallation, installation_id)
            if installation:
                session.delete(installation)
    session.commit()
    attachments.delete_project_files(project_id)


def create_sprint(
    session: Session,
    project: Project,
    *,
    name: str,
    goal: str,
    start_date: date,
    end_date: date,
) -> Sprint:
    if end_date <= start_date:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "end_date must be after start_date"
        )
    if session.exec(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == SprintStatus.PLANNING,
        )
    ).first():
        raise HTTPException(status.HTTP_409_CONFLICT, _PLANNING_SPRINT_EXISTS)
    sprint = Sprint(
        project_id=project.id,
        name=name,
        goal=goal,
        start_date=start_date,
        end_date=end_date,
    )
    session.add(sprint)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _PLANNING_SPRINT_EXISTS) from None
    session.refresh(sprint)
    return sprint


def update_sprint(session: Session, sprint: Sprint, **changes) -> Sprint:
    if sprint.status is not SprintStatus.PLANNING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sprint must be planning")
    for field in ("name", "goal", "start_date", "end_date"):
        if field in changes and changes[field] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field} may not be null")
    start_date = changes.get("start_date", sprint.start_date)
    end_date = changes.get("end_date", sprint.end_date)
    if end_date <= start_date:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "end_date must be after start_date"
        )
    for field in ("name", "goal", "start_date", "end_date"):
        if field in changes:
            setattr(sprint, field, changes[field])
    session.add(sprint)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _SPRINT_UPDATE_CONFLICT) from None
    session.refresh(sprint)
    return sprint


def start_sprint(session: Session, sprint: Sprint) -> Sprint:
    if sprint.status is not SprintStatus.PLANNING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sprint must be planning")
    if session.exec(
        select(Sprint).where(
            Sprint.project_id == sprint.project_id,
            Sprint.status == SprintStatus.ACTIVE,
        )
    ).first():
        raise HTTPException(status.HTTP_409_CONFLICT, _ACTIVE_SPRINT_EXISTS)
    sprint.committed_points = sum(
        points or 0
        for points in session.exec(select(Ticket.story_points).where(Ticket.sprint_id == sprint.id))
    )
    sprint.status = SprintStatus.ACTIVE
    session.add(sprint)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _ACTIVE_SPRINT_EXISTS) from None
    session.refresh(sprint)
    return sprint


def close_sprint(
    session: Session,
    sprint: Sprint,
    next_sprint: Sprint,
    *,
    goal_achieved: bool | None = None,
    review_notes: str | None = None,
) -> Sprint:
    try:
        sprint = session.get(Sprint, sprint.id, populate_existing=True)
        next_sprint = session.get(Sprint, next_sprint.id, populate_existing=True)
        if sprint is None or sprint.status is not SprintStatus.ACTIVE:
            raise HTTPException(status.HTTP_409_CONFLICT, "Sprint must be active")
        if next_sprint is None or next_sprint.project_id != sprint.project_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Next sprint must belong to the same project"
            )
        if next_sprint.status is not SprintStatus.PLANNING:
            raise HTTPException(status.HTTP_409_CONFLICT, "Next sprint must be planning")

        if review_notes is not None and len(review_notes) > 4000:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "review_notes is too long")
        claim = session.exec(
            update(Sprint)
            .where(Sprint.id == sprint.id, Sprint.status == SprintStatus.ACTIVE)
            .values(status=SprintStatus.CLOSED, closed_at=utcnow())
        )
        if claim.rowcount != 1:
            session.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "Sprint must be active")

        tickets = session.exec(select(Ticket).where(Ticket.sprint_id == sprint.id)).all()
        completed_points = 0
        for ticket in tickets:
            session.add(
                SprintTicketHistory(
                    sprint_id=sprint.id,
                    ticket_id=ticket.id,
                    status_at_close=ticket.status,
                    story_points_at_close=ticket.story_points,
                    was_completed=ticket.status is TicketStatus.DONE,
                )
            )
            if ticket.status is TicketStatus.DONE:
                completed_points += ticket.story_points or 0

        for ticket in tickets:
            if ticket.status is TicketStatus.DONE:
                continue
            ticket.sprint_id = next_sprint.id
            ticket.rollover_count += 1
            ticket.delayed_days = (
                max(0, (sprint.end_date - ticket.first_sprint_entered_at.date()).days)
                if ticket.first_sprint_entered_at is not None
                else None
            )

        sprint.completed_points = completed_points
        sprint.goal_achieved = goal_achieved
        sprint.review_notes = review_notes.strip() if review_notes else None
        session.commit()
    except HTTPException:
        raise
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _SPRINT_CLOSE_CONFLICT) from None
    except OperationalError as exc:
        session.rollback()
        if str(exc.orig).lower().startswith(_SQLITE_LOCK_MESSAGES):
            raise HTTPException(status.HTTP_409_CONFLICT, _SPRINT_CLOSE_CONFLICT) from None
        raise
    except Exception:
        session.rollback()
        raise
    session.refresh(sprint)
    return sprint


def _depth(value, level: int = 1) -> int:
    if isinstance(value, dict):
        return max((_depth(v, level + 1) for v in value.values()), default=level)
    if isinstance(value, list):
        return max((_depth(v, level + 1) for v in value), default=level)
    return level - 1


def validate_meta(meta) -> None:
    if not isinstance(meta, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "meta must be a JSON object")
    if _depth(meta) > META_MAX_DEPTH:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"meta may not nest deeper than {META_MAX_DEPTH} levels",
        )
    if len(json.dumps(meta).encode("utf-8")) > META_MAX_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "meta exceeds 8 KB")


def validate_assignee(session: Session, project: Project, assignee_id: str | None) -> None:
    if assignee_id is None:
        return
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == assignee_id,
        )
    ).first()
    if member is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "assignee must be a project member"
        )


def validate_sprint_assignment(
    session: Session, project: Project, sprint_id: str | None
) -> Sprint | None:
    if sprint_id is None:
        return None
    sprint = session.get(Sprint, sprint_id)
    if sprint is None or sprint.project_id != project.id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "sprint must belong to this project"
        )
    if sprint.status not in (SprintStatus.PLANNING, SprintStatus.ACTIVE):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "sprint must be planning or active"
        )
    return sprint


def allocate_ticket_number(session: Session, project_id: str) -> int:
    # Atomic per-project counter: UPDATE ... RETURNING runs inside the
    # caller's open transaction, alongside the ticket insert that follows.
    # A SELECT MAX(ticket_number) + 1 would read a value another concurrent
    # writer could be about to take; this claims the number as part of the
    # same write. Because it shares the transaction with the insert, a
    # rollback (e.g. the insert violates a constraint) undoes the increment
    # too, so the sequence stays gapless, not just unique.
    row = session.exec(
        text(
            "UPDATE project SET next_ticket_number = next_ticket_number + 1 "
            "WHERE id = :project_id RETURNING next_ticket_number - 1"
        ).bindparams(project_id=project_id)
    ).one()
    return int(row[0])


def create_ticket(
    session: Session,
    project: Project,
    creator: User,
    *,
    title: str,
    description: str = "",
    type: TicketType = TicketType.TASK,
    priority: Priority = Priority.MEDIUM,
    story_points: int | None = None,
    due_date: date | None = None,
    sprint_id: str | None = None,
    assignee_id: str | None = None,
    meta: dict | None = None,
    tasks: BackgroundTasks | None = None,
    attachment_rows: Sequence[TicketAttachment] = (),
) -> Ticket:
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "title must not be empty")
    if len(clean_title) > TITLE_MAX_LENGTH:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"title must be at most {TITLE_MAX_LENGTH} characters",
        )
    if len(description) > DESCRIPTION_MAX_LENGTH:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"description must be at most {DESCRIPTION_MAX_LENGTH} characters",
        )
    if story_points is not None and story_points not in VALID_STORY_POINTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "story_points must be one of 1, 2, 3, 5, 8, 13",
        )
    if meta is None:
        meta = {}
    validate_meta(meta)
    validate_assignee(session, project, assignee_id)
    sprint = validate_sprint_assignment(session, project, sprint_id)
    ticket_number = allocate_ticket_number(session, project.id)
    ticket = Ticket(
        ticket_number=ticket_number,
        project_id=project.id,
        title=clean_title,
        description=description,
        type=type,
        status=TicketStatus.BACKLOG,
        priority=priority,
        backlog_rank=ticket_number,
        story_points=story_points,
        due_date=due_date,
        sprint_id=sprint.id if sprint else None,
        first_sprint_entered_at=utcnow() if sprint else None,
        creator_id=creator.id,
        assignee_id=assignee_id,
        meta=meta,
    )
    session.add(ticket)
    if attachment_rows:
        # SQLite checks foreign keys per insert and the ORM does not order these tables.
        session.flush()
        for row in attachment_rows:
            row.ticket_id = ticket.id
        session.add_all(attachment_rows)
    session.commit()
    session.refresh(ticket)
    schedule(tasks, session, project, EVENT_TICKET_CREATED, ticket)
    return ticket


def update_ticket(
    session: Session, ticket: Ticket, project: Project, *, commit: bool = True, **changes
) -> Ticket:
    for field, value in changes.items():
        if value is None and field not in NULLABLE_TICKET_FIELDS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field} may not be null")
    if "meta" in changes and changes["meta"] is not None:
        validate_meta(changes["meta"])
    if "assignee_id" in changes:
        validate_assignee(session, project, changes["assignee_id"])
    if "sprint_id" in changes:
        validate_sprint_assignment(session, project, changes["sprint_id"])
    if "title" in changes and changes["title"] is not None:
        changes["title"] = changes["title"].strip()
        if not changes["title"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "title must not be empty")
    if changes.get("sprint_id") is not None and ticket.first_sprint_entered_at is None:
        ticket.first_sprint_entered_at = utcnow()
    for field, value in changes.items():
        setattr(ticket, field, value)
    session.add(ticket)
    if commit:
        session.commit()
        session.refresh(ticket)
    return ticket


def set_status(
    session: Session,
    ticket: Ticket,
    new_status: TicketStatus,
    resolution_notes: str | None = None,
    *,
    project: Project | None = None,
    tasks: BackgroundTasks | None = None,
    commit: bool = True,
) -> Ticket:
    # commit=False leaves the transaction to the caller (the GitHub webhook
    # moves tickets inside its delivery-dedupe transaction).
    # Any status may move to any other; there is no transition graph to
    # enforce. completed_at tracks DONE membership: entering DONE stamps it,
    # leaving clears it. Re-affirming DONE (a double-clicked dropdown) must
    # not restamp it to a new timestamp, so only stamp when not already DONE.
    entered_done = ticket.status != TicketStatus.DONE and new_status == TicketStatus.DONE
    if new_status == TicketStatus.DONE:
        if ticket.completed_at is None:
            ticket.completed_at = utcnow()
    else:
        ticket.completed_at = None
    ticket.status = new_status
    # resolution_notes is only overwritten when the request supplies one; it
    # is never cleared as a side effect of leaving DONE (ADR: losing an
    # author's note to a mis-clicked dropdown is worse than a stale one).
    if resolution_notes is not None:
        ticket.resolution_notes = resolution_notes
    session.add(ticket)
    if not commit:
        session.flush()
        return ticket
    session.commit()
    session.refresh(ticket)
    if entered_done and project is not None:
        schedule(tasks, session, project, EVENT_TICKET_DONE, ticket)
    return ticket
