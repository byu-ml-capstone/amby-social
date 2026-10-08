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
    encrypt_token,
    gmail_is_configured,
    issue_gmail_state,
    read_gmail_state,
)
from app.models import (
    GmailConnection,
    GmailConnectionStatus,
    Priority,
    Project,
    ProjectMember,
    Role,
    TicketType,
    User,
)
from app.routers.web_sprints import _settings

router = APIRouter(tags=["web"])
_LABEL_IDS_FORM = Form(default=[])
_ALL_LABEL_IDS_FORM = Form(default=[])
_TYPES_FORM = Form(default=[])
_PRIORITIES_FORM = Form(default=[])
_REVOKED = "Google access was revoked. Reconnect Gmail."


def _connection(session: Session, project: Project) -> GmailConnection:
    connection = session.exec(
        select(GmailConnection).where(GmailConnection.project_id == project.id)
    ).first()
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gmail is not connected")
    return connection


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
        connection.status = GmailConnectionStatus.NEEDS_REAUTH
        connection.last_error = _REVOKED
        session.add(connection)
        session.commit()
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

    connection = session.exec(
        select(GmailConnection).where(GmailConnection.project_id == project.id)
    ).first()
    if connection is None:
        connection = GmailConnection(
            project_id=project.id,
            user_id=user.id,
            google_email=google_email,
            refresh_token_enc="",
            history_id=history_id,
        )
    elif connection.google_email != google_email:
        # Another mailbox: its label ids and history ids mean nothing here.
        connection.label_mapping = {}
        connection.history_id = history_id
    # Reconnecting the same mailbox keeps history_id, so mail that arrived while access was
    # broken is still imported on the next cycle.
    connection.user_id = user.id
    connection.google_email = google_email[:255]
    connection.refresh_token_enc = encrypt_token(refresh_token)
    connection.status = GmailConnectionStatus.ACTIVE
    connection.last_error = None
    session.add(connection)
    session.commit()
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
    connection = _connection(session, project)
    labels = _mailbox_labels(session, connection)
    if labels is None:
        return _settings(
            request,
            session,
            user,
            project,
            member,
            error=_REVOKED,
            status_code=status.HTTP_409_CONFLICT,
        )
    labels.sort(
        key=lambda label: (label["id"] not in connection.label_mapping, label["name"].lower())
    )
    return _settings(request, session, user, project, member, gmail_available_labels=labels)


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
    connection = _connection(session, project)
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
    selected = set(label_ids)
    if not selected <= set(rows):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid label mapping")
    labels = _mailbox_labels(session, connection)
    if labels is None:
        return _settings(
            request,
            session,
            user,
            project,
            member,
            error=_REVOKED,
            status_code=status.HTTP_409_CONFLICT,
        )
    names = {label["id"]: label["name"] for label in labels}
    if not selected <= set(names):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Selected label is not available"
        )
    connection.label_mapping = {
        label_id: {
            "name": names[label_id],
            "type": rows[label_id][0].value,
            "priority": rows[label_id][1].value,
        }
        for label_id in names
        if label_id in selected
    }
    session.add(connection)
    session.commit()
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
    connection = _connection(session, project)
    # Revoking ends the whole Google grant, so leave it while another project reads this mailbox.
    shared = session.exec(
        select(GmailConnection.id).where(
            GmailConnection.google_email == connection.google_email,
            GmailConnection.id != connection.id,
        )
    ).first()
    if shared is None:
        try:
            with GmailClient() as gmail:
                gmail.revoke(decrypt_token(connection.refresh_token_enc))
        except (httpx.HTTPError, InvalidToken):
            pass  # best effort: the stored token is deleted either way
    session.delete(connection)
    session.commit()
    return _settings_redirect(project)
