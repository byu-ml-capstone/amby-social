import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.auth import current_user, project_owner, verify_csrf
from app.db import get_session
from app.gmail import (
    GmailAuthError,
    GmailClient,
    authorize_url,
    decrypt_token,
    gmail_is_configured,
    issue_gmail_state,
    read_gmail_state,
)
from app.gmail_connections import (
    REVOKED,
    disconnect,
    get_connection,
    mark_needs_reauth,
    save_connection,
    save_label_mapping,
)
from app.models import (
    GmailConnection,
    Priority,
    Project,
    ProjectMember,
    Role,
    TicketType,
    User,
)
from app.routers.web_settings import render_settings

router = APIRouter(tags=["web"])
_LABEL_IDS_FORM = Form(default=[])
_ALL_LABEL_IDS_FORM = Form(default=[])
_TYPES_FORM = Form(default=[])
_PRIORITIES_FORM = Form(default=[])


def _settings_redirect(project: Project, query: str = "") -> RedirectResponse:
    return RedirectResponse(
        f"/projects/{project.slug}/settings{query}", status_code=status.HTTP_303_SEE_OTHER
    )


def _mailbox_labels(session: Session, connection: GmailConnection) -> list[dict] | None:
    """User labels in the mailbox, or None after marking the connection for reauth."""
    try:
        with GmailClient() as gmail:
            access_token, _ = gmail.access_token(decrypt_token(connection.refresh_token_enc))
            return gmail.labels(access_token)
    except (GmailAuthError, InvalidToken):
        mark_needs_reauth(session, connection, REVOKED)
        return None
    except httpx.HTTPError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Gmail is unavailable. Try again."
        ) from None


@router.post(
    "/projects/{slug}/settings/integrations/gmail/connect",
    dependencies=[Depends(verify_csrf)],
)
def start_gmail_connect(
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
) -> Response:
    project, _ = access
    if not gmail_is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Gmail is not configured")
    return RedirectResponse(
        authorize_url(issue_gmail_state(project.id, user.id)),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/integrations/gmail/callback")
def gmail_oauth_callback(
    state: str = "",
    code: str | None = None,
    error: str | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project = session.get(Project, read_gmail_state(state, user.id))
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    membership = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
        )
    ).first()
    if membership is None or membership.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Project owner role required")
    if error:
        reason = "oauth_denied" if error == "access_denied" else "verification_failed"
        return _settings_redirect(project, f"?gmail_error={reason}")
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing Google OAuth code")
    try:
        with GmailClient() as gmail:
            access_token, refresh_token = gmail.exchange_code(code)
            profile = gmail.profile(access_token)
        google_email = profile["emailAddress"]
        history_id = str(profile["historyId"])
        if not isinstance(google_email, str) or not google_email:
            raise ValueError("Gmail profile missing email address")
    except (httpx.HTTPError, ValueError, KeyError, GmailAuthError):
        return _settings_redirect(project, "?gmail_error=verification_failed")

    save_connection(
        session,
        project,
        user,
        google_email=google_email,
        refresh_token=refresh_token,
        history_id=history_id,
    )
    return RedirectResponse(
        f"/projects/{project.slug}/settings/integrations/gmail/labels",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/projects/{slug}/settings/integrations/gmail/labels")
def gmail_label_selection(
    request: Request,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    connection = get_connection(session, project)
    labels = _mailbox_labels(session, connection)
    if labels is None:
        return render_settings(
            request,
            session,
            user,
            project,
            member,
            error=REVOKED,
            status_code=status.HTTP_409_CONFLICT,
        )
    labels.sort(
        key=lambda label: (label["id"] not in connection.label_mapping, label["name"].lower())
    )
    return render_settings(request, session, user, project, member, gmail_available_labels=labels)


@router.post(
    "/projects/{slug}/settings/integrations/gmail/labels",
    dependencies=[Depends(verify_csrf)],
)
def save_gmail_labels(
    request: Request,
    label_ids: list[str] = _LABEL_IDS_FORM,
    all_label_ids: list[str] = _ALL_LABEL_IDS_FORM,
    types: list[str] = _TYPES_FORM,
    priorities: list[str] = _PRIORITIES_FORM,
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, member = access
    connection = get_connection(session, project)
    try:
        rows = {
            label_id: (TicketType(ticket_type), Priority(priority))
            for label_id, ticket_type, priority in zip(
                all_label_ids, types, priorities, strict=True
            )
        }
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid label mapping"
        ) from None
    labels = _mailbox_labels(session, connection)
    if labels is None:
        return render_settings(
            request,
            session,
            user,
            project,
            member,
            error=REVOKED,
            status_code=status.HTTP_409_CONFLICT,
        )
    save_label_mapping(session, connection, labels, rows, set(label_ids))
    return _settings_redirect(project, "?saved=1")


@router.post(
    "/projects/{slug}/settings/integrations/gmail/disconnect",
    dependencies=[Depends(verify_csrf)],
)
def disconnect_gmail(
    confirm: str = Form(""),
    access: tuple[Project, ProjectMember] = Depends(project_owner),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = access
    if confirm != "Disconnect Gmail":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "confirm must equal Disconnect Gmail"
        )
    disconnect(session, get_connection(session, project))
    return _settings_redirect(project)
