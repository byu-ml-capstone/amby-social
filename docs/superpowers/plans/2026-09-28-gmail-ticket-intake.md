# Gmail Ticket Intake (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When someone applies a mapped label to a message in the marketing team's shared Gmail account, a backlog ticket is created in the connected Kanban Flow project.

**Architecture:** An owner connects one Gmail mailbox per project through Google OAuth (`gmail.readonly`) and maps Gmail labels to ticket type and priority. A background task started in the FastAPI lifespan runs every 90 seconds. It executes one sync cycle in a worker thread: it reads `history.list`, fetches newly labeled messages, parses each into a compact title and description, and calls the existing `services.create_ticket()`. Rows in `IntegrationDelivery` stop the same message, or a later reply in the same thread, from becoming a second ticket.

**Tech Stack:** FastAPI, SQLModel/SQLite, Alembic, `httpx` (sync client, no Google SDK), `itsdangerous`, `cryptography.fernet`, stdlib `html.parser`, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-gmail-ticket-intake-design.md`

## Global Constraints

- Phase 1 only. No Jev/TypeSafe code.
- OAuth scope is exactly `https://www.googleapis.com/auth/gmail.readonly`.
- No Google SDK. Google HTTP goes through `httpx`, following `app/github.py`.
- `EMAIL_BODY_MAX_CHARS = 2000` (a starting value).
- Poll interval `POLL_SECONDS = 90`. The app runs with one uvicorn worker (`--workers 1`).
- Refresh tokens are stored encrypted with Fernet and are never logged.
- Python ≥ 3.11. Ruff runs with line length 100 and rules `E,F,I,UP,B`. Run `uv run ruff check --fix . && uv run ruff format .` before every commit.
- pytest treats `DeprecationWarning` as an error.
- Baseline before Task 1: `uv run pytest` passes 715 tests (takes about 5 minutes).

## Where this plan deviates from the spec (decided while reading the code)

1. **Sync runs in a thread using the sync `httpx` client.** The spec said async httpx plus `to_thread` for DB work. Running the whole cycle in `asyncio.to_thread` still keeps the event loop free. It also reuses the `GitHubClient` style and keeps the tests synchronous.
2. **Delivery keys carry the project id**: `GMAIL` → `"<project_id>:<message_id>"`, `GMAIL_THREAD` → `"<project_id>:<thread_id>"`. Without the prefix, two projects connected to the same mailbox would share dedupe rows and only one project would get the ticket.
3. **Added a fourth setting, `GOOGLE_REDIRECT_URI`.** Google requires an exact match. Deriving the URI from the request would give `http://` behind Nginx.
4. **Status values are uppercase** (`ACTIVE`, `NEEDS_REAUTH`) to match every other enum in `app/models.py`.
5. **Reauth notification.** `app/notifications.py` only sends project chat webhooks. There is no per-user notification. When a connection needs reauth, the settings page shows "Reconnect required" and the error, and a `WARNING` is logged. Tickets created from mail do fire the normal "New ticket" chat notification, the same as tickets created on the web.
6. **Reconnecting the same mailbox keeps `history_id` and the label mapping**, so mail that arrived while access was broken still gets imported. Connecting a different mailbox resets both.
7. **Only user labels are offered** (Gmail `type == "user"`), not system labels like INBOX.
8. **Spec open item resolved:** MCP `list_tickets` returns a 120-character summary. `get_ticket` returns the full description, so the 2000-character cap still matters.

## Review Focus

1. **Conversation-view labeling.** Labeling a thread applies the label to every message in it, so several `labelAdded` events arrive. The expected result is one ticket. Task 5: `test_reply_in_ticketed_thread_is_skipped`, `test_message_with_two_mapped_labels_creates_one_ticket`.
2. **HTML-only newsletters** with `<style>` blocks. The description must not contain CSS. Task 3: `test_html_only_message_drops_style_script_and_markup`.
3. **Reconnecting the same mailbox** after a password change. The mapping is kept and mail from the outage is imported. Task 7: `test_reconnect_same_mailbox_keeps_mapping_and_history`.
4. **A label removed before the poll runs.** No ticket should be created. Task 5: `test_label_removed_before_fetch_is_ignored`.
5. **A message that crashes the parser.** It is skipped, later mail still imports, and the history does not stall forever. Task 5: `test_unparseable_message_is_skipped_and_history_advances`.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/config.py` (modify) | Four Google settings and a production check |
| `app/models.py` (modify) | `GmailConnectionStatus`, `GmailConnection` |
| `alembic/versions/a3f7c9e2b815_add_gmail_connection.py` (create) | Table migration |
| `app/services.py` (modify) | `delete_project` removes the Gmail connection |
| `app/gmail_parse.py` (create) | Pure function `parse_message(message) -> ParsedEmail` |
| `app/gmail.py` (create) | Config check, token encryption, OAuth state, `GmailClient` (httpx) |
| `app/gmail_sync.py` (create) | `sync_connection`, `sync_all`, `poll_forever` |
| `app/main.py` (modify) | Start and cancel the poller in the lifespan; include the Gmail router |
| `app/routers/gmail.py` (create) | Connect, callback, label picker, disconnect |
| `app/routers/web_sprints.py` (modify) | Gmail context in `_settings`; `gmail_error` messages |
| `app/templates/project_settings.html` (modify) | Gmail integration card |
| `tests/gmail_messages.py` (create) | Shared Gmail message builders for tests |
| `tests/test_gmail_parse.py`, `tests/test_gmail_client.py`, `tests/test_gmail_sync.py`, `tests/test_gmail_models.py`, `tests/test_web_gmail.py` (create) | Tests |
| `tests/test_security.py`, `tests/test_migrations.py` (modify) | Config and migration tests |
| `.env.example`, `README.md` (modify) | Setup documentation |

---

### Task 1: Google settings and production validation

**Files:**
- Modify: `app/config.py` (fields after `github_web_url`, check at the end of `validate_production_security`)
- Modify: `pyproject.toml` (via `uv add`)
- Test: `tests/test_security.py`

**Interfaces:**
- Produces: `settings.google_client_id`, `settings.google_client_secret`, `settings.google_redirect_uri`, `settings.token_encryption_key`, all `str | None`, default `None`.

- [ ] **Step 1: Make `cryptography` an explicit dependency**

It is already installed through `pyjwt[crypto]` (version 50.0.1). Declaring it directly protects the Fernet import if that transitive path ever changes.

Run: `uv add "cryptography>=50.0.1"`
Expected: `pyproject.toml` dependencies now include `"cryptography>=50.0.1"`, and `uv.lock` is updated.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_security.py`:

```python
from cryptography.fernet import Fernet

_GMAIL = {
    "google_client_id": "gid",
    "google_client_secret": "gsecret",
    "google_redirect_uri": "https://kanban.example.com/integrations/gmail/callback",
}


@pytest.mark.parametrize(
    "overrides",
    [
        _GMAIL,
        {**_GMAIL, "token_encryption_key": "not-a-fernet-key"},
        {
            **_GMAIL,
            "token_encryption_key": Fernet.generate_key().decode(),
            "google_redirect_uri": "http://kanban.example.com/integrations/gmail/callback",
        },
    ],
)
def test_production_gmail_settings_reject_unsafe_values(overrides):
    with pytest.raises(ValidationError):
        _production_settings(**overrides)


def test_production_gmail_settings_accept_a_fernet_key():
    config = _production_settings(**_GMAIL, token_encryption_key=Fernet.generate_key().decode())
    assert config.google_client_id == "gid"


def test_production_without_gmail_needs_no_encryption_key():
    assert _production_settings().token_encryption_key is None
```

Move the `from cryptography.fernet import Fernet` line up into the file's import block so ruff's `I` rule stays happy.

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_security.py -v`
Expected: the new tests FAIL. `Settings` ignores unknown fields (`extra="ignore"`), so nothing raises yet and `google_client_id` does not exist.

- [ ] **Step 4: Implement**

In `app/config.py`, add `from cryptography.fernet import Fernet` to the imports. Add these fields after `github_web_url`:

```python
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None
    token_encryption_key: str | None = None
```

At the end of `validate_production_security`, just before the final `return self`, add:

```python
        if self.google_client_id:
            if not (self.google_redirect_uri or "").startswith("https://"):
                raise ValueError("production GOOGLE_REDIRECT_URI must be an https URL")
            try:
                Fernet(self.token_encryption_key or "")
            except ValueError:
                raise ValueError(
                    "production TOKEN_ENCRYPTION_KEY must come from Fernet.generate_key()"
                ) from None
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_security.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add pyproject.toml uv.lock app/config.py tests/test_security.py
git commit -m "feat: add Google OAuth settings for Gmail intake

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `GmailConnection` model, migration, project-delete cleanup

**Files:**
- Modify: `app/models.py` (enum next to the other enums; model after `IntegrationDelivery`)
- Create: `alembic/versions/a3f7c9e2b815_add_gmail_connection.py`
- Modify: `app/services.py` (`delete_project`, and the models import list)
- Test: `tests/test_gmail_models.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces:
  - `GmailConnectionStatus(StrEnum)`: `ACTIVE`, `NEEDS_REAUTH`.
  - `GmailConnection` fields:
    - `id: str`
    - `project_id: str` (unique)
    - `user_id: str`
    - `google_email: str`
    - `refresh_token_enc: str`
    - `label_mapping: dict`, shaped as `{label_id: {"name": str, "type": TicketType value, "priority": Priority value}}`
    - `history_id: str`
    - `status: GmailConnectionStatus`
    - `last_synced_at: datetime | None`
    - `last_error: str | None`
    - `created_at: datetime`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gmail_models.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import GmailConnection, GmailConnectionStatus, Project
from app.services import delete_project


def _connection(project, owner):
    return GmailConnection(
        project_id=project.id,
        user_id=owner.id,
        google_email="marketing@example.com",
        refresh_token_enc="encrypted",
        history_id="100",
    )


def test_connection_defaults(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    row = _connection(project, owner)
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.status == GmailConnectionStatus.ACTIVE
    assert row.label_mapping == {}
    assert row.last_synced_at is None
    assert row.last_error is None


def test_one_gmail_connection_per_project(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    session.add(_connection(project, owner))
    session.commit()
    session.add(_connection(project, owner))
    with pytest.raises(IntegrityError):
        session.commit()


def test_project_delete_removes_gmail_connection(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    session.add(_connection(project, owner))
    session.commit()
    delete_project(session, session.get(Project, project.id), project.slug)
    session.commit()
    assert session.exec(select(GmailConnection)).all() == []
```

Append to `tests/test_migrations.py`:

```python
def test_gmail_migration_round_trip(tmp_path):
    db = tmp_path / "gmail.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": os.environ["PATH"]}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True, env=env)
    assert "gmail_connection" in inspect(make_engine(f"sqlite:///{db}")).get_table_names()
    subprocess.run(["uv", "run", "alembic", "downgrade", "f2a1b3c4d5e6"], check=True, env=env)
    assert "gmail_connection" not in inspect(make_engine(f"sqlite:///{db}")).get_table_names()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_gmail_models.py tests/test_migrations.py::test_gmail_migration_round_trip -v`
Expected: FAIL with `ImportError: cannot import name 'GmailConnection'`.

- [ ] **Step 3: Implement the model**

In `app/models.py`, add after `class Priority`:

```python
class GmailConnectionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    NEEDS_REAUTH = "NEEDS_REAUTH"
```

Append at the end of the file:

```python
class GmailConnection(SQLModel, table=True):
    __tablename__ = "gmail_connection"

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", unique=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    google_email: str = Field(max_length=255)
    refresh_token_enc: str
    label_mapping: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    history_id: str = Field(max_length=32)
    status: GmailConnectionStatus = Field(default=GmailConnectionStatus.ACTIVE)
    last_synced_at: datetime | None = None
    last_error: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=utcnow)
```

- [ ] **Step 4: Write the migration**

The current head is `f2a1b3c4d5e6`. Create `alembic/versions/a3f7c9e2b815_add_gmail_connection.py`:

```python
"""add gmail connection

Revision ID: a3f7c9e2b815
Revises: f2a1b3c4d5e6
"""

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision = "a3f7c9e2b815"
down_revision = "f2a1b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_connection",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("google_email", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("refresh_token_enc", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("label_mapping", sa.JSON(), nullable=False),
        sa.Column("history_id", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "NEEDS_REAUTH", name="gmailconnectionstatus"),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index("ix_gmail_connection_user_id", "gmail_connection", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_gmail_connection_user_id", table_name="gmail_connection")
    op.drop_table("gmail_connection")
```

- [ ] **Step 5: Clean up on project delete**

In `app/services.py`, add `GmailConnection` to the `from app.models import (...)` list in alphabetical order. In `delete_project`, directly after the `ProjectChatWebhook` delete line, add:

```python
    session.execute(delete(GmailConnection).where(GmailConnection.project_id == project.id))
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_gmail_models.py tests/test_migrations.py -v`
Expected: all PASS. This includes the existing `test_migration_produces_the_same_tables_as_the_models`, which compares migrated columns against the model.

- [ ] **Step 7: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add app/models.py app/services.py alembic/versions/a3f7c9e2b815_add_gmail_connection.py tests/test_gmail_models.py tests/test_migrations.py
git commit -m "feat: add gmail_connection table

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Email parsing layer

**Files:**
- Create: `app/gmail_parse.py`
- Create: `tests/gmail_messages.py`
- Test: `tests/test_gmail_parse.py`

**Interfaces:**
- Consumes: `app.services.TITLE_MAX_LENGTH` (255).
- Produces:
  - `EMAIL_BODY_MAX_CHARS = 2000`.
  - `ParsedEmail(title: str, body: str, sender: str, attachment_count: int)` with a `.description -> str` property.
  - `parse_message(message: dict) -> ParsedEmail`. The input is a Gmail `messages.get(format=full)` resource.
  - Test helpers: `tests.gmail_messages.b64(text, charset="utf-8") -> str` and `tests.gmail_messages.gmail_message(message_id="msg-1", *, thread_id=None, labels=("Label_1",), subject=..., sender=..., body=..., payload=None) -> dict`.

- [ ] **Step 1: Create the shared test helpers**

Create `tests/gmail_messages.py`:

```python
import base64


def b64(text: str, charset: str = "utf-8") -> str:
    """Gmail API body encoding: unpadded base64url."""
    return base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip("=")


def gmail_message(
    message_id: str = "msg-1",
    *,
    thread_id: str | None = None,
    labels: tuple[str, ...] = ("Label_1",),
    subject: str = "Need a flyer",
    sender: str = "Jane Doe <jane@example.com>",
    body: str = "Please make a flyer for the fall event.",
    payload: dict | None = None,
) -> dict:
    """A `messages.get(format=full)` resource. Gmail thread ids equal the first message id."""
    return {
        "id": message_id,
        "threadId": thread_id or message_id,
        "labelIds": list(labels),
        "snippet": body[:100],
        "payload": payload
        or {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"data": b64(body)},
        },
    }
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_gmail_parse.py`:

```python
from app.gmail_parse import EMAIL_BODY_MAX_CHARS, parse_message
from tests.gmail_messages import b64, gmail_message


def _headers(subject="Need a flyer", sender="Jane Doe <jane@example.com>", *extra):
    return [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}, *extra]


def test_plain_text_message_becomes_title_body_and_sender():
    parsed = parse_message(
        gmail_message(
            subject="Re: Fwd: Need a flyer",
            body="Please make a flyer.\r\n\r\n\r\n\r\nThanks!",
        )
    )
    assert parsed.title == "Need a flyer"
    assert parsed.sender == "Jane Doe <jane@example.com>"
    assert parsed.body == "Please make a flyer.\n\nThanks!"
    assert parsed.attachment_count == 0
    assert parsed.description == (
        "From: Jane Doe <jane@example.com>\n\nPlease make a flyer.\n\nThanks!"
    )


def test_multipart_prefers_plain_text_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "headers": _headers(),
        "parts": [
            {"mimeType": "text/html", "body": {"data": b64("<p>HTML version</p>")}},
            {"mimeType": "text/plain", "body": {"data": b64("Plain version")}},
        ],
    }
    assert parse_message(gmail_message(payload=payload)).body == "Plain version"


def test_html_only_message_drops_style_script_and_markup():
    markup = (
        "<html><head><style>.hero { color: red; }</style></head><body>"
        "<script>track()</script><h1>Fall event</h1><p>Need&nbsp;a <b>flyer</b></p>"
        "<br>Thanks</body></html>"
    )
    payload = {"mimeType": "text/html", "headers": _headers(), "body": {"data": b64(markup)}}
    assert parse_message(gmail_message(payload=payload)).body == (
        "Fall event\n\nNeed a flyer\n\nThanks"
    )


def test_quoted_plain_text_reply_is_removed():
    body = (
        "Can we move it to Friday?\n\n"
        "On Mon, Sep 28, 2026 at 9:00 AM Jane Doe <jane@example.com>\nwrote:\n"
        "> Original request\n> more"
    )
    assert parse_message(gmail_message(body=body)).body == "Can we move it to Friday?"


def test_quoted_html_reply_is_removed():
    markup = (
        "<div>Sounds good</div><div class='gmail_quote'>"
        "<div>On Mon, Sep 28, 2026 Jane wrote:</div><blockquote>Old text</blockquote></div>"
    )
    payload = {"mimeType": "text/html", "headers": _headers(), "body": {"data": b64(markup)}}
    assert parse_message(gmail_message(payload=payload)).body == "Sounds good"


def test_body_that_is_only_a_quote_falls_back_to_raw_text():
    assert parse_message(gmail_message(body="> note only")).body == "> note only"


def test_message_without_any_body_uses_the_snippet():
    message = gmail_message(payload={"mimeType": "multipart/mixed", "headers": _headers()})
    message["snippet"] = "Tom &amp; Jerry"
    assert parse_message(message).body == "Tom & Jerry"


def test_empty_subject_uses_sender():
    assert parse_message(gmail_message(subject="  Re:  ")).title == (
        "(no subject) from Jane Doe <jane@example.com>"
    )


def test_long_subject_is_cut_to_title_limit():
    assert len(parse_message(gmail_message(subject="x" * 400)).title) == 255


def test_long_body_is_capped():
    parsed = parse_message(gmail_message(body="x" * (EMAIL_BODY_MAX_CHARS + 500)))
    assert parsed.body == "x" * EMAIL_BODY_MAX_CHARS + "\n\n…(truncated, 500 chars omitted)"


def test_attachments_are_counted_not_decoded():
    payload = {
        "mimeType": "multipart/mixed",
        "headers": _headers(),
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("See attached")}},
            {
                "mimeType": "application/pdf",
                "filename": "brief.pdf",
                "body": {"attachmentId": "att-1", "size": 1234},
            },
            {
                "mimeType": "image/png",
                "filename": "logo.png",
                "body": {"attachmentId": "att-2", "size": 99},
            },
        ],
    }
    parsed = parse_message(gmail_message(payload=payload))
    assert parsed.attachment_count == 2
    assert parsed.body == "See attached"
    assert parsed.description.endswith("(2 attachments not imported)")


def test_declared_charset_is_respected():
    content_type = {"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}
    payload = {
        "mimeType": "text/plain",
        "headers": _headers("Menu", "Chef <chef@example.com>", content_type),
        "body": {"data": b64("Café menu", "iso-8859-1")},
    }
    assert parse_message(gmail_message(payload=payload)).body == "Café menu"


def test_unknown_charset_falls_back_to_utf8():
    content_type = {"name": "Content-Type", "value": "text/plain; charset=x-unknown"}
    payload = {
        "mimeType": "text/plain",
        "headers": _headers("Hi", "A <a@example.com>", content_type),
        "body": {"data": b64("hello")},
    }
    assert parse_message(gmail_message(payload=payload)).body == "hello"
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_gmail_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.gmail_parse'`.

- [ ] **Step 4: Implement**

Create `app/gmail_parse.py`:

```python
"""Turn a Gmail API message resource into ticket fields.

Pure: no network, no DB. Only labeled mail gets here, so volume is small; the body cap
keeps each ticket description small because descriptions reach LLM clients through MCP.
"""

import base64
import html
import re
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser

from app.services import TITLE_MAX_LENGTH

EMAIL_BODY_MAX_CHARS = 2000  # starting value; tune with real marketing mail
SENDER_MAX_LENGTH = 255

_SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fwd?)\s*:\s*)+", re.IGNORECASE)
# ponytail: English reply header only ("On <date>, <name> wrote:", which Gmail may wrap onto
# a second line); add localized patterns if the team's mail clients use another locale.
_REPLY_HEADER = re.compile(r"^On\b[^\n]*(?:\n[^\n]*)?\bwrote:[ \t]*$", re.MULTILINE)
_CHARSET = re.compile(r'charset="?([\w.:-]+)', re.IGNORECASE)


@dataclass(frozen=True)
class ParsedEmail:
    title: str
    body: str
    sender: str
    attachment_count: int

    @property
    def description(self) -> str:
        parts = [f"From: {self.sender}", self.body]
        if self.attachment_count:
            noun = "attachment" if self.attachment_count == 1 else "attachments"
            parts.append(f"({self.attachment_count} {noun} not imported)")
        return "\n\n".join(part for part in parts if part)


class _TextExtractor(HTMLParser):
    _SKIP = {"head", "title", "style", "script", "blockquote"}
    _BREAK = {"br", "p", "div", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BREAK:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in self._BREAK:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            self.chunks.append(data)


def _html_to_text(markup: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    return "".join(extractor.chunks)


def _header(headers: list[dict], name: str) -> str:
    wanted = name.lower()
    return next(
        (h.get("value") or "" for h in headers if (h.get("name") or "").lower() == wanted),
        "",
    )


def _parts(part: dict) -> Iterator[dict]:
    yield part
    for child in part.get("parts") or []:
        yield from _parts(child)


def _decode(part: dict) -> str:
    data = (part.get("body") or {}).get("data") or ""
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    match = _CHARSET.search(_header(part.get("headers") or [], "Content-Type"))
    try:
        return raw.decode(match.group(1) if match else "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _raw_body(payload: dict) -> str:
    parts = [part for part in _parts(payload) if not part.get("filename")]
    for mime_type in ("text/plain", "text/html"):
        part = next(
            (
                p
                for p in parts
                if p.get("mimeType") == mime_type and (p.get("body") or {}).get("data")
            ),
            None,
        )
        if part is not None:
            text = _decode(part)
            return _html_to_text(text) if mime_type == "text/html" else text
    return ""


def _collapse(text: str) -> str:
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _strip_quoted(text: str) -> str:
    match = _REPLY_HEADER.search(text)
    if match:
        text = text[: match.start()]
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(">"))


def _truncate(body: str) -> str:
    if len(body) <= EMAIL_BODY_MAX_CHARS:
        return body
    omitted = len(body) - EMAIL_BODY_MAX_CHARS
    return f"{body[:EMAIL_BODY_MAX_CHARS].rstrip()}\n\n…(truncated, {omitted} chars omitted)"


def parse_message(message: dict) -> ParsedEmail:
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    sender = " ".join(_header(headers, "From").split())[:SENDER_MAX_LENGTH] or "unknown sender"
    subject = " ".join(_SUBJECT_PREFIX.sub("", _header(headers, "Subject")).split())
    title = (subject or f"(no subject) from {sender}")[:TITLE_MAX_LENGTH]
    raw = _collapse(_raw_body(payload).replace("\r\n", "\n"))
    body = _collapse(_strip_quoted(raw)) or raw or html.unescape(message.get("snippet") or "")
    attachments = sum(1 for part in _parts(payload) if part.get("filename"))
    return ParsedEmail(
        title=title, body=_truncate(body), sender=sender, attachment_count=attachments
    )
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_gmail_parse.py -v`
Expected: all PASS. If `test_html_only_message_drops_style_script_and_markup` fails on whitespace, print `repr(body)` and fix `_collapse`. Do not loosen the assertion.

- [ ] **Step 6: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add app/gmail_parse.py tests/gmail_messages.py tests/test_gmail_parse.py
git commit -m "feat: parse Gmail messages into compact ticket fields

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Gmail client, OAuth state, and token encryption

**Files:**
- Create: `app/gmail.py`
- Test: `tests/test_gmail_client.py`

**Interfaces:**
- Consumes: `settings.google_*` and `settings.token_encryption_key` (Task 1).
- Produces (all in `app.gmail`):
  - Constants: `AUTH_URL`, `TOKEN_URL`, `REVOKE_URL`, `API_URL`, `SCOPE`.
  - `class GmailAuthError(Exception)`: the refresh token is no longer valid.
  - `gmail_is_configured(config=settings) -> bool`.
  - `encrypt_token(token: str, config=settings) -> str` and `decrypt_token(value: str, config=settings) -> str`. Decryption raises `cryptography.fernet.InvalidToken` on a bad key.
  - `issue_gmail_state(project_id: str, user_id: str) -> str`.
  - `read_gmail_state(signed_state: str, user_id: str) -> str`. Returns the project id. Raises `HTTPException` 400 (bad or expired) or 403 (another user). The TTL is `_STATE_MAX_AGE = 600` seconds.
  - `authorize_url(state: str, config=settings) -> str`.
  - `GmailClient(config=settings, client: httpx.Client | None = None)`. It is a context manager with these methods:
    - `exchange_code(code) -> tuple[str, str]`, returning `(access_token, refresh_token)`.
    - `access_token(refresh_token) -> tuple[str, int]`, returning `(token, expires_in)`. Raises `GmailAuthError` on `invalid_grant`.
    - `revoke(refresh_token) -> None`.
    - `profile(access_token) -> dict`, containing `emailAddress` and `historyId`.
    - `labels(access_token) -> list[dict]`, user labels only.
    - `history(access_token, start_history_id) -> tuple[list[dict], str]`, returning `(records, latest_history_id)`.
    - `recent_label_messages(access_token, label_id) -> list[dict]`, returning `{id, threadId}` items from the last 2 days.
    - `message(access_token, message_id) -> dict`, in full format.
  - HTTP errors surface as `httpx.HTTPStatusError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gmail_client.py`:

```python
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

import app.gmail as gmail_module
from app.config import Settings
from app.gmail import (
    SCOPE,
    GmailAuthError,
    GmailClient,
    authorize_url,
    decrypt_token,
    encrypt_token,
    gmail_is_configured,
    issue_gmail_state,
    read_gmail_state,
)

REDIRECT = "https://kanban.example.com/integrations/gmail/callback"


@pytest.fixture
def gmail_settings():
    return Settings(
        _env_file=None,
        session_secret="test-session-secret",
        google_client_id="gid",
        google_client_secret="gsecret",
        google_redirect_uri=REDIRECT,
        token_encryption_key=Fernet.generate_key().decode(),
    )


def _client(config, handler):
    return GmailClient(config, httpx.Client(transport=httpx.MockTransport(handler)))


def test_configuration_requires_every_google_setting(gmail_settings):
    assert gmail_is_configured(gmail_settings) is True
    for field in (
        "google_client_id",
        "google_client_secret",
        "google_redirect_uri",
        "token_encryption_key",
    ):
        assert gmail_is_configured(gmail_settings.model_copy(update={field: None})) is False


def test_refresh_token_round_trips_through_encryption(gmail_settings):
    stored = encrypt_token("1//refresh", gmail_settings)
    assert "1//refresh" not in stored
    assert decrypt_token(stored, gmail_settings) == "1//refresh"


def test_authorize_url_requests_offline_readonly_access(gmail_settings):
    url = authorize_url("signed-state", gmail_settings)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert parse_qs(urlparse(url).query) == {
        "client_id": ["gid"],
        "redirect_uri": [REDIRECT],
        "response_type": ["code"],
        "scope": [SCOPE],
        "access_type": ["offline"],
        "prompt": ["consent"],
        "state": ["signed-state"],
    }
    assert SCOPE == "https://www.googleapis.com/auth/gmail.readonly"


def test_state_is_bound_to_the_user():
    state = issue_gmail_state("project-1", "user-1")
    assert read_gmail_state(state, "user-1") == "project-1"
    with pytest.raises(HTTPException) as other_user:
        read_gmail_state(state, "user-2")
    assert other_user.value.status_code == 403
    with pytest.raises(HTTPException) as tampered:
        read_gmail_state(state + "x", "user-1")
    assert tampered.value.status_code == 400


def test_expired_state_is_rejected(monkeypatch):
    state = issue_gmail_state("project-1", "user-1")
    monkeypatch.setattr(gmail_module, "_STATE_MAX_AGE", -1)
    with pytest.raises(HTTPException) as expired:
        read_gmail_state(state, "user-1")
    assert expired.value.status_code == 400


def test_exchange_code_returns_access_and_refresh_tokens(gmail_settings):
    def handler(request):
        assert request.url == "https://oauth2.googleapis.com/token"
        assert parse_qs(request.content.decode()) == {
            "code": ["auth-code"],
            "redirect_uri": [REDIRECT],
            "grant_type": ["authorization_code"],
            "client_id": ["gid"],
            "client_secret": ["gsecret"],
        }
        return httpx.Response(
            200,
            json={"access_token": "ya29.access", "refresh_token": "1//refresh", "expires_in": 3599},
        )

    with _client(gmail_settings, handler) as gmail:
        assert gmail.exchange_code("auth-code") == ("ya29.access", "1//refresh")


def test_exchange_code_without_refresh_token_is_rejected(gmail_settings):
    def handler(request):
        return httpx.Response(200, json={"access_token": "ya29.access"})

    with _client(gmail_settings, handler) as gmail, pytest.raises(ValueError):
        gmail.exchange_code("auth-code")


def test_access_token_uses_the_refresh_grant(gmail_settings):
    def handler(request):
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == ["1//refresh"]
        return httpx.Response(200, json={"access_token": "ya29.fresh", "expires_in": 3599})

    with _client(gmail_settings, handler) as gmail:
        assert gmail.access_token("1//refresh") == ("ya29.fresh", 3599)


def test_invalid_grant_raises_auth_error(gmail_settings):
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with _client(gmail_settings, handler) as gmail, pytest.raises(GmailAuthError):
        gmail.access_token("1//revoked")


def test_other_token_failures_raise_http_errors(gmail_settings):
    def handler(request):
        return httpx.Response(503, json={"error": "backend_error"})

    with _client(gmail_settings, handler) as gmail, pytest.raises(httpx.HTTPStatusError):
        gmail.access_token("1//refresh")


def test_history_follows_pages_and_returns_latest_history_id(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/history"
        assert request.headers["Authorization"] == "Bearer ya29.access"
        assert request.url.params["startHistoryId"] == "100"
        assert request.url.params.get_list("historyTypes") == ["messageAdded", "labelAdded"]
        if request.url.params.get("pageToken") == "p2":
            return httpx.Response(200, json={"history": [{"id": "102"}], "historyId": "106"})
        return httpx.Response(
            200,
            json={"history": [{"id": "101"}], "nextPageToken": "p2", "historyId": "105"},
        )

    with _client(gmail_settings, handler) as gmail:
        assert gmail.history("ya29.access", "100") == ([{"id": "101"}, {"id": "102"}], "106")


def test_history_without_changes(gmail_settings):
    def handler(request):
        return httpx.Response(200, json={"historyId": "100"})

    with _client(gmail_settings, handler) as gmail:
        assert gmail.history("ya29.access", "100") == ([], "100")


def test_expired_history_raises_404(gmail_settings):
    def handler(request):
        return httpx.Response(404, json={"error": {"code": 404}})

    with _client(gmail_settings, handler) as gmail, pytest.raises(httpx.HTTPStatusError) as exc:
        gmail.history("ya29.access", "1")
    assert exc.value.response.status_code == 404


def test_labels_returns_only_user_labels(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/labels"
        return httpx.Response(
            200,
            json={
                "labels": [
                    {"id": "INBOX", "name": "INBOX", "type": "system"},
                    {"id": "Label_1", "name": "Flyers", "type": "user"},
                ]
            },
        )

    with _client(gmail_settings, handler) as gmail:
        assert gmail.labels("ya29.access") == [{"id": "Label_1", "name": "Flyers", "type": "user"}]


def test_recent_label_messages_filters_by_label_and_age(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/messages"
        assert request.url.params["labelIds"] == "Label_1"
        assert request.url.params["q"] == "newer_than:2d"
        if request.url.params.get("pageToken") == "p2":
            return httpx.Response(200, json={"resultSizeEstimate": 0})
        return httpx.Response(
            200,
            json={"messages": [{"id": "m1", "threadId": "m1"}], "nextPageToken": "p2"},
        )

    with _client(gmail_settings, handler) as gmail:
        assert gmail.recent_label_messages("ya29.access", "Label_1") == [
            {"id": "m1", "threadId": "m1"}
        ]


def test_message_fetches_full_format(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/messages/m1"
        assert request.url.params["format"] == "full"
        return httpx.Response(200, json={"id": "m1"})

    with _client(gmail_settings, handler) as gmail:
        assert gmail.message("ya29.access", "m1") == {"id": "m1"}


def test_revoke_posts_the_refresh_token(gmail_settings):
    def handler(request):
        assert request.url == "https://oauth2.googleapis.com/revoke"
        assert parse_qs(request.content.decode()) == {"token": ["1//refresh"]}
        return httpx.Response(200)

    with _client(gmail_settings, handler) as gmail:
        gmail.revoke("1//refresh")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_gmail_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.gmail'`.

- [ ] **Step 3: Implement**

Create `app/gmail.py`:

```python
from urllib.parse import quote, urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer

from app.config import Settings, settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://gmail.googleapis.com/gmail/v1/users/me/"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

_STATE_MAX_AGE = 600
_STATE_SERIALIZER = URLSafeTimedSerializer(settings.session_secret, salt="kf-gmail-connect")


class GmailAuthError(Exception):
    """The stored refresh token no longer works (invalid_grant); the owner must reconnect."""


def gmail_is_configured(config: Settings = settings) -> bool:
    return all(
        (
            config.google_client_id,
            config.google_client_secret,
            config.google_redirect_uri,
            config.token_encryption_key,
        )
    )


def encrypt_token(token: str, config: Settings = settings) -> str:
    return Fernet(config.token_encryption_key).encrypt(token.encode()).decode()


def decrypt_token(value: str, config: Settings = settings) -> str:
    return Fernet(config.token_encryption_key).decrypt(value.encode()).decode()


def issue_gmail_state(project_id: str, user_id: str) -> str:
    return _STATE_SERIALIZER.dumps({"project_id": project_id, "user_id": user_id})


def read_gmail_state(signed_state: str, user_id: str) -> str:
    try:
        payload = _STATE_SERIALIZER.loads(signed_state, max_age=_STATE_MAX_AGE)
        project_id = payload["project_id"]
        state_user_id = payload["user_id"]
    except (BadData, KeyError, TypeError):
        raise HTTPException(400, "Invalid Gmail state") from None
    if state_user_id != user_id:
        raise HTTPException(403, "Gmail state belongs to another user")
    return project_id


def authorize_url(state: str, config: Settings = settings) -> str:
    query = urlencode(
        {
            "client_id": config.google_client_id,
            "redirect_uri": config.google_redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def _oauth_error(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("error") if isinstance(body, dict) else None


class GmailClient:
    def __init__(self, config: Settings = settings, client: httpx.Client | None = None) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.Client()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _token_request(self, data: dict[str, str]) -> dict:
        response = self._client.post(
            TOKEN_URL,
            data={
                **data,
                "client_id": self._config.google_client_id,
                "client_secret": self._config.google_client_secret,
            },
            timeout=10.0,
        )
        if response.status_code == 400 and _oauth_error(response) == "invalid_grant":
            raise GmailAuthError("Google refresh token is no longer valid")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            raise ValueError("Google OAuth response missing access token")
        return body

    def exchange_code(self, code: str) -> tuple[str, str]:
        body = self._token_request(
            {
                "code": code,
                "redirect_uri": self._config.google_redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        refresh_token = body.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ValueError("Google OAuth response missing refresh token")
        return body["access_token"], refresh_token

    def access_token(self, refresh_token: str) -> tuple[str, int]:
        body = self._token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})
        expires_in = body.get("expires_in")
        return body["access_token"], expires_in if isinstance(expires_in, int) else 3600

    def revoke(self, refresh_token: str) -> None:
        response = self._client.post(REVOKE_URL, data={"token": refresh_token}, timeout=10.0)
        response.raise_for_status()

    def _get(self, access_token: str, path: str, params: dict | None = None) -> dict:
        response = self._client.get(
            f"{API_URL}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=10.0,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Gmail API returned invalid JSON")
        return body

    def _pages(
        self, access_token: str, path: str, params: dict, item_key: str
    ) -> tuple[list[dict], dict]:
        items: list[dict] = []
        query = params
        while True:
            body = self._get(access_token, path, query)
            items.extend(body.get(item_key) or [])
            page_token = body.get("nextPageToken")
            if not page_token:
                return items, body
            query = {**params, "pageToken": page_token}

    def profile(self, access_token: str) -> dict:
        return self._get(access_token, "profile")

    def labels(self, access_token: str) -> list[dict]:
        labels = self._get(access_token, "labels").get("labels") or []
        return [label for label in labels if label.get("type") == "user"]

    def history(self, access_token: str, start_history_id: str) -> tuple[list[dict], str]:
        records, last_page = self._pages(
            access_token,
            "history",
            {"startHistoryId": start_history_id, "historyTypes": ["messageAdded", "labelAdded"]},
            "history",
        )
        return records, str(last_page.get("historyId") or start_history_id)

    def recent_label_messages(self, access_token: str, label_id: str) -> list[dict]:
        messages, _ = self._pages(
            access_token, "messages", {"labelIds": label_id, "q": "newer_than:2d"}, "messages"
        )
        return messages

    def message(self, access_token: str, message_id: str) -> dict:
        return self._get(access_token, f"messages/{quote(message_id, safe='')}", {"format": "full"})
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_gmail_client.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add app/gmail.py tests/test_gmail_client.py
git commit -m "feat: add Gmail OAuth and API client

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Sync cycle and poll loop

**Files:**
- Create: `app/gmail_sync.py`
- Test: `tests/test_gmail_sync.py`

**Interfaces:**
- Consumes:
  - `GmailClient` methods, `GmailAuthError`, `decrypt_token` (Task 4).
  - `parse_message` (Task 3).
  - `GmailConnection` and `GmailConnectionStatus` (Task 2).
  - `services.create_ticket(session, project, creator, *, title, description, type, priority, meta, tasks)`.
  - `IntegrationDelivery(provider, delivery_id, event_type)`.
- Produces:
  - `POLL_SECONDS = 90`.
  - `sync_connection(session: Session, connection: GmailConnection, gmail) -> None`. It raises on transient errors and leaves `history_id` unchanged when it does.
  - `sync_all(engine: Engine | None = None) -> None`. It never raises for a per-connection failure.
  - `async poll_forever() -> None`.
  - `_access_tokens: dict[str, tuple[str, float]]`, a module-level cache. Tests clear it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gmail_sync.py`:

```python
import asyncio

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session, select

import app.gmail_sync as gmail_sync
import app.notifications as notifications
from app.config import settings
from app.gmail import GmailAuthError, encrypt_token
from app.gmail_sync import sync_all, sync_connection
from app.models import (
    GmailConnection,
    GmailConnectionStatus,
    Priority,
    ProjectChatWebhook,
    Ticket,
    TicketType,
    WebhookType,
)
from tests.gmail_messages import gmail_message

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
        return self.messages[message_id]


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
                {"message": {"id": "m1", "threadId": "m1", "labelIds": [FLYERS]}, "labelIds": [FLYERS]}
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
                {"message": {"id": "m1", "threadId": "m1", "labelIds": [URGENT, FLYERS]}, "labelIds": [FLYERS]}
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
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_gmail_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.gmail_sync'`.

- [ ] **Step 3: Implement**

Create `app/gmail_sync.py`:

```python
import asyncio
import logging
import time

import httpx
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import Engine, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db import get_engine
from app.gmail import GmailAuthError, GmailClient, decrypt_token
from app.gmail_parse import parse_message
from app.models import (
    GmailConnection,
    GmailConnectionStatus,
    IntegrationDelivery,
    Priority,
    Project,
    TicketType,
    User,
    utcnow,
)
from app.services import create_ticket

logger = logging.getLogger(__name__)

POLL_SECONDS = 90
# Errors a retry can never fix: bad mail content, a mapping that fails validation.
_PERMANENT_ERRORS = (HTTPException, ValueError, KeyError, TypeError)
_access_tokens: dict[str, tuple[str, float]] = {}  # connection id -> (token, monotonic expiry)


def _access_token(connection: GmailConnection, gmail: GmailClient) -> str:
    cached = _access_tokens.get(connection.id)
    if cached and cached[1] > time.monotonic() + 60:
        return cached[0]
    token, expires_in = gmail.access_token(decrypt_token(connection.refresh_token_enc))
    _access_tokens[connection.id] = (token, time.monotonic() + expires_in)
    return token


def _labeled_message_ids(history: list[dict], mapped: set[str]) -> list[str]:
    ids: dict[str, None] = {}
    for record in history:
        for item in (record.get("messagesAdded") or []) + (record.get("labelsAdded") or []):
            message = item.get("message") or {}
            labels = set(item.get("labelIds") or []) | set(message.get("labelIds") or [])
            if message.get("id") and labels & mapped:
                ids[message["id"]] = None
    return list(ids)


def _import_message(session: Session, connection: GmailConnection, message: dict) -> str | None:
    """Create a ticket for one message. Returns an error for mail that can never import."""
    label_id = next(
        (label for label in message.get("labelIds") or [] if label in connection.label_mapping),
        None,
    )
    if label_id is None:
        return None  # the label came off before this cycle reached the message
    message_key = f"{connection.project_id}:{message['id']}"
    thread_key = f"{connection.project_id}:{message.get('threadId') or message['id']}"
    seen = session.exec(
        select(IntegrationDelivery.id).where(
            or_(
                and_(
                    IntegrationDelivery.provider == "GMAIL",
                    IntegrationDelivery.delivery_id == message_key,
                ),
                and_(
                    IntegrationDelivery.provider == "GMAIL_THREAD",
                    IntegrationDelivery.delivery_id == thread_key,
                ),
            )
        )
    ).first()
    if seen is not None:
        return None
    tasks = BackgroundTasks()
    try:
        mapping = connection.label_mapping[label_id]
        parsed = parse_message(message)
        session.add(
            IntegrationDelivery(provider="GMAIL", delivery_id=message_key, event_type="message")
        )
        session.add(
            IntegrationDelivery(
                provider="GMAIL_THREAD", delivery_id=thread_key, event_type="thread"
            )
        )
        create_ticket(
            session,
            session.get(Project, connection.project_id),
            session.get(User, connection.user_id),
            title=parsed.title,
            description=parsed.description,
            type=TicketType(mapping["type"]),
            priority=Priority(mapping["priority"]),
            meta={
                "source": "gmail",
                "from": parsed.sender,
                "message_id": message["id"],
                "thread_id": message.get("threadId"),
            },
            tasks=tasks,
        )
    except IntegrityError:
        session.rollback()  # another worker imported this message first
        return None
    except _PERMANENT_ERRORS as exc:
        session.rollback()
        detail = exc.detail if isinstance(exc, HTTPException) else f"{type(exc).__name__}: {exc}"
        return f"Skipped Gmail message {message['id']}: {detail}"
    for task in tasks.tasks:  # same chat notification as web-created tickets
        task.func(*task.args, **task.kwargs)
    return None


def sync_connection(session: Session, connection: GmailConnection, gmail: GmailClient) -> None:
    """Import newly labeled mail for one connection.

    Transient Gmail or network errors propagate before `history_id` moves, so the next cycle
    replays the batch; delivery rows keep the replay from creating duplicate tickets.
    """
    token = _access_token(connection, gmail)
    try:
        history, latest = gmail.history(token, connection.history_id)
        message_ids = _labeled_message_ids(history, set(connection.label_mapping))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        # Gmail keeps roughly a week of history; past that, rescan recent labeled mail.
        message_ids = list(
            dict.fromkeys(
                message["id"]
                for label_id in connection.label_mapping
                for message in gmail.recent_label_messages(token, label_id)
            )
        )
        latest = str(gmail.profile(token)["historyId"])
    errors = []
    for message_id in message_ids:
        error = _import_message(session, connection, gmail.message(token, message_id))
        if error:
            errors.append(error)
    connection.history_id = latest
    connection.last_synced_at = utcnow()
    connection.last_error = errors[-1][:500] if errors else None
    session.add(connection)
    session.commit()


def sync_all(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with Session(engine) as session:
        connection_ids = session.exec(
            select(GmailConnection.id).where(
                GmailConnection.status == GmailConnectionStatus.ACTIVE
            )
        ).all()
    if not connection_ids:
        return
    with GmailClient() as gmail:
        for connection_id in connection_ids:
            with Session(engine) as session:
                connection = session.get(GmailConnection, connection_id)
                if connection is None or connection.status != GmailConnectionStatus.ACTIVE:
                    continue
                try:
                    sync_connection(session, connection, gmail)
                except GmailAuthError:
                    session.rollback()
                    _access_tokens.pop(connection_id, None)
                    connection.status = GmailConnectionStatus.NEEDS_REAUTH
                    connection.last_error = "Google access was revoked. Reconnect Gmail."
                    session.add(connection)
                    session.commit()
                    logger.warning("gmail connection needs reauth: connection_id=%s", connection_id)
                except Exception as exc:
                    session.rollback()
                    _access_tokens.pop(connection_id, None)  # a 401 means the cached token died
                    connection.last_error = f"{type(exc).__name__}; retrying next cycle"
                    session.add(connection)
                    session.commit()
                    logger.warning(
                        "gmail sync failed: connection_id=%s", connection_id, exc_info=True
                    )


async def poll_forever() -> None:
    # ponytail: runs inside the single uvicorn worker (Dockerfile `--workers 1`). Extra
    # workers would each poll; delivery rows still block duplicate tickets, but move this to
    # one separate process (or add a lock) before scaling out.
    while True:
        await asyncio.sleep(POLL_SECONDS)
        try:
            await asyncio.to_thread(sync_all)
        except Exception:
            logger.exception("gmail poll cycle failed")
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_gmail_sync.py -v`
Expected: all PASS.

If `test_created_ticket_notifies_chat_webhooks` fails, check `schedule()` in `app/notifications.py`. It enqueues `dispatch` by module global, so the monkeypatch on `app.notifications.dispatch` has to be what `tasks.add_task` receives. Do not change `notifications.py` to make the test pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add app/gmail_sync.py tests/test_gmail_sync.py
git commit -m "feat: poll Gmail history and create tickets from labeled mail

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Start the poller in the app lifespan

**Files:**
- Modify: `app/main.py:1-31`
- Test: `tests/test_gmail_sync.py` (append)

**Interfaces:**
- Consumes: `gmail_is_configured()` (Task 4), `poll_forever()` (Task 5).
- Produces: `app.main.poll_forever`, a name that Task 7 tests monkeypatch.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gmail_sync.py` (add `from fastapi.testclient import TestClient` and `import app.main as main` to the imports):

```python
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
```

The autouse `encryption_key` fixture already sets `token_encryption_key`.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/test_gmail_sync.py::test_lifespan_starts_poller_only_when_gmail_is_configured -v`
Expected: FAIL with `AttributeError: <module 'app.main'> has no attribute 'poll_forever'`.

- [ ] **Step 3: Implement**

In `app/main.py`, add `import asyncio` at the top. Add the two imports below to the `app.*` import block, keeping it sorted:

```python
from app.gmail import gmail_is_configured
from app.gmail_sync import poll_forever
```

Replace `lifespan` with:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    poller = asyncio.create_task(poll_forever()) if gmail_is_configured() else None
    try:
        async with http_session_manager(settings.allowed_hosts, settings.allowed_origins):
            yield
    finally:
        if poller is not None:
            poller.cancel()
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_gmail_sync.py tests/test_health.py tests/test_mcp_server.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add app/main.py tests/test_gmail_sync.py
git commit -m "feat: run Gmail poller in the app lifespan

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Connect flow, label picker, disconnect, settings card

**Files:**
- Create: `app/routers/gmail.py`
- Modify: `app/routers/web_sprints.py`
  - `_settings` signature and context (lines 74–134)
  - `project_settings` (lines 332–350)
  - `_GITHUB_ERRORS` area (lines 67–71)
  - imports
- Modify: `app/main.py`: include the router before `web_sprints.router`
- Modify: `app/templates/project_settings.html`: new card after the GitHub `</article>`
- Test: `tests/test_web_gmail.py`

**Interfaces:**
- Consumes: everything from Tasks 2 and 4; `project_owner`, `current_user`, `verify_csrf` from `app.auth`; `web_sprints._settings`.
- Produces these routes:
  - `POST /projects/{slug}/settings/integrations/gmail/connect`
  - `GET /integrations/gmail/callback`
  - `GET` and `POST /projects/{slug}/settings/integrations/gmail/labels`. The POST takes form fields `label_ids[]`, `all_label_ids[]`, `types[]`, `priorities[]`.
  - `POST /projects/{slug}/settings/integrations/gmail/disconnect`, with `confirm=Disconnect Gmail`.
- Produces: `_settings(..., gmail_available_labels: list[dict] | None = None)`.

**Route precedence:** `web_sprints` has a `POST /projects/{slug}/settings/integrations/{provider}/disconnect` route that would 404 for `gmail`. The Gmail router must be included **before** `web_sprints.router`. `test_disconnect_revokes_and_deletes` pins this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_gmail.py`:

```python
import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlmodel import select

import app.main as main
import app.routers.gmail as gmail_routes
from app.auth import make_csrf_token
from app.config import settings
from app.gmail import AUTH_URL, decrypt_token, encrypt_token, issue_gmail_state, read_gmail_state
from app.gmail import GmailClient as RealGmailClient
from app.models import GmailConnection, GmailConnectionStatus


@pytest.fixture
def configured_gmail(monkeypatch):
    for name, value in {
        "google_client_id": "gid",
        "google_client_secret": "gsecret",
        "google_redirect_uri": "https://kanban.example.com/integrations/gmail/callback",
        "token_encryption_key": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setattr(settings, name, value)
    # The lifespan starts the real poller once Gmail is configured; keep it idle in route tests.
    monkeypatch.setattr(main, "poll_forever", lambda: asyncio.sleep(3600))


@pytest.fixture
def world(make_user, make_project, add_member):
    owner = make_user(email="ada@example.com", name="Ada")
    member = make_user(email="bob@example.com", name="Bob")
    project = make_project(owner, name="Marketing")
    add_member(project, member)
    return SimpleNamespace(owner=owner, member=member, project=project)


def _csrf(user):
    return {"_csrf": make_csrf_token(user.id)}


def _use_google(monkeypatch, *, email="marketing@example.com", history_id="500", token_error=None,
                revoke_status=200, requests=None):
    labels = [
        {"id": "Label_1", "name": "Flyers", "type": "user"},
        {"id": "Label_2", "name": "Urgent", "type": "user"},
        {"id": "INBOX", "name": "INBOX", "type": "system"},
    ]

    def handler(request):
        if requests is not None:
            requests.append(request)
        if request.url.path == "/token":
            if token_error:
                return httpx.Response(400, json={"error": token_error})
            form = parse_qs(request.content.decode())
            body = {"access_token": "ya29.access", "expires_in": 3599}
            if form["grant_type"] == ["authorization_code"]:
                body["refresh_token"] = "1//refresh"
            return httpx.Response(200, json=body)
        if request.url.path == "/revoke":
            return httpx.Response(revoke_status)
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": email, "historyId": history_id})
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json={"labels": labels})
        raise AssertionError(f"unexpected Google request {request.url}")

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(gmail_routes, "GmailClient", lambda: RealGmailClient(settings, transport))


def _connection(session, world, **overrides):
    values = {
        "project_id": world.project.id,
        "user_id": world.owner.id,
        "google_email": "marketing@example.com",
        "refresh_token_enc": encrypt_token("1//refresh"),
        "history_id": "100",
        "label_mapping": {"Label_1": {"name": "Flyers", "type": "TASK", "priority": "MEDIUM"}},
    }
    values.update(overrides)
    row = GmailConnection(**values)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _stored(session, world):
    session.expire_all()
    return session.exec(
        select(GmailConnection).where(GmailConnection.project_id == world.project.id)
    ).first()


def _callback(client, world, user, **params):
    query = {"state": issue_gmail_state(world.project.id, user.id), "code": "auth-code", **params}
    return client.get("/integrations/gmail/callback", params=query, follow_redirects=False)


def test_owner_starts_gmail_connect(configured_gmail, client, world, login_as):
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/connect",
        data=_csrf(world.owner),
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(AUTH_URL)
    state = parse_qs(urlparse(location).query)["state"][0]
    assert read_gmail_state(state, world.owner.id) == world.project.id


def test_member_cannot_start_gmail_connect(configured_gmail, client, world, login_as):
    login_as(world.member.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/connect", data=_csrf(world.member)
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "form"),
    [
        ("get", "labels", None),
        ("post", "labels", {"label_ids": [], "all_label_ids": [], "types": [], "priorities": []}),
        ("post", "disconnect", {"confirm": "Disconnect Gmail"}),
    ],
)
def test_member_is_blocked_from_gmail_owner_routes(
    configured_gmail, client, world, login_as, session, method, path, form
):
    _connection(session, world)
    login_as(world.member.email)
    url = f"/projects/marketing/settings/integrations/gmail/{path}"
    if method == "get":
        response = client.get(url)
    else:
        response = client.post(url, data={**_csrf(world.member), **form})
    assert response.status_code == 403
    assert _stored(session, world) is not None


def test_connect_without_google_settings_is_unavailable(client, world, login_as):
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/connect", data=_csrf(world.owner)
    )
    assert response.status_code == 503


def test_callback_stores_encrypted_connection_and_opens_label_picker(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _use_google(monkeypatch)
    login_as(world.owner.email)
    response = _callback(client, world, world.owner)
    assert response.status_code == 303
    assert response.headers["location"] == "/projects/marketing/settings/integrations/gmail/labels"
    row = _stored(session, world)
    assert row.google_email == "marketing@example.com"
    assert row.history_id == "500"
    assert row.status == GmailConnectionStatus.ACTIVE
    assert row.user_id == world.owner.id
    assert "1//refresh" not in row.refresh_token_enc
    assert decrypt_token(row.refresh_token_enc) == "1//refresh"


def test_callback_rejects_state_from_another_user(configured_gmail, client, world, login_as):
    login_as(world.member.email)
    state = issue_gmail_state(world.project.id, world.owner.id)
    response = client.get("/integrations/gmail/callback", params={"state": state, "code": "c"})
    assert response.status_code == 403


def test_callback_requires_owner_role(configured_gmail, client, world, login_as, session):
    login_as(world.member.email)
    response = _callback(client, world, world.member)
    assert response.status_code == 403
    assert _stored(session, world) is None


def test_callback_access_denied_shows_message(configured_gmail, client, world, login_as, session):
    login_as(world.owner.email)
    response = _callback(client, world, world.owner, error="access_denied")
    assert response.headers["location"] == "/projects/marketing/settings?gmail_error=oauth_denied"
    assert _stored(session, world) is None
    assert "Gmail authorization was denied." in client.get(response.headers["location"]).text


def test_callback_google_failure_shows_message(
    configured_gmail, client, world, login_as, monkeypatch
):
    _use_google(monkeypatch, token_error="invalid_grant")
    login_as(world.owner.email)
    response = _callback(client, world, world.owner)
    assert response.headers["location"] == (
        "/projects/marketing/settings?gmail_error=verification_failed"
    )


def test_reconnect_same_mailbox_keeps_mapping_and_history(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(
        session,
        world,
        status=GmailConnectionStatus.NEEDS_REAUTH,
        last_error="Google access was revoked. Reconnect Gmail.",
        refresh_token_enc=encrypt_token("1//old"),
    )
    _use_google(monkeypatch)
    login_as(world.owner.email)
    _callback(client, world, world.owner)
    row = _stored(session, world)
    assert row.status == GmailConnectionStatus.ACTIVE
    assert row.last_error is None
    assert row.history_id == "100"
    assert row.label_mapping == {"Label_1": {"name": "Flyers", "type": "TASK", "priority": "MEDIUM"}}
    assert decrypt_token(row.refresh_token_enc) == "1//refresh"


def test_reconnect_different_mailbox_resets_mapping_and_history(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch, email="events@example.com")
    login_as(world.owner.email)
    _callback(client, world, world.owner)
    row = _stored(session, world)
    assert row.google_email == "events@example.com"
    assert row.label_mapping == {}
    assert row.history_id == "500"


def test_label_picker_lists_only_user_labels(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch)
    login_as(world.owner.email)
    response = client.get("/projects/marketing/settings/integrations/gmail/labels")
    assert response.status_code == 200
    assert "Flyers" in response.text
    assert "Urgent" in response.text
    assert 'value="INBOX"' not in response.text
    assert 'name="label_ids" value="Label_1" checked' in response.text


def test_save_labels_stores_mapping(configured_gmail, client, world, login_as, session, monkeypatch):
    _connection(session, world)
    _use_google(monkeypatch)
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/labels",
        data={
            **_csrf(world.owner),
            "label_ids": ["Label_2"],
            "all_label_ids": ["Label_1", "Label_2"],
            "types": ["TASK", "BUG"],
            "priorities": ["MEDIUM", "URGENT"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/projects/marketing/settings?saved=1"
    assert _stored(session, world).label_mapping == {
        "Label_2": {"name": "Urgent", "type": "BUG", "priority": "URGENT"}
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"label_ids": ["Label_9"], "all_label_ids": ["Label_9"], "types": ["TASK"], "priorities": ["LOW"]},
        {"label_ids": ["Label_1"], "all_label_ids": ["Label_1"], "types": ["EPIC"], "priorities": ["LOW"]},
        {"label_ids": ["Label_1"], "all_label_ids": ["Label_1"], "types": ["TASK"], "priorities": []},
        {"label_ids": ["Label_2"], "all_label_ids": ["Label_1"], "types": ["TASK"], "priorities": ["LOW"]},
    ],
)
def test_save_labels_rejects_invalid_mapping(
    configured_gmail, client, world, login_as, session, monkeypatch, fields
):
    _connection(session, world)
    _use_google(monkeypatch)
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/labels",
        data={**_csrf(world.owner), **fields},
    )
    assert response.status_code == 422
    assert _stored(session, world).label_mapping == {
        "Label_1": {"name": "Flyers", "type": "TASK", "priority": "MEDIUM"}
    }


def test_revoked_access_on_label_page_marks_reauth(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch, token_error="invalid_grant")
    login_as(world.owner.email)
    response = client.get("/projects/marketing/settings/integrations/gmail/labels")
    assert response.status_code == 409
    assert "Reconnect Gmail" in response.text
    assert _stored(session, world).status == GmailConnectionStatus.NEEDS_REAUTH


def test_disconnect_revokes_and_deletes(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    requests = []
    _use_google(monkeypatch, requests=requests)
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/disconnect",
        data={**_csrf(world.owner), "confirm": "Disconnect Gmail"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _stored(session, world) is None
    [revoke] = [r for r in requests if r.url.path == "/revoke"]
    assert parse_qs(revoke.content.decode()) == {"token": ["1//refresh"]}


def test_disconnect_still_deletes_when_revoke_fails(
    configured_gmail, client, world, login_as, session, monkeypatch
):
    _connection(session, world)
    _use_google(monkeypatch, revoke_status=400)
    login_as(world.owner.email)
    client.post(
        "/projects/marketing/settings/integrations/gmail/disconnect",
        data={**_csrf(world.owner), "confirm": "Disconnect Gmail"},
    )
    assert _stored(session, world) is None


def test_disconnect_requires_confirmation(configured_gmail, client, world, login_as, session):
    _connection(session, world)
    login_as(world.owner.email)
    response = client.post(
        "/projects/marketing/settings/integrations/gmail/disconnect",
        data={**_csrf(world.owner), "confirm": "yes"},
    )
    assert response.status_code == 422
    assert _stored(session, world) is not None


def test_settings_card_shows_mapping_to_owner_and_status_to_member(
    configured_gmail, client, world, login_as, session
):
    _connection(session, world)
    login_as(world.owner.email)
    owner_page = client.get("/projects/marketing/settings").text
    assert "marketing@example.com" in owner_page
    assert "Flyers → TASK · MEDIUM" in owner_page
    assert "Edit labels" in owner_page

    login_as(world.member.email)
    member_page = client.get("/projects/marketing/settings").text
    assert "Gmail" in member_page
    assert "marketing@example.com" not in member_page


def test_settings_card_offers_reconnect_when_access_was_revoked(
    configured_gmail, client, world, login_as, session
):
    _connection(
        session,
        world,
        status=GmailConnectionStatus.NEEDS_REAUTH,
        last_error="Google access was revoked. Reconnect Gmail.",
    )
    login_as(world.owner.email)
    page = client.get("/projects/marketing/settings").text
    assert "Reconnect required" in page
    assert "Reconnect Gmail" in page
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/test_web_gmail.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routers.gmail'`.

- [ ] **Step 3: Add Gmail context to the settings page**

In `app/routers/web_sprints.py`:

1. Imports. Add `from app.gmail import gmail_is_configured`. Add `GmailConnection` and `GmailConnectionStatus` to the `app.models` import list.

2. Below `_GITHUB_ERRORS`, add:

```python
_GMAIL_ERRORS = {
    "oauth_denied": "Gmail authorization was denied.",
    "verification_failed": "Gmail could not be connected. Try again.",
}
```

3. In `_settings`, add the keyword parameter `gmail_available_labels: list[dict] | None = None` after `github_available_repositories`. Before `return render(`, add:

```python
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
```

Then add these keys to the context dict:

```python
            "gmail_status": gmail_status,
            "gmail_connection": gmail_connection if is_owner else None,
            "gmail_configured": gmail_is_configured() if is_owner else None,
            "gmail_available_labels": gmail_available_labels if is_owner else None,
            "ticket_types": TicketType,
            "priorities": Priority,
```

4. In `project_settings`, add the parameter `gmail_error: str | None = None` after `github_error`. Replace the `error=` argument with:

```python
        error=_GITHUB_ERRORS.get(github_error or "") or _GMAIL_ERRORS.get(gmail_error or ""),
```

- [ ] **Step 4: Create the router**

Create `app/routers/gmail.py`:

```python
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
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gmail is unavailable. Try again.") from None


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
    labels = _mailbox_labels(session, _connection(session, project))
    if labels is None:
        return _settings(
            request, session, user, project, member, error=_REVOKED,
            status_code=status.HTTP_409_CONFLICT,
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
            request, session, user, project, member, error=_REVOKED,
            status_code=status.HTTP_409_CONFLICT,
        )
    names = {label["id"]: label["name"] for label in labels}
    if not selected <= set(names):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Selected label is not available")
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
    try:
        with GmailClient() as gmail:
            gmail.revoke(decrypt_token(connection.refresh_token_enc))
    except (httpx.HTTPError, InvalidToken):
        pass  # best effort: the stored token is deleted either way
    session.delete(connection)
    session.commit()
    return _settings_redirect(project)
```

- [ ] **Step 5: Register the router before `web_sprints`**

In `app/main.py`, add `gmail` to the `from app.routers import (...)` list in alphabetical order. At the bottom, change:

```python
app.include_router(web.router)
app.include_router(web_sprints.router)
```

to:

```python
app.include_router(web.router)
# Before web_sprints: its /settings/integrations/{provider}/disconnect would swallow gmail.
app.include_router(gmail.router)
app.include_router(web_sprints.router)
```

- [ ] **Step 6: Add the settings card**

In `app/templates/project_settings.html`, find the GitHub card's closing `</article>` (the one right before `</div>` and `</section>` of the integrations section). Insert this after it:

```html
      <article class="integration-card">
        <div><h3>Gmail</h3><p class="integration-status">{{ gmail_status }}</p></div>
        {% if role == 'OWNER' %}
        {% if gmail_connection %}
        <p>Mailbox: <strong>{{ gmail_connection.google_email }}</strong></p>
        {% if gmail_connection.label_mapping %}
        <ul class="github-repository-names" aria-label="Gmail labels that create tickets">
          {% for mapping in gmail_connection.label_mapping.values() %}<li>{{ mapping['name'] }} → {{ mapping['type'] }} · {{ mapping['priority'] }}</li>{% endfor %}
        </ul>
        {% else %}
        <p>No labels create tickets yet.</p>
        {% endif %}
        {% if gmail_connection.last_error %}<p class="form-error">{{ gmail_connection.last_error }}</p>{% endif %}
        {% endif %}
        {% if gmail_available_labels is not none %}
        <form class="app-form" action="/projects/{{ project.slug }}/settings/integrations/gmail/labels" method="post" data-testid="owner-settings-controls">
          <input type="hidden" name="_csrf" value="{{ csrf_token }}">
          <fieldset class="github-repository-list">
            <legend>Labels that create tickets</legend>
            {% for label in gmail_available_labels %}
            {% set current = gmail_connection.label_mapping.get(label['id'], {}) %}
            <div class="github-repository-option">
              <input type="hidden" name="all_label_ids" value="{{ label['id'] }}">
              <label><input type="checkbox" name="label_ids" value="{{ label['id'] }}"{% if current %} checked{% endif %}> <span>{{ label['name'] }}</span></label>
              <select name="types" aria-label="Ticket type for {{ label['name'] }}">{% for ticket_type in ticket_types %}<option value="{{ ticket_type.value }}"{% if current.get('type', 'TASK') == ticket_type.value %} selected{% endif %}>{{ ticket_type.value }}</option>{% endfor %}</select>
              <select name="priorities" aria-label="Priority for {{ label['name'] }}">{% for priority in priorities %}<option value="{{ priority.value }}"{% if current.get('priority', 'MEDIUM') == priority.value %} selected{% endif %}>{{ priority.value }}</option>{% endfor %}</select>
            </div>
            {% else %}
            <p>This mailbox has no labels yet. Create one in Gmail first.</p>
            {% endfor %}
          </fieldset>
          <button class="app-primary-button" type="submit">Save labels</button>
        </form>
        {% else %}
        {% if gmail_status != 'Connected' %}
        <form action="/projects/{{ project.slug }}/settings/integrations/gmail/connect" method="post" data-testid="owner-settings-controls">
          <input type="hidden" name="_csrf" value="{{ csrf_token }}">
          <button class="app-secondary-button" type="submit"{% if not gmail_configured %} disabled{% endif %}>{{ 'Reconnect Gmail' if gmail_connection else 'Connect Gmail' }}</button>
        </form>
        {% if not gmail_configured %}<p>Google OAuth settings not configured</p>{% endif %}
        {% else %}
        <a class="app-secondary-button" href="/projects/{{ project.slug }}/settings/integrations/gmail/labels">Edit labels</a>
        {% endif %}
        {% if gmail_connection %}
        <form class="app-form" action="/projects/{{ project.slug }}/settings/integrations/gmail/disconnect" method="post" data-testid="owner-settings-controls">
          <input type="hidden" name="_csrf" value="{{ csrf_token }}">
          <label>Type <strong>Disconnect Gmail</strong> to confirm <input name="confirm" required></label>
          <button class="app-danger-button" type="submit">Disconnect</button>
        </form>
        {% endif %}
        {% endif %}
        {% endif %}
      </article>
```

- [ ] **Step 7: Run the tests and confirm they pass**

Run: `uv run pytest tests/test_web_gmail.py tests/test_web_project_settings.py -v`
Expected: all PASS.

`tests/test_authorization_matrix.py` is a hand-maintained table and does not list the GitHub integration routes either. It does not need Gmail rows. `test_member_is_blocked_from_gmail_owner_routes` covers the role checks.

- [ ] **Step 8: Commit**

```bash
uv run ruff check --fix . && uv run ruff format .
git add app/routers/gmail.py app/routers/web_sprints.py app/main.py app/templates/project_settings.html tests/test_web_gmail.py
git commit -m "feat: connect Gmail and map labels from project settings

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Setup documentation and full verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md` (new section after "## Chat notifications")

- [ ] **Step 1: Document the settings**

Append to `.env.example`:

```
# Gmail ticket intake (optional; leave empty to disable).
# TOKEN_ENCRYPTION_KEY: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=
TOKEN_ENCRYPTION_KEY=
```

Insert into `README.md` after the "Chat notifications" section:

```markdown
## Gmail ticket intake

A project owner connects one Gmail mailbox and picks which Gmail labels create
tickets (each with a ticket type and priority). About every 90 seconds the app
reads new labeled mail and adds a backlog ticket: the subject becomes the title,
and the description holds the sender plus the body without quoted replies,
capped at 2000 characters. Replies in a thread that already has a ticket are
skipped. Attachments are counted, not imported.

One-time Google setup (needs a Workspace admin):

1. Create a GCP project under the company Workspace org and enable the Gmail API.
2. OAuth consent screen: type **Internal**. Scope: `gmail.readonly`.
3. Create a Web OAuth client with redirect URI
   `https://<host>/integrations/gmail/callback`.
4. Admin console → Security → API controls: mark the client **Trusted**.
5. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, and
   `TOKEN_ENCRYPTION_KEY` in `.env`, then restart.

If the mailbox password changes or access is revoked, the settings card shows
"Reconnect required". Reconnecting the same mailbox keeps the label mapping and
imports mail that arrived in the meantime. The poller runs inside the single
uvicorn worker. Don't add workers without moving it out.
```

- [ ] **Step 2: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all tests PASS (715 baseline plus the new Gmail tests) and ruff is clean. If anything fails, fix it before continuing. Do not mark this step done while there are failures.

- [ ] **Step 3: Update the knowledge graph**

Run: `graphify update .`
Expected: completes without error (AST-only, no API cost).

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md
git commit -m "docs: document Gmail ticket intake setup

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Manual check after the Workspace admin setup (not automated)**

1. Set the four env vars on a staging or local HTTPS host, then run `uv run alembic upgrade head`.
2. In project settings, click Connect Gmail, approve, map one label, and save.
3. Apply that label to a test message in Gmail. Within about 2 minutes, a backlog ticket appears with a clean title and description.
4. Apply the label to a reply in the same thread. No second ticket is created.
5. Disconnect. The app no longer appears under the Google account's third-party access.
