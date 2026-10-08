from datetime import date

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import attachments
from app.auth import (
    DUMMY_HASH,
    SESSION_COOKIE,
    current_user,
    hash_password,
    issue_api_token,
    load_project_and_membership,
    make_csrf_token,
    optional_user,
    project_owner,
    project_reader,
    project_writer,
    verify_csrf,
    verify_password,
)
from app.db import get_session
from app.github_sync import branch_command, ticket_development, ticket_reference
from app.models import (
    ApiToken,
    GitHubConnectState,
    Priority,
    Project,
    ProjectMember,
    Role,
    Sprint,
    SprintStatus,
    Ticket,
    TicketAttachment,
    TicketComment,
    TicketStatus,
    TicketType,
    User,
    utcnow,
)
from app.notifications import schedule_comment_mention
from app.routers.api_auth import _set_session
from app.schemas import StatusUpdate, TicketUpdate
from app.services import (
    create_project,
    create_ticket,
    delete_ticket_record,
    register_user,
    set_status,
    update_ticket,
)

router = APIRouter(tags=["web"])

COLUMNS = (
    TicketStatus.BACKLOG,
    TicketStatus.SELECTED,
    TicketStatus.IN_PROGRESS,
    TicketStatus.DONE,
)
BOARD_TICKETS_PER_COLUMN = 200

# Reused to give the register form the same EmailStr format check RegisterRequest gives the
# JSON route, without hand-rolling a regex.
_email_adapter = TypeAdapter(EmailStr)

# Hoisted so `Form(...)` isn't called in an argument default (ruff B008).
_DEFAULT_TICKET_TYPE = Form(TicketType.TASK)
_STATUS_FORM_FIELD = Form(..., alias="status")
_TYPE_FILTER_QUERY = Query(None, alias="type")
_MENTION_IDS_FORM = Form(None)
_FILES_FORM = File(...)
_DUE_DATE_FORM = Form(None)


def render(
    request: Request,
    name: str,
    context: dict,
    status_code: int = 200,
    session: Session | None = None,
) -> Response:
    from app.main import templates

    user = context.get("user")
    shell_context = {}
    if session and user and not name.startswith("partials/"):
        shell_context = _shell_context(session, user)
        project = context.get("project")
        if project is not None:
            status_order = {
                SprintStatus.ACTIVE: 0,
                SprintStatus.PLANNING: 1,
                SprintStatus.CLOSED: 2,
            }
            shell_context["sprint_options"] = sorted(
                session.exec(select(Sprint).where(Sprint.project_id == project.id)).all(),
                key=lambda sprint: (status_order[sprint.status], sprint.start_date),
            )
            shell_context["current_sprint"] = next(
                (
                    sprint
                    for sprint in shell_context["sprint_options"]
                    if sprint.status in (SprintStatus.ACTIVE, SprintStatus.PLANNING)
                ),
                None,
            )
    context = {
        **shell_context,
        "request": request,
        "csrf_token": make_csrf_token(user.id) if user else "",
        **context,
    }
    return templates.TemplateResponse(request, name, context, status_code=status_code)


@router.get("/")
def index(user: User | None = Depends(optional_user)) -> Response:
    target = "/dashboard" if user else "/login"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login")
def login_page(request: Request, user: User | None = Depends(optional_user)) -> Response:
    if user:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html", {"user": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    user = session.exec(
        select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
    ).first()
    # Mirror api_auth.login exactly: run verify_password on a real hash even for an unknown
    # email, so bcrypt's cost lands on both branches. Short-circuiting on `user is None` (as
    # the earlier version of this route did) kept the message identical but not the timing --
    # a non-existent account would return in microseconds while a wrong password cost a full
    # bcrypt round, exactly the gap DUMMY_HASH exists to close.
    hashed = user.password_hash if user else DUMMY_HASH
    if not verify_password(password, hashed) or user is None:
        return render(
            request,
            "login.html",
            {"user": None, "error": "Invalid email or password"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user.id)
    return response


@router.get("/register")
def register_page(request: Request, user: User | None = Depends(optional_user)) -> Response:
    if user:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "register.html", {"user": None})


@router.post("/register")
def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    try:
        email = _email_adapter.validate_python(email)
    except ValidationError:
        return render(
            request,
            "register.html",
            {"user": None, "error": "Enter a valid email address"},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if len(password) < 8:
        return render(
            request,
            "register.html",
            {"user": None, "error": "Password must be at least 8 characters"},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    # hash_password (bcrypt) raises ValueError past 72 UTF-8 bytes; the JSON route catches this
    # in RegisterRequest's validator (app/schemas.py) before it ever reaches hash_password. This
    # form-based route bypasses that schema, so the same guard belongs here too -- otherwise an
    # over-length password crashes with a 500 instead of a normal re-rendered error.
    if len(password.encode("utf-8")) > 72:
        return render(
            request,
            "register.html",
            {"user": None, "error": "Password must be at most 72 bytes"},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        user = register_user(session, name=name, email=str(email), password=password)
    except HTTPException as exc:
        error = (
            "That email is already registered"
            if exc.status_code == status.HTTP_409_CONFLICT
            else exc.detail
        )
        return render(
            request,
            "register.html",
            {"user": None, "error": error},
            status_code=exc.status_code,
        )
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user.id)
    return response


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout_submit() -> Response:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def _user_settings(
    request: Request,
    session: Session,
    user: User,
    *,
    error: str | None = None,
    status_code: int = 200,
    issued_token: str | None = None,
    profile_saved: bool = False,
    password_saved: bool = False,
) -> Response:
    tokens = session.exec(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())
    ).all()
    return render(
        request,
        "user_settings.html",
        {
            "user": user,
            "tokens": tokens,
            "error": error,
            "issued_token": issued_token,
            "base_url": str(request.base_url).rstrip("/"),
            "profile_saved": profile_saved,
            "password_saved": password_saved,
            "account_settings": True,
        },
        status_code=status_code,
        session=session,
    )


@router.get("/settings")
def user_settings(
    request: Request,
    profile_saved: bool = False,
    password_saved: bool = False,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    return _user_settings(
        request,
        session,
        user,
        profile_saved=profile_saved,
        password_saved=password_saved,
    )


@router.post("/settings/profile", dependencies=[Depends(verify_csrf)])
def update_profile(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    name = name.strip()
    if not name or len(name) > 50:
        return _user_settings(
            request,
            session,
            user,
            error="Name must be between 1 and 50 characters",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        normalized_email = str(_email_adapter.validate_python(email)).lower()
    except ValidationError:
        return _user_settings(
            request,
            session,
            user,
            error="Enter a valid email address",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    existing = session.exec(
        select(User).where(User.email == normalized_email, User.id != user.id)
    ).first()
    if existing is not None:
        return _user_settings(
            request,
            session,
            user,
            error="That email is already registered",
            status_code=status.HTTP_409_CONFLICT,
        )
    user.name = name
    user.email = normalized_email
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return _user_settings(
            request,
            session,
            user,
            error="That email is already registered",
            status_code=status.HTTP_409_CONFLICT,
        )
    return RedirectResponse("/settings?profile_saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/password", dependencies=[Depends(verify_csrf)])
def update_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    if not verify_password(current_password, user.password_hash):
        return _user_settings(
            request,
            session,
            user,
            error="Current password is incorrect",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if new_password != confirm_password:
        return _user_settings(
            request,
            session,
            user,
            error="New passwords do not match",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if len(new_password) < 8:
        return _user_settings(
            request,
            session,
            user,
            error="Password must be at least 8 characters",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if len(new_password.encode("utf-8")) > 72:
        return _user_settings(
            request,
            session,
            user,
            error="Password must be at most 72 bytes",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    user.password_hash = hash_password(new_password)
    session.add(user)
    session.commit()
    return RedirectResponse("/settings?password_saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/tokens", dependencies=[Depends(verify_csrf)])
def create_user_token(
    request: Request,
    label: str = Form(""),
    scope: str = Form("read"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    label = label.strip()
    if not label or len(label) > 100:
        return _user_settings(
            request,
            session,
            user,
            error="Token label must be between 1 and 100 characters",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if scope not in {"read", "write"}:
        return _user_settings(
            request,
            session,
            user,
            error="Token scope must be read or write",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    _, plaintext = issue_api_token(session, user, label, scope)
    return _user_settings(
        request,
        session,
        user,
        issued_token=plaintext,
        status_code=status.HTTP_201_CREATED,
    )


@router.post("/settings/tokens/{token_id}/revoke", dependencies=[Depends(verify_csrf)])
def revoke_user_token(
    token_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    token = session.exec(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
    ).first()
    if token is not None and token.revoked_at is None:
        token.revoked_at = utcnow()
        session.add(token)
        session.commit()
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/delete", dependencies=[Depends(verify_csrf)])
def delete_user_account(
    request: Request,
    current_password: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    if not verify_password(current_password, user.password_hash):
        return _user_settings(
            request,
            session,
            user,
            error="Current password is incorrect",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    owned_memberships = session.exec(
        select(ProjectMember).where(
            ProjectMember.user_id == user.id, ProjectMember.role == Role.OWNER
        )
    ).all()
    # ponytail: one lookup per owned project; use NOT EXISTS if ownership counts grow.
    for membership in owned_memberships:
        other_owner = session.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == membership.project_id,
                ProjectMember.user_id != user.id,
                ProjectMember.role == Role.OWNER,
            )
        ).first()
        if other_owner is None:
            project = session.get(Project, membership.project_id)
            return _user_settings(
                request,
                session,
                user,
                error=f"Add another owner or delete {project.name} before deleting your account",
                status_code=status.HTTP_409_CONFLICT,
            )
    for row in session.exec(select(ApiToken).where(ApiToken.user_id == user.id)).all():
        session.delete(row)
    for row in session.exec(select(ProjectMember).where(ProjectMember.user_id == user.id)).all():
        session.delete(row)
    for row in session.exec(
        select(GitHubConnectState).where(GitHubConnectState.user_id == user.id)
    ).all():
        session.delete(row)
    user.name = "Deleted user"
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.password_hash = "!"
    user.deleted_at = utcnow()
    session.add(user)
    session.commit()
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def _shell_context(session: Session, user: User) -> dict:
    rows = session.exec(
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.created_at.desc())
    ).all()
    return {
        "projects": [project for project, _ in rows],
        "roles": {project.id: member.role.value for project, member in rows},
    }


def _project_ticket(session: Session, project: Project, ticket_number: int) -> Ticket:
    ticket = session.exec(
        select(Ticket).where(Ticket.project_id == project.id, Ticket.ticket_number == ticket_number)
    ).first()
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket


def _comment_members(session: Session, project: Project, mention_ids: list[str]) -> list[User]:
    mention_ids = list(dict.fromkeys(mention_ids))
    if not mention_ids:
        return []
    users = session.exec(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project.id, User.id.in_(mention_ids))
    ).all()
    if len(users) != len(mention_ids):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Mentions must be project members"
        )
    by_id = {user.id: user for user in users}
    return [by_id[user_id] for user_id in mention_ids]


def _comment_input(
    session: Session, project: Project, body: str, mention_ids: list[str]
) -> tuple[str, list[User]]:
    body = body.strip()
    if not body:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Comment cannot be empty")
    if len(body) > 5000:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Comment must be at most 5000 characters",
        )
    return body, _comment_members(session, project, mention_ids)


def _project_comment(session: Session, ticket: Ticket, comment_id: str) -> TicketComment:
    comment = session.get(TicketComment, comment_id)
    if comment is None or comment.ticket_id != ticket.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    return comment


def _ticket_comments(
    session: Session, project: Project, ticket: Ticket, viewer: User
) -> tuple[list[dict], list[User]]:
    members = session.exec(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project.id)
        .order_by(User.name)
    ).all()
    viewer_membership = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == viewer.id,
        )
    ).one()
    is_owner = viewer_membership.role == Role.OWNER
    names = {member.id: member.name or member.email for member in members}
    rows = session.exec(
        select(TicketComment, User)
        .join(User, User.id == TicketComment.author_id)
        .where(TicketComment.ticket_id == ticket.id)
        .order_by(TicketComment.created_at)
    ).all()
    return (
        [
            {
                "comment": comment,
                "author_name": author.name or author.email,
                "mention_names": [
                    names[user_id]
                    for user_id in comment.mentioned_user_ids
                    if user_id in names and f"@{names[user_id]}" not in comment.body
                ],
                "can_edit": comment.author_id == viewer.id,
                "can_delete": comment.author_id == viewer.id or is_owner,
            }
            for comment, author in rows
        ],
        members,
    )


def _render_ticket_comments(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    ticket: Ticket,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    comments, members = _ticket_comments(session, project, ticket, user)
    return render(
        request,
        "partials/ticket_comments.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            "comments": comments,
            "members": members,
            "comment_error": error,
        },
        status_code=status_code,
    )


def _attachment_context(
    session: Session, project: Project, ticket: Ticket, viewer: User, error: str | None = None
) -> dict:
    role = session.exec(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == viewer.id
        )
    ).one()
    rows = session.exec(
        select(TicketAttachment)
        .where(TicketAttachment.ticket_id == ticket.id)
        .order_by(TicketAttachment.created_at)
    ).all()
    items = [
        {
            "attachment": row,
            "url": f"/projects/{project.slug}/attachments/{row.id}",
            "delete_url": (
                f"/projects/{project.slug}/tickets/{ticket.ticket_number}"
                f"/attachments/{row.id}/delete"
            ),
            "can_delete": row.uploaded_by == viewer.id or role == Role.OWNER,
        }
        for row in rows
    ]
    return {
        "attachment_images": [item for item in items if item["attachment"].is_image],
        "attachment_files": [item for item in items if not item["attachment"].is_image],
        "attachment_error": error,
        "attachment_max_bytes": attachments.ATTACHMENT_MAX_BYTES,
    }


def _render_ticket_attachments(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    ticket: Ticket,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return render(
        request,
        "partials/ticket_attachments.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            **_attachment_context(session, project, ticket, user, error),
        },
        status_code=status_code,
    )


def _ticket_detail(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    ticket: Ticket,
    *,
    error: str | None = None,
    comment_error: str | None = None,
    status_code: int = status.HTTP_200_OK,
    page: bool = False,
    submitted_values: dict[str, str] | None = None,
    saved: bool = False,
) -> Response:
    comments, members = _ticket_comments(session, project, ticket, user)
    reference = ticket_reference(project, ticket)
    command = branch_command(project, ticket)
    development = ticket_development(session, ticket)
    sprints = session.exec(
        select(Sprint)
        .where(
            Sprint.project_id == project.id,
            Sprint.status.in_((SprintStatus.ACTIVE, SprintStatus.PLANNING)),
        )
        .order_by(Sprint.start_date)
    ).all()
    form_values = {
        "title": ticket.title,
        "description": ticket.description,
        "type": ticket.type.value,
        "priority": ticket.priority.value,
        "story_points": str(ticket.story_points or ""),
        "due_date": ticket.due_date.isoformat() if ticket.due_date else "",
        "assignee_id": ticket.assignee_id or "",
        "status": ticket.status.value,
        "resolution_notes": ticket.resolution_notes or "",
        "blocked_reason": ticket.blocked_reason or "",
        "sprint_id": ticket.sprint_id or "",
    }
    if submitted_values is not None:
        form_values.update(submitted_values)
    return render(
        request,
        "ticket_detail.html" if page else "partials/ticket_detail.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            "ticket_reference": reference,
            "branch_command": command,
            "development_rows": development,
            "members": members,
            "comments": comments,
            "comment_error": comment_error,
            **_attachment_context(session, project, ticket, user),
            "sprints": sprints,
            "ticket_types": TicketType,
            "priorities": Priority,
            "columns": COLUMNS,
            "error": error,
            "description_editing": error is not None,
            "saved": saved,
            "form_values": form_values,
            "active_tab": "board",
            "selected_sprint_id": ticket.sprint_id,
            "detail_drawer": not page,
        },
        status_code=status_code,
        session=session if page else None,
    )


def _dashboard_context(session: Session, user: User) -> dict:
    today = date.today()
    open_rows = session.exec(
        select(Ticket, Project)
        .join(Project, Ticket.project_id == Project.id)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Ticket.assignee_id == user.id,
            ProjectMember.user_id == user.id,
            Ticket.status != TicketStatus.DONE,
        )
        .order_by(Ticket.due_date.is_(None), Ticket.due_date, Ticket.created_at.desc())
        .limit(100)
    ).all()
    recently_completed_tickets = session.exec(
        select(Ticket, Project)
        .join(Project, Ticket.project_id == Project.id)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Ticket.assignee_id == user.id,
            ProjectMember.user_id == user.id,
            Ticket.status == TicketStatus.DONE,
        )
        .order_by(Ticket.completed_at.desc())
        .limit(10)
    ).all()
    overdue_tickets = []
    in_progress_tickets = []
    next_tickets = []
    for row in open_rows:
        ticket = row[0]
        if ticket.due_date is not None and ticket.due_date < today:
            overdue_tickets.append(row)
        elif ticket.status is TicketStatus.IN_PROGRESS:
            in_progress_tickets.append(row)
        else:
            next_tickets.append(row)
    return {
        "user": user,
        "today": today,
        "overdue_tickets": overdue_tickets,
        "in_progress_tickets": in_progress_tickets,
        "next_tickets": next_tickets,
        "recently_completed_tickets": recently_completed_tickets,
    }


@router.get("/dashboard")
def dashboard(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "dashboard.html", _dashboard_context(session, user), session=session)


@router.post("/projects", dependencies=[Depends(verify_csrf)])
def create_project_form(
    request: Request,
    name: str = Form(...),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    # create_project (app/services.py) is the same function the JSON route
    # (POST /api/v1/projects) calls -- slug derivation, the name limits
    # (Ruling R36, since this form bypasses ProjectCreate entirely), the slug
    # collision check, and the creator-becomes-OWNER membership insert all
    # live there exactly once.
    try:
        project = create_project(session, name, user)
    except HTTPException as exc:
        context = _dashboard_context(session, user)
        context.update({"error": exc.detail, "name": name})
        return render(
            request,
            "dashboard.html",
            context,
            status_code=exc.status_code,
            session=session,
        )
    return RedirectResponse(f"/projects/{project.slug}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{slug}")
def board(
    slug: str,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
    sprint_id: str | None = None,
    mine: bool = False,
    assignee_id: str | None = None,
    type_filter: str | None = _TYPE_FILTER_QUERY,
    priority: str | None = None,
) -> Response:
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    # load_project_and_membership already 404s when the project itself is missing; a non-member
    # must get the same 404 rather than learn the project exists, so check member too.
    project, member = load_project_and_membership(slug, user, session)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")

    try:
        type_filter = TicketType(type_filter) if type_filter else None
        priority = Priority(priority) if priority else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid board filter") from exc

    sprint = session.exec(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == SprintStatus.ACTIVE,
        )
    ).first()
    if sprint is None:
        sprint = session.exec(
            select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.status == SprintStatus.PLANNING,
            )
        ).first()
    if sprint is None:
        return RedirectResponse(
            f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
        )
    if sprint_id:
        sprint = session.exec(
            select(Sprint).where(
                Sprint.id == sprint_id,
                Sprint.project_id == project.id,
                Sprint.status.in_((SprintStatus.ACTIVE, SprintStatus.PLANNING)),
            )
        ).first()
        if sprint is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")

    sprint_summary = None
    if sprint.status is SprintStatus.ACTIVE:
        summary_tickets = session.exec(select(Ticket).where(Ticket.sprint_id == sprint.id)).all()
        done = [ticket for ticket in summary_tickets if ticket.status is TicketStatus.DONE]
        total = len(summary_tickets)
        sprint_summary = {
            "completed_count": len(done),
            "total_count": total,
            "completion_percent": round(len(done) * 100 / total) if total else 0,
            "completed_points": sum(ticket.story_points or 0 for ticket in done),
            "total_points": sum(ticket.story_points or 0 for ticket in summary_tickets),
            "rollover_count": sum(ticket.rollover_count > 0 for ticket in summary_tickets),
            "at_risk_count": sum(
                bool(ticket.blocked_reason)
                or (ticket.due_date is not None and ticket.due_date < date.today())
                or ticket.rollover_count >= 2
                or (ticket.delayed_days or 0) >= 14
                for ticket in summary_tickets
                if ticket.status is not TicketStatus.DONE
            ),
        }

    filters = [Ticket.project_id == project.id, Ticket.sprint_id == sprint.id]
    if mine:
        filters.append(Ticket.assignee_id == user.id)
    if assignee_id:
        filters.append(Ticket.assignee_id == assignee_id)
    if type_filter:
        filters.append(Ticket.type == type_filter)
    if priority:
        filters.append(Ticket.priority == priority)

    tickets_by_status = {}
    truncated_columns = set()
    for column in COLUMNS:
        tickets = session.exec(
            select(Ticket)
            .where(*filters, Ticket.status == column)
            .order_by(Ticket.ticket_number.desc())
            .limit(BOARD_TICKETS_PER_COLUMN + 1)
        ).all()
        if len(tickets) > BOARD_TICKETS_PER_COLUMN:
            truncated_columns.add(column.value)
        tickets_by_status[column.value] = tickets[:BOARD_TICKETS_PER_COLUMN]
    members = session.exec(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project.id)
        .order_by(User.name)
    ).all()
    return render(
        request,
        "board.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "board",
            "sprint": sprint,
            "sprint_summary": sprint_summary,
            "selected_sprint_id": sprint.id,
            "columns": COLUMNS,
            "tickets_by_status": tickets_by_status,
            "truncated_columns": truncated_columns,
            "mine": mine,
            "assignee_id": assignee_id,
            "type_filter": type_filter,
            "priority_filter": priority,
            "ticket_types": TicketType,
            "priorities": Priority,
            "members": members,
            "assignee_names": {member.id: member.name or member.email for member in members},
            "destination_sprints": session.exec(
                select(Sprint)
                .where(
                    Sprint.project_id == project.id,
                    Sprint.status.in_((SprintStatus.ACTIVE, SprintStatus.PLANNING)),
                )
                .order_by(Sprint.start_date)
            ).all(),
            "today": date.today(),
        },
        session=session,
    )


@router.get("/projects/{slug}/tickets/{ticket_number}")
def ticket_detail(
    slug: str,
    ticket_number: int,
    request: Request,
    card: bool = False,
    saved: bool = False,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    if card:
        assignee_names = {}
        if ticket.assignee_id:
            assignee = session.get(User, ticket.assignee_id)
            if assignee:
                assignee_names[assignee.id] = assignee.name or assignee.email
        return render(
            request,
            "partials/ticket_card.html",
            {
                "user": user,
                "project": project,
                "ticket": ticket,
                "assignee_names": assignee_names,
                "today": date.today(),
            },
        )
    return _ticket_detail(
        request,
        session,
        user,
        project,
        ticket,
        page=not request.headers.get("HX-Request"),
        saved=saved,
    )


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/comments",
    dependencies=[Depends(verify_csrf)],
)
def create_ticket_comment_form(
    slug: str,
    ticket_number: int,
    request: Request,
    tasks: BackgroundTasks,
    body: str = Form(""),
    mention_ids: list[str] | None = _MENTION_IDS_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    try:
        body, mentioned_users = _comment_input(session, project, body, mention_ids or [])
    except HTTPException as exc:
        if request.headers.get("HX-Request") != "true":
            return _ticket_detail(
                request,
                session,
                user,
                project,
                ticket,
                page=True,
                comment_error=exc.detail,
                status_code=exc.status_code,
            )
        return _render_ticket_comments(
            request,
            session,
            user,
            project,
            ticket,
            error=exc.detail,
            status_code=exc.status_code,
        )
    comment = TicketComment(
        ticket_id=ticket.id,
        author_id=user.id,
        body=body,
        mentioned_user_ids=[mentioned.id for mentioned in mentioned_users],
    )
    session.add(comment)
    session.commit()
    schedule_comment_mention(tasks, session, project, ticket, user, mentioned_users, body)
    if request.headers.get("HX-Request") == "true":
        return _render_ticket_comments(request, session, user, project, ticket)
    return RedirectResponse(
        f"/projects/{project.slug}/tickets/{ticket.ticket_number}#ticket-comments",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/comments/{comment_id}/edit",
    dependencies=[Depends(verify_csrf)],
)
def edit_ticket_comment_form(
    slug: str,
    ticket_number: int,
    comment_id: str,
    request: Request,
    tasks: BackgroundTasks,
    body: str = Form(""),
    mention_ids: list[str] | None = _MENTION_IDS_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    comment = _project_comment(session, ticket, comment_id)
    if comment.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the author can edit this comment")
    try:
        body, mentioned_users = _comment_input(session, project, body, mention_ids or [])
    except HTTPException as exc:
        if request.headers.get("HX-Request") != "true":
            return _ticket_detail(
                request,
                session,
                user,
                project,
                ticket,
                page=True,
                comment_error=exc.detail,
                status_code=exc.status_code,
            )
        return _render_ticket_comments(
            request,
            session,
            user,
            project,
            ticket,
            error=exc.detail,
            status_code=exc.status_code,
        )
    previous_mentions = set(comment.mentioned_user_ids)
    comment.body = body
    comment.mentioned_user_ids = [mentioned.id for mentioned in mentioned_users]
    comment.updated_at = utcnow()
    session.add(comment)
    session.commit()
    new_mentions = [
        mentioned for mentioned in mentioned_users if mentioned.id not in previous_mentions
    ]
    schedule_comment_mention(tasks, session, project, ticket, user, new_mentions, body)
    if request.headers.get("HX-Request") == "true":
        return _render_ticket_comments(request, session, user, project, ticket)
    return RedirectResponse(
        f"/projects/{project.slug}/tickets/{ticket.ticket_number}#ticket-comments",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/comments/{comment_id}/delete",
    dependencies=[Depends(verify_csrf)],
)
def delete_ticket_comment_form(
    slug: str,
    ticket_number: int,
    comment_id: str,
    request: Request,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, membership = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    comment = _project_comment(session, ticket, comment_id)
    if comment.author_id != user.id and membership.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete this comment")
    session.delete(comment)
    session.commit()
    if request.headers.get("HX-Request") == "true":
        return _render_ticket_comments(request, session, user, project, ticket)
    return RedirectResponse(
        f"/projects/{project.slug}/tickets/{ticket.ticket_number}#ticket-comments",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/attachments",
    dependencies=[Depends(verify_csrf)],
)
def upload_ticket_attachments(
    slug: str,
    ticket_number: int,
    request: Request,
    files: list[UploadFile] = _FILES_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    uploads = (
        (
            upload.filename,
            upload.content_type,
            upload.file.read(attachments.ATTACHMENT_MAX_BYTES + 1),
        )
        for upload in files
    )
    try:
        rows = attachments.attach_uploads(session, ticket, user.id, uploads)
    except HTTPException as exc:
        session.rollback()
        return _render_ticket_attachments(
            request, session, user, project, ticket, error=exc.detail, status_code=exc.status_code
        )
    try:
        session.commit()
    except Exception:
        session.rollback()
        attachments.discard(rows)
        raise
    return _render_ticket_attachments(request, session, user, project, ticket)


@router.get("/projects/{slug}/attachments/{attachment_id}")
def ticket_attachment_file(
    slug: str,
    attachment_id: str,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_reader),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    row = session.get(TicketAttachment, attachment_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return attachments.file_response(row)


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/attachments/{attachment_id}/delete",
    dependencies=[Depends(verify_csrf)],
)
def delete_ticket_attachment(
    slug: str,
    ticket_number: int,
    attachment_id: str,
    request: Request,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, membership = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    row = session.get(TicketAttachment, attachment_id)
    if row is None or row.ticket_id != ticket.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    if row.uploaded_by != user.id and membership.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete this attachment")
    session.delete(row)
    session.commit()
    attachments.delete(project.id, attachment_id)
    return _render_ticket_attachments(request, session, user, project, ticket)


@router.post("/projects/{slug}/tickets/{ticket_number}", dependencies=[Depends(verify_csrf)])
def update_ticket_form(
    slug: str,
    ticket_number: int,
    request: Request,
    tasks: BackgroundTasks,
    title: str = Form(""),
    description: str = Form(""),
    type: str = Form(""),
    priority: str = Form(""),
    story_points: str = Form(""),
    due_date: date | None = _DUE_DATE_FORM,
    assignee_id: str = Form(""),
    status_value: str = Form("", alias="status"),
    resolution_notes: str = Form(""),
    blocked_reason: str = Form(""),
    sprint_id: str = Form(""),
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    ticket = _project_ticket(session, project, ticket_number)
    is_hx = bool(request.headers.get("HX-Request"))
    submitted_values = {
        "title": title,
        "description": description,
        "type": type,
        "priority": priority,
        "story_points": story_points,
        "due_date": due_date.isoformat() if due_date else "",
        "assignee_id": assignee_id,
        "status": status_value,
        "resolution_notes": resolution_notes,
        "blocked_reason": blocked_reason,
        "sprint_id": sprint_id,
    }
    try:
        points = int(story_points) if story_points else None
        changes = TicketUpdate(
            title=title,
            description=description,
            type=type,
            priority=priority,
            story_points=points,
            due_date=due_date,
            assignee_id=assignee_id or None,
            resolution_notes=resolution_notes or None,
            blocked_reason=blocked_reason or None,
            sprint_id=sprint_id or None,
        ).model_dump(exclude_unset=True)
        status_update = StatusUpdate(status=status_value, resolution_notes=resolution_notes or None)
        changed_fields = [
            field for field, value in changes.items() if getattr(ticket, field) != value
        ]
        if ticket.status is not status_update.status:
            changed_fields.append("status")
        if changed_fields:
            meta = dict(ticket.meta)
            activity = list(meta.get("activity", []))
            activity.append(
                {
                    "at": utcnow().isoformat(),
                    "actor": user.name,
                    "summary": f"Updated: {', '.join(changed_fields)}",
                }
            )
            meta["activity"] = activity[-50:]
            changes["meta"] = meta
        update_ticket(session, ticket, project, commit=False, **changes)
        ticket = set_status(
            session,
            ticket,
            status_update.status,
            status_update.resolution_notes,
            project=project,
            tasks=tasks,
        )
    except (ValidationError, ValueError) as exc:
        error = (
            exc.errors()[0]["msg"]
            if isinstance(exc, ValidationError)
            else "points must be a number"
        )
        return _ticket_detail(
            request,
            session,
            user,
            project,
            ticket,
            error=error,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            page=not is_hx,
            submitted_values=submitted_values,
        )
    except HTTPException as exc:
        return _ticket_detail(
            request,
            session,
            user,
            project,
            ticket,
            error=exc.detail,
            status_code=exc.status_code,
            page=not is_hx,
            submitted_values=submitted_values,
        )
    if not is_hx:
        return RedirectResponse(
            f"/projects/{project.slug}/tickets/{ticket.ticket_number}?saved=true",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    response = _ticket_detail(request, session, user, project, ticket, saved=True)
    response.headers["HX-Refresh"] = "true"
    return response


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/delete",
    dependencies=[Depends(verify_csrf)],
)
def delete_ticket_form(
    request: Request,
    ticket_number: int,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    ticket = _project_ticket(session, project, ticket_number)
    try:
        delete_ticket_record(session, ticket)
    except HTTPException as exc:
        return Response(str(exc.detail), status_code=exc.status_code, media_type="text/plain")
    if request.headers.get("HX-Request"):
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.headers["HX-Refresh"] = "true"
        return response
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{slug}/tickets", dependencies=[Depends(verify_csrf)])
def create_ticket_form(
    request: Request,
    tasks: BackgroundTasks,
    title: str = Form(...),
    type: TicketType = _DEFAULT_TICKET_TYPE,
    description: str = Form(""),
    sprint_id: str | None = Form(None),
    due_date: date | None = _DUE_DATE_FORM,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    # create_ticket (app/services.py) is the same function the JSON route calls -- atomic
    # ticket-number allocation, title/description/story_points/meta limits (Ruling R24, since
    # this form bypasses any Pydantic schema), and the TICKET_CREATED notification all live
    # there exactly once.
    try:
        create_ticket(
            session,
            project,
            user,
            title=title,
            description=description,
            type=type,
            priority=Priority.MEDIUM,
            due_date=due_date,
            sprint_id=sprint_id,
            tasks=tasks,
        )
    except HTTPException as exc:
        # Not a page and not JSON: the modal's error slot renders whatever text comes back
        # (see partials/ticket_modal.html's htmx:after-request handler), so plain text is enough.
        return Response(str(exc.detail), status_code=exc.status_code, media_type="text/plain")
    if request.headers.get("HX-Request"):
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.headers["HX-Redirect"] = f"/projects/{project.slug}"
        return response
    return RedirectResponse(f"/projects/{project.slug}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{slug}/tickets/{ticket_number}/status", dependencies=[Depends(verify_csrf)])
def change_status_form(
    request: Request,
    ticket_number: int,
    tasks: BackgroundTasks,
    status_value: TicketStatus = _STATUS_FORM_FIELD,
    project_and_member: tuple[Project, ProjectMember] = Depends(project_writer),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = project_and_member
    # Board renders human-readable ticket_number, not id -- the one place in the
    # product a ticket is addressed this way. Numbers are project-scoped, so the
    # lookup filters on project_id *and* ticket_number together: number alone
    # would resolve across projects.
    ticket = _project_ticket(session, project, ticket_number)
    # set_status (app/services.py) owns the transition rules -- completed_at,
    # resolution_notes preservation, idempotency, and the TICKET_DONE
    # notification -- shared with the JSON route (app/routers/api_tickets.py).
    ticket = set_status(session, ticket, status_value, project=project, tasks=tasks)
    assignee_names = {}
    if ticket.assignee_id:
        assignee = session.get(User, ticket.assignee_id)
        if assignee:
            assignee_names[assignee.id] = assignee.name or assignee.email
    # Ruling R41: the card needs csrf_token to render its status control with a
    # working token, or the *next* status change on this page 403s.
    return render(
        request,
        "partials/ticket_card.html",
        {
            "user": user,
            "project": project,
            "ticket": ticket,
            "columns": COLUMNS,
            "assignee_names": assignee_names,
            "today": date.today(),
        },
    )
