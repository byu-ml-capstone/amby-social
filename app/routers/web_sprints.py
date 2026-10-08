from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlmodel import Session, select

from app.auth import current_user, project_owner, project_reader, verify_csrf
from app.config import settings
from app.db import get_session
from app.github import (
    AvailableRepository,
    GitHubClient,
    consume_github_state,
    github_is_configured,
    issue_github_state,
    read_github_state,
)
from app.github_sync import (
    disconnect_project_github,
    save_project_repositories,
    sync_open_pull_requests,
)
from app.gmail import gmail_is_configured
from app.models import (
    GitHubConnectState,
    GitHubInstallation,
    GmailConnection,
    GmailConnectionStatus,
    Priority,
    Project,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    ProjectMember,
    Role,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketStatus,
    TicketType,
    User,
    WebhookType,
    utcnow,
)
from app.routers.web import render
from app.schemas import SprintCreate
from app.services import (
    add_project_member,
    chat_webhooks,
    close_sprint,
    create_sprint,
    delete_project,
    disconnect_chat_webhook,
    project_members,
    remove_project_member,
    set_chat_webhook,
    start_sprint,
    update_project,
    update_project_member,
    update_ticket,
)

router = APIRouter(tags=["web"])
_TICKET_IDS_FORM = Form(...)
_MEMBER_ROLE_FORM = Form(Role.MEMBER)
_REQUIRED_ROLE_FORM = Form(...)
_REPOSITORY_IDS_FORM = Form(default=[])
_GITHUB_ERRORS = {
    "installation_cancelled": "GitHub installation was cancelled.",
    "oauth_denied": "GitHub authorization was denied.",
    "verification_failed": "GitHub could not verify that installation.",
}

_GMAIL_ERRORS = {
    "oauth_denied": "Gmail authorization was denied.",
    "verification_failed": "Gmail could not be connected. Try again.",
}


def _settings(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    member: ProjectMember,
    *,
    error: str | None = None,
    values: dict | None = None,
    status_code: int = status.HTTP_200_OK,
    saved: bool = False,
    github_selection_state: str | None = None,
    github_available_repositories: list[AvailableRepository] | None = None,
    gmail_available_labels: list[dict] | None = None,
) -> Response:
    github_repositories = session.exec(
        select(ProjectGitHubRepository)
        .where(ProjectGitHubRepository.project_id == project.id)
        .order_by(ProjectGitHubRepository.full_name)
    ).all()
    github_active_repositories = [row for row in github_repositories if row.active]
    if any(not row.active and row.disconnected_at is None for row in github_repositories):
        github_status = "Connection needs attention"
    elif github_active_repositories:
        github_status = "Connected"
    elif github_repositories:
        github_status = "Disconnected"
    else:
        github_status = "Not connected"
    github_account_login = None
    binding = session.get(ProjectGitHubConnection, project.id)
    if binding is not None and member.role is Role.OWNER:
        installation = session.get(GitHubInstallation, binding.installation_id)
        github_account_login = installation.account_login if installation else None
    gmail_connection = session.exec(
        select(GmailConnection).where(GmailConnection.project_id == project.id)
    ).first()
    if gmail_connection is None:
        gmail_status = "Not connected"
    elif gmail_connection.status == GmailConnectionStatus.NEEDS_REAUTH:
        gmail_status = "Reconnect required"
    else:
        gmail_status = "Connected"
    is_owner = member.role is Role.OWNER
    return render(
        request,
        "project_settings.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "settings",
            "members": project_members(session, project.id),
            "chat_integrations": {
                webhook.provider.value: True for webhook in chat_webhooks(session, project.id)
            },
            "error": error,
            "saved": saved,
            "values": values or {},
            "github_has_repositories": bool(github_repositories),
            "github_repository_names": [row.full_name for row in github_active_repositories],
            "github_status": github_status,
            "github_account_login": github_account_login,
            "github_configured": github_is_configured() if member.role is Role.OWNER else None,
            "github_selection_state": github_selection_state,
            "github_available_repositories": (
                (github_available_repositories or []) if member.role is Role.OWNER else []
            ),
            "gmail_status": gmail_status,
            "gmail_connection": gmail_connection if is_owner else None,
            "gmail_configured": gmail_is_configured() if is_owner else None,
            "gmail_available_labels": gmail_available_labels if is_owner else None,
            "ticket_types": TicketType,
            "priorities": Priority,
        },
        status_code=status_code,
        session=session,
    )


def _backlog(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    member: ProjectMember,
    status_code: int = status.HTTP_200_OK,
    q: str = "",
    priority: Priority | None = None,
    type_filter: TicketType | None = None,
    **context,
) -> Response:
    planning_sprint = session.exec(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == SprintStatus.PLANNING,
        )
    ).first()
    filters = [Ticket.project_id == project.id, Ticket.sprint_id.is_(None)]
    if q:
        filters.append(Ticket.title.ilike(f"%{q.strip()}%"))
    if priority:
        filters.append(Ticket.priority == priority)
    if type_filter:
        filters.append(Ticket.type == type_filter)
    tickets = session.exec(
        select(Ticket).where(*filters).order_by(Ticket.backlog_rank.desc())
    ).all()
    planning_tickets = (
        session.exec(select(Ticket).where(Ticket.sprint_id == planning_sprint.id)).all()
        if planning_sprint
        else []
    )
    recent_completed = session.exec(
        select(Sprint.completed_points)
        .where(Sprint.project_id == project.id, Sprint.status == SprintStatus.CLOSED)
        .order_by(Sprint.end_date.desc())
        .limit(3)
    ).all()
    recent_velocity = (
        round(sum(points or 0 for points in recent_completed) / len(recent_completed))
        if recent_completed
        else None
    )
    return render(
        request,
        "backlog.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "backlog",
            "planning_sprint": planning_sprint,
            "planning_tickets": planning_tickets,
            "planning_ticket_count": len(planning_tickets),
            "planning_points": sum(ticket.story_points or 0 for ticket in planning_tickets),
            "planning_unestimated_count": sum(
                ticket.story_points is None for ticket in planning_tickets
            ),
            "recent_velocity": recent_velocity,
            "tickets": tickets,
            "q": q,
            "priority_filter": priority,
            "type_filter": type_filter,
            "priorities": Priority,
            "ticket_types": TicketType,
            "sprint": None,
            "destination_sprints": session.exec(
                select(Sprint)
                .where(
                    Sprint.project_id == project.id,
                    Sprint.status.in_((SprintStatus.ACTIVE, SprintStatus.PLANNING)),
                )
                .order_by(Sprint.start_date)
            ).all(),
            "values": {},
            "field_errors": {},
            "error": None,
            **context,
        },
        status_code=status_code,
        session=session,
    )


def _project_sprint(session: Session, project: Project, sprint_id: str) -> Sprint:
    sprint = session.exec(
        select(Sprint).where(Sprint.id == sprint_id, Sprint.project_id == project.id)
    ).first()
    if sprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")
    return sprint


def _history_rows(session: Session, sprint_ids: list[str]):
    if not sprint_ids:
        return []
    return session.exec(
        select(SprintTicketHistory, Ticket)
        .join(Ticket, Ticket.id == SprintTicketHistory.ticket_id)
        .where(SprintTicketHistory.sprint_id.in_(sprint_ids))
        .order_by(Ticket.ticket_number)
    ).all()


def _history_delay_total(sprint: Sprint, rows: list[tuple[SprintTicketHistory, Ticket]]) -> int:
    return sum(
        max(0, (sprint.end_date - ticket.first_sprint_entered_at.date()).days)
        for history, ticket in rows
        if not history.was_completed and ticket.first_sprint_entered_at
    )


def _close_preview(
    request: Request,
    session: Session,
    user: User,
    project: Project,
    sprint: Sprint,
    *,
    error: str | None = None,
    selected_next_sprint_id: str = "",
    status_code: int = status.HTTP_200_OK,
) -> Response:
    tickets = session.exec(select(Ticket).where(Ticket.sprint_id == sprint.id)).all()
    planning_sprints = session.exec(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == SprintStatus.PLANNING,
        )
    ).all()
    return render(
        request,
        "partials/sprint_close.html",
        {
            "user": user,
            "project": project,
            "sprint": sprint,
            "planning_sprints": planning_sprints,
            "completed_points": sum(
                ticket.story_points or 0 for ticket in tickets if ticket.status is TicketStatus.DONE
            ),
            "unfinished_count": sum(ticket.status is not TicketStatus.DONE for ticket in tickets),
            "error": error,
            "selected_next_sprint_id": selected_next_sprint_id,
        },
        status_code=status_code,
    )


@router.get("/projects/{slug}/backlog")
def backlog(
    request: Request,
    q: str = "",
    priority: Priority | None = None,
    type: TicketType | None = None,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    return _backlog(
        request, session, user, project, member, q=q, priority=priority, type_filter=type
    )


@router.post("/projects/{slug}/backlog/{ticket_id}/move", dependencies=[Depends(verify_csrf)])
def move_backlog_ticket(
    ticket_id: str,
    direction: str = Form(...),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    tickets = session.exec(
        select(Ticket)
        .where(Ticket.project_id == project.id, Ticket.sprint_id.is_(None))
        .order_by(Ticket.backlog_rank.desc())
    ).all()
    index = next((i for i, ticket in enumerate(tickets) if ticket.id == ticket_id), None)
    offset = -1 if direction == "up" else 1 if direction == "down" else 0
    if index is None or not offset or not 0 <= index + offset < len(tickets):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Cannot move ticket")
    other = tickets[index + offset]
    tickets[index].backlog_rank, other.backlog_rank = (
        other.backlog_rank,
        tickets[index].backlog_rank,
    )
    session.add_all((tickets[index], other))
    session.commit()
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/projects/{slug}/settings")
def project_settings(
    request: Request,
    saved: bool = False,
    github_error: str | None = None,
    gmail_error: str | None = None,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    return _settings(
        request,
        session,
        user,
        project,
        member,
        saved=saved,
        error=_GITHUB_ERRORS.get(github_error or "") or _GMAIL_ERRORS.get(gmail_error or ""),
    )


@router.post("/projects/{slug}/settings/project", dependencies=[Depends(verify_csrf)])
def update_project_settings(
    request: Request,
    name: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    values = {"name": name}
    try:
        update_project(session, project, name=name)
    except HTTPException as exc:
        return _settings(
            request,
            session,
            user,
            project,
            member,
            error=exc.detail,
            values=values,
            status_code=exc.status_code,
        )
    return RedirectResponse(
        f"/projects/{project.slug}/settings?saved=1", status_code=status.HTTP_303_SEE_OTHER
    )


def _chat_provider(provider: str) -> WebhookType:
    if provider not in {"SLACK", "TEAMS", "DISCORD"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat provider not found")
    return WebhookType(provider)


@router.post(
    "/projects/{slug}/settings/integrations/{provider}",
    dependencies=[Depends(verify_csrf)],
)
def connect_chat_integration(
    provider: str,
    request: Request,
    url: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    try:
        set_chat_webhook(session, project, _chat_provider(provider), url)
    except HTTPException as exc:
        return _settings(
            request,
            session,
            user,
            project,
            member,
            error=exc.detail,
            status_code=exc.status_code,
        )
    return RedirectResponse(
        f"/projects/{project.slug}/settings", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/projects/{slug}/settings/integrations/github/disconnect",
    dependencies=[Depends(verify_csrf)],
)
def disconnect_github_integration(
    confirm: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    if confirm != "Disconnect GitHub":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "confirm must equal Disconnect GitHub",
        )
    disconnect_project_github(session, project)
    return RedirectResponse(
        f"/projects/{project.slug}/settings",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/projects/{slug}/settings/integrations/{provider}/disconnect",
    dependencies=[Depends(verify_csrf)],
)
def disconnect_chat_integration(
    provider: str,
    confirm: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    chat_provider = _chat_provider(provider)
    if confirm != "Disconnect":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "confirm must equal Disconnect")
    disconnect_chat_webhook(session, project, chat_provider)
    return RedirectResponse(
        f"/projects/{project.slug}/settings", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/projects/{slug}/settings/integrations/github/connect",
    dependencies=[Depends(verify_csrf)],
)
def start_github_connect(
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    if not github_is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "GitHub App is not configured")
    state = issue_github_state(session, project.id, user.id)
    query = urlencode({"state": state})
    return RedirectResponse(
        f"{settings.github_web_url}/apps/{settings.github_app_slug}/installations/new?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/integrations/github/setup")
def github_setup(
    installation_id: str | None = None,
    state: str = "",
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    connect_state = consume_github_state(session, state, user.id)
    project = session.get(Project, connect_state.project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if installation_id is None:
        return RedirectResponse(
            f"/projects/{project.slug}/settings?github_error=installation_cancelled",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not installation_id.isascii() or not installation_id.isdecimal():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid GitHub installation")
    pending_installation_id = int(installation_id)
    if pending_installation_id <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid GitHub installation")
    oauth_state = issue_github_state(
        session,
        project.id,
        user.id,
        pending_installation_id=pending_installation_id,
    )
    query = urlencode({"client_id": settings.github_client_id, "state": oauth_state})
    return RedirectResponse(
        f"{settings.github_web_url}/login/oauth/authorize?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/integrations/github/callback")
def github_oauth_callback(
    state: str = "",
    code: str | None = None,
    error: str | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    connect_state = consume_github_state(session, state, user.id)
    project = session.get(Project, connect_state.project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if error:
        reason = "oauth_denied" if error == "access_denied" else "verification_failed"
        return RedirectResponse(
            f"/projects/{project.slug}/settings?github_error={reason}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing GitHub OAuth code")
    installation_id = connect_state.pending_installation_id
    if installation_id is None or installation_id <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid GitHub installation")

    try:
        with GitHubClient() as github:
            user_token = github.exchange_code(code)
            github.verify_user_installation(user_token, installation_id)
            del user_token
            payload = github.installation(installation_id)
        payload_id = payload.get("id") if isinstance(payload, dict) else None
        account = payload.get("account") if isinstance(payload, dict) else None
        account_id = account.get("id") if isinstance(account, dict) else None
        account_login = account.get("login") if isinstance(account, dict) else None
        if (
            isinstance(payload_id, bool)
            or not isinstance(payload_id, int)
            or payload_id != installation_id
            or isinstance(account_id, bool)
            or not isinstance(account_id, int)
            or not isinstance(account_login, str)
            or not account_login.strip()
        ):
            raise ValueError("Invalid GitHub installation response")
    except (httpx.HTTPError, ValueError):
        return RedirectResponse(
            f"/projects/{project.slug}/settings?github_error=verification_failed",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    installation = session.exec(
        select(GitHubInstallation).where(
            GitHubInstallation.github_installation_id == installation_id
        )
    ).first()
    if installation is None:
        installation = GitHubInstallation(
            github_installation_id=installation_id,
            account_id=account_id,
            account_login=account_login.strip(),
            connected_by_id=user.id,
        )
    else:
        installation.account_id = account_id
        installation.account_login = account_login.strip()
        installation.connected_by_id = user.id
        installation.updated_at = utcnow()
    session.add(installation)
    session.commit()

    selection_state = issue_github_state(
        session,
        project.id,
        user.id,
        pending_installation_id=installation_id,
    )
    query = urlencode({"state": selection_state})
    return RedirectResponse(
        f"/projects/{project.slug}/settings/integrations/github/repositories?{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _github_selection_installation(
    session: Session,
    project: Project,
    connect_state: GitHubConnectState,
) -> GitHubInstallation:
    if connect_state.project_id != project.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "GitHub state belongs to another project")
    installation_id = connect_state.pending_installation_id
    if installation_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid GitHub installation")
    installation = session.exec(
        select(GitHubInstallation).where(
            GitHubInstallation.github_installation_id == installation_id
        )
    ).first()
    if installation is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid GitHub installation")
    return installation


def _sync_github_repositories(
    session: Session,
    connection_ids: list[str],
    github: GitHubClient,
) -> list[str]:
    failed = []
    for connection_id in connection_ids:
        try:
            with session.begin():
                connection = session.get(ProjectGitHubRepository, connection_id)
                if connection is None or not connection.active:
                    continue
                sync_open_pull_requests(session, connection, github)
        except httpx.HTTPError:
            failed.append(connection_id)
    return failed


@router.get("/projects/{slug}/settings/integrations/github/repositories")
def github_repository_selection(
    request: Request,
    state: str = "",
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    connect_state = read_github_state(session, state, user.id)
    installation = _github_selection_installation(session, project, connect_state)
    with GitHubClient() as github:
        repositories = github.repositories(installation.github_installation_id)
    return _settings(
        request,
        session,
        user,
        project,
        member,
        github_selection_state=state,
        github_available_repositories=repositories,
    )


@router.post(
    "/projects/{slug}/settings/integrations/github/repositories",
    dependencies=[Depends(verify_csrf)],
)
def save_github_repository_selection(
    request: Request,
    state: str = Form(""),
    repository_ids: list[str] = _REPOSITORY_IDS_FORM,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    connect_state = consume_github_state(session, state, user.id)
    installation = _github_selection_installation(session, project, connect_state)
    with GitHubClient() as github:
        available = github.repositories(installation.github_installation_id)
        if any(not value.isascii() or not value.isdecimal() for value in repository_ids):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Invalid repository selection",
            )
        try:
            selected_ids = {int(value) for value in repository_ids}
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Invalid repository selection",
            ) from None
        available_by_id = {repository.id: repository for repository in available}
        if not selected_ids.issubset(available_by_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Selected repository is not available",
            )
        selected = [repository for repository in available if repository.id in selected_ids]
        connections = save_project_repositories(session, project, installation, selected)
        connection_ids = [connection.id for connection in connections]
        session.rollback()
        failed = _sync_github_repositories(session, connection_ids, github)
    if failed:
        return _settings(
            request,
            session,
            user,
            project,
            member,
            error="GitHub sync failed for one or more repositories.",
        )
    return RedirectResponse(
        f"/projects/{project.slug}/settings",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/projects/{slug}/settings/integrations/github/sync",
    dependencies=[Depends(verify_csrf)],
)
def retry_github_sync(
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    connection_ids = list(
        session.exec(
            select(ProjectGitHubRepository.id)
            .where(
                ProjectGitHubRepository.project_id == project.id,
                ProjectGitHubRepository.active.is_(True),
            )
            .order_by(ProjectGitHubRepository.github_repository_id)
        ).all()
    )
    session.rollback()
    with GitHubClient() as github:
        failed = _sync_github_repositories(session, connection_ids, github)
    if failed:
        return _settings(
            request,
            session,
            user,
            project,
            member,
            error="GitHub sync failed for one or more repositories.",
        )
    return RedirectResponse(
        f"/projects/{project.slug}/settings?github_synced=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/projects/{slug}/settings/members", dependencies=[Depends(verify_csrf)])
def add_project_member_settings(
    request: Request,
    email: str = Form(""),
    role: Role = _MEMBER_ROLE_FORM,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    try:
        add_project_member(session, project, email, role)
    except HTTPException as exc:
        return _settings(
            request, session, user, project, member, error=exc.detail, status_code=exc.status_code
        )
    return RedirectResponse(
        f"/projects/{project.slug}/settings", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{slug}/settings/members/{user_id}", dependencies=[Depends(verify_csrf)])
def update_project_member_settings(
    user_id: str,
    request: Request,
    role: Role = _REQUIRED_ROLE_FORM,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    try:
        update_project_member(session, project, user_id, role)
    except HTTPException as exc:
        return _settings(
            request, session, user, project, member, error=exc.detail, status_code=exc.status_code
        )
    return RedirectResponse(
        f"/projects/{project.slug}/settings", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post(
    "/projects/{slug}/settings/members/{user_id}/remove", dependencies=[Depends(verify_csrf)]
)
def remove_project_member_settings(
    user_id: str,
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    try:
        remove_project_member(session, project, user_id)
    except HTTPException as exc:
        return _settings(
            request, session, user, project, member, error=exc.detail, status_code=exc.status_code
        )
    return RedirectResponse(
        "/dashboard" if user_id == user.id else f"/projects/{project.slug}/settings",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/projects/{slug}/settings/delete", dependencies=[Depends(verify_csrf)])
def delete_project_settings(
    request: Request,
    confirm: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    try:
        delete_project(session, project, confirm)
    except HTTPException as exc:
        return _settings(
            request, session, user, project, member, error=exc.detail, status_code=exc.status_code
        )
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{slug}/sprints")
def sprint_history(
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    sprints = session.exec(
        select(Sprint)
        .where(Sprint.project_id == project.id, Sprint.status == SprintStatus.CLOSED)
        .order_by(Sprint.closed_at.desc(), Sprint.end_date.desc())
    ).all()
    rows_by_sprint = {sprint.id: [] for sprint in sprints}
    for history, ticket in _history_rows(session, [sprint.id for sprint in sprints]):
        rows_by_sprint[history.sprint_id].append((history, ticket))
    summaries = [
        {
            "sprint": sprint,
            "rollover_count": sum(
                not history.was_completed for history, _ in rows_by_sprint[sprint.id]
            ),
            "completed_count": sum(
                history.was_completed for history, _ in rows_by_sprint[sprint.id]
            ),
            "delay_total": _history_delay_total(sprint, rows_by_sprint[sprint.id]),
        }
        for sprint in sprints
    ]
    return render(
        request,
        "sprint_history.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "history",
            "summaries": summaries,
            "velocity_points": [sprint.completed_points or 0 for sprint in reversed(sprints[:5])],
        },
        session=session,
    )


@router.get("/projects/{slug}/sprints/{sprint_id}")
def sprint_history_detail(
    sprint_id: str,
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_reader),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    sprint = _project_sprint(session, project, sprint_id)
    if sprint.status != SprintStatus.CLOSED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")
    rows = _history_rows(session, [sprint.id])
    return render(
        request,
        "sprint_history_detail.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "active_tab": "history",
            "sprint": sprint,
            "selected_sprint_id": sprint.id,
            "history": rows,
            "rollover_count": sum(not entry.was_completed for entry, _ in rows),
            "delay_total": _history_delay_total(sprint, rows),
        },
        session=session,
    )


@router.post("/projects/{slug}/sprints", dependencies=[Depends(verify_csrf)])
def create_sprint_form(
    request: Request,
    name: str = Form(""),
    goal: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    values = {"name": name, "goal": goal, "start_date": start_date, "end_date": end_date}
    try:
        sprint_data = SprintCreate(**values)
        create_sprint(session, project, **sprint_data.model_dump())
    except ValidationError as exc:
        field_errors = {
            error["loc"][-1] if error["loc"] else "form": error["msg"] for error in exc.errors()
        }
        return _backlog(
            request,
            session,
            user,
            project,
            member,
            values=values,
            field_errors=field_errors,
            sprint_form_open=True,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except HTTPException as exc:
        return _backlog(
            request,
            session,
            user,
            project,
            member,
            values=values,
            error=exc.detail,
            sprint_form_open=True,
            status_code=exc.status_code,
        )
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/projects/{slug}/sprints/{sprint_id}/start", dependencies=[Depends(verify_csrf)])
def start_sprint_form(
    sprint_id: str,
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    sprint = _project_sprint(session, project, sprint_id)
    try:
        start_sprint(session, sprint)
    except HTTPException as exc:
        return _backlog(
            request,
            session,
            user,
            project,
            member,
            start_error=exc.detail,
            start_form_open=True,
            status_code=exc.status_code,
        )
    return RedirectResponse(f"/projects/{project.slug}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{slug}/sprints/{sprint_id}/close")
def close_sprint_preview(
    sprint_id: str,
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    return _close_preview(
        request, session, user, project, _project_sprint(session, project, sprint_id)
    )


@router.post("/projects/{slug}/sprints/{sprint_id}/close", dependencies=[Depends(verify_csrf)])
def close_sprint_form(
    sprint_id: str,
    request: Request,
    next_sprint_id: str = Form(""),
    goal_achieved: str = Form(""),
    review_notes: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    sprint = _project_sprint(session, project, sprint_id)
    if not next_sprint_id:
        return _close_preview(
            request,
            session,
            user,
            project,
            sprint,
            error="Select a planning sprint",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    next_sprint = session.get(Sprint, next_sprint_id)
    if next_sprint is None:
        return _close_preview(
            request,
            session,
            user,
            project,
            sprint,
            error="Select a planning sprint",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        review = {}
        if goal_achieved in {"yes", "no"}:
            review["goal_achieved"] = goal_achieved == "yes"
        if review_notes:
            review["review_notes"] = review_notes
        close_sprint(session, sprint, next_sprint, **review)
    except HTTPException as exc:
        return _close_preview(
            request,
            session,
            user,
            project,
            sprint,
            error=exc.detail,
            selected_next_sprint_id=next_sprint_id,
            status_code=exc.status_code,
        )
    target = f"/projects/{project.slug}"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{slug}/sprints/{sprint_id}/tickets", dependencies=[Depends(verify_csrf)])
def assign_tickets_to_planning_sprint(
    sprint_id: str,
    ticket_ids: list[str] = _TICKET_IDS_FORM,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    sprint = session.exec(
        select(Sprint).where(
            Sprint.id == sprint_id,
            Sprint.project_id == project.id,
            Sprint.status == SprintStatus.PLANNING,
        )
    ).first()
    if sprint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sprint not found")
    tickets = session.exec(
        select(Ticket).where(
            Ticket.project_id == project.id,
            Ticket.sprint_id.is_(None),
            Ticket.id.in_(ticket_ids),
        )
    ).all()
    if len(tickets) != len(set(ticket_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    try:
        for ticket in tickets:
            update_ticket(session, ticket, project, sprint_id=sprint.id, commit=False)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return RedirectResponse(
        f"/projects/{project.slug}/backlog", status_code=status.HTTP_303_SEE_OTHER
    )
