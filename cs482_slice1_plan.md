# Kanban Flow Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first working slice of Kanban Flow — registration, projects, membership, tickets with atomically allocated per-project numbers, a server-rendered Kanban board, and non-blocking chat notifications — running locally under `docker compose`.

**Architecture:** A single FastAPI process serves two surfaces from one codebase: JSON routes under `/api/v1/*` and server-rendered HTML routes driven by HTMX. Both surfaces call the same functions in `app/services.py`, so ticket-number allocation and notification scheduling exist in exactly one place. Persistence is embedded SQLite in WAL mode with foreign keys enforced per connection. Chat delivery runs in a Starlette `BackgroundTask` after the response is sent, so a chat outage can never reverse a database mutation.

**Tech Stack:** Python 3.11+, uv, FastAPI, SQLModel/SQLAlchemy, Alembic, SQLite (WAL), Jinja2, HTMX, Alpine.js, Tailwind (CDN), httpx, bcrypt, itsdangerous, markdown-it-py, bleach, pytest, ruff.

**Spec:** `cs482_slice1_design.md` (which supersedes `cs482_workflow.md` wherever they disagree). Executors read both; the design document is authoritative.

---

## Global Constraints

Every task inherits these. Values are copied verbatim from the spec.

- **Python 3.11+.** Single Uvicorn worker only (`--workers 1`); SQLite permits one writer.
- **Routes that touch the database are declared `def`, not `async def`,** so the synchronous session runs in FastAPI's threadpool instead of blocking the event loop.
- **PRAGMAs applied to every connection:** `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`.
- **`ticket_number` is allocated by `UPDATE project SET next_ticket_number = next_ticket_number + 1 WHERE id = :project_id RETURNING next_ticket_number - 1`,** inside the same transaction as the ticket insert. Never `MAX(...) + 1`.
- **Project routes are keyed by `{slug}`, never `{project_id}`. `slug` is immutable** once created.
- **Error codes:** `401` unauthenticated · `403` authenticated but insufficient role · `404` resource absent **or** caller is not a project member (reads) · `409` state conflict · `422` validation failure.
- **Field limits:** `title` ≤ 255 chars, non-empty after trimming · `description` ≤ 20,000 chars · `story_points` ∈ `{1, 2, 3, 5, 8, 13}` or NULL · `meta` must be a JSON object, ≤ 3 levels of nesting, ≤ 8 KB serialized · `name` ≤ 50 · `email` ≤ 255 · project `name` ≤ 100 · `slug` ≤ 50.
- **List endpoints:** `limit` default 50, max 200, plus `cursor`.
- **Notification events:** exactly `TICKET_CREATED` and `TICKET_DONE`. No duplicate suppression.
- **Notification delivery:** 5-second per-attempt timeout, 3 retries at 1s / 2s / 4s, then one `WARNING` naming `project_id` and `ticket_number`. Never blocks or reverses the triggering mutation.
- **Markdown is stored raw and sanitized at render time only** — `markdown-it-py` with raw HTML disabled, then `bleach` with an allow-list. Never sanitized on write.
- **Session cookie:** `HttpOnly`, `SameSite=Lax`, `Secure` when `settings.secure_cookies` is true.
- **CSRF:** a session-bound signed token in a hidden form field, validated on every HTML route `POST`.
- **Out of scope for this slice:** sprints, velocity, `SprintTicketHistory`, rollover, AI reports, GitHub inbound webhooks, `ApiToken`/PAT, the MCP server, deployment, rate limiting. Do not add the columns.
- **Tests use file-backed SQLite in `tmp_path`,** never `sqlite:///:memory:`. In-memory reproduces neither WAL, nor foreign-key enforcement, nor lock contention.
- **Commit after every task.** Run `uv run ruff check . && uv run ruff format --check .` before each commit.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml` | Dependencies, ruff and pytest configuration | 1 |
| `app/config.py` | `Settings` from environment / `.env` | 1 |
| `app/main.py` | App construction, router mounting, Jinja environment, `/health` | 1, 18, 19 |
| `app/db.py` | Engine factory, PRAGMA listener, `get_session` dependency | 2 |
| `app/models.py` | The four SQLModel tables and every enum | 3 |
| `alembic/` | Migration environment and the initial revision | 4 |
| `app/auth.py` | Password hashing, session cookie, CSRF, `current_user`, project access dependencies | 5, 6, 7, 9 |
| `app/schemas.py` | Request and response models | 6, 8, 10, 11, 13 |
| `app/services.py` | `validate_meta`, `allocate_ticket_number`, `create_ticket`, `set_status` | 11, 14, 17 |
| `app/notifications.py` | Payload builder, three formatters, `dispatch`, `schedule` | 15, 16, 17 |
| `app/rendering.py` | Markdown → sanitized HTML, exposed as a Jinja filter | 18 |
| `app/routers/api_auth.py` | `/api/v1/auth/*` | 6 |
| `app/routers/api_projects.py` | `/api/v1/projects/*` including membership | 8, 9, 10 |
| `app/routers/api_tickets.py` | `/api/v1/tickets/*` and the board query | 11, 13, 14 |
| `app/routers/web.py` | Every HTML route | 19, 20, 21, 22, 23 |
| `app/templates/` | Jinja templates and card/modal partials | 19–23 |
| `tests/conftest.py` | Engine, session, client, and factory fixtures | 2, 6, 8 |
| `docker-compose.yml`, `Dockerfile`, `README.md`, `.env.example` | Local run | 26 |

Routers are split by resource rather than by layer, because the files that change together are the ones serving the same resource. `app/auth.py` holds both the credential primitives and the access dependencies: they share the session cookie and are always read together.

---

## Task 1: Project scaffolding and health check

**Files:**
- Create: `pyproject.toml`, `app/__init__.py`, `app/config.py`, `app/main.py`, `tests/__init__.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Produces: `app.config.Settings`, the module-level `app.config.settings`, and `app.main.app` (a `FastAPI` instance). Every later task imports `app.main.app` in tests and `settings` for configuration.

- [ ] **Step 1: Initialize the project and add dependencies**

```bash
uv init --no-workspace --name kanbanflow --python 3.11
rm -f main.py hello.py
uv add fastapi "uvicorn[standard]" sqlmodel alembic pydantic-settings \
       itsdangerous bcrypt jinja2 python-multipart httpx markdown-it-py bleach
uv add --dev pytest ruff
```

- [ ] **Step 2: Configure ruff and pytest in `pyproject.toml`**

Append to `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
filterwarnings = ["error::DeprecationWarning"]
```

- [ ] **Step 3: Write failing test**

`tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 4: Run the test and verify it fails**

Run: `uv run pytest tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 5: Write `app/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./kanbanflow.db"
    session_secret: str = "dev-insecure-secret-change-me"
    secure_cookies: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
```

- [ ] **Step 6: Write `app/main.py` and the empty `__init__.py` files**

```python
from fastapi import FastAPI

app = FastAPI(title="Kanban Flow")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

Create empty `app/__init__.py` and `tests/__init__.py`.

- [ ] **Step 7: Run the test and verify it passes**

Run: `uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "chore: scaffold FastAPI app with config and health check"
```

---

## Task 2: Database engine, PRAGMAs, and session dependency

**Files:**
- Create: `app/db.py`, `tests/conftest.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `app.config.settings`
- Produces:
  - `make_engine(database_url: str) -> Engine` — attaches the PRAGMA listener and sets `check_same_thread=False` so the Task 12 concurrency test can share one engine across threads.
  - `get_engine() -> Engine` — cached engine built from `settings.database_url`.
  - `get_session() -> Iterator[Session]` — the FastAPI dependency every router depends on. Tests override it.
- Produces fixtures: `engine`, `session`, `client`.

- [ ] **Step 1: Write failing test**

`tests/test_db.py`:

```python
from sqlalchemy import text


def test_pragmas_are_applied_to_every_connection(engine):
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1


def test_sqlite_supports_returning(engine):
    with engine.connect() as conn:
        version = conn.execute(text("select sqlite_version()")).scalar()
    major, minor, *_ = (int(p) for p in version.split("."))
    assert (major, minor) >= (3, 35), f"RETURNING needs SQLite 3.35+, found {version}"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL — the `engine` fixture does not exist.

- [ ] **Step 3: Write `app/db.py`**

```python
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

from app.config import settings

PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
)


def make_engine(database_url: str) -> Engine:
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        for pragma in PRAGMAS:
            cursor.execute(pragma)
        cursor.close()

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine(settings.database_url)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
```

- [ ] **Step 4: Write `tests/conftest.py`**

```python
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.db import get_session, make_engine
from app.main import app


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine):
    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: SQLite engine with WAL, foreign keys, and busy timeout"
```

---

## Task 3: Models and enums

**Files:**
- Create: `app/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces the four tables and five enums used by every subsequent task:
  - `Role` (`OWNER`, `MEMBER`), `WebhookType` (`NONE`, `TEAMS`, `SLACK`, `DISCORD`), `TicketType` (`STORY`, `BUG`, `DEMO_REQUEST`, `TASK`), `TicketStatus` (`BACKLOG`, `SELECTED`, `IN_PROGRESS`, `DONE`), `Priority` (`LOW`, `MEDIUM`, `HIGH`, `URGENT`)
  - `User`, `Project`, `ProjectMember`, `Ticket`
  - `utcnow() -> datetime` — the single timestamp source; every model default uses it.

- [ ] **Step 1: Write failing test**

`tests/test_models.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models import Project, ProjectMember, Role, Ticket, TicketStatus, TicketType, User


def make_user(session: Session, email: str = "a@b.com") -> User:
    user = User(name="A", email=email, password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_foreign_keys_are_enforced(session: Session):
    ticket = Ticket(
        ticket_number=1,
        project_id="does-not-exist",
        title="t",
        type=TicketType.TASK,
        status=TicketStatus.BACKLOG,
        creator_id="also-missing",
    )
    session.add(ticket)
    with pytest.raises(IntegrityError):
        session.commit()


def test_email_is_unique(session: Session):
    make_user(session, "dup@example.com")
    session.add(User(name="B", email="dup@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_member_pair_is_unique(session: Session):
    user = make_user(session)
    project = Project(name="P", slug="p")
    session.add(project)
    session.commit()
    session.refresh(project)
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.OWNER))
    session.commit()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.MEMBER))
    with pytest.raises(IntegrityError):
        session.commit()


def test_ticket_number_is_unique_per_project(session: Session):
    user = make_user(session)
    project = Project(name="P", slug="p")
    session.add(project)
    session.commit()
    session.refresh(project)
    for _ in range(2):
        session.add(
            Ticket(
                ticket_number=7,
                project_id=project.id,
                title="t",
                type=TicketType.TASK,
                status=TicketStatus.BACKLOG,
                creator_id=user.id,
            )
        )
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Write `app/models.py`**

```python
import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Role(str, Enum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"


class WebhookType(str, Enum):
    NONE = "NONE"
    TEAMS = "TEAMS"
    SLACK = "SLACK"
    DISCORD = "DISCORD"


class TicketType(str, Enum):
    STORY = "STORY"
    BUG = "BUG"
    DEMO_REQUEST = "DEMO_REQUEST"
    TASK = "TASK"


class TicketStatus(str, Enum):
    BACKLOG = "BACKLOG"
    SELECTED = "SELECTED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class Priority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class User(SQLModel, table=True):
    __tablename__ = "user"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(max_length=50)
    email: str = Field(max_length=255, unique=True, index=True)
    password_hash: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=utcnow)


class Project(SQLModel, table=True):
    __tablename__ = "project"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(max_length=100)
    slug: str = Field(max_length=50, unique=True, index=True)
    webhook_type: WebhookType = Field(default=WebhookType.NONE)
    webhook_url: str | None = Field(default=None, max_length=500)
    next_ticket_number: int = Field(default=1)
    created_at: datetime = Field(default_factory=utcnow)


class ProjectMember(SQLModel, table=True):
    __tablename__ = "project_member"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_member_project_user"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    role: Role = Field(default=Role.MEMBER)
    joined_at: datetime = Field(default_factory=utcnow)


class Ticket(SQLModel, table=True):
    __tablename__ = "ticket"
    __table_args__ = (
        UniqueConstraint("project_id", "ticket_number", name="uq_ticket_project_number"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    ticket_number: int = Field(index=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    title: str = Field(max_length=255)
    description: str = Field(default="", max_length=20000)
    type: TicketType = Field(default=TicketType.TASK)
    status: TicketStatus = Field(default=TicketStatus.BACKLOG, index=True)
    priority: Priority = Field(default=Priority.MEDIUM)
    story_points: int | None = Field(default=None)
    creator_id: str = Field(foreign_key="user.id")
    assignee_id: str | None = Field(default=None, foreign_key="user.id", index=True)
    resolution_notes: str | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add User, Project, ProjectMember, and Ticket models"
```

---

## Task 4: Alembic migration environment

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/versions/<hash>_initial_schema.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: `app.models` metadata, `app.config.settings`
- Produces: `uv run alembic upgrade head` as the schema-creation path used by `docker compose` in Task 26. Tests keep using `SQLModel.metadata.create_all`; this task proves the two agree.

- [ ] **Step 1: Initialize Alembic**

```bash
uv run alembic init alembic
```

- [ ] **Step 2: Point `alembic/env.py` at the app metadata**

Replace the `target_metadata` line and the URL configuration in `alembic/env.py`:

```python
from sqlmodel import SQLModel

from app.config import settings
from app import models  # noqa: F401  — imported for its side effect of registering tables

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = SQLModel.metadata
```

In both `run_migrations_offline()` and `run_migrations_online()`, add `render_as_batch=True` to the `context.configure(...)` call. SQLite cannot `ALTER` most constraints in place, and batch mode is what makes the slice 2 migration possible at all.

- [ ] **Step 3: Write failing test**

`tests/test_migrations.py`:

```python
import subprocess

from sqlalchemy import inspect
from sqlmodel import SQLModel

from app.db import make_engine


def test_migration_produces_the_same_tables_as_the_models(tmp_path):
    db = tmp_path / "migrated.db"
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        env={"DATABASE_URL": f"sqlite:///{db}", "PATH": __import__("os").environ["PATH"]},
    )
    migrated = set(inspect(make_engine(f"sqlite:///{db}")).get_table_names())
    expected = set(SQLModel.metadata.tables) | {"alembic_version"}
    assert expected <= migrated
```

- [ ] **Step 4: Run the test and verify it fails**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: FAIL — no revision exists, so `upgrade head` creates only `alembic_version`.

- [ ] **Step 5: Generate the initial revision**

```bash
uv run alembic revision --autogenerate -m "initial schema"
```

Open the generated file in `alembic/versions/` and confirm it creates `user`, `project`, `project_member`, and `ticket` with both unique constraints. Autogenerate occasionally misses SQLModel's `sa_column` JSON field — if `ticket.meta` is absent, add `sa.Column("meta", sa.JSON(), nullable=False)` by hand.

- [ ] **Step 6: Run the test and verify it passes**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add Alembic environment and initial schema revision"
```

---

## Task 5: Password hashing and session cookie primitives

**Files:**
- Create: `app/auth.py`
- Test: `tests/test_auth_primitives.py`

**Interfaces:**
- Consumes: `app.config.settings`
- Produces:
  - `SESSION_COOKIE = "kf_session"`
  - `SESSION_MAX_AGE = 60 * 60 * 24 * 14`
  - `hash_password(plain: str) -> str`
  - `verify_password(plain: str, hashed: str) -> bool`
  - `make_session_cookie(user_id: str) -> str`
  - `read_session_cookie(raw: str) -> str | None` — returns the user id, or `None` when the signature is invalid or the cookie has expired.
  - `DUMMY_HASH: str` — a precomputed bcrypt hash that Task 6 verifies against when an email is unknown, so login timing does not distinguish the two failure modes.

- [ ] **Step 1: Write failing test**

`tests/test_auth_primitives.py`:

```python
import time

from app.auth import (
    DUMMY_HASH,
    SESSION_MAX_AGE,
    hash_password,
    make_session_cookie,
    read_session_cookie,
    verify_password,
)


def test_password_round_trip():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_dummy_hash_never_verifies():
    assert verify_password("anything at all", DUMMY_HASH) is False


def test_session_cookie_round_trip():
    raw = make_session_cookie("user-123")
    assert read_session_cookie(raw) == "user-123"


def test_tampered_cookie_is_rejected():
    raw = make_session_cookie("user-123")
    assert read_session_cookie(raw[:-1] + ("x" if raw[-1] != "x" else "y")) is None


def test_expired_cookie_is_rejected(monkeypatch):
    raw = make_session_cookie("user-123")
    monkeypatch.setattr(time, "time", lambda: time.time() + SESSION_MAX_AGE + 10)
    assert read_session_cookie(raw) is None
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_auth_primitives.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 3: Write `app/auth.py`**

```python
import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

SESSION_COOKIE = "kf_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14
_SESSION_SALT = "kf-session"

_serializer = URLSafeTimedSerializer(settings.session_secret, salt=_SESSION_SALT)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


DUMMY_HASH = hash_password("kanbanflow-dummy-password-for-constant-time-login")


def make_session_cookie(user_id: str) -> str:
    return _serializer.dumps(user_id)


def read_session_cookie(raw: str) -> str | None:
    try:
        return _serializer.loads(raw, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
```

bcrypt rejects secrets longer than 72 bytes, which is why Task 6's schema caps the password field at 72 characters.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_auth_primitives.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add password hashing and signed session cookies"
```

---

## Task 6: Registration, login, logout, and `current_user`

**Files:**
- Create: `app/schemas.py`, `app/routers/__init__.py`, `app/routers/api_auth.py`
- Modify: `app/auth.py` (append the dependency), `app/main.py` (mount the router), `tests/conftest.py` (add factories)
- Test: `tests/test_auth_api.py`

**Interfaces:**
- Consumes: `app.auth` primitives, `app.db.get_session`, `app.models.User`
- Produces:
  - `app.schemas.RegisterRequest(name, email, password)`, `LoginRequest(email, password)`, `UserOut(id, name, email, created_at)`
  - `app.auth.current_user(request: Request, session: Session = Depends(get_session)) -> User` — raises `401` when the cookie is missing, tampered, expired, or names a deleted user.
  - `app.auth.optional_user(...) -> User | None` — used by the HTML routes in Task 19 to redirect anonymous visitors instead of erroring.
  - Fixtures `make_user(email=..., password=...) -> User` and `login(client, email, password)`.
- Routes: `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`

- [ ] **Step 1: Write failing test**

`tests/test_auth_api.py`:

```python
from app.auth import SESSION_COOKIE


def test_register_sets_a_session_cookie(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "Ada@Example.com", "password": "hunter22"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == "ada@example.com"
    assert SESSION_COOKIE in response.cookies


def test_duplicate_email_is_rejected_case_insensitively(client):
    payload = {"name": "Ada", "email": "ada@example.com", "password": "hunter22"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    payload["email"] = "ADA@example.com"
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_login_succeeds_and_logout_clears_the_cookie(client, make_user):
    make_user(email="ada@example.com", password="hunter22")
    response = client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "hunter22"}
    )
    assert response.status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.cookies.get(SESSION_COOKIE) in (None, "")


def test_unknown_email_and_wrong_password_are_indistinguishable(client, make_user):
    make_user(email="ada@example.com", password="hunter22")
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "hunter22"}
    )
    wrong = client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "nope"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_short_password_is_rejected(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "ada@example.com", "password": "short"},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_auth_api.py -v`
Expected: FAIL — the `make_user` fixture and the routes do not exist yet.

- [ ] **Step 3: Write `app/schemas.py`**

```python
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    created_at: datetime
```

`EmailStr` requires the `email-validator` package: `uv add email-validator`.

- [ ] **Step 4: Append the dependencies to `app/auth.py`**

```python
from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from app.db import get_session
from app.models import User


def optional_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    user_id = read_session_cookie(raw)
    if user_id is None:
        return None
    return session.get(User, user_id)


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user
```

- [ ] **Step 5: Write `app/routers/api_auth.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select

from app.auth import (
    DUMMY_HASH,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    hash_password,
    make_session_cookie,
    verify_password,
)
from app.config import settings
from app.db import get_session
from app.models import User
from app.schemas import LoginRequest, RegisterRequest, UserOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _set_session(response: Response, user_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, response: Response, session: Session = Depends(get_session)):
    email = body.email.lower()
    if session.exec(select(User).where(User.email == email)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(name=body.name, email=email, password_hash=hash_password(body.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    _set_session(response, user.id)
    return user


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, response: Response, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == body.email.lower())).first()
    hashed = user.password_hash if user else DUMMY_HASH
    if not verify_password(body.password, hashed) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    _set_session(response, user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
```

The `hashed = ... if user else DUMMY_HASH` line is the whole point of `DUMMY_HASH`: bcrypt runs in both branches, so an unknown email costs the same time as a wrong password.

- [ ] **Step 6: Mount the router in `app/main.py`**

```python
from app.routers import api_auth

app.include_router(api_auth.router)
```

- [ ] **Step 7: Add the `make_user` and `login_as` fixtures to `tests/conftest.py`**

```python
import pytest
from sqlmodel import Session

from app.auth import hash_password
from app.models import User


@pytest.fixture
def make_user(engine):
    def _make(email: str = "user@example.com", password: str = "hunter22", name: str = "User"):
        with Session(engine) as session:
            user = User(name=name, email=email.lower(), password_hash=hash_password(password))
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    return _make


@pytest.fixture
def login_as(client):
    def _login(email: str, password: str = "hunter22"):
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response

    return _login
```

- [ ] **Step 8: Run the tests and verify they pass**

Run: `uv run pytest tests/test_auth_api.py -v`
Expected: PASS (5 tests)

- [ ] **Step 9: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add registration, login, logout, and current_user dependency"
```

---

## Task 7: CSRF tokens

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_csrf.py`

**Interfaces:**
- Produces:
  - `CSRF_FIELD = "_csrf"`
  - `CSRF_MAX_AGE = 60 * 60 * 24`
  - `make_csrf_token(user_id: str) -> str`
  - `verify_csrf(request: Request, user: User = Depends(current_user)) -> None` — reads `_csrf` from the form body, raises `403` when missing, tampered, expired, or issued to a different user. Task 20 onward attaches it to every HTML `POST` as a route dependency.

- [ ] **Step 1: Write failing test**

`tests/test_csrf.py`:

```python
import pytest
from fastapi import HTTPException

from app.auth import make_csrf_token, read_csrf_token


def test_csrf_round_trip():
    assert read_csrf_token(make_csrf_token("user-1")) == "user-1"


def test_tampered_csrf_token_returns_none():
    token = make_csrf_token("user-1")
    assert read_csrf_token(token[:-1] + ("x" if token[-1] != "x" else "y")) is None


def test_csrf_token_is_bound_to_its_user():
    from app.auth import assert_csrf_matches

    with pytest.raises(HTTPException) as excinfo:
        assert_csrf_matches(make_csrf_token("user-1"), "user-2")
    assert excinfo.value.status_code == 403


def test_missing_csrf_token_is_rejected():
    from app.auth import assert_csrf_matches

    with pytest.raises(HTTPException) as excinfo:
        assert_csrf_matches(None, "user-1")
    assert excinfo.value.status_code == 403
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_csrf.py -v`
Expected: FAIL with `ImportError: cannot import name 'make_csrf_token'`

- [ ] **Step 3: Append to `app/auth.py`**

```python
CSRF_FIELD = "_csrf"
CSRF_MAX_AGE = 60 * 60 * 24
_csrf_serializer = URLSafeTimedSerializer(settings.session_secret, salt="kf-csrf")


def make_csrf_token(user_id: str) -> str:
    return _csrf_serializer.dumps(user_id)


def read_csrf_token(raw: str) -> str | None:
    try:
        return _csrf_serializer.loads(raw, max_age=CSRF_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def assert_csrf_matches(raw: str | None, user_id: str) -> None:
    if not raw or read_csrf_token(raw) != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")


async def verify_csrf(request: Request, user: User = Depends(current_user)) -> None:
    form = await request.form()
    assert_csrf_matches(form.get(CSRF_FIELD), user.id)
```

`verify_csrf` is the one `async def` in the codebase: reading the form body is I/O on the request stream, not a database call.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_csrf.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add session-bound CSRF tokens for HTML form posts"
```

---

## Task 8: Slug derivation and project creation

**Files:**
- Create: `app/routers/api_projects.py`
- Modify: `app/schemas.py`, `app/main.py`, `tests/conftest.py`
- Test: `tests/test_projects.py`

**Interfaces:**
- Produces:
  - `app.routers.api_projects.slugify(name: str) -> str`
  - `app.schemas.ProjectCreate(name)`, `ProjectOut(id, name, slug, webhook_type, webhook_url, created_at, role)`
  - Routes: `POST /api/v1/projects`, `GET /api/v1/projects`, `GET /api/v1/projects/{slug}`
  - Fixture `make_project(owner, name="Payment Gateway") -> Project`

- [ ] **Step 1: Write failing test**

`tests/test_projects.py`:

```python
import pytest

from app.routers.api_projects import slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Payment Gateway", "payment-gateway"),
        ("  Spaced  Out  ", "spaced-out"),
        ("Rock & Roll!!", "rock-roll"),
        ("CS482 — Team 3", "cs482-team-3"),
        ("x" * 80, "x" * 50),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_create_project_makes_the_creator_an_owner(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    response = client.post("/api/v1/projects", json={"name": "Payment Gateway"})
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "payment-gateway"
    assert body["role"] == "OWNER"


def test_duplicate_slug_returns_409(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    client.post("/api/v1/projects", json={"name": "Payment Gateway"})
    response = client.post("/api/v1/projects", json={"name": "payment gateway"})
    assert response.status_code == 409
    assert "payment-gateway" in response.text


def test_unslugifiable_name_returns_422(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    assert client.post("/api/v1/projects", json={"name": "!!!"}).status_code == 422


def test_project_list_shows_only_projects_you_belong_to(client, make_user, login_as):
    make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    login_as("ada@example.com")
    client.post("/api/v1/projects", json={"name": "Ada Project"})
    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    assert client.get("/api/v1/projects").json() == []


def test_non_member_gets_404_on_project_detail(client, make_user, login_as):
    make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    login_as("ada@example.com")
    client.post("/api/v1/projects", json={"name": "Ada Project"})
    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    assert client.get("/api/v1/projects/ada-project").status_code == 404


def test_anonymous_request_is_401(client):
    assert client.post("/api/v1/projects", json={"name": "X"}).status_code == 401
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_projects.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.routers.api_projects'`

- [ ] **Step 3: Add the project schemas to `app/schemas.py`**

```python
from app.models import WebhookType


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ProjectOut(BaseModel):
    id: str
    name: str
    slug: str
    webhook_type: WebhookType
    webhook_url: str | None
    created_at: datetime
    role: str | None = None
```

- [ ] **Step 4: Write `app/routers/api_projects.py`**

```python
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.auth import current_user
from app.db import get_session
from app.models import Project, ProjectMember, Role, User
from app.schemas import ProjectCreate, ProjectOut

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50].strip("-")


def _out(project: Project, role: Role | None) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        slug=project.slug,
        webhook_type=project.webhook_type,
        webhook_url=project.webhook_url,
        created_at=project.created_at,
        role=role.value if role else None,
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    slug = slugify(body.name)
    if not slug:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name yields an empty slug")
    if session.exec(select(Project).where(Project.slug == slug)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Slug already taken: {slug}")
    project = Project(name=body.name.strip(), slug=slug)
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.OWNER))
    session.commit()
    session.refresh(project)
    return _out(project, Role.OWNER)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    user: User = Depends(current_user), session: Session = Depends(get_session)
) -> list[ProjectOut]:
    rows = session.exec(
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.created_at.desc())
    ).all()
    return [_out(project, member.role) for project, member in rows]
```

The membership row is inserted after `session.flush()` and before `session.commit()`, so a project can never exist without an owner.

- [ ] **Step 5: Mount the router and add the `make_project` fixture**

In `app/main.py`:

```python
from app.routers import api_auth, api_projects

app.include_router(api_projects.router)
```

In `tests/conftest.py`:

```python
from app.models import Project, ProjectMember, Role
from app.routers.api_projects import slugify


@pytest.fixture
def make_project(engine):
    def _make(owner, name: str = "Payment Gateway"):
        with Session(engine) as session:
            project = Project(name=name, slug=slugify(name))
            session.add(project)
            session.flush()
            session.add(
                ProjectMember(project_id=project.id, user_id=owner.id, role=Role.OWNER)
            )
            session.commit()
            session.refresh(project)
            return project

    return _make


@pytest.fixture
def add_member(engine):
    def _add(project, user, role=Role.MEMBER):
        with Session(engine) as session:
            session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
            session.commit()

    return _add
```

- [ ] **Step 6: Run the tests and verify all but the detail test pass**

Run: `uv run pytest tests/test_projects.py -v`
Expected: every test passes except `test_non_member_gets_404_on_project_detail`, which fails with `405` because `GET /api/v1/projects/{slug}` does not exist yet. Task 9 adds it together with the access dependencies it needs.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add project creation with immutable derived slugs"
```

---

## Task 9: Project access dependencies and settings routes

**Files:**
- Modify: `app/auth.py`, `app/routers/api_projects.py`, `app/schemas.py`
- Test: `tests/test_project_access.py`

**Interfaces:**
- Produces three dependencies, each taking `slug` from the path and returning `tuple[Project, ProjectMember]`:
  - `project_reader` — `404` if the project is missing **or** the caller is not a member.
  - `project_writer` — `404` if the project is missing; `403` if the caller is not a member.
  - `project_owner` — `404` if the project is missing; `403` if the caller is not a member or is not `OWNER`.
- Produces `app.schemas.ProjectUpdate(name, webhook_type, webhook_url)`
- Routes: `GET /api/v1/projects/{slug}`, `PATCH /api/v1/projects/{slug}`, `DELETE /api/v1/projects/{slug}`

The three-way split is what makes the spec's "404 on read, 403 on write" rule a single decision rather than a conditional repeated in every handler.

- [ ] **Step 1: Write failing test**

`tests/test_project_access.py`:

```python
def test_member_can_read_but_not_edit_settings(client, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, make_user(email="cat@example.com"))
    login_as("cat@example.com")
    assert client.get(f"/api/v1/projects/{project.slug}").status_code == 200
    assert client.patch(f"/api/v1/projects/{project.slug}", json={"name": "New"}).status_code == 403


def test_owner_can_edit_name_but_slug_never_changes(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner, name="Payment Gateway")
    login_as("ada@example.com")
    response = client.patch(f"/api/v1/projects/{project.slug}", json={"name": "Billing"})
    assert response.status_code == 200
    assert response.json()["name"] == "Billing"
    assert response.json()["slug"] == "payment-gateway"


def test_non_member_reads_404_and_writes_403(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    assert client.get(f"/api/v1/projects/{project.slug}").status_code == 404
    assert client.patch(f"/api/v1/projects/{project.slug}", json={"name": "X"}).status_code == 403


def test_missing_project_is_404_even_for_writes(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    assert client.patch("/api/v1/projects/nope", json={"name": "X"}).status_code == 404


def test_webhook_url_must_be_https_and_present(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert client.patch(
        f"/api/v1/projects/{project.slug}", json={"webhook_type": "SLACK"}
    ).status_code == 422
    assert client.patch(
        f"/api/v1/projects/{project.slug}",
        json={"webhook_type": "SLACK", "webhook_url": "http://example.com/hook"},
    ).status_code == 422
    assert client.patch(
        f"/api/v1/projects/{project.slug}",
        json={"webhook_type": "SLACK", "webhook_url": "https://example.com/hook"},
    ).status_code == 200


def test_delete_requires_confirmation(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert client.delete(f"/api/v1/projects/{project.slug}").status_code == 422
    assert client.delete(
        f"/api/v1/projects/{project.slug}?confirm={project.slug}"
    ).status_code == 204
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_project_access.py -v`
Expected: FAIL — none of the routes exist.

- [ ] **Step 3: Append the access dependencies to `app/auth.py`**

Add `select` to the existing `from sqlmodel import Session` line at the top of the file first.

```python
from app.models import Project, ProjectMember, Role


def _load(slug: str, user: User, session: Session) -> tuple[Project, ProjectMember | None]:
    project = session.exec(select(Project).where(Project.slug == slug)).first()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
        )
    ).first()
    return project, member


def project_reader(
    slug: str, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> tuple[Project, ProjectMember]:
    project, member = _load(slug, user, session)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project, member


def project_writer(
    slug: str, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> tuple[Project, ProjectMember]:
    project, member = _load(slug, user, session)
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")
    return project, member


def project_owner(
    slug: str, user: User = Depends(current_user), session: Session = Depends(get_session)
) -> tuple[Project, ProjectMember]:
    project, member = project_writer(slug, user, session)
    if member.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner role required")
    return project, member
```

- [ ] **Step 4: Add `ProjectUpdate` to `app/schemas.py`**

```python
class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    webhook_type: WebhookType | None = None
    webhook_url: str | None = Field(default=None, max_length=500)
```

- [ ] **Step 5: Add the three routes to `app/routers/api_projects.py`**

```python
from fastapi import Query

from app.auth import project_owner, project_reader
from app.models import Ticket
from app.schemas import ProjectUpdate


@router.get("/{slug}", response_model=ProjectOut)
def get_project(access=Depends(project_reader)):
    project, member = access
    return _out(project, member.role)


@router.patch("/{slug}", response_model=ProjectOut)
def update_project(
    body: ProjectUpdate,
    access=Depends(project_owner),
    session: Session = Depends(get_session),
):
    project, member = access
    if body.name is not None:
        project.name = body.name.strip()
    if body.webhook_type is not None:
        project.webhook_type = body.webhook_type
    if body.webhook_url is not None:
        project.webhook_url = body.webhook_url
    if project.webhook_type != WebhookType.NONE:
        if not project.webhook_url or not project.webhook_url.startswith("https://"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "webhook_url must be an https URL when webhook_type is set",
            )
    session.add(project)
    session.commit()
    session.refresh(project)
    return _out(project, member.role)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    confirm: str = Query(default=""),
    access=Depends(project_owner),
    session: Session = Depends(get_session),
) -> None:
    project, _ = access
    if confirm != project.slug:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "confirm must equal the slug")
    for ticket in session.exec(select(Ticket).where(Ticket.project_id == project.id)).all():
        session.delete(ticket)
    for member in session.exec(
        select(ProjectMember).where(ProjectMember.project_id == project.id)
    ).all():
        session.delete(member)
    session.delete(project)
    session.commit()
```

Import `WebhookType` from `app.models` at the top of the file. The cascade is written explicitly rather than relying on `ON DELETE CASCADE`, because the FK definitions in Task 3 do not declare one and adding it later would need a batch migration.

- [ ] **Step 6: Run the tests and verify they pass**

Run: `uv run pytest tests/test_project_access.py tests/test_projects.py -v`
Expected: PASS — including `test_non_member_gets_404_on_project_detail` from Task 8.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add project access dependencies and settings routes"
```

---

## Task 10: Membership management

**Files:**
- Modify: `app/routers/api_projects.py`, `app/schemas.py`
- Test: `tests/test_membership.py`

**Interfaces:**
- Produces `app.schemas.MemberAdd(email, role)`, `MemberUpdate(role)`, `MemberOut(user_id, name, email, role, joined_at)`
- Routes: `GET`, `POST /api/v1/projects/{slug}/members`, `PATCH`, `DELETE /api/v1/projects/{slug}/members/{user_id}`

- [ ] **Step 1: Write failing test**

`tests/test_membership.py`:

```python
def test_owner_adds_member_by_email(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/api/v1/projects/{project.slug}/members",
        json={"email": "bob@example.com", "role": "MEMBER"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == "bob@example.com"


def test_adding_unknown_email_is_404_and_duplicate_is_409(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert client.post(
        f"/api/v1/projects/{project.slug}/members",
        json={"email": "ghost@example.com", "role": "MEMBER"},
    ).status_code == 404
    body = {"email": "bob@example.com", "role": "MEMBER"}
    client.post(f"/api/v1/projects/{project.slug}/members", json=body)
    assert client.post(f"/api/v1/projects/{project.slug}/members", json=body).status_code == 409


def test_member_cannot_manage_membership(client, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, bob)
    login_as("bob@example.com")
    assert client.get(f"/api/v1/projects/{project.slug}/members").status_code == 200
    assert client.post(
        f"/api/v1/projects/{project.slug}/members",
        json={"email": "ada@example.com", "role": "MEMBER"},
    ).status_code == 403


def test_last_owner_cannot_be_demoted_or_removed(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert client.patch(
        f"/api/v1/projects/{project.slug}/members/{owner.id}", json={"role": "MEMBER"}
    ).status_code == 409
    assert client.delete(
        f"/api/v1/projects/{project.slug}/members/{owner.id}"
    ).status_code == 409


def test_removing_a_member_nulls_their_assigned_tickets(
    client, make_user, make_project, add_member, login_as, session
):
    from app.models import Ticket, TicketType

    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, bob)
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        title="t",
        type=TicketType.TASK,
        creator_id=owner.id,
        assignee_id=bob.id,
    )
    session.add(ticket)
    session.commit()
    login_as("ada@example.com")
    assert client.delete(
        f"/api/v1/projects/{project.slug}/members/{bob.id}"
    ).status_code == 204
    session.expire_all()
    assert session.get(Ticket, ticket.id).assignee_id is None
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_membership.py -v`
Expected: FAIL with `404`/`405` on every membership route.

- [ ] **Step 3: Add the membership schemas to `app/schemas.py`**

```python
from app.models import Role


class MemberAdd(BaseModel):
    email: EmailStr
    role: Role = Role.MEMBER


class MemberUpdate(BaseModel):
    role: Role


class MemberOut(BaseModel):
    user_id: str
    name: str
    email: str
    role: Role
    joined_at: datetime
```

- [ ] **Step 4: Add the routes to `app/routers/api_projects.py`**

```python
from app.auth import project_writer
from app.schemas import MemberAdd, MemberOut, MemberUpdate


def _members(session: Session, project_id: str) -> list[MemberOut]:
    rows = session.exec(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.joined_at)
    ).all()
    return [
        MemberOut(
            user_id=user.id,
            name=user.name,
            email=user.email,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member, user in rows
    ]


def _owner_count(session: Session, project_id: str) -> int:
    return len(
        session.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.role == Role.OWNER
            )
        ).all()
    )


@router.get("/{slug}/members", response_model=list[MemberOut])
def list_members(access=Depends(project_reader), session: Session = Depends(get_session)):
    project, _ = access
    return _members(session, project.id)


@router.post("/{slug}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    body: MemberAdd, access=Depends(project_owner), session: Session = Depends(get_session)
):
    project, _ = access
    target = session.exec(select(User).where(User.email == body.email.lower())).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No registered user with that email")
    existing = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == target.id
        )
    ).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a member")
    session.add(ProjectMember(project_id=project.id, user_id=target.id, role=body.role))
    session.commit()
    return next(m for m in _members(session, project.id) if m.user_id == target.id)


@router.patch("/{slug}/members/{user_id}", response_model=MemberOut)
def update_member(
    user_id: str,
    body: MemberUpdate,
    access=Depends(project_owner),
    session: Session = Depends(get_session),
):
    project, _ = access
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user_id
        )
    ).first()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member")
    if member.role == Role.OWNER and body.role != Role.OWNER and _owner_count(session, project.id) == 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "A project must keep at least one OWNER")
    member.role = body.role
    session.add(member)
    session.commit()
    return next(m for m in _members(session, project.id) if m.user_id == user_id)


@router.delete("/{slug}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: str, access=Depends(project_owner), session: Session = Depends(get_session)
) -> None:
    project, _ = access
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user_id
        )
    ).first()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member")
    if member.role == Role.OWNER and _owner_count(session, project.id) == 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "A project must keep at least one OWNER")
    for ticket in session.exec(
        select(Ticket).where(Ticket.project_id == project.id, Ticket.assignee_id == user_id)
    ).all():
        ticket.assignee_id = None
        session.add(ticket)
    session.delete(member)
    session.commit()
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/test_membership.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add project membership management"
```

---

## Task 11: `meta` validation, atomic ticket numbers, and ticket creation

**Files:**
- Create: `app/services.py`, `app/routers/api_tickets.py`
- Modify: `app/schemas.py`, `app/main.py`
- Test: `tests/test_ticket_create.py`

**Interfaces:**
- Produces:
  - `app.services.validate_meta(meta: dict) -> None` — raises `HTTPException(422)`
  - `app.services.allocate_ticket_number(session: Session, project_id: str) -> int`
  - `app.services.create_ticket(session, project, creator, *, title, description="", type=TicketType.TASK, priority=Priority.MEDIUM, story_points=None, assignee_id=None, meta=None) -> Ticket`
  - `app.schemas.TicketCreate`, `TicketOut`
  - Route: `POST /api/v1/tickets` (body carries `slug`)
- Task 17 wraps `create_ticket` with notification scheduling; Task 22 calls it from the web route. Neither duplicates the allocation.

- [ ] **Step 1: Write failing test**

`tests/test_ticket_create.py`:

```python
import pytest
from fastapi import HTTPException

from app.services import validate_meta


def test_meta_must_be_an_object():
    with pytest.raises(HTTPException) as excinfo:
        validate_meta(["not", "an", "object"])
    assert excinfo.value.status_code == 422


def test_meta_depth_limit():
    validate_meta({"a": {"b": {"c": 1}}})
    with pytest.raises(HTTPException):
        validate_meta({"a": {"b": {"c": {"d": 1}}}})


def test_meta_size_limit():
    with pytest.raises(HTTPException):
        validate_meta({"blob": "x" * 9000})


def test_ticket_numbers_start_at_one_and_increment(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    numbers = []
    for i in range(3):
        response = client.post(
            "/api/v1/tickets", json={"slug": project.slug, "title": f"Ticket {i}"}
        )
        assert response.status_code == 201
        numbers.append(response.json()["ticket_number"])
    assert numbers == [1, 2, 3]


def test_created_ticket_defaults(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    body = client.post("/api/v1/tickets", json={"slug": project.slug, "title": "  Trimmed  "}).json()
    assert body["title"] == "Trimmed"
    assert body["status"] == "BACKLOG"
    assert body["priority"] == "MEDIUM"
    assert body["story_points"] is None
    assert body["meta"] == {}


def test_assignee_must_be_a_project_member(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    outsider = make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "T", "assignee_id": outsider.id},
    )
    assert response.status_code == 422


def test_invalid_story_points_rejected(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert client.post(
        "/api/v1/tickets", json={"slug": project.slug, "title": "T", "story_points": 4}
    ).status_code == 422


def test_non_member_cannot_create(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    assert client.post(
        "/api/v1/tickets", json={"slug": project.slug, "title": "T"}
    ).status_code == 403
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_ticket_create.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services'`

- [ ] **Step 3: Write `app/services.py`**

```python
import json

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlmodel import Session, select

from app.models import (
    Priority,
    Project,
    ProjectMember,
    Ticket,
    TicketStatus,
    TicketType,
    User,
)

META_MAX_BYTES = 8 * 1024
META_MAX_DEPTH = 3


def _depth(value, level: int = 1) -> int:
    if isinstance(value, dict):
        return max((_depth(v, level + 1) for v in value.values()), default=level)
    if isinstance(value, list):
        return max((_depth(v, level + 1) for v in value), default=level)
    return level - 1


def validate_meta(meta) -> None:
    if not isinstance(meta, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "meta must be a JSON object")
    if _depth(meta) > META_MAX_DEPTH:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"meta may not nest deeper than {META_MAX_DEPTH} levels",
        )
    if len(json.dumps(meta).encode("utf-8")) > META_MAX_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "meta exceeds 8 KB")


def allocate_ticket_number(session: Session, project_id: str) -> int:
    row = session.exec(
        text(
            "UPDATE project SET next_ticket_number = next_ticket_number + 1 "
            "WHERE id = :project_id RETURNING next_ticket_number - 1"
        ).bindparams(project_id=project_id)
    ).one()
    return int(row[0])


def create_ticket(
    session: Session,
    project: Project,
    creator: User,
    *,
    title: str,
    description: str = "",
    type: TicketType = TicketType.TASK,
    priority: Priority = Priority.MEDIUM,
    story_points: int | None = None,
    assignee_id: str | None = None,
    meta: dict | None = None,
) -> Ticket:
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "title must not be empty")
    meta = meta or {}
    validate_meta(meta)
    if assignee_id is not None:
        member = session.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == assignee_id,
            )
        ).first()
        if member is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "assignee must be a project member"
            )
    ticket = Ticket(
        ticket_number=allocate_ticket_number(session, project.id),
        project_id=project.id,
        title=clean_title,
        description=description,
        type=type,
        status=TicketStatus.BACKLOG,
        priority=priority,
        story_points=story_points,
        creator_id=creator.id,
        assignee_id=assignee_id,
        meta=meta,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket
```

`allocate_ticket_number` runs inside the caller's open transaction, so the counter increment and the insert commit or roll back together. That is what keeps the sequence gapless.

- [ ] **Step 4: Add the ticket schemas to `app/schemas.py`**

```python
from typing import Literal

from app.models import Priority, TicketStatus, TicketType

STORY_POINTS = Literal[1, 2, 3, 5, 8, 13]


class TicketCreate(BaseModel):
    slug: str
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)
    type: TicketType = TicketType.TASK
    priority: Priority = Priority.MEDIUM
    story_points: STORY_POINTS | None = None
    assignee_id: str | None = None
    meta: dict = Field(default_factory=dict)


class TicketOut(BaseModel):
    id: str
    ticket_number: int
    project_id: str
    title: str
    description: str
    type: TicketType
    status: TicketStatus
    priority: Priority
    story_points: int | None
    creator_id: str
    assignee_id: str | None
    resolution_notes: str | None
    completed_at: datetime | None
    meta: dict
    created_at: datetime
```

`STORY_POINTS` as a `Literal` is what turns the Fibonacci constraint into a `422` without a hand-written validator.

- [ ] **Step 5: Write `app/routers/api_tickets.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.auth import current_user
from app.db import get_session
from app.models import Project, ProjectMember, Ticket, User
from app.schemas import TicketCreate, TicketOut
from app.services import create_ticket

router = APIRouter(prefix="/api/v1", tags=["tickets"])


def _project_for_write(slug: str, user: User, session: Session) -> Project:
    project = session.exec(select(Project).where(Project.slug == slug)).first()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
        )
    ).first()
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")
    return project


@router.post("/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def post_ticket(
    body: TicketCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Ticket:
    project = _project_for_write(body.slug, user, session)
    return create_ticket(
        session,
        project,
        user,
        title=body.title,
        description=body.description,
        type=body.type,
        priority=body.priority,
        story_points=body.story_points,
        assignee_id=body.assignee_id,
        meta=body.meta,
    )
```

- [ ] **Step 6: Mount the router in `app/main.py`**

```python
from app.routers import api_auth, api_projects, api_tickets

app.include_router(api_tickets.router)
```

- [ ] **Step 7: Run the tests and verify they pass**

Run: `uv run pytest tests/test_ticket_create.py -v`
Expected: PASS (8 tests)

- [ ] **Step 8: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add ticket creation with atomic per-project numbering"
```

---

## Task 12: Prove the ticket-number allocation under concurrency (V-1)

**Files:**
- Test: `tests/test_concurrency.py`

**Interfaces:**
- Consumes: `app.services.create_ticket`, the `engine` fixture
- Produces: no source changes. The deliverable is evidence for V-1 and FR-12. If this task fails, the cause is in Task 11's SQL, not in the test.

- [ ] **Step 1: Write the test**

`tests/test_concurrency.py`:

```python
import time
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select

from app.models import Project, ProjectMember, Role, Ticket, User
from app.services import create_ticket

CONCURRENT_CREATIONS = 50


def test_fifty_concurrent_creations_yield_fifty_consecutive_numbers(engine):
    with Session(engine) as setup:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        project = Project(name="Payment Gateway", slug="payment-gateway")
        setup.add(user)
        setup.add(project)
        setup.commit()
        setup.refresh(user)
        setup.refresh(project)
        setup.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.OWNER))
        setup.commit()
        user_id, project_id = user.id, project.id

    durations: list[float] = []

    def create_one(index: int) -> int:
        with Session(engine) as session:
            project = session.get(Project, project_id)
            user = session.get(User, user_id)
            started = time.perf_counter()
            ticket = create_ticket(session, project, user, title=f"Ticket {index}")
            durations.append(time.perf_counter() - started)
            return ticket.ticket_number

    with ThreadPoolExecutor(max_workers=CONCURRENT_CREATIONS) as pool:
        numbers = list(pool.map(create_one, range(CONCURRENT_CREATIONS)))

    assert sorted(numbers) == list(range(1, CONCURRENT_CREATIONS + 1))

    with Session(engine) as check:
        rows = check.exec(select(Ticket).where(Ticket.project_id == project_id)).all()
    assert len(rows) == CONCURRENT_CREATIONS

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 0.05, f"p95 write latency was {p95 * 1000:.1f} ms, budget is 50 ms"
```

The assertion `sorted(numbers) == list(range(1, 51))` covers three claims at once: no duplicates, no gaps, and exactly fifty rows. A `database is locked` error surfaces as an exception out of `pool.map`, so it needs no separate assertion.

- [ ] **Step 2: Run the test and verify it passes**

Run: `uv run pytest tests/test_concurrency.py -v -s`
Expected: PASS. If it fails on `database is locked`, check that `busy_timeout=5000` is in `app/db.py`'s `PRAGMAS` and that `check_same_thread=False` is set. If it fails on duplicates, the allocation is not using `RETURNING` inside the transaction.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: prove gapless ticket numbers under 50 concurrent creations"
```

---

## Task 13: Ticket read, list, update, and delete

**Files:**
- Modify: `app/routers/api_tickets.py`, `app/schemas.py`
- Test: `tests/test_ticket_api.py`

**Interfaces:**
- Produces:
  - `app.schemas.TicketUpdate(title, description, type, priority, story_points, assignee_id, resolution_notes, meta)` — every field optional
  - `app.schemas.TicketPage(items: list[TicketOut], next_cursor: int | None)`
  - `app.routers.api_tickets.load_ticket_for_read(ticket_id, user, session) -> tuple[Ticket, Project, ProjectMember]` — reused by Task 14
  - Routes: `GET /api/v1/projects/{slug}/tickets`, `GET /api/v1/tickets/{ticket_id}`, `PATCH /api/v1/tickets/{ticket_id}`, `DELETE /api/v1/tickets/{ticket_id}`
- Pagination: results are ordered by `ticket_number` descending; `cursor` is a `ticket_number` and the page contains rows strictly below it. `next_cursor` is the last returned `ticket_number`, or `null` on the final page.

- [ ] **Step 1: Write failing test**

`tests/test_ticket_api.py`:

```python
import pytest


@pytest.fixture
def seeded(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    for i in range(5):
        client.post(
            "/api/v1/tickets",
            json={
                "slug": project.slug,
                "title": f"Ticket {i}",
                "priority": "HIGH" if i % 2 else "LOW",
                "type": "BUG" if i % 2 else "STORY",
            },
        )
    return owner, project


def test_board_query_is_newest_first(client, seeded):
    _, project = seeded
    items = client.get(f"/api/v1/projects/{project.slug}/tickets").json()["items"]
    assert [t["ticket_number"] for t in items] == [5, 4, 3, 2, 1]


def test_board_query_filters(client, seeded):
    _, project = seeded
    items = client.get(
        f"/api/v1/projects/{project.slug}/tickets", params={"priority": "HIGH"}
    ).json()["items"]
    assert {t["priority"] for t in items} == {"HIGH"}
    items = client.get(
        f"/api/v1/projects/{project.slug}/tickets", params={"type": "BUG"}
    ).json()["items"]
    assert {t["type"] for t in items} == {"BUG"}


def test_board_query_paginates(client, seeded):
    _, project = seeded
    first = client.get(f"/api/v1/projects/{project.slug}/tickets", params={"limit": 2}).json()
    assert [t["ticket_number"] for t in first["items"]] == [5, 4]
    assert first["next_cursor"] == 4
    second = client.get(
        f"/api/v1/projects/{project.slug}/tickets", params={"limit": 2, "cursor": 4}
    ).json()
    assert [t["ticket_number"] for t in second["items"]] == [3, 2]


def test_limit_above_200_is_rejected(client, seeded):
    _, project = seeded
    assert client.get(
        f"/api/v1/projects/{project.slug}/tickets", params={"limit": 201}
    ).status_code == 422


def test_patch_updates_fields_and_rejects_bad_meta(client, seeded):
    _, project = seeded
    ticket_id = client.get(f"/api/v1/projects/{project.slug}/tickets").json()["items"][0]["id"]
    response = client.patch(
        f"/api/v1/tickets/{ticket_id}",
        json={"title": "Renamed", "story_points": 8, "meta": {"git_commit": "abc1234"}},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"
    assert response.json()["story_points"] == 8
    assert client.patch(
        f"/api/v1/tickets/{ticket_id}", json={"meta": {"blob": "x" * 9000}}
    ).status_code == 422


def test_non_member_gets_404_on_ticket_read(client, seeded, make_user, login_as):
    _, project = seeded
    ticket_id = client.get(f"/api/v1/projects/{project.slug}/tickets").json()["items"][0]["id"]
    make_user(email="bob@example.com")
    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    assert client.get(f"/api/v1/tickets/{ticket_id}").status_code == 404


def test_only_owner_deletes(client, seeded, make_user, add_member, login_as):
    _, project = seeded
    ticket_id = client.get(f"/api/v1/projects/{project.slug}/tickets").json()["items"][0]["id"]
    bob = make_user(email="bob@example.com")
    add_member(project, bob)
    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    assert client.delete(f"/api/v1/tickets/{ticket_id}").status_code == 403
    client.post("/api/v1/auth/logout")
    login_as("ada@example.com")
    assert client.delete(f"/api/v1/tickets/{ticket_id}").status_code == 204
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_ticket_api.py -v`
Expected: FAIL — the read, list, patch, and delete routes do not exist.

- [ ] **Step 3: Add the schemas to `app/schemas.py`**

```python
class TicketUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    type: TicketType | None = None
    priority: Priority | None = None
    story_points: STORY_POINTS | None = None
    assignee_id: str | None = None
    resolution_notes: str | None = None
    meta: dict | None = None


class TicketPage(BaseModel):
    items: list[TicketOut]
    next_cursor: int | None = None
```

- [ ] **Step 4: Add the routes to `app/routers/api_tickets.py`**

```python
from fastapi import Query

from app.auth import project_reader
from app.models import Priority, ProjectMember, Role, TicketType
from app.schemas import TicketPage, TicketUpdate
from app.services import validate_meta


def load_ticket_for_read(
    ticket_id: str, user: User, session: Session
) -> tuple[Ticket, Project, ProjectMember]:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == ticket.project_id, ProjectMember.user_id == user.id
        )
    ).first()
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return ticket, session.get(Project, ticket.project_id), member


@router.get("/projects/{slug}/tickets", response_model=TicketPage)
def list_tickets(
    status_filter: str | None = Query(default=None, alias="status"),
    assignee_id: str | None = None,
    type_filter: TicketType | None = Query(default=None, alias="type"),
    priority: Priority | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = None,
    access=Depends(project_reader),
    session: Session = Depends(get_session),
) -> TicketPage:
    project, _ = access
    query = select(Ticket).where(Ticket.project_id == project.id)
    if status_filter:
        query = query.where(Ticket.status == status_filter)
    if assignee_id:
        query = query.where(Ticket.assignee_id == assignee_id)
    if type_filter:
        query = query.where(Ticket.type == type_filter)
    if priority:
        query = query.where(Ticket.priority == priority)
    if cursor is not None:
        query = query.where(Ticket.ticket_number < cursor)
    rows = session.exec(query.order_by(Ticket.ticket_number.desc()).limit(limit)).all()
    next_cursor = rows[-1].ticket_number if len(rows) == limit else None
    return TicketPage(items=rows, next_cursor=next_cursor)


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(
    ticket_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Ticket:
    ticket, _, _ = load_ticket_for_read(ticket_id, user, session)
    return ticket


@router.patch("/tickets/{ticket_id}", response_model=TicketOut)
def patch_ticket(
    ticket_id: str,
    body: TicketUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Ticket:
    ticket, project, _ = load_ticket_for_read(ticket_id, user, session)
    data = body.model_dump(exclude_unset=True)
    if "meta" in data and data["meta"] is not None:
        validate_meta(data["meta"])
    if data.get("assignee_id") is not None:
        member = session.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == data["assignee_id"],
            )
        ).first()
        if member is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "assignee must be a project member"
            )
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip()
        if not data["title"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "title must not be empty")
    for field, value in data.items():
        setattr(ticket, field, value)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@router.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(
    ticket_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> None:
    ticket, _, member = load_ticket_for_read(ticket_id, user, session)
    if member.role != Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner role required")
    session.delete(ticket)
    session.commit()
```

`status` is shadowed by the query parameter name in the spec, which is why the parameter is declared as `status_filter` with `alias="status"` — the FastAPI `status` module stays importable in the same file.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/test_ticket_api.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add ticket read, board query, update, and delete"
```

---

## Task 14: Status transitions and `completed_at`

**Files:**
- Modify: `app/services.py`, `app/routers/api_tickets.py`, `app/schemas.py`
- Test: `tests/test_status.py`

**Interfaces:**
- Produces:
  - `app.services.set_status(session, ticket, new_status: TicketStatus, resolution_notes: str | None = None) -> Ticket`
  - `app.schemas.StatusUpdate(status, resolution_notes)`
  - Route: `PATCH /api/v1/tickets/{ticket_id}/status`
- Behavior: any status may move to any other. Entering `DONE` stamps `completed_at`; leaving `DONE` clears it. `resolution_notes` is overwritten only when the request supplies one, and is **never** cleared by a status change.

- [ ] **Step 1: Write failing test**

`tests/test_status.py`:

```python
import pytest


@pytest.fixture
def ticket_id(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    return client.post("/api/v1/tickets", json={"slug": project.slug, "title": "T"}).json()["id"]


def test_any_transition_is_allowed_in_both_directions(client, ticket_id):
    for target in ["IN_PROGRESS", "BACKLOG", "DONE", "SELECTED", "DONE"]:
        response = client.patch(
            f"/api/v1/tickets/{ticket_id}/status", json={"status": target}
        )
        assert response.status_code == 200
        assert response.json()["status"] == target


def test_completed_at_is_stamped_and_cleared(client, ticket_id):
    done = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"}).json()
    assert done["completed_at"] is not None
    back = client.patch(
        f"/api/v1/tickets/{ticket_id}/status", json={"status": "IN_PROGRESS"}
    ).json()
    assert back["completed_at"] is None


def test_resolution_notes_survive_leaving_done(client, ticket_id):
    client.patch(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "DONE", "resolution_notes": "fixed in abc1234"},
    )
    back = client.patch(
        f"/api/v1/tickets/{ticket_id}/status", json={"status": "BACKLOG"}
    ).json()
    assert back["resolution_notes"] == "fixed in abc1234"


def test_unknown_status_is_422(client, ticket_id):
    assert client.patch(
        f"/api/v1/tickets/{ticket_id}/status", json={"status": "SHIPPED"}
    ).status_code == 422


def test_non_member_cannot_transition(client, ticket_id, make_user, login_as):
    make_user(email="bob@example.com")
    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    assert client.patch(
        f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"}
    ).status_code == 404
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_status.py -v`
Expected: FAIL with `405 Method Not Allowed`.

- [ ] **Step 3: Append `set_status` to `app/services.py`**

```python
from app.models import utcnow


def set_status(
    session: Session,
    ticket: Ticket,
    new_status: TicketStatus,
    resolution_notes: str | None = None,
) -> Ticket:
    ticket.status = new_status
    if new_status == TicketStatus.DONE:
        ticket.completed_at = utcnow()
    else:
        ticket.completed_at = None
    if resolution_notes is not None:
        ticket.resolution_notes = resolution_notes
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket
```

There is no transition table and no guard. Enum membership is validated by Pydantic before this function is reached, and per ADR-012 that is the only validation there is.

- [ ] **Step 4: Add `StatusUpdate` to `app/schemas.py` and the route to `app/routers/api_tickets.py`**

```python
class StatusUpdate(BaseModel):
    status: TicketStatus
    resolution_notes: str | None = None
```

```python
from app.schemas import StatusUpdate
from app.services import set_status


@router.patch("/tickets/{ticket_id}/status", response_model=TicketOut)
def patch_status(
    ticket_id: str,
    body: StatusUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Ticket:
    ticket, _, _ = load_ticket_for_read(ticket_id, user, session)
    return set_status(session, ticket, body.status, body.resolution_notes)
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/test_status.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add free status transitions with completed_at maintenance"
```

---

## Task 15: Notification payloads and the three formatters

**Files:**
- Create: `app/notifications.py`
- Test: `tests/test_formatters.py`

**Interfaces:**
- Produces:
  - `EVENT_TICKET_CREATED = "TICKET_CREATED"`, `EVENT_TICKET_DONE = "TICKET_DONE"`
  - `build_payload(project: Project, event: str, ticket: Ticket) -> dict` — the flat, ORM-free dict that crosses into the background task. Keys: `event`, `project_id`, `project_name`, `project_slug`, `ticket_number`, `title`, `status`, `type`, `priority`, `assignee_id`.
  - `format_slack(payload) -> dict`, `format_discord(payload) -> dict`, `format_teams(payload) -> dict`
  - `FORMATTERS: dict[WebhookType, Callable[[dict], dict]]`
- Every formatter is a pure function of the payload dict, which is why they are testable without a database or a network.

- [ ] **Step 1: Write failing test**

`tests/test_formatters.py`:

```python
import json

from app.models import WebhookType
from app.notifications import (
    EVENT_TICKET_CREATED,
    FORMATTERS,
    format_discord,
    format_slack,
    format_teams,
)

PAYLOAD = {
    "event": EVENT_TICKET_CREATED,
    "project_id": "p1",
    "project_name": "Payment Gateway",
    "project_slug": "payment-gateway",
    "ticket_number": 42,
    "title": "Card declines on retry",
    "status": "BACKLOG",
    "type": "BUG",
    "priority": "HIGH",
    "assignee_id": None,
}


def test_every_webhook_type_except_none_has_a_formatter():
    assert set(FORMATTERS) == {WebhookType.SLACK, WebhookType.DISCORD, WebhookType.TEAMS}


def test_slack_payload_has_blocks_and_mentions_the_ticket():
    body = format_slack(PAYLOAD)
    assert "blocks" in body
    assert "#42" in json.dumps(body)


def test_discord_payload_has_embeds():
    body = format_discord(PAYLOAD)
    assert body["embeds"][0]["title"].startswith("#42")


def test_teams_payload_is_an_adaptive_card():
    body = format_teams(PAYLOAD)
    assert body["type"] == "message"
    assert body["attachments"][0]["contentType"] == (
        "application/vnd.microsoft.card.adaptive"
    )
    assert "#42" in json.dumps(body)


def test_formatters_are_json_serializable():
    for formatter in FORMATTERS.values():
        json.dumps(formatter(PAYLOAD))
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_formatters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.notifications'`

- [ ] **Step 3: Write `app/notifications.py`**

```python
from collections.abc import Callable

from app.models import Project, Ticket, WebhookType

EVENT_TICKET_CREATED = "TICKET_CREATED"
EVENT_TICKET_DONE = "TICKET_DONE"

_HEADLINES = {
    EVENT_TICKET_CREATED: "New ticket",
    EVENT_TICKET_DONE: "Ticket completed",
}


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
    return (
        f"{_HEADLINES.get(payload['event'], payload['event'])} "
        f"#{payload['ticket_number']}: {payload['title']}"
    )


def _detail(payload: dict) -> str:
    return (
        f"{payload['project_name']} · {payload['type']} · "
        f"{payload['priority']} · {payload['status']}"
    )


def format_slack(payload: dict) -> dict:
    return {
        "text": _headline(payload),
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{_headline(payload)}*"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": _detail(payload)}]},
        ],
    }


def format_discord(payload: dict) -> dict:
    return {
        "embeds": [
            {
                "title": f"#{payload['ticket_number']} {payload['title']}",
                "description": _detail(payload),
            }
        ]
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
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_formatters.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add chat notification payloads for Slack, Discord, and Teams"
```

---

## Task 16: Delivery with timeout, retries, and a warning on failure

**Files:**
- Modify: `app/notifications.py`
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Produces:
  - `TIMEOUT_SECONDS = 5.0`, `BACKOFF_SECONDS = (1, 2, 4)`
  - `dispatch(webhook_type: WebhookType, webhook_url: str, payload: dict, client: httpx.Client | None = None) -> None` — never raises. The optional `client` parameter exists so tests can inject an `httpx.MockTransport` instead of monkeypatching a module global.
  - Module logger named `app.notifications`.
- Behavior: up to 4 attempts total (1 initial + 3 retries) sleeping 1s, 2s, 4s between them. A `2xx`/`3xx` response stops immediately. Exhaustion logs one `WARNING` containing `project_id` and `ticket_number`.

- [ ] **Step 1: Write failing test**

`tests/test_dispatch.py`:

```python
import httpx
import pytest

from app.models import WebhookType
from app.notifications import BACKOFF_SECONDS, dispatch

PAYLOAD = {
    "event": "TICKET_CREATED",
    "project_id": "p1",
    "project_name": "P",
    "project_slug": "p",
    "ticket_number": 42,
    "title": "T",
    "status": "BACKLOG",
    "type": "BUG",
    "priority": "HIGH",
    "assignee_id": None,
}


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("app.notifications.time.sleep", lambda seconds: slept.append(seconds))
    return slept


def test_successful_delivery_posts_once(no_sleep):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)

    assert len(calls) == 1
    assert no_sleep == []


def test_failure_retries_three_times_then_warns(no_sleep, caplog):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("black hole", request=request)

    with caplog.at_level("WARNING", logger="app.notifications"):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)

    assert attempts["n"] == 4
    assert no_sleep == list(BACKOFF_SECONDS)
    assert len(caplog.records) == 1
    assert "p1" in caplog.text and "42" in caplog.text


def test_server_error_is_retried(no_sleep):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500 if attempts["n"] < 3 else 200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)

    assert attempts["n"] == 3


def test_dispatch_is_a_noop_without_a_url(no_sleep):
    dispatch(WebhookType.NONE, "", PAYLOAD)
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: FAIL with `ImportError: cannot import name 'dispatch'`

- [ ] **Step 3: Append the dispatcher to `app/notifications.py`**

```python
import logging
import time

import httpx

logger = logging.getLogger("app.notifications")

TIMEOUT_SECONDS = 5.0
BACKOFF_SECONDS = (1, 2, 4)


def dispatch(
    webhook_type: WebhookType,
    webhook_url: str,
    payload: dict,
    client: httpx.Client | None = None,
) -> None:
    formatter = FORMATTERS.get(webhook_type)
    if formatter is None or not webhook_url:
        return
    body = formatter(payload)
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
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
            payload["project_id"],
            payload["ticket_number"],
        )
    finally:
        if owned:
            client.close()
```

`dispatch` swallows every `httpx.HTTPError` by design: this function runs after the response has been sent, so raising would only produce an unhandled exception in a background thread. The `WARNING` is the entire failure surface, exactly as ADR-013 specifies.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_dispatch.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: deliver chat webhooks with timeout, retries, and failure logging"
```

---

## Task 17: Schedule notifications from the service layer (V-4)

**Files:**
- Modify: `app/notifications.py`, `app/services.py`, `app/routers/api_tickets.py`
- Test: `tests/test_notification_wiring.py`

**Interfaces:**
- Produces `app.notifications.schedule(tasks: BackgroundTasks | None, project: Project, event: str, ticket: Ticket) -> None` — builds the payload **immediately** (while the session is alive) and enqueues only the resulting dict. A `None` task list makes it a no-op, so `create_ticket` stays callable from the Task 12 concurrency test with no FastAPI request in scope.
- Modifies `create_ticket` and `set_status` to take an optional `tasks: BackgroundTasks | None = None` keyword. `create_ticket` fires `TICKET_CREATED`; `set_status` fires `TICKET_DONE` only when the new status is `DONE`.

- [ ] **Step 1: Write failing test**

`tests/test_notification_wiring.py`:

```python
import time

import pytest
from sqlmodel import Session, select

from app.models import Project, Ticket, WebhookType


@pytest.fixture
def slack_project(engine, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        stored = session.get(Project, project.id)
        stored.webhook_type = WebhookType.SLACK
        stored.webhook_url = "https://example.com/hook"
        session.add(stored)
        session.commit()
    return project


def test_creating_a_ticket_dispatches_one_payload(client, slack_project, login_as, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.notifications.dispatch", lambda wt, url, payload, client=None: sent.append(payload)
    )
    login_as("ada@example.com")
    client.post("/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"})
    assert len(sent) == 1
    assert sent[0]["event"] == "TICKET_CREATED"
    assert sent[0]["ticket_number"] == 1


def test_only_done_transitions_notify(client, slack_project, login_as, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.notifications.dispatch", lambda wt, url, payload, client=None: sent.append(payload)
    )
    login_as("ada@example.com")
    ticket_id = client.post(
        "/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"}
    ).json()["id"]
    sent.clear()
    client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "IN_PROGRESS"})
    assert sent == []
    client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"})
    assert [p["event"] for p in sent] == ["TICKET_DONE"]


def test_round_trip_to_done_sends_two_cards(client, slack_project, login_as, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.notifications.dispatch", lambda wt, url, payload, client=None: sent.append(payload)
    )
    login_as("ada@example.com")
    ticket_id = client.post(
        "/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"}
    ).json()["id"]
    sent.clear()
    for target in ["DONE", "IN_PROGRESS", "DONE"]:
        client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": target})
    assert len(sent) == 2, "no duplicate suppression is intentional (D-03)"


def test_dispatch_runs_after_the_row_is_committed(client, slack_project, login_as, monkeypatch, engine):
    visible: list[bool] = []

    def spy(webhook_type, url, payload, client=None):
        with Session(engine) as session:
            rows = session.exec(
                select(Ticket).where(Ticket.ticket_number == payload["ticket_number"])
            ).all()
            visible.append(len(rows) == 1)

    monkeypatch.setattr("app.notifications.dispatch", spy)
    login_as("ada@example.com")
    client.post("/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"})
    assert visible == [True]


def test_black_hole_webhook_does_not_break_creation(
    client, slack_project, login_as, monkeypatch, caplog, engine
):
    monkeypatch.setattr("app.notifications.time.sleep", lambda seconds: None)
    with Session(engine) as session:
        stored = session.get(Project, slack_project.id)
        stored.webhook_url = "https://127.0.0.1:9/hook"
        session.add(stored)
        session.commit()
    login_as("ada@example.com")
    with caplog.at_level("WARNING", logger="app.notifications"):
        response = client.post(
            "/api/v1/tickets", json={"slug": slack_project.slug, "title": "Survives"}
        )
    assert response.status_code == 201
    assert response.json()["ticket_number"] == 1
    assert "ticket_number=1" in caplog.text


def test_creation_latency_excludes_delivery(client, slack_project, login_as, monkeypatch):
    monkeypatch.setattr("app.notifications.dispatch", lambda *a, **k: None)
    login_as("ada@example.com")
    started = time.perf_counter()
    response = client.post("/api/v1/tickets", json={"slug": slack_project.slug, "title": "T"})
    elapsed = time.perf_counter() - started
    assert response.status_code == 201
    assert elapsed < 0.5, f"handler took {elapsed * 1000:.0f} ms, budget is 500 ms"
```

`test_black_hole_webhook_does_not_break_creation` points the URL at port 9, the discard port, which refuses connections immediately — so the real `dispatch` runs all four attempts fast with sleeps patched out. `TestClient` waits for background tasks before returning, which is why the 500 ms budget is asserted in a separate test with delivery stubbed: the number that budget is about is the handler's own cost, not the delivery's.

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_notification_wiring.py -v`
Expected: FAIL — nothing calls `dispatch`.

- [ ] **Step 3: Append `schedule` to `app/notifications.py`**

```python
from fastapi import BackgroundTasks


def schedule(
    tasks: BackgroundTasks | None, project: Project, event: str, ticket: Ticket
) -> None:
    if tasks is None or project.webhook_type == WebhookType.NONE or not project.webhook_url:
        return
    payload = build_payload(project, event, ticket)
    tasks.add_task(dispatch, project.webhook_type, project.webhook_url, payload)
```

`build_payload` is called here, inside the request, so no ORM object is captured by the background closure.

- [ ] **Step 4: Wire the service functions**

In `app/services.py`, add `tasks: BackgroundTasks | None = None` to `create_ticket`'s keyword arguments and end it with:

```python
    schedule(tasks, project, EVENT_TICKET_CREATED, ticket)
    return ticket
```

Change `set_status`'s signature to `set_status(session, ticket, new_status, resolution_notes=None, *, project: Project | None = None, tasks: BackgroundTasks | None = None)` and end it with:

```python
    if new_status == TicketStatus.DONE and project is not None:
        schedule(tasks, project, EVENT_TICKET_DONE, ticket)
    return ticket
```

Import `BackgroundTasks` from `fastapi` and `EVENT_TICKET_CREATED`, `EVENT_TICKET_DONE`, `schedule` from `app.notifications`.

- [ ] **Step 5: Pass `BackgroundTasks` from the routes**

In `app/routers/api_tickets.py`, add `tasks: BackgroundTasks` as a parameter to `post_ticket` and `patch_status`, then forward it: `create_ticket(..., tasks=tasks)` and `set_status(session, ticket, body.status, body.resolution_notes, project=project, tasks=tasks)`. `patch_status` must now unpack the project from `load_ticket_for_read`.

- [ ] **Step 6: Run the whole suite and verify it passes**

Run: `uv run pytest -v`
Expected: PASS, including the Task 12 concurrency test — `create_ticket` still works with `tasks=None`.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: schedule chat notifications on ticket creation and completion"
```

---

## Task 18: Markdown rendering and sanitization (V-9)

**Files:**
- Create: `app/rendering.py`
- Test: `tests/test_rendering.py`

**Interfaces:**
- Produces `render_markdown(text: str | None) -> str` — returns sanitized HTML. Registered as the Jinja filter `markdown` in Task 19 and used by every template that shows a description.
- Sanitization runs here and nowhere else. Nothing on the write path ever touches user text.

- [ ] **Step 1: Write failing test**

`tests/test_rendering.py`:

```python
from app.rendering import render_markdown


def test_basic_markdown_renders():
    html = render_markdown("**bold** and `code`")
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html


def test_script_tags_are_neutralized():
    html = render_markdown("<script>alert('xss')</script>")
    assert "<script>" not in html
    assert "alert" in html


def test_event_handlers_are_stripped():
    html = render_markdown('<img src=x onerror="alert(1)">')
    assert "onerror" not in html


def test_javascript_urls_are_stripped():
    html = render_markdown("[click](javascript:alert(1))")
    assert "javascript:" not in html


def test_none_and_empty_are_safe():
    assert render_markdown(None) == ""
    assert render_markdown("") == ""
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_rendering.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rendering'`

- [ ] **Step 3: Write `app/rendering.py`**

```python
import bleach
from markdown_it import MarkdownIt

ALLOWED_TAGS = [
    "p", "br", "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "hr", "table", "thead", "tbody", "tr", "th", "td",
]
ALLOWED_ATTRIBUTES = {"a": ["href", "title"]}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

_md = MarkdownIt("commonmark", {"html": False, "linkify": False})


def render_markdown(text: str | None) -> str:
    if not text:
        return ""
    return bleach.clean(
        _md.render(text),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=False,
    )
```

`html: False` stops markdown-it from passing raw HTML through, and `bleach` with `strip=False` escapes whatever survives so the payload stays visible as text rather than disappearing. Both layers are needed: the first blocks the common case, the second covers anything markdown-it emits that the allow-list does not name.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_rendering.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: render Markdown through an allow-list sanitizer"
```

---

## Task 19: Base templates, auth pages, and web auth routes

**Files:**
- Create: `app/templates/base.html`, `app/templates/login.html`, `app/templates/register.html`, `app/routers/web.py`
- Modify: `app/main.py`
- Test: `tests/test_web_auth.py`

**Interfaces:**
- Produces:
  - `app.main.templates` — the `Jinja2Templates` instance with the `markdown` filter registered. Tasks 20–23 import it.
  - `app.routers.web.router` mounted without a prefix.
  - Routes: `GET /`, `GET /login`, `POST /login`, `GET /register`, `POST /register`, `POST /logout`
- The HTML auth routes accept form bodies and set the same cookie as the JSON routes by calling `app.routers.api_auth._set_session`. There is one session format, not two.

- [ ] **Step 1: Write failing test**

`tests/test_web_auth.py`:

```python
from app.auth import SESSION_COOKIE


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "<form" in response.text


def test_register_form_creates_a_session_and_redirects(client):
    response = client.post(
        "/register",
        data={"name": "Ada", "email": "ada@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert SESSION_COOKIE in response.cookies


def test_duplicate_registration_rerenders_with_an_error(client, make_user):
    make_user(email="ada@example.com")
    response = client.post(
        "/register",
        data={"name": "Ada", "email": "ada@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "already registered" in response.text.lower()


def test_bad_login_rerenders_with_an_error(client, make_user):
    make_user(email="ada@example.com")
    response = client.post(
        "/login", data={"email": "ada@example.com", "password": "wrong"}, follow_redirects=False
    )
    assert response.status_code == 401
    assert "invalid" in response.text.lower()


def test_root_redirects_anonymous_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_web_auth.py -v`
Expected: FAIL with `404` on every route.

- [ ] **Step 3: Write `app/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Kanban Flow{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@1.9.12" defer></script>
  <script src="https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js" defer></script>
</head>
<body class="bg-slate-50 text-slate-900">
  <header class="border-b bg-white px-6 py-3 flex items-center gap-4">
    <a href="/dashboard" class="font-semibold">Kanban Flow</a>
    {% block nav %}{% endblock %}
    {% if user %}
      <form method="post" action="/logout" class="ml-auto">
        <input type="hidden" name="_csrf" value="{{ csrf_token }}">
        <button class="text-sm text-slate-600 hover:underline">Sign out</button>
      </form>
    {% endif %}
  </header>
  <main class="p-6">{% block content %}{% endblock %}</main>
</body>
</html>
```

- [ ] **Step 4: Write `app/templates/login.html` and `app/templates/register.html`**

`login.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="max-w-sm mx-auto bg-white p-6 rounded border">
  <h1 class="text-lg font-semibold mb-4">Sign in</h1>
  {% if error %}<p class="mb-3 text-sm text-red-600">{{ error }}</p>{% endif %}
  <form method="post" action="/login" class="space-y-3">
    <input class="w-full border rounded px-3 py-2" type="email" name="email" placeholder="Email" required>
    <input class="w-full border rounded px-3 py-2" type="password" name="password" placeholder="Password" required>
    <button class="w-full bg-slate-900 text-white rounded py-2">Sign in</button>
  </form>
  <p class="mt-4 text-sm">No account? <a class="underline" href="/register">Register</a></p>
</div>
{% endblock %}
```

`register.html` is the same with an added `name` input, `action="/register"`, the heading "Create account", and a link back to `/login`.

Neither form carries a CSRF token: there is no session to bind one to before the user authenticates, and neither route performs an authenticated action.

- [ ] **Step 5: Write `app/routers/web.py`**

```python
from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.auth import hash_password, make_csrf_token, optional_user, verify_password
from app.db import get_session
from app.models import User
from app.routers.api_auth import _set_session

router = APIRouter(tags=["web"])


def render(request: Request, name: str, context: dict, status_code: int = 200) -> Response:
    from app.main import templates

    user = context.get("user")
    context = {
        "request": request,
        "csrf_token": make_csrf_token(user.id) if user else "",
        **context,
    }
    return templates.TemplateResponse(name, context, status_code=status_code)


@router.get("/")
def index(user: User | None = Depends(optional_user)) -> Response:
    target = "/dashboard" if user else "/login"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login")
def login_page(request: Request) -> Response:
    return render(request, "login.html", {"user": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    user = session.exec(select(User).where(User.email == email.lower())).first()
    if user is None or not verify_password(password, user.password_hash):
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
def register_page(request: Request) -> Response:
    return render(request, "register.html", {"user": None})


@router.post("/register")
def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    if len(password) < 8:
        return render(
            request,
            "register.html",
            {"user": None, "error": "Password must be at least 8 characters"},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if session.exec(select(User).where(User.email == email.lower())).first():
        return render(
            request,
            "register.html",
            {"user": None, "error": "That email is already registered"},
            status_code=status.HTTP_409_CONFLICT,
        )
    user = User(name=name, email=email.lower(), password_hash=hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session(response, user.id)
    return response


@router.post("/logout")
def logout_submit() -> Response:
    from app.auth import SESSION_COOKIE

    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
```

`render` imports `templates` lazily inside the function to avoid a circular import between `app.main` and `app.routers.web`.

- [ ] **Step 6: Register the template environment in `app/main.py`**

```python
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.rendering import render_markdown
from app.routers import api_auth, api_projects, api_tickets, web

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["markdown"] = render_markdown

app.include_router(web.router)
```

Mount `web.router` last so its `/` route does not shadow an API path.

- [ ] **Step 7: Run the tests and verify they pass**

Run: `uv run pytest tests/test_web_auth.py -v`
Expected: PASS (5 tests)

- [ ] **Step 8: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add base layout and server-rendered authentication pages"
```

---

## Task 20: Dashboard page with project creation

**Files:**
- Create: `app/templates/dashboard.html`
- Modify: `app/routers/web.py`
- Test: `tests/test_web_dashboard.py`

**Interfaces:**
- Consumes `app.routers.api_projects.slugify`, `app.auth.verify_csrf`
- Routes: `GET /dashboard`, `POST /projects`
- This is the first task whose `POST` carries the CSRF dependency. Every later HTML `POST` follows the same shape.

- [ ] **Step 1: Write failing test**

`tests/test_web_dashboard.py`:

```python
import re

from app.auth import make_csrf_token


def csrf_for(user):
    return make_csrf_token(user.id)


def test_dashboard_lists_projects(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_project(owner, name="Payment Gateway")
    login_as("ada@example.com")
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Payment Gateway" in response.text


def test_dashboard_requires_login(client):
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_create_project_from_the_form(client, make_user, login_as):
    owner = make_user(email="ada@example.com")
    login_as("ada@example.com")
    response = client.post(
        "/projects",
        data={"name": "Payment Gateway", "_csrf": csrf_for(owner)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/projects/payment-gateway"


def test_create_project_without_csrf_is_403(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    response = client.post("/projects", data={"name": "X"}, follow_redirects=False)
    assert response.status_code == 403


def test_create_project_with_another_users_csrf_is_403(client, make_user, login_as):
    make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    login_as("ada@example.com")
    response = client.post(
        "/projects", data={"name": "X", "_csrf": csrf_for(bob)}, follow_redirects=False
    )
    assert response.status_code == 403


def test_rendered_form_contains_a_usable_csrf_token(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    page = client.get("/dashboard").text
    token = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    response = client.post(
        "/projects", data={"name": "From Page", "_csrf": token}, follow_redirects=False
    )
    assert response.status_code == 303
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_web_dashboard.py -v`
Expected: FAIL with `404` on `/dashboard`.

- [ ] **Step 3: Write `app/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="max-w-3xl mx-auto space-y-6">
  <section class="bg-white border rounded p-4">
    <h1 class="font-semibold mb-3">Your projects</h1>
    {% if projects %}
      <ul class="divide-y">
        {% for project in projects %}
          <li class="py-2 flex items-center">
            <a class="underline" href="/projects/{{ project.slug }}">{{ project.name }}</a>
            <span class="ml-auto text-xs uppercase text-slate-500">{{ roles[project.id] }}</span>
          </li>
        {% endfor %}
      </ul>
    {% else %}
      <p class="text-sm text-slate-500">No projects yet.</p>
    {% endif %}
  </section>
  <section class="bg-white border rounded p-4">
    <h2 class="font-semibold mb-3">New project</h2>
    {% if error %}<p class="mb-3 text-sm text-red-600">{{ error }}</p>{% endif %}
    <form method="post" action="/projects" class="flex gap-2">
      <input type="hidden" name="_csrf" value="{{ csrf_token }}">
      <input class="flex-1 border rounded px-3 py-2" name="name" placeholder="Project name" required>
      <button class="bg-slate-900 text-white rounded px-4">Create</button>
    </form>
  </section>
</div>
{% endblock %}
```

- [ ] **Step 4: Add the routes to `app/routers/web.py`**

```python
from app.auth import current_user, verify_csrf
from app.models import Project, ProjectMember, Role
from app.routers.api_projects import slugify


def _require_login(user: User | None) -> Response | None:
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return None


@router.get("/dashboard")
def dashboard(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> Response:
    redirect = _require_login(user)
    if redirect:
        return redirect
    rows = session.exec(
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.created_at.desc())
    ).all()
    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "projects": [project for project, _ in rows],
            "roles": {project.id: member.role.value for project, member in rows},
        },
    )


@router.post("/projects", dependencies=[Depends(verify_csrf)])
def create_project_form(
    request: Request,
    name: str = Form(...),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    slug = slugify(name)
    error = None
    if not slug:
        error = "That name produces an empty slug"
    elif session.exec(select(Project).where(Project.slug == slug)).first():
        error = f"Slug already taken: {slug}"
    if error:
        rows = session.exec(
            select(Project, ProjectMember)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == user.id)
        ).all()
        return render(
            request,
            "dashboard.html",
            {
                "user": user,
                "projects": [p for p, _ in rows],
                "roles": {p.id: m.role.value for p, m in rows},
                "error": error,
            },
            status_code=status.HTTP_409_CONFLICT,
        )
    project = Project(name=name.strip(), slug=slug)
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.OWNER))
    session.commit()
    return RedirectResponse(f"/projects/{slug}", status_code=status.HTTP_303_SEE_OTHER)
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/test_web_dashboard.py -v`
Expected: PASS (6 tests). The redirect target `/projects/{slug}` does not exist until Task 21, which is why every test here uses `follow_redirects=False`.

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add dashboard with CSRF-protected project creation"
```

---

## Task 21: Board page

**Files:**
- Create: `app/templates/board.html`, `app/templates/partials/ticket_card.html`
- Modify: `app/routers/web.py`
- Test: `tests/test_web_board.py`

**Interfaces:**
- Produces:
  - `app.routers.web.COLUMNS: tuple[TicketStatus, ...]` — `(BACKLOG, SELECTED, IN_PROGRESS, DONE)`, the fixed column set.
  - `partials/ticket_card.html` rendering one ticket, with a wrapper `id="ticket-{{ ticket.id }}"`. Tasks 22 and 23 return this same partial standalone.
  - Column containers with `id="column-BACKLOG"` and so on, which Task 22 targets with `hx-target`.
  - Route: `GET /projects/{slug}`

- [ ] **Step 1: Write failing test**

`tests/test_web_board.py`:

```python
def test_board_renders_four_fixed_columns(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    page = client.get(f"/projects/{project.slug}").text
    for column in ["BACKLOG", "SELECTED", "IN_PROGRESS", "DONE"]:
        assert f'id="column-{column}"' in page


def test_board_shows_tickets_in_their_columns(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    created = client.post(
        "/api/v1/tickets", json={"slug": project.slug, "title": "Card declines"}
    ).json()
    client.patch(f"/api/v1/tickets/{created['id']}/status", json={"status": "IN_PROGRESS"})
    page = client.get(f"/projects/{project.slug}").text
    assert "Card declines" in page
    assert f'id="ticket-{created["id"]}"' in page


def test_board_renders_markdown_descriptions_inertly(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    client.post(
        "/api/v1/tickets",
        json={
            "slug": project.slug,
            "title": "XSS attempt",
            "description": "<script>alert('xss')</script> and <img src=x onerror=\"alert(1)\">",
        },
    )
    page = client.get(f"/projects/{project.slug}").text
    assert "<script>alert" not in page
    assert "onerror" not in page
    assert "alert" in page


def test_non_member_gets_404_for_the_board(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    assert client.get(f"/projects/{project.slug}").status_code == 404


def test_anonymous_visitor_is_redirected(client, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    response = client.get(f"/projects/{project.slug}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_web_board.py -v`
Expected: FAIL with `404`.

- [ ] **Step 3: Write `app/templates/partials/ticket_card.html`**

```html
<article id="ticket-{{ ticket.id }}" class="bg-white border rounded p-3 mb-2 text-sm">
  <div class="flex items-baseline gap-2">
    <span class="font-mono text-xs text-slate-500">#{{ ticket.ticket_number }}</span>
    <span class="font-medium">{{ ticket.title }}</span>
  </div>
  <div class="mt-1 flex gap-2 text-xs text-slate-500">
    <span>{{ ticket.type.value }}</span>
    <span>{{ ticket.priority.value }}</span>
    {% if ticket.story_points %}<span>{{ ticket.story_points }} pts</span>{% endif %}
  </div>
  {% if ticket.description %}
    <div class="prose prose-sm mt-2">{{ ticket.description | markdown | safe }}</div>
  {% endif %}
  <form class="mt-2"
        hx-post="/projects/{{ project.slug }}/tickets/{{ ticket.ticket_number }}/status"
        hx-target="#ticket-{{ ticket.id }}"
        hx-swap="outerHTML">
    <input type="hidden" name="_csrf" value="{{ csrf_token }}">
    <select name="status" class="border rounded text-xs px-1 py-0.5"
            onchange="this.form.requestSubmit()">
      {% for column in columns %}
        <option value="{{ column.value }}" {% if column == ticket.status %}selected{% endif %}>
          {{ column.value }}
        </option>
      {% endfor %}
    </select>
  </form>
</article>
```

`| markdown | safe` is the only place `safe` appears in the templates. It is safe precisely because `render_markdown` ran `bleach` first; anywhere else, `safe` would be a vulnerability.

- [ ] **Step 4: Write `app/templates/board.html`**

```html
{% extends "base.html" %}
{% block title %}{{ project.name }}{% endblock %}
{% block content %}
<div x-data="{ modal: false }">
  <div class="flex items-center mb-4">
    <h1 class="text-lg font-semibold">{{ project.name }}</h1>
    <button class="ml-auto bg-slate-900 text-white rounded px-3 py-1.5 text-sm"
            @click="modal = true">+ New Ticket</button>
  </div>

  <div class="grid grid-cols-4 gap-4">
    {% for column in columns %}
      <section class="bg-slate-100 rounded p-2">
        <h2 class="text-xs font-semibold uppercase text-slate-600 mb-2">{{ column.value }}</h2>
        <div id="column-{{ column.value }}">
          {% for ticket in tickets_by_status[column.value] %}
            {% include "partials/ticket_card.html" %}
          {% endfor %}
        </div>
      </section>
    {% endfor %}
  </div>

  {% include "partials/ticket_modal.html" %}
</div>
{% endblock %}
```

- [ ] **Step 5: Create a placeholder `app/templates/partials/ticket_modal.html`**

```html
{# Replaced in full by Task 22 #}
```

- [ ] **Step 6: Add the board route to `app/routers/web.py`**

```python
from app.models import Ticket, TicketStatus

COLUMNS = (
    TicketStatus.BACKLOG,
    TicketStatus.SELECTED,
    TicketStatus.IN_PROGRESS,
    TicketStatus.DONE,
)


@router.get("/projects/{slug}")
def board(
    slug: str,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> Response:
    redirect = _require_login(user)
    if redirect:
        return redirect
    project = session.exec(select(Project).where(Project.slug == slug)).first()
    member = (
        session.exec(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
            )
        ).first()
        if project
        else None
    )
    if project is None or member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    tickets = session.exec(
        select(Ticket)
        .where(Ticket.project_id == project.id)
        .order_by(Ticket.ticket_number.desc())
    ).all()
    return render(
        request,
        "board.html",
        {
            "user": user,
            "project": project,
            "role": member.role.value,
            "columns": COLUMNS,
            "tickets_by_status": {
                column.value: [t for t in tickets if t.status == column] for column in COLUMNS
            },
        },
    )
```

Import `HTTPException` from `fastapi` at the top of `web.py`.

- [ ] **Step 7: Run the tests and verify they pass**

Run: `uv run pytest tests/test_web_board.py -v`
Expected: PASS (5 tests). This also satisfies V-9 end to end: the sanitizer unit test in Task 18 proves the function, and `test_board_renders_markdown_descriptions_inertly` proves it is actually wired into the page.

- [ ] **Step 8: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: render the four-column Kanban board with sanitized descriptions"
```

---

## Task 22: Ticket creation modal and the HTMX fragment response

**Files:**
- Create: `app/templates/partials/ticket_modal.html` (replacing the placeholder)
- Modify: `app/routers/web.py`
- Test: `tests/test_web_ticket_create.py`

**Interfaces:**
- Consumes `app.services.create_ticket`, `app.auth.verify_csrf`
- Route: `POST /projects/{slug}/tickets` — returns **one rendered `ticket_card.html` fragment** with status `201`, not a redirect and not a full page.
- The modal form declares `hx-target="#column-BACKLOG"` and `hx-swap="afterbegin"`, so a new ticket lands at the top of the backlog column with no page reload. This is the success criterion in `cs482_workflow.md` §1.

- [ ] **Step 1: Write failing test**

`tests/test_web_ticket_create.py`:

```python
import re

from app.auth import make_csrf_token


def test_modal_targets_the_backlog_column(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    page = client.get(f"/projects/{project.slug}").text
    assert 'hx-target="#column-BACKLOG"' in page
    assert 'hx-swap="afterbegin"' in page
    assert f'hx-post="/projects/{project.slug}/tickets"' in page


def test_submitting_the_modal_returns_a_card_fragment(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={
            "title": "Card declines on retry",
            "type": "BUG",
            "description": "Happens on the second attempt",
            "_csrf": make_csrf_token(owner.id),
        },
    )
    assert response.status_code == 201
    assert "<html" not in response.text
    assert response.text.strip().startswith("<article")
    assert "Card declines on retry" in response.text
    assert "#1" in response.text


def test_the_created_ticket_appears_on_the_board(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "Visible later", "type": "TASK", "_csrf": make_csrf_token(owner.id)},
    )
    page = client.get(f"/projects/{project.slug}").text
    backlog = page.split('id="column-BACKLOG"')[1].split("</section>")[0]
    assert "Visible later" in backlog


def test_empty_title_is_422(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "   ", "type": "TASK", "_csrf": make_csrf_token(owner.id)},
    )
    assert response.status_code == 422


def test_non_member_submission_is_403(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "Sneaky", "type": "TASK", "_csrf": make_csrf_token(bob.id)},
    )
    assert response.status_code == 403


def test_five_interactions_or_fewer(client, make_user, make_project, login_as):
    """Open modal, title, type, description, submit — the form must ask for nothing else."""
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    page = client.get(f"/projects/{project.slug}").text
    modal = page.split('id="ticket-modal"')[1].split("</form>")[0]
    required = re.findall(r"<(?:input|select|textarea)[^>]*\brequired\b[^>]*>", modal)
    assert len(required) <= 2, "only title and type may be required"
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_web_ticket_create.py -v`
Expected: FAIL — the modal is still a placeholder comment.

- [ ] **Step 3: Write `app/templates/partials/ticket_modal.html`**

```html
<div x-show="modal" x-cloak
     class="fixed inset-0 bg-black/40 flex items-center justify-center p-4">
  <div id="ticket-modal" class="bg-white rounded p-5 w-full max-w-md" @click.outside="modal = false">
    <h2 class="font-semibold mb-3">New ticket</h2>
    <form hx-post="/projects/{{ project.slug }}/tickets"
          hx-target="#column-BACKLOG"
          hx-swap="afterbegin"
          @htmx:after-request="if ($event.detail.successful) { modal = false; $el.reset(); }"
          class="space-y-3">
      <input type="hidden" name="_csrf" value="{{ csrf_token }}">
      <input class="w-full border rounded px-3 py-2" name="title" placeholder="Title" required>
      <select class="w-full border rounded px-3 py-2" name="type" required>
        <option value="STORY">Story</option>
        <option value="BUG">Bug</option>
        <option value="DEMO_REQUEST">Demo request</option>
        <option value="TASK" selected>Task</option>
      </select>
      <textarea class="w-full border rounded px-3 py-2" name="description" rows="4"
                placeholder="Description (Markdown)"></textarea>
      <div class="flex gap-2 justify-end">
        <button type="button" class="px-3 py-1.5 text-sm" @click="modal = false">Cancel</button>
        <button class="bg-slate-900 text-white rounded px-4 py-1.5 text-sm">Create</button>
      </div>
    </form>
  </div>
</div>
```

Alpine holds `modal` open/closed and nothing else; HTMX performs the request and the DOM insertion. That division is decision D-02.

- [ ] **Step 4: Add the route to `app/routers/web.py`**

```python
from app.models import Priority, TicketType
from app.services import create_ticket


def _project_for_member(slug: str, user: User, session: Session) -> tuple[Project, ProjectMember]:
    project = session.exec(select(Project).where(Project.slug == slug)).first()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    member = session.exec(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
        )
    ).first()
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")
    return project, member


def _card(request: Request, project: Project, ticket: Ticket, user: User, code: int) -> Response:
    return render(
        request,
        "partials/ticket_card.html",
        {"user": user, "project": project, "ticket": ticket, "columns": COLUMNS},
        status_code=code,
    )


@router.post("/projects/{slug}/tickets", dependencies=[Depends(verify_csrf)])
def create_ticket_form(
    slug: str,
    request: Request,
    tasks: BackgroundTasks,
    title: str = Form(...),
    type: TicketType = Form(TicketType.TASK),
    description: str = Form(""),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = _project_for_member(slug, user, session)
    ticket = create_ticket(
        session,
        project,
        user,
        title=title,
        description=description,
        type=type,
        priority=Priority.MEDIUM,
        tasks=tasks,
    )
    return _card(request, project, ticket, user, status.HTTP_201_CREATED)
```

Import `BackgroundTasks` from `fastapi`. `create_ticket` already raises `422` on an empty title, so the web route needs no separate check.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/test_web_ticket_create.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add ticket modal returning an HTMX card fragment"
```

---

## Task 23: Status dropdown on the board

**Files:**
- Modify: `app/routers/web.py`
- Test: `tests/test_web_status.py`

**Interfaces:**
- Route: `POST /projects/{slug}/tickets/{ticket_number}/status` — returns the re-rendered `ticket_card.html` fragment, which HTMX swaps with `outerHTML`. The card markup written in Task 21 already points at this URL.
- This is the one place in the product where a ticket is addressed by `ticket_number` rather than `id`: the board renders human-readable numbers, so round-tripping a UUID through the page would serve nothing.

- [ ] **Step 1: Write failing test**

`tests/test_web_status.py`:

```python
from app.auth import make_csrf_token


def make_ticket(client, project, owner):
    return client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "T", "type": "TASK", "_csrf": make_csrf_token(owner.id)},
    )


def test_dropdown_moves_the_card_and_returns_a_fragment(
    client, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    make_ticket(client, project, owner)
    response = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "IN_PROGRESS", "_csrf": make_csrf_token(owner.id)},
    )
    assert response.status_code == 200
    assert response.text.strip().startswith("<article")
    assert "<html" not in response.text
    page = client.get(f"/projects/{project.slug}").text
    in_progress = page.split('id="column-IN_PROGRESS"')[1].split("</section>")[0]
    assert "#1" in in_progress


def test_unknown_ticket_number_is_404(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets/999/status",
        data={"status": "DONE", "_csrf": make_csrf_token(owner.id)},
    )
    assert response.status_code == 404


def test_status_change_without_csrf_is_403(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    make_ticket(client, project, owner)
    response = client.post(
        f"/projects/{project.slug}/tickets/1/status", data={"status": "DONE"}
    )
    assert response.status_code == 403


def test_ticket_numbers_do_not_leak_across_projects(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    first = make_project(owner, name="First")
    second = make_project(owner, name="Second")
    login_as("ada@example.com")
    make_ticket(client, first, owner)
    response = client.post(
        f"/projects/{second.slug}/tickets/1/status",
        data={"status": "DONE", "_csrf": make_csrf_token(owner.id)},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_web_status.py -v`
Expected: FAIL with `404` — the route does not exist.

- [ ] **Step 3: Add the route to `app/routers/web.py`**

```python
from app.models import TicketStatus
from app.services import set_status


@router.post(
    "/projects/{slug}/tickets/{ticket_number}/status", dependencies=[Depends(verify_csrf)]
)
def change_status_form(
    slug: str,
    ticket_number: int,
    request: Request,
    tasks: BackgroundTasks,
    status_value: TicketStatus = Form(..., alias="status"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    project, _ = _project_for_member(slug, user, session)
    ticket = session.exec(
        select(Ticket).where(
            Ticket.project_id == project.id, Ticket.ticket_number == ticket_number
        )
    ).first()
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    ticket = set_status(session, ticket, status_value, project=project, tasks=tasks)
    return _card(request, project, ticket, user, status.HTTP_200_OK)
```

The query filters on `project_id` **and** `ticket_number` together. Numbers are project-scoped, so a lookup by number alone would resolve across projects — which is what `test_ticket_numbers_do_not_leak_across_projects` guards.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_web_status.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: add board status dropdown returning an updated card"
```

---

## Task 24: `Origin` check for cookie-authenticated API calls

**Files:**
- Modify: `app/main.py`, `app/config.py`
- Test: `tests/test_origin_guard.py`

**Interfaces:**
- Produces `app.config.Settings.allowed_origins: list[str]` (default `["http://localhost:8000"]`) and an HTTP middleware that rejects any state-changing `/api/v1/*` request which authenticates by session cookie and carries a foreign `Origin`.
- This is the second half of D-11. The HTML routes are covered by the CSRF token; the JSON routes are not, and until slice 2 issues Bearer PATs they accept the same cookie a browser sends automatically. Without this check, a page on another origin can drive `POST /api/v1/tickets` on a logged-in user's behalf.
- A request with **no** `Origin` header is allowed: non-browser clients (curl, the future MCP server, the test suite) do not send one, and browsers always do on cross-origin writes.

- [ ] **Step 1: Write failing test**

`tests/test_origin_guard.py`:

```python
def test_same_origin_write_is_allowed(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "T"},
        headers={"Origin": "http://localhost:8000"},
    )
    assert response.status_code == 201


def test_foreign_origin_write_is_rejected(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "T"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403


def test_missing_origin_is_allowed(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post("/api/v1/tickets", json={"slug": project.slug, "title": "T"})
    assert response.status_code == 201


def test_reads_are_not_blocked_by_origin(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.get(
        f"/api/v1/projects/{project.slug}", headers={"Origin": "https://evil.example.com"}
    )
    assert response.status_code == 200
```

Reads stay open because the same-origin policy already stops an attacker from reading the response body, and blocking them would break nothing an attacker cares about while breaking legitimate cross-origin tooling.

- [ ] **Step 2: Run the test and verify it fails**

Run: `uv run pytest tests/test_origin_guard.py -v`
Expected: FAIL — `test_foreign_origin_write_is_rejected` returns `201`.

- [ ] **Step 3: Add the setting to `app/config.py`**

```python
    allowed_origins: list[str] = ["http://localhost:8000"]
```

- [ ] **Step 4: Add the middleware to `app/main.py`**

```python
from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth import SESSION_COOKIE

UNSAFE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


@app.middleware("http")
async def block_foreign_origin_cookie_writes(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method in UNSAFE_METHODS
        and request.url.path.startswith("/api/v1/")
        and SESSION_COOKIE in request.cookies
        and origin
        and origin not in settings.allowed_origins
    ):
        return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
    return await call_next(request)
```

Import `settings` from `app.config`. The condition requires **all** of: an unsafe method, an API path, a session cookie, and a foreign `Origin`. A Bearer-authenticated request in slice 2 carries no session cookie and is therefore never affected.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/test_origin_guard.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS. No existing test sends an `Origin` header, so none is affected.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add -A
git commit -m "feat: reject cross-origin cookie-authenticated API writes"
```

---

## Task 25: Authorization matrix (V-8)

**Files:**
- Test: `tests/test_authorization_matrix.py`

**Interfaces:**
- Consumes every route built so far. No source changes are expected. If a cell fails, fix the route's dependency — `project_reader` for reads, `project_writer` for member writes, `project_owner` for owner-only writes — rather than special-casing the handler.

- [ ] **Step 1: Write the test**

`tests/test_authorization_matrix.py`:

```python
import pytest

OWNER_ONLY = "owner_only"
ANY_MEMBER = "any_member"


def endpoints(project, ticket_id, other_user_id):
    slug = project.slug
    return [
        (ANY_MEMBER, "post", "/api/v1/tickets", {"json": {"slug": slug, "title": "T"}}),
        (ANY_MEMBER, "patch", f"/api/v1/tickets/{ticket_id}", {"json": {"title": "Edited"}}),
        (
            ANY_MEMBER,
            "patch",
            f"/api/v1/tickets/{ticket_id}/status",
            {"json": {"status": "DONE"}},
        ),
        (OWNER_ONLY, "delete", f"/api/v1/tickets/{ticket_id}", {}),
        (OWNER_ONLY, "patch", f"/api/v1/projects/{slug}", {"json": {"name": "Renamed"}}),
        (
            OWNER_ONLY,
            "post",
            f"/api/v1/projects/{slug}/members",
            {"json": {"email": "carol@example.com", "role": "MEMBER"}},
        ),
        (
            OWNER_ONLY,
            "patch",
            f"/api/v1/projects/{slug}/members/{other_user_id}",
            {"json": {"role": "OWNER"}},
        ),
        (OWNER_ONLY, "delete", f"/api/v1/projects/{slug}/members/{other_user_id}", {}),
        (OWNER_ONLY, "delete", f"/api/v1/projects/{slug}?confirm={slug}", {}),
    ]


@pytest.fixture
def world(client, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    member = make_user(email="bob@example.com")
    make_user(email="carol@example.com")
    outsider = make_user(email="dan@example.com")
    project = make_project(owner)
    add_member(project, member)
    login_as("ada@example.com")
    ticket_id = client.post(
        "/api/v1/tickets", json={"slug": project.slug, "title": "Seed"}
    ).json()["id"]
    client.post("/api/v1/auth/logout")
    return {
        "project": project,
        "ticket_id": ticket_id,
        "owner": owner,
        "member": member,
        "outsider": outsider,
    }


def call(client, method, url, kwargs):
    return getattr(client, method)(url, **kwargs)


def test_owner_is_allowed_everywhere(client, world, login_as):
    login_as("ada@example.com")
    for _, method, url, kwargs in endpoints(
        world["project"], world["ticket_id"], world["member"].id
    ):
        response = call(client, method, url, kwargs)
        assert response.status_code < 400, f"{method} {url} -> {response.status_code}"


def test_member_is_blocked_only_on_owner_actions(client, world, login_as):
    login_as("bob@example.com")
    for kind, method, url, kwargs in endpoints(
        world["project"], world["ticket_id"], world["owner"].id
    ):
        response = call(client, method, url, kwargs)
        if kind == OWNER_ONLY:
            assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        else:
            assert response.status_code < 400, f"{method} {url} -> {response.status_code}"


def test_outsider_writes_are_403(client, world, login_as):
    login_as("dan@example.com")
    for _, method, url, kwargs in endpoints(
        world["project"], world["ticket_id"], world["member"].id
    ):
        response = call(client, method, url, kwargs)
        assert response.status_code in (403, 404), f"{method} {url} -> {response.status_code}"


def test_outsider_reads_are_404(client, world, login_as):
    login_as("dan@example.com")
    slug = world["project"].slug
    for url in [
        f"/api/v1/projects/{slug}",
        f"/api/v1/projects/{slug}/members",
        f"/api/v1/projects/{slug}/tickets",
        f"/api/v1/tickets/{world['ticket_id']}",
        f"/projects/{slug}",
    ]:
        assert client.get(url).status_code == 404, url


def test_anonymous_api_calls_are_401(client, world):
    slug = world["project"].slug
    assert client.get(f"/api/v1/projects/{slug}").status_code == 401
    assert client.post("/api/v1/tickets", json={"slug": slug, "title": "T"}).status_code == 401
```

Owner-only endpoints are ordered so the destructive ones run last: deleting the project first would make every later assertion a `404` for the wrong reason.

- [ ] **Step 2: Run the test and fix any failing cell**

Run: `uv run pytest tests/test_authorization_matrix.py -v`
Expected: PASS. A `404` where the matrix expects `403` means the handler used `project_reader` instead of `project_writer`; a `200` where it expects `403` means the role check is missing.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: assert the full authorization matrix across every mutating route"
```

---

## Task 26: Board query benchmark (V-2)

**Files:**
- Test: `tests/test_benchmark.py`
- Modify: `cs482_slice1_design.md` (record the measured figure)

**Interfaces:**
- Consumes the `engine` fixture and `app.models`. Produces the measured p95 that replaces the unverified read-latency claim in `cs482_workflow.md` §2.

- [ ] **Step 1: Write the benchmark**

`tests/test_benchmark.py`:

```python
import time

import pytest
from sqlmodel import Session, select

from app.models import Project, Ticket, TicketStatus, TicketType, User

TICKET_COUNT = 5000
QUERY_RUNS = 50


@pytest.mark.benchmark
def test_board_query_p95_under_20ms(engine, capsys):
    with Session(engine) as session:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        project = Project(name="Payment Gateway", slug="payment-gateway")
        session.add(user)
        session.add(project)
        session.commit()
        session.refresh(user)
        session.refresh(project)
        statuses = list(TicketStatus)
        session.add_all(
            [
                Ticket(
                    ticket_number=n,
                    project_id=project.id,
                    title=f"Ticket {n}",
                    description="x" * 200,
                    type=TicketType.TASK,
                    status=statuses[n % len(statuses)],
                    creator_id=user.id,
                )
                for n in range(1, TICKET_COUNT + 1)
            ]
        )
        session.commit()
        project_id = project.id

    durations = []
    with Session(engine) as session:
        for _ in range(QUERY_RUNS):
            started = time.perf_counter()
            rows = session.exec(
                select(Ticket)
                .where(Ticket.project_id == project_id)
                .order_by(Ticket.ticket_number.desc())
                .limit(50)
            ).all()
            durations.append(time.perf_counter() - started)
            assert len(rows) == 50

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    with capsys.disabled():
        print(f"\nV-2 board query p95 over {TICKET_COUNT} tickets: {p95 * 1000:.2f} ms")
    assert p95 < 0.020, f"p95 was {p95 * 1000:.2f} ms, budget is 20 ms"
```

- [ ] **Step 2: Register the marker in `pyproject.toml`**

```toml
[tool.pytest.ini_options]
markers = ["benchmark: timing-sensitive; excluded from the default run"]
addopts = "-m 'not benchmark'"
```

Keep the existing `testpaths` and `filterwarnings` entries in the same section.

- [ ] **Step 3: Run the benchmark and record the number**

Run: `uv run pytest tests/test_benchmark.py -m benchmark -v -s`
Expected: PASS, with the measured p95 printed.

- [ ] **Step 4: Write the measured figure into the design document**

In `cs482_slice1_design.md` §10, replace the V-2 row's "Record the measured p95; target under 20 ms" with the actual number and the machine it was measured on. A measured figure that misses the 20 ms budget is still recorded — then raise the budget with a note, or add an index, rather than deleting the row.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: benchmark the board query over 5,000 tickets and record V-2"
```

---

## Task 27: Local run — Docker, environment, and README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`
- Test: manual verification recorded in the README

**Interfaces:**
- Produces the command the grader runs: `docker compose up`. The SQLite file lives on a bind-mounted host volume so a container rebuild cannot destroy it.

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 UV_SYSTEM_PYTHON=1
WORKDIR /srv

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
```

`--workers 1` is not a default worth overriding: SQLite permits a single writer, and a second worker produces `database is locked` under concurrent writes.

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
services:
  web:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      DATABASE_URL: sqlite:////data/kanbanflow.db
    volumes:
      - ./data:/data
```

- [ ] **Step 3: Write `.env.example`**

```
# Copy to .env before running. Never commit .env.
SESSION_SECRET=change-me-to-a-long-random-string
SECURE_COOKIES=false
DATABASE_URL=sqlite:///./kanbanflow.db
```

- [ ] **Step 4: Write `README.md`**

````markdown
# Kanban Flow — Slice 1

Engineering workflow tracker. This slice covers accounts, projects, membership,
tickets, the Kanban board, and chat notifications. Sprints, velocity, AI reports,
the GitHub webhook, and the MCP server arrive in slice 2.

## Run locally

```bash
cp .env.example .env          # then set SESSION_SECRET
docker compose up --build
```

Open <http://localhost:8000>. Register an account, create a project, and add a ticket.

## Develop

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --workers 1
uv run pytest                      # unit and integration tests
uv run pytest -m benchmark -s      # timing checks (V-1 latency, V-2 board query)
uv run ruff check . && uv run ruff format --check .
```

## Chat notifications

In the project settings, set `webhook_type` to `SLACK`, `DISCORD`, or `TEAMS`
and paste the channel's incoming-webhook URL (https only). Cards are sent when a
ticket is created and when one moves to `DONE`. Delivery runs in the background:
a failed webhook is logged as a `WARNING` and never reverses the ticket change.

## Design documents

- `cs482_slice1_design.md` — the authoritative spec for this slice
- `cs482_slice1_plan.md` — the implementation plan
- `cs482_workflow.md`, `cs482_tool_review.md` — original product research and specification
````

- [ ] **Step 5: Verify the container runs end to end**

```bash
cp .env.example .env
docker compose up --build -d
curl -fsS http://localhost:8000/health
docker compose down
```

Expected: `{"status":"ok"}`, and `./data/kanbanflow.db` exists on the host afterward.

- [ ] **Step 6: Run the full suite one final time**

Run: `uv run pytest -v && uv run pytest -m benchmark -v -s`
Expected: every test passes.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: add Docker, compose, environment template, and README"
```

---

## Coverage Check

Every requirement the slice 1 design assigns to this plan, and the task that satisfies it.

| Requirement | Task |
|---|---|
| FR-01 registration, login, session cookie | 5, 6, 19 |
| FR-02 dashboard and board rendering | 20, 21 |
| FR-03 bounded JSON `meta`, distinct `type` | 3, 11 |
| FR-06 background chat dispatch that cannot block or reverse | 15, 16, 17 |
| FR-10 project-scoped authorization | 9, 25 |
| FR-11 Markdown sanitized at render time | 18, 21 |
| FR-12 gapless per-project `ticket_number` under concurrency | 11, 12 |
| V-1 fifty concurrent creations | 12 |
| V-2 board query latency, measured not assumed | 26 |
| V-4 webhook failure cannot damage the product | 17 |
| V-8 authorization matrix | 25 |
| V-9 rendered Markdown is inert | 18, 21 |
| D-02 HTMX requests, Alpine for UI state, split web/API routes | 21, 22, 23 |
| D-03 two notification events, no suppression | 17 |
| D-04 slug-keyed routes, immutable slug | 8, 9 |
| D-09 runs locally under `docker compose` | 27 |
| D-10 uv, ruff, pytest, alembic, pydantic-settings | 1, 4 |
| D-11 session-bound CSRF on HTML posts | 7, 20, 22, 23 |
| D-11 `Origin` check on cookie-authenticated JSON writes | 24 |
| D-12 no sprint columns in this slice | 3 |

FR-04, FR-05, FR-07, FR-08, and FR-09 belong to slice 2 and have no task here by design.

