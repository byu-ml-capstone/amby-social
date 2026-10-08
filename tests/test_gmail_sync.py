import asyncio
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app.gmail_sync as gmail_sync
import app.main as main
import app.notifications as notifications
from app import attachments
from app.config import settings
from app.gmail import GmailAuthError, encrypt_token
from app.gmail_sync import sync_all, sync_connection
from app.models import (
    AttachmentSource,
    GmailConnection,
    GmailConnectionStatus,
    Priority,
    ProjectChatWebhook,
    Ticket,
    TicketAttachment,
    TicketType,
    WebhookType,
)
from tests.gmail_messages import attachment_part, gmail_message, with_attachments
from tests.images import image_bytes

FLYERS = "Label_1"
URGENT = "Label_2"


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key", Fernet.generate_key().decode())
    gmail_sync._access_tokens.clear()
    yield
    gmail_sync._access_tokens.clear()


def _added(messages):
    return [
        {
            "id": "150",
            "messagesAdded": [
                {"message": {"id": m["id"], "threadId": m["threadId"], "labelIds": m["labelIds"]}}
                for m in messages
            ],
        }
    ]


class FakeGmail:
    def __init__(self, messages=(), history=None, latest="200"):
        self.messages = {m["id"]: m for m in messages}
        self.history_records = _added(messages) if history is None else history
        self.latest = latest
        self.history_status = None
        self.broken_history_ids = set()
        self.auth_error = False
        self.failing_message_ids = set()
        self.token_calls = 0
        self.attachments = {}
        self.failing_attachment_ids = set()
        self.attachment_calls = []
        self.attachment_errors = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def access_token(self, refresh_token):
        self.token_calls += 1
        if self.auth_error:
            raise GmailAuthError("revoked")
        assert refresh_token == "1//refresh"
        return "ya29.access", 3600

    def history(self, access_token, start_history_id):
        status = 500 if start_history_id in self.broken_history_ids else self.history_status
        if status:
            request = httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/history")
            raise httpx.HTTPStatusError(
                "history failed", request=request, response=httpx.Response(status, request=request)
            )
        return self.history_records, self.latest

    def recent_label_messages(self, access_token, label_id):
        return [
            {"id": m["id"], "threadId": m["threadId"]}
            for m in self.messages.values()
            if label_id in m["labelIds"]
        ]

    def profile(self, access_token):
        return {"emailAddress": "marketing@example.com", "historyId": self.latest}

    def message(self, access_token, message_id):
        if message_id in self.failing_message_ids:
            raise httpx.ConnectError("gmail unavailable")
        if message_id not in self.messages:
            request = httpx.Request("GET", f"https://gmail.googleapis.com/messages/{message_id}")
            raise httpx.HTTPStatusError(
                "gone", request=request, response=httpx.Response(404, request=request)
            )
        return self.messages[message_id]

    def attachment(self, access_token, message_id, attachment_id):
        self.attachment_calls.append(attachment_id)
        if attachment_id in self.attachment_errors:
            raise self.attachment_errors[attachment_id]
        if attachment_id in self.failing_attachment_ids:
            raise httpx.ConnectError("gmail unavailable")
        if attachment_id not in self.attachments:
            request = httpx.Request(
                "GET", f"https://gmail.googleapis.com/attachments/{attachment_id}"
            )
            raise httpx.HTTPStatusError(
                "gone", request=request, response=httpx.Response(404, request=request)
            )
        return self.attachments[attachment_id]


def _make_connection(session, project, owner, **overrides):
    values = {
        "project_id": project.id,
        "user_id": owner.id,
        "google_email": "marketing@example.com",
        "refresh_token_enc": encrypt_token("1//refresh"),
        "label_mapping": {
            FLYERS: {"name": "Flyers", "type": "TASK", "priority": "MEDIUM"},
            URGENT: {"name": "Urgent", "type": "BUG", "priority": "URGENT"},
        },
        "history_id": "100",
    }
    values.update(overrides)
    row = GmailConnection(**values)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def world(session, make_user, make_project):
    owner = make_user(email="marketing-owner@example.com")
    project = make_project(owner, name="Marketing")
    return owner, project, _make_connection(session, project, owner)


def _tickets(session, project_id=None):
    session.expire_all()
    query = select(Ticket).order_by(Ticket.ticket_number)
    if project_id:
        query = query.where(Ticket.project_id == project_id)
    return session.exec(query).all()


def test_labeled_message_becomes_backlog_ticket(session, world):
    owner, project, connection = world
    sync_connection(session, connection, FakeGmail([gmail_message("m1")]))
    [ticket] = _tickets(session)
    assert ticket.project_id == project.id
    assert ticket.title == "Need a flyer"
    assert ticket.description.startswith("From: Jane Doe <jane@example.com>\n\nPlease make")
    assert ticket.type == TicketType.TASK
    assert ticket.priority == Priority.MEDIUM
    assert ticket.sprint_id is None
    assert ticket.creator_id == owner.id
    assert ticket.meta == {
        "source": "gmail",
        "from": "Jane Doe <jane@example.com>",
        "message_id": "m1",
        "thread_id": "m1",
    }
    session.refresh(connection)
    assert connection.history_id == "200"
    assert connection.last_synced_at is not None
    assert connection.last_error is None


def test_label_mapping_sets_type_and_priority(session, world):
    _, _, connection = world
    sync_connection(session, connection, FakeGmail([gmail_message("m1", labels=(URGENT,))]))
    [ticket] = _tickets(session)
    assert (ticket.type, ticket.priority) == (TicketType.BUG, Priority.URGENT)


def test_unmapped_label_is_ignored_but_history_advances(session, world):
    _, _, connection = world
    sync_connection(session, connection, FakeGmail([gmail_message("m1", labels=("Label_9",))]))
    assert _tickets(session) == []
    session.refresh(connection)
    assert connection.history_id == "200"


def test_label_removed_before_fetch_is_ignored(session, world):
    _, _, connection = world
    history = [
        {
            "id": "150",
            "labelsAdded": [
                {
                    "message": {"id": "m1", "threadId": "m1", "labelIds": [FLYERS]},
                    "labelIds": [FLYERS],
                }
            ],
        }
    ]
    sync_connection(session, connection, FakeGmail([gmail_message("m1", labels=())], history))
    assert _tickets(session) == []


def test_same_message_is_imported_once_across_cycles(session, world):
    _, _, connection = world
    gmail = FakeGmail([gmail_message("m1")])
    sync_connection(session, connection, gmail)
    sync_connection(session, connection, gmail)
    assert len(_tickets(session)) == 1


def test_message_with_two_mapped_labels_creates_one_ticket(session, world):
    _, _, connection = world
    message = gmail_message("m1", labels=(URGENT, FLYERS))
    history = [
        *_added([message]),
        {
            "id": "151",
            "labelsAdded": [
                {
                    "message": {"id": "m1", "threadId": "m1", "labelIds": [URGENT, FLYERS]},
                    "labelIds": [FLYERS],
                }
            ],
        },
    ]
    sync_connection(session, connection, FakeGmail([message], history))
    [ticket] = _tickets(session)
    assert ticket.type == TicketType.BUG  # first mapped label on the message wins


def test_reply_in_ticketed_thread_is_skipped(session, world):
    _, _, connection = world
    first = gmail_message("t1", subject="Need a flyer")
    reply = gmail_message("m2", thread_id="t1", subject="Re: Need a flyer", body="Any update?")
    sync_connection(session, connection, FakeGmail([first, reply]))
    [ticket] = _tickets(session)
    assert ticket.meta["message_id"] == "t1"


def test_same_mailbox_in_two_projects_creates_a_ticket_in_each(
    session, world, make_user, make_project
):
    _, first_project, first_connection = world
    other_owner = make_user(email="events-owner@example.com")
    second_project = make_project(other_owner, name="Events")
    second_connection = _make_connection(session, second_project, other_owner)
    gmail = FakeGmail([gmail_message("m1")])
    sync_connection(session, first_connection, gmail)
    sync_connection(session, second_connection, gmail)
    assert len(_tickets(session, first_project.id)) == 1
    assert len(_tickets(session, second_project.id)) == 1


def test_transient_failure_keeps_history_id_and_replay_does_not_duplicate(session, world):
    _, _, connection = world
    gmail = FakeGmail([gmail_message("m1"), gmail_message("m2", subject="Second")])
    gmail.failing_message_ids = {"m2"}
    with pytest.raises(httpx.ConnectError):
        sync_connection(session, connection, gmail)
    session.refresh(connection)
    assert connection.history_id == "100"
    assert len(_tickets(session)) == 1

    gmail.failing_message_ids = set()
    sync_connection(session, connection, gmail)
    assert [t.title for t in _tickets(session)] == ["Need a flyer", "Second"]


def test_expired_history_falls_back_to_recent_labeled_mail(session, world):
    _, _, connection = world
    gmail = FakeGmail([gmail_message("m1"), gmail_message("m2", labels=("Label_9",))], latest="300")
    gmail.history_status = 404
    sync_connection(session, connection, gmail)
    assert [t.meta["message_id"] for t in _tickets(session)] == ["m1"]
    session.refresh(connection)
    assert connection.history_id == "300"


def test_other_history_errors_propagate(session, world):
    _, _, connection = world
    gmail = FakeGmail([gmail_message("m1")])
    gmail.history_status = 500
    with pytest.raises(httpx.HTTPStatusError):
        sync_connection(session, connection, gmail)
    session.refresh(connection)
    assert connection.history_id == "100"


def test_unparseable_message_is_skipped_and_history_advances(session, world, monkeypatch):
    _, _, connection = world
    real_parse = gmail_sync.parse_message

    def parse(message):
        if message["id"] == "m1":
            raise ValueError("bad payload")
        return real_parse(message)

    monkeypatch.setattr(gmail_sync, "parse_message", parse)
    sync_connection(
        session, connection, FakeGmail([gmail_message("m1"), gmail_message("m2", subject="Ok")])
    )
    assert [t.title for t in _tickets(session)] == ["Ok"]
    session.refresh(connection)
    assert connection.history_id == "200"
    assert "m1" in connection.last_error


def test_message_deleted_before_fetch_is_skipped_and_history_advances(session, world):
    _, _, connection = world
    history = _added([gmail_message("gone"), gmail_message("m2", subject="Still here")])
    sync_connection(
        session, connection, FakeGmail([gmail_message("m2", subject="Still here")], history)
    )
    assert [t.title for t in _tickets(session)] == ["Still here"]
    session.refresh(connection)
    assert connection.history_id == "200"


def test_any_parser_bug_skips_only_that_message(session, world, monkeypatch):
    _, _, connection = world
    real_parse = gmail_sync.parse_message

    def parse(message):
        if message["id"] == "m1":
            raise AttributeError("'NoneType' object has no attribute 'get'")
        return real_parse(message)

    monkeypatch.setattr(gmail_sync, "parse_message", parse)
    sync_connection(
        session, connection, FakeGmail([gmail_message("m1"), gmail_message("m2", subject="Ok")])
    )
    assert [t.title for t in _tickets(session)] == ["Ok"]
    session.refresh(connection)
    assert connection.history_id == "200"


def test_skipped_message_is_logged_and_stays_visible(session, world, monkeypatch, caplog):
    _, _, connection = world
    monkeypatch.setattr(gmail_sync, "parse_message", lambda message: 1 / 0)
    with caplog.at_level("WARNING", logger="app.gmail_sync"):
        sync_connection(session, connection, FakeGmail([gmail_message("m1")]))
    assert "m1" in caplog.text
    sync_connection(session, connection, FakeGmail(history=[], latest="300"))
    session.refresh(connection)
    assert "m1" in connection.last_error


def test_clean_cycle_clears_a_transient_error(session, world):
    _, _, connection = world
    connection.last_error = "HTTPStatusError; retrying next cycle"
    session.add(connection)
    session.commit()
    sync_connection(session, connection, FakeGmail(history=[]))
    session.refresh(connection)
    assert connection.last_error is None


def test_labeled_conversation_uses_the_first_message(session, world):
    _, _, connection = world
    original = gmail_message("t1", subject="Need a flyer", body="Original request")
    reply = gmail_message("m2", thread_id="t1", subject="Re: Need a flyer", body="Any update?")
    sync_connection(session, connection, FakeGmail([original, reply], _added([reply, original])))
    [ticket] = _tickets(session)
    assert ticket.meta["message_id"] == "t1"
    assert "Original request" in ticket.description


def test_access_token_is_cached_between_cycles(session, world):
    _, _, connection = world
    gmail = FakeGmail([gmail_message("m1")])
    sync_connection(session, connection, gmail)
    sync_connection(session, connection, gmail)
    assert gmail.token_calls == 1


def test_created_ticket_notifies_chat_webhooks(session, world, monkeypatch):
    _, project, connection = world
    session.add(
        ProjectChatWebhook(
            project_id=project.id, provider=WebhookType.SLACK, url="https://example.com/hook"
        )
    )
    session.commit()
    sent = []
    monkeypatch.setattr(
        notifications, "dispatch", lambda provider, url, payload: sent.append((url, payload))
    )
    sync_connection(session, connection, FakeGmail([gmail_message("m1")]))
    [(url, payload)] = sent
    assert url == "https://example.com/hook"
    assert payload["event"] == "TICKET_CREATED"
    assert payload["title"] == "Need a flyer"


def test_sync_all_marks_revoked_connection_for_reauth(engine, session, world, monkeypatch):
    _, _, connection = world
    gmail = FakeGmail([gmail_message("m1")])
    gmail.auth_error = True
    monkeypatch.setattr(gmail_sync, "GmailClient", lambda: gmail)
    sync_all(engine)
    session.refresh(connection)
    assert connection.status == GmailConnectionStatus.NEEDS_REAUTH
    assert "Reconnect" in connection.last_error
    sync_all(engine)
    assert gmail.token_calls == 1  # connections needing reauth are not polled


def test_sync_all_isolates_a_failing_connection(
    engine, session, world, make_user, make_project, monkeypatch
):
    _, first_project, first_connection = world
    other_owner = make_user(email="events-owner@example.com")
    second_project = make_project(other_owner, name="Events")
    _make_connection(session, second_project, other_owner, history_id="broken")
    gmail = FakeGmail([gmail_message("m1")])
    gmail.broken_history_ids = {"broken"}
    monkeypatch.setattr(gmail_sync, "GmailClient", lambda: gmail)
    sync_all(engine)
    assert len(_tickets(session, first_project.id)) == 1
    with Session(engine) as check:
        broken = check.exec(
            select(GmailConnection).where(GmailConnection.project_id == second_project.id)
        ).one()
        assert broken.last_error.startswith("HTTPStatusError")
        assert broken.history_id == "broken"
        assert broken.status == GmailConnectionStatus.ACTIVE
    session.refresh(first_connection)
    assert first_connection.last_error is None


def test_poll_loop_survives_a_failed_cycle(monkeypatch):
    calls = []

    def fake_sync_all():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(gmail_sync, "sync_all", fake_sync_all)
    monkeypatch.setattr(gmail_sync, "POLL_SECONDS", 0)

    async def run():
        task = asyncio.create_task(gmail_sync.poll_forever())
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(asyncio.wait_for(run(), timeout=5))
    assert len(calls) >= 2


def test_lifespan_starts_poller_only_when_gmail_is_configured(monkeypatch):
    started = []

    def fake_poll():
        started.append(True)
        return asyncio.sleep(3600)

    monkeypatch.setattr(main, "poll_forever", fake_poll)
    with TestClient(main.app):
        pass
    assert started == []

    for name, value in {
        "google_client_id": "gid",
        "google_client_secret": "gsecret",
        "google_redirect_uri": "https://kanban.example.com/integrations/gmail/callback",
    }.items():
        monkeypatch.setattr(settings, name, value)
    with TestClient(main.app):
        pass
    assert started == [True]


def _stored_files():
    root = Path(settings.attachments_dir)
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _attachment_rows(session):
    session.expire_all()
    return session.exec(select(TicketAttachment).order_by(TicketAttachment.filename)).all()


def _mail_with(*parts):
    return gmail_message("m1", payload=with_attachments(*parts))


def test_mail_attachments_are_stored_on_the_ticket(session, world):
    owner, project, connection = world
    gmail = FakeGmail(
        [
            _mail_with(
                attachment_part("att-1", "brief.pdf"),
                attachment_part("att-2", "shot.png", mime_type="image/png"),
                attachment_part("att-3", "logo.png", mime_type="image/png", content_id="sig"),
            )
        ]
    )
    gmail.attachments = {"att-1": b"%PDF-1.4 brief", "att-2": image_bytes()}
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    brief, shot = _attachment_rows(session)
    assert (brief.filename, brief.is_image, shot.filename, shot.is_image) == (
        "brief.pdf",
        False,
        "shot.png",
        True,
    )
    assert {row.ticket_id for row in (brief, shot)} == {ticket.id}
    assert {row.source for row in (brief, shot)} == {AttachmentSource.GMAIL}
    assert {row.uploaded_by for row in (brief, shot)} == {owner.id}
    assert gmail.attachment_calls == ["att-1", "att-2"]  # the inline signature logo is skipped
    assert len(_stored_files()) == 2
    assert "not imported" not in ticket.description


def test_oversize_attachment_is_noted_and_never_downloaded(session, world):
    _, _, connection = world
    size = attachments.ATTACHMENT_MAX_BYTES + 1
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "huge.zip", size, "application/zip"))])
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    assert gmail.attachment_calls == []
    assert ticket.description.endswith("- huge.zip (10.0 MB) — not imported, open in Gmail")
    assert _attachment_rows(session) == []


def test_attachment_that_would_fill_the_project_is_noted(session, world, monkeypatch):
    _, _, connection = world
    monkeypatch.setattr(attachments, "PROJECT_QUOTA_BYTES", 100)
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "brief.pdf", 1234))])
    gmail.attachments = {"att-1": b"x" * 1234}
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    assert "brief.pdf (0.0 MB) — not imported, open in Gmail" in ticket.description
    assert gmail.attachment_calls == []


def test_attachment_gone_from_gmail_is_noted_and_the_ticket_still_imports(session, world):
    _, _, connection = world
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "brief.pdf"))])
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    assert "brief.pdf (0.0 MB) — not imported, open in Gmail" in ticket.description


def test_transient_attachment_failure_commits_nothing_and_replays_once(session, world):
    _, _, connection = world
    gmail = FakeGmail(
        [_mail_with(attachment_part("att-1", "brief.pdf"), attachment_part("att-2", "deck.pdf"))]
    )
    gmail.attachments = {"att-1": b"%PDF brief", "att-2": b"%PDF deck"}
    gmail.failing_attachment_ids = {"att-2"}
    with pytest.raises(httpx.ConnectError):
        sync_connection(session, connection, gmail)
    session.rollback()  # what sync_all does after a failed connection
    session.refresh(connection)
    assert connection.history_id == "100"
    assert _tickets(session) == [] and _attachment_rows(session) == [] and _stored_files() == []

    gmail.failing_attachment_ids = set()
    sync_connection(session, connection, gmail)
    assert len(_tickets(session)) == 1
    assert [row.filename for row in _attachment_rows(session)] == ["brief.pdf", "deck.pdf"]
    assert len(_stored_files()) == 2


def test_rejected_ticket_leaves_no_attachment_files(session, world, monkeypatch):
    _, _, connection = world

    def reject(*args, **kwargs):
        raise HTTPException(422, "title must not be empty")

    monkeypatch.setattr(gmail_sync, "create_ticket", reject)
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "brief.pdf"))])
    gmail.attachments = {"att-1": b"%PDF brief"}
    sync_connection(session, connection, gmail)
    assert _tickets(session) == [] and _stored_files() == []
    session.refresh(connection)
    assert connection.last_error.startswith("Skipped Gmail message m1")


def _status_error(status):
    request = httpx.Request("GET", "https://gmail.googleapis.com/attachments/att-1")
    return httpx.HTTPStatusError(
        "failed", request=request, response=httpx.Response(status, request=request)
    )


@pytest.mark.parametrize(
    "error", [_status_error(403), _status_error(400), ValueError("Incorrect padding")]
)
def test_attachment_that_can_never_download_is_noted_and_intake_moves_on(session, world, error):
    _, _, connection = world
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "brief.pdf"))])
    gmail.attachment_errors = {"att-1": error}
    sync_connection(session, connection, gmail)
    [ticket] = _tickets(session)
    assert "brief.pdf (0.0 MB) — not imported, open in Gmail" in ticket.description
    session.refresh(connection)
    assert connection.history_id == "200"


@pytest.mark.parametrize("status", [401, 429, 503])
def test_retryable_attachment_errors_hold_the_batch(session, world, status):
    _, _, connection = world
    gmail = FakeGmail([_mail_with(attachment_part("att-1", "brief.pdf"))])
    gmail.attachment_errors = {"att-1": _status_error(status)}
    with pytest.raises(httpx.HTTPStatusError):
        sync_connection(session, connection, gmail)
