"""Project settings pages: the project itself, members, and chat integrations."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.auth import current_user, project_owner, project_reader, verify_csrf
from app.db import get_session
from app.github import (
    AvailableRepository,
    github_is_configured,
)
from app.gmail import gmail_is_configured
from app.models import (
    GitHubInstallation,
    GmailConnection,
    GmailConnectionStatus,
    Priority,
    Project,
    ProjectGitHubConnection,
    ProjectGitHubRepository,
    ProjectMember,
    Role,
    TicketType,
    User,
    WebhookType,
)
from app.routers.web_common import render
from app.services import (
    add_project_member,
    chat_webhooks,
    delete_project,
    disconnect_chat_webhook,
    project_members,
    remove_project_member,
    set_chat_webhook,
    update_project,
    update_project_member,
)

router = APIRouter(tags=["web"])
_MEMBER_ROLE_FORM = Form(Role.MEMBER)
_REQUIRED_ROLE_FORM = Form(...)
_GITHUB_ERRORS = {
    "installation_cancelled": "GitHub installation was cancelled.",
    "oauth_denied": "GitHub authorization was denied.",
    "verification_failed": "GitHub could not verify that installation.",
}

_GMAIL_ERRORS = {
    "oauth_denied": "Gmail authorization was denied.",
    "verification_failed": "Gmail could not be connected. Try again.",
}


def render_settings(
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
    return render_settings(
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
        return render_settings(
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
        return render_settings(
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
        return render_settings(
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
        return render_settings(
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
        return render_settings(
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
        return render_settings(
            request, session, user, project, member, error=exc.detail, status_code=exc.status_code
        )
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
