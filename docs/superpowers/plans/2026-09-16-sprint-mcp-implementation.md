# Sprint MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let authenticated owners list, create, start, and close sprints from MCP using the same API and authorization rules as the web UI.

**Architecture:** Add revocable hashed personal access tokens to the backend, then implement a small stdio MCP server as an HTTPS client. The MCP server owns no database logic and sends every operation through the FastAPI API.

**Tech Stack:** Python 3.11, FastAPI, SQLModel/Alembic, httpx, MCP Python SDK v2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-notion-sprint-workflow-design.md`

**Prerequisite:** Complete `docs/superpowers/plans/2026-09-16-sprint-core-implementation.md` first. The web plan is not required.

## Global Constraints

- Use the official `mcp[cli]>=2,<3` Python SDK and `mcp.server.MCPServer`.
- Use stdio for the local MCP server; do not host a second public MCP HTTP service.
- The MCP process reads `KANBANFLOW_BASE_URL` and `KANBANFLOW_API_TOKEN` from environment variables.
- Store only SHA-256 token hashes and a display prefix; return plaintext exactly once.
- MCP uses the same FastAPI authorization as web/API callers; only `OWNER` can mutate sprints.
- Do not add natural-language parsing, retries for state-changing calls, or direct SQLite access.

---

### Task 1: Add revocable API tokens

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `app/models.py`
- Create: `alembic/versions/8a71d03f4c22_add_api_tokens.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `ApiToken` and the MCP SDK dependency.
- Consumed by: token routes and bearer authentication.

- [ ] **Step 1: Add and lock the official SDK**

Run: `uv add 'mcp[cli]>=2,<3'`
Expected: `pyproject.toml` and `uv.lock` change without unrelated upgrades.

- [ ] **Step 2: Write a failing token model test**

```python
def test_api_token_stores_hash_and_prefix(session, make_user):
    user = make_user()
    token = ApiToken(
        user_id=user.id,
        label="Cursor",
        prefix="kf_ab12",
        token_hash="0" * 64,
    )
    session.add(token)
    session.commit()
    assert token.revoked_at is None
```

- [ ] **Step 3: Run and verify failure**

Run: `uv run pytest tests/test_models.py::test_api_token_stores_hash_and_prefix -v`
Expected: FAIL because `ApiToken` does not exist.

- [ ] **Step 4: Add the token model**

```python
class ApiToken(SQLModel, table=True):
    __tablename__ = "api_token"
    id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    label: str = Field(max_length=100)
    prefix: str = Field(max_length=12)
    token_hash: str = Field(max_length=64, unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
```

- [ ] **Step 5: Add and verify the migration**

Create `api_token` with `down_revision = "6c25b12d1a91"`, indexes for `user_id` and unique `token_hash`, and a downgrade that drops indexes before the table.

Run: `uv run pytest tests/test_models.py tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock app/models.py alembic/versions/8a71d03f4c22_add_api_tokens.py tests/test_models.py tests/test_migrations.py
git commit -m "feat: add revocable API tokens"
```

### Task 2: Issue, list, revoke, and authenticate tokens

**Files:**
- Modify: `app/schemas.py`
- Modify: `app/auth.py`
- Create: `app/routers/api_tokens.py`
- Modify: `app/routers/__init__.py`
- Modify: `app/main.py`
- Create: `tests/test_api_tokens.py`

**Interfaces:**
- Produces: `issue_api_token`, bearer-token resolution, and `/api/v1/tokens` routes.
- Consumed by: all existing API dependencies and the MCP server.

- [ ] **Step 1: Write failing token API tests**

Cover issuance, plaintext shown once, database hash mismatch with plaintext, list without plaintext, bearer authentication, revocation, revoked-token `401`, unknown-token `401`, and ownership of revocation.

```python
def test_issued_token_authenticates(client, make_user, login_as):
    user = make_user(email="ada@example.com")
    login_as(user.email)
    issued = client.post("/api/v1/tokens", json={"label": "Cursor"}).json()
    client.post("/api/v1/auth/logout")
    response = client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {issued['token']}"},
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run and verify 404 failure**

Run: `uv run pytest tests/test_api_tokens.py -v`
Expected: FAIL because token routes do not exist.

- [ ] **Step 3: Add schemas and token primitives**

Add `TokenCreate(label)`, `TokenOut`, and `TokenIssued(TokenOut)` with a `token` field. Generate `kf_` plus `secrets.token_urlsafe(32)`, store `hashlib.sha256(plaintext.encode()).hexdigest()`, and retain the first 10 characters as prefix.

- [ ] **Step 4: Accept bearer tokens in the shared user dependency**

When `Authorization` starts with `Bearer `, hash the supplied token, find a non-revoked `ApiToken`, and load its user. If invalid, return `401` without falling back to a cookie. Update `last_used_at` only when it is absent or older than one hour to avoid a database write on every MCP call. Cookie behavior remains unchanged when no bearer header exists.

- [ ] **Step 5: Add token routes**

Implement `GET /api/v1/tokens`, `POST /api/v1/tokens`, and idempotent `DELETE /api/v1/tokens/{id}`. Listing and deletion operate only on the current user's tokens.

- [ ] **Step 6: Run token and auth regressions**

Run: `uv run pytest tests/test_api_tokens.py tests/test_auth_api.py tests/test_web_auth.py tests/test_origin_guard.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/schemas.py app/auth.py app/routers/api_tokens.py app/routers/__init__.py app/main.py tests/test_api_tokens.py
git commit -m "feat: authenticate API tokens"
```

### Task 3: Build the minimal MCP HTTP client

**Files:**
- Create: `app/mcp_server.py`
- Create: `tests/test_mcp_server.py`

**Interfaces:**
- Produces: `mcp`, `MCPSettings`, and `api_request(method, path, json=None, *, transport=None)`.
- Consumed by: sprint MCP tools.

- [ ] **Step 1: Write failing client tests**

Use `httpx.MockTransport` to assert base URL joining, bearer header, JSON decoding, timeout, and concise API error propagation without exposing the token.

```python
@pytest.mark.anyio
async def test_api_request_sends_bearer_token(monkeypatch):
    seen = {}
    async def handler(request):
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"items": []})
    result = await api_request("GET", "/api/v1/projects/demo/sprints", transport=httpx.MockTransport(handler))
    assert seen["authorization"] == "Bearer test-token"
    assert result == {"items": []}
```

- [ ] **Step 2: Run and verify import failure**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL because `app.mcp_server` does not exist.

- [ ] **Step 3: Add settings and request helper**

Define `MCPSettings` with `env_prefix="KANBANFLOW_"`, required `api_token`, `base_url="http://localhost:8000"`, and a 10-second timeout. `api_request` uses `httpx.AsyncClient`, sends the bearer token, and raises `ValueError(f"Kanban Flow API {status}: {detail}")` on non-2xx responses. Never include request headers or token text in exceptions.

- [ ] **Step 4: Create the MCP server object**

```python
from mcp.server import MCPServer

mcp = MCPServer("Kanban Flow")
```

Keep the file importable without making a network call; settings are resolved when a tool runs.

- [ ] **Step 5: Run client tests**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add MCP API client"
```

### Task 4: Expose sprint MCP tools

**Files:**
- Modify: `app/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

**Interfaces:**
- Produces: `list_sprints`, `create_sprint`, `start_sprint`, and `close_sprint` MCP tools.
- Consumes: sprint JSON APIs from the core plan.

- [ ] **Step 1: Write failing in-memory MCP tests**

Use the SDK's `Client(mcp)` transport and monkeypatch `api_request`. Assert tool input schemas, exact API method/path/body, lean list output, and propagation of `403`, `409`, and `422` messages.

```python
@pytest.mark.anyio
async def test_create_sprint_tool_calls_api(monkeypatch):
    calls = []
    async def fake_request(method, path, json=None):
        calls.append((method, path, json))
        return {"id": "sprint-1", "status": "PLANNING"}
    monkeypatch.setattr(mcp_server, "api_request", fake_request)
    async with Client(mcp_server.mcp) as client:
        await client.call_tool("create_sprint", {
            "slug": "demo",
            "name": "Sprint 2",
            "goal": "Ship reports",
            "start_date": "2026-09-28",
            "end_date": "2026-10-05",
        })
    assert calls == [("POST", "/api/v1/projects/demo/sprints", {
        "name": "Sprint 2",
        "goal": "Ship reports",
        "start_date": "2026-09-28",
        "end_date": "2026-10-05",
    })]
```

- [ ] **Step 2: Run and verify missing-tool failure**

Run: `uv run pytest tests/test_mcp_server.py -k tool -v`
Expected: FAIL because tools are not registered.

- [ ] **Step 3: Add four typed tools**

Decorate four async functions with `@mcp.tool()`. Tool signatures use project slug, sprint ID, ISO date strings, and required `next_sprint_id`. Each docstring states owner-only behavior. `list_sprints` returns only ID, name, status, dates, goal, and point totals to limit context use.

- [ ] **Step 4: Run MCP tests**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: expose sprint MCP tools"
```

### Task 5: Document and verify MCP setup

**Files:**
- Modify: `README.md`
- Create: `.env.mcp.example`

**Interfaces:**
- Produces: copyable local MCP configuration and final acceptance evidence.

- [ ] **Step 1: Add safe example configuration**

Create an example containing placeholders only:

```dotenv
KANBANFLOW_BASE_URL=https://kanban.example.com
KANBANFLOW_API_TOKEN=replace-with-token-shown-once
```

Document issuing a token, configuring the two variables, and running `uv run mcp run app/mcp_server.py`. State that plaintext tokens must never be committed.

- [ ] **Step 2: Run MCP and backend tests**

Run: `uv run pytest tests/test_mcp_server.py tests/test_api_tokens.py tests/test_sprint_api.py -v`
Expected: PASS.
Run: `uv run pytest`
Expected: all non-benchmark tests PASS.

- [ ] **Step 3: Run security and quality checks**

Run: `git grep -n -E 'kf_[A-Za-z0-9_-]{32,}' -- .`
Expected: no output.
Run: `uv run ruff check . && uv run ruff format --check . && git diff --check`
Expected: all commands exit 0.

- [ ] **Step 4: Commit**

```bash
git add README.md .env.mcp.example
git commit -m "docs: explain sprint MCP setup"
```
