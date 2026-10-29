"""GitHub App connection: install, authorize, choose repositories, sync."""

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.auth import current_user, project_owner, verify_csrf
from app.config import settings
from app.db import get_session
from app.github import (
    GitHubClient,
    consume_github_state,
    github_is_configured,
    issue_github_state,
    read_github_state,
)
from app.github_sync import (
    disconnect_project_github,
    save_installation,
    save_project_repositories,
    sync_open_pull_requests,
)
from app.models import (
    GitHubConnectState,
    GitHubInstallation,
    Project,
    ProjectGitHubRepository,
    ProjectMember,
    User,
)
from app.routers.web_settings import render_settings

router = APIRouter(tags=["web"])
_REPOSITORY_IDS_FORM = Form(default=[])


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

    save_installation(
        session, installation_id, account_id=account_id, account_login=account_login, user=user
    )

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
    return render_settings(
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
        return render_settings(
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
        return render_settings(
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
