import logging
import time
from collections.abc import Callable

import httpx
from fastapi import BackgroundTasks
from sqlmodel import Session

from app.models import Project, Ticket, User, WebhookType

logger = logging.getLogger("app.notifications")

TIMEOUT_SECONDS = 5.0
BACKOFF_SECONDS = (1, 2, 4)

EVENT_TICKET_CREATED = "TICKET_CREATED"
EVENT_TICKET_DONE = "TICKET_DONE"
EVENT_COMMENT_MENTION = "COMMENT_MENTION"

_HEADLINES = {
    EVENT_TICKET_CREATED: "New ticket",
    EVENT_TICKET_DONE: "Ticket completed",
    EVENT_COMMENT_MENTION: "New mention",
}

_UNASSIGNED = "Unassigned"


def build_payload(project: Project, event: str, ticket: Ticket) -> dict:
    return {
        "event": event,
        "project_id": project.id,
        "project_name": project.name,
        "project_slug": project.slug,
        "ticket_number": ticket.ticket_number,
        "title": ticket.title,
        "status": ticket.status.value,
        "type": ticket.type.value,
        "priority": ticket.priority.value,
        "assignee_id": ticket.assignee_id,
    }


def _headline(payload: dict) -> str:
    if payload["event"] == EVENT_COMMENT_MENTION:
        names = ", ".join(payload["mentioned_names"])
        return f"{payload['author_name']} mentioned {names} on #{payload['ticket_number']}"
    return (
        f"{_HEADLINES.get(payload['event'], payload['event'])} "
        f"#{payload['ticket_number']}: {payload['title']}"
    )


def _detail(payload: dict) -> str:
    if payload["event"] == EVENT_COMMENT_MENTION:
        return f"{payload['project_name']} · {payload['comment_excerpt']}"
    assignee = payload["assignee_id"] or _UNASSIGNED
    return (
        f"{payload['project_name']} · {payload['type']} · "
        f"{payload['priority']} · {payload['status']} · {assignee}"
    )


def _escape_slack(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_slack(payload: dict) -> dict:
    # Titles can come from outside senders (Gmail intake), so escape them for every event.
    payload = {
        **payload,
        "title": _escape_slack(payload["title"]),
        "project_name": _escape_slack(payload["project_name"]),
    }
    if payload["event"] == EVENT_COMMENT_MENTION:
        payload = {
            **payload,
            "author_name": _escape_slack(payload["author_name"]),
            "mentioned_names": [_escape_slack(name) for name in payload["mentioned_names"]],
            "comment_excerpt": _escape_slack(payload["comment_excerpt"]),
        }
    return {
        "text": _headline(payload),
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{_headline(payload)}*"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": _detail(payload)}]},
        ],
    }


def format_discord(payload: dict) -> dict:
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": (
                    _headline(payload)
                    if payload["event"] == EVENT_COMMENT_MENTION
                    else f"#{payload['ticket_number']} {payload['title']}"
                ),
                "description": _detail(payload),
            }
        ],
    }


def format_teams(payload: dict) -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": _headline(payload), "weight": "Bolder"},
                        {"type": "TextBlock", "text": _detail(payload), "isSubtle": True},
                    ],
                },
            }
        ],
    }


FORMATTERS: dict[WebhookType, Callable[[dict], dict]] = {
    WebhookType.SLACK: format_slack,
    WebhookType.DISCORD: format_discord,
    WebhookType.TEAMS: format_teams,
}


def dispatch(
    webhook_type: WebhookType,
    webhook_url: str,
    payload: dict,
    client: httpx.Client | None = None,
) -> None:
    """Best-effort chat webhook delivery. Never raises.

    Runs after the response has been sent (inside a background task per Task 17),
    so there is no caller left to handle an exception. Retries up to 3 times with
    1s/2s/4s backoff, then logs one WARNING and gives up.
    """
    formatter = FORMATTERS.get(webhook_type)
    if formatter is None or not webhook_url:
        return
    owned = client is None
    try:
        try:
            from app.services import validate_webhook_url

            validate_webhook_url(webhook_url)
            client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
            body = formatter(payload)
            for attempt in range(len(BACKOFF_SECONDS) + 1):
                try:
                    response = client.post(webhook_url, json=body, timeout=TIMEOUT_SECONDS)
                    if response.status_code < 400:
                        return
                except httpx.HTTPError:
                    pass
                if attempt < len(BACKOFF_SECONDS):
                    time.sleep(BACKOFF_SECONDS[attempt])
            logger.warning(
                "chat webhook delivery failed after %d attempts: project_id=%s ticket_number=%s",
                len(BACKOFF_SECONDS) + 1,
                payload.get("project_id"),
                payload.get("ticket_number"),
            )
        finally:
            if owned and client is not None:
                client.close()
    except Exception:
        # Construction, formatting, delivery, and owned-client cleanup are all
        # best-effort because background-task exceptions stop later tasks.
        logger.warning(
            "chat webhook delivery raised unexpectedly: project_id=%s ticket_number=%s",
            payload.get("project_id"),
            payload.get("ticket_number"),
            exc_info=True,
        )


def schedule(
    tasks: BackgroundTasks | None,
    session: Session,
    project: Project,
    event: str,
    ticket: Ticket,
) -> None:
    """Queue a chat notification for delivery after the response is sent.

    Builds the payload immediately, while the session is still alive, and
    enqueues only the resulting plain dict -- nothing ORM-shaped crosses
    into the background closure. `tasks=None` (no FastAPI request in scope,
    e.g. a direct service-layer call from a script or test) is a no-op.
    """
    if tasks is None:
        return
    from app.services import chat_webhooks

    payload = build_payload(project, event, ticket)
    for webhook in chat_webhooks(session, project.id):
        tasks.add_task(dispatch, webhook.provider, webhook.url, payload)


def schedule_comment_mention(
    tasks: BackgroundTasks,
    session: Session,
    project: Project,
    ticket: Ticket,
    author: User,
    mentioned_users: list[User],
    body: str,
) -> None:
    if not mentioned_users:
        return
    from app.services import chat_webhooks

    payload = {
        **build_payload(project, EVENT_COMMENT_MENTION, ticket),
        "author_name": author.name or author.email,
        "mentioned_names": [user.name or user.email for user in mentioned_users],
        "comment_excerpt": body[:200],
    }
    for webhook in chat_webhooks(session, project.id):
        tasks.add_task(dispatch, webhook.provider, webhook.url, payload)
