"""A project's Gmail connection: connect, choose labels, mark for reconnect, disconnect."""

import httpx
from cryptography.fernet import InvalidToken
from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.gmail import GmailClient, decrypt_token, encrypt_token
from app.models import GmailConnection, GmailConnectionStatus, Priority, Project, TicketType, User

REVOKED = "Google access was revoked. Reconnect Gmail."


def get_connection(session: Session, project: Project) -> GmailConnection:
    connection = session.exec(
        select(GmailConnection).where(GmailConnection.project_id == project.id)
    ).first()
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gmail is not connected")
    return connection


def mark_needs_reauth(session: Session, connection: GmailConnection, reason: str) -> None:
    connection.status = GmailConnectionStatus.NEEDS_REAUTH
    connection.last_error = reason
    session.add(connection)
    session.commit()


def save_connection(
    session: Session,
    project: Project,
    user: User,
    *,
    google_email: str,
    refresh_token: str,
    history_id: str,
) -> GmailConnection:
    """Connect or reconnect the project's mailbox; tickets will be created as `user`."""
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
    return connection


def save_label_mapping(
    session: Session,
    connection: GmailConnection,
    mailbox_labels: list[dict],
    choices: dict[str, tuple[TicketType, Priority]],
    selected: set[str],
) -> None:
    """Store {label_id: {"name", "type", "priority"}}, the shape gmail_sync reads."""
    if not selected <= set(choices):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid label mapping")
    names = {label["id"]: label["name"] for label in mailbox_labels}
    if not selected <= set(names):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Selected label is not available"
        )
    connection.label_mapping = {
        label_id: {
            "name": names[label_id],
            "type": choices[label_id][0].value,
            "priority": choices[label_id][1].value,
        }
        for label_id in names
        if label_id in selected
    }
    session.add(connection)
    session.commit()


def disconnect(session: Session, connection: GmailConnection) -> None:
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
