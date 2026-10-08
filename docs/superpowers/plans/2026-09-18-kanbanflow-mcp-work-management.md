# Kanban Flow MCP Work Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing project, ticket, sprint, and membership workflows
as explicit Kanban Flow MCP tools.

**Architecture:** Add thin tools to `app/mcp_server.py` that delegate to the
existing API through `_tool_request`. Use `_path_segment` for path values and a
small standard-library query helper for optional filters. Keep validation,
authorization, and lifecycle rules in the API.

**Tech Stack:** Python 3.11, MCP Python SDK, httpx, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-09-18-kanbanflow-mcp-work-management-design.md`

## Global Constraints

- Do not add API endpoints or dependencies.
- Do not expose a generic arbitrary-path HTTP tool.
- Preserve API authorization, validation, and concise error mapping.
- Do not add sprint deletion, comments, GitHub operations, or token management.
- Use TDD: run every named test red before adding its production code.
- Keep implementation in `app/mcp_server.py` and tests in `tests/test_mcp_server.py`.

---

### Task 1: Project Tools and Query Encoding

**Files:**

- Modify: `app/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Produces: `_with_query(path: str, **params: object) -> str`
- Produces: `list_projects()`, `get_project(slug)`,
  `update_project(slug, name)`, `delete_project(slug, confirm_slug)`
- Consumes: existing `_path_segment` and `_tool_request`

- [ ] **Step 1: Write the failing project-tool test**

Add `test_project_tools_delegate_to_the_api` using the existing fake-request
pattern. Call all four new tools through `Client(mcp)` and assert:

```python
assert calls == [
    ("GET", "/api/v1/projects", None),
    ("GET", "/api/v1/projects/mcp-check", None),
    ("PATCH", "/api/v1/projects/mcp-check", {"name": "Renamed"}),
    ("DELETE", "/api/v1/projects/mcp-check?confirm=mcp-check", None),
]
```

Also extend the tool-set assertion with the four names and assert required
fields for `get_project`, `update_project`, and `delete_project`.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest tests/test_mcp_server.py::test_project_tools_delegate_to_the_api -q
```

Expected: FAIL because `list_projects` and the other new tools are not
registered.

- [ ] **Step 3: Implement the query helper and project tools**

Import `urlencode` beside the existing URL helpers and add:

```python
def _with_query(path: str, **params: object) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return f"{path}?{query}" if query else path
```

Add explicit MCP tools that delegate as follows:

```python
@mcp.tool()
async def list_projects() -> list[dict[str, object]]:
    """List projects available to the token's user."""
    return await _tool_request("GET", "/api/v1/projects")


@mcp.tool()
async def get_project(slug: str) -> dict[str, object]:
    """Get a project available to the token's user."""
    return await _tool_request("GET", f"/api/v1/projects/{_path_segment(slug)}")


@mcp.tool()
async def update_project(slug: str, name: str) -> dict[str, object]:
    """Rename a project. Owner-only."""
    return await _tool_request(
        "PATCH", f"/api/v1/projects/{_path_segment(slug)}", {"name": name}
    )


@mcp.tool()
async def delete_project(slug: str, confirm_slug: str) -> None:
    """Delete a project and its contents. Owner-only; confirmation must match slug."""
    return await _tool_request(
        "DELETE",
        _with_query(f"/api/v1/projects/{_path_segment(slug)}", confirm=confirm_slug),
    )
```

- [ ] **Step 4: Run project and full MCP tests**

Run:

```bash
uv run pytest tests/test_mcp_server.py::test_project_tools_delegate_to_the_api -q
uv run pytest tests/test_mcp_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the project tools**

```bash
git add app/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add MCP project management tools"
```

---

### Task 2: Ticket Tools

**Files:**

- Modify: `app/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Produces: `create_ticket`, `list_tickets`, `get_ticket`, `update_ticket`,
  `update_ticket_status`, and `delete_ticket`
- Consumes: `_with_query`, `_path_segment`, `_tool_request`, and model enums
  `Priority`, `TicketStatus`, `TicketType`

- [ ] **Step 1: Write the failing ticket-tool test**

Add one test, `test_ticket_tools_delegate_to_the_api`, that monkeypatches
`api_request`, invokes all six tools through `Client(mcp)`, and verifies these
representative calls:

```python
assert calls == [
    (
        "POST",
        "/api/v1/tickets",
        {
            "slug": "mcp-check",
            "title": "MCP ticket",
            "description": "",
            "type": "TASK",
            "priority": "MEDIUM",
            "story_points": None,
            "sprint_id": None,
            "assignee_id": None,
        },
    ),
    (
        "GET",
        "/api/v1/projects/mcp-check/tickets?status=TODO&limit=10",
        None,
    ),
    ("GET", "/api/v1/tickets/ticket-1", None),
    ("PATCH", "/api/v1/tickets/ticket-1", {"title": "Renamed"}),
    (
        "PATCH",
        "/api/v1/tickets/ticket-1/status",
        {"status": "IN_PROGRESS", "resolution_notes": None},
    ),
    ("DELETE", "/api/v1/tickets/ticket-1", None),
]
```

Use `changes={"title": "Renamed"}` for the update call. Extend the tool-set
schema test with all ticket tool names and their required identifiers.

- [ ] **Step 2: Run the ticket test and verify RED**

```bash
uv run pytest tests/test_mcp_server.py::test_ticket_tools_delegate_to_the_api -q
```

Expected: FAIL because `create_ticket` is not registered.

- [ ] **Step 3: Implement the ticket tools**

Import `Priority`, `TicketStatus`, and `TicketType` from `app.models`. Implement
thin tools with these signatures:

```python
async def create_ticket(
    slug: str,
    title: str,
    description: str = "",
    type: TicketType = TicketType.TASK,
    priority: Priority = Priority.MEDIUM,
    story_points: int | None = None,
    sprint_id: str | None = None,
    assignee_id: str | None = None,
) -> dict[str, object]

async def list_tickets(
    slug: str,
    status: TicketStatus | None = None,
    type: TicketType | None = None,
    priority: Priority | None = None,
    sprint_id: str | None = None,
    assignee_id: str | None = None,
    cursor: int | None = None,
    limit: int | None = None,
) -> dict[str, object]

async def get_ticket(ticket_id: str) -> dict[str, object]
async def update_ticket(ticket_id: str, changes: dict[str, object]) -> dict[str, object]
async def update_ticket_status(
    ticket_id: str,
    status: TicketStatus,
    resolution_notes: str | None = None,
) -> dict[str, object]
async def delete_ticket(ticket_id: str) -> None
```

Use `/api/v1/tickets`, `/api/v1/projects/{slug}/tickets`,
`/api/v1/tickets/{ticket_id}`, and `/api/v1/tickets/{ticket_id}/status` exactly.
Pass enum `.value` strings in query and JSON payloads. Forward `changes`
unchanged so explicit `null` remains distinguishable from an omitted key.

- [ ] **Step 4: Run ticket and full MCP tests**

```bash
uv run pytest tests/test_mcp_server.py::test_ticket_tools_delegate_to_the_api -q
uv run pytest tests/test_mcp_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the ticket tools**

```bash
git add app/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add MCP ticket management tools"
```

---

### Task 3: Remaining Sprint Tools

**Files:**

- Modify: `app/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Produces: `get_sprint`, `update_sprint`, `get_sprint_history`
- Consumes: `_path_segment`, `_tool_request`, and `date`

- [ ] **Step 1: Write the failing sprint-tool test**

Add `test_remaining_sprint_tools_delegate_to_the_api`. Through `Client(mcp)`,
get a sprint, update its name and dates, and fetch its history. Assert:

```python
assert calls == [
    ("GET", "/api/v1/sprints/sprint-1", None),
    (
        "PATCH",
        "/api/v1/sprints/sprint-1",
        {
            "name": "Sprint 2",
            "start_date": "2026-10-06",
            "end_date": "2026-10-13",
        },
    ),
    ("GET", "/api/v1/sprints/sprint-1/history", None),
]
```

Extend the schema test with these three tools and their required sprint ID.

- [ ] **Step 2: Run the sprint test and verify RED**

```bash
uv run pytest tests/test_mcp_server.py::test_remaining_sprint_tools_delegate_to_the_api -q
```

Expected: FAIL because `get_sprint` is not registered.

- [ ] **Step 3: Implement the sprint tools**

Implement:

```python
async def get_sprint(sprint_id: str) -> dict[str, object]

async def update_sprint(
    sprint_id: str,
    name: str | None = None,
    goal: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, object]

async def get_sprint_history(sprint_id: str) -> list[dict[str, object]]
```

Build the update body from non-`None` values and serialize supplied dates with
`.isoformat()`. Delegate to `GET /api/v1/sprints/{id}`,
`PATCH /api/v1/sprints/{id}`, and `GET /api/v1/sprints/{id}/history`.

- [ ] **Step 4: Run sprint and full MCP tests**

```bash
uv run pytest tests/test_mcp_server.py::test_remaining_sprint_tools_delegate_to_the_api -q
uv run pytest tests/test_mcp_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the sprint tools**

```bash
git add app/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: complete MCP sprint management tools"
```

---

### Task 4: Project Membership Tools and Documentation

**Files:**

- Modify: `app/mcp_server.py`
- Modify: `README.md`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Produces: `list_project_members`, `add_project_member`,
  `update_project_member`, `remove_project_member`
- Consumes: `_path_segment`, `_tool_request`, and model enum `Role`

- [ ] **Step 1: Write the failing membership-tool test**

Add `test_membership_tools_delegate_to_the_api`. Invoke all four tools and
assert:

```python
assert calls == [
    ("GET", "/api/v1/projects/mcp-check/members", None),
    (
        "POST",
        "/api/v1/projects/mcp-check/members",
        {"email": "member@example.com", "role": "MEMBER"},
    ),
    (
        "PATCH",
        "/api/v1/projects/mcp-check/members/user-1",
        {"role": "OWNER"},
    ),
    ("DELETE", "/api/v1/projects/mcp-check/members/user-1", None),
]
```

Extend the schema test with all four membership tool names and required fields.

- [ ] **Step 2: Run the membership test and verify RED**

```bash
uv run pytest tests/test_mcp_server.py::test_membership_tools_delegate_to_the_api -q
```

Expected: FAIL because `list_project_members` is not registered.

- [ ] **Step 3: Implement the membership tools**

Import `Role` from `app.models` and implement:

```python
async def list_project_members(slug: str) -> list[dict[str, object]]
async def add_project_member(
    slug: str, email: str, role: Role = Role.MEMBER
) -> dict[str, object]
async def update_project_member(
    slug: str, user_id: str, role: Role
) -> dict[str, object]
async def remove_project_member(slug: str, user_id: str) -> None
```

Use `/api/v1/projects/{slug}/members` and
`/api/v1/projects/{slug}/members/{user_id}`. Encode both path values and send
role `.value` strings.

- [ ] **Step 4: Update MCP documentation**

Replace the README statement that tools only manage sprints with a compact list
covering project CRUD, ticket CRUD/status, sprint lifecycle/history, and member
management. Explicitly state that destructive tools retain API owner checks and
that sprint deletion is unavailable.

- [ ] **Step 5: Run complete verification**

```bash
uv run pytest tests/test_mcp_server.py -q
uv run ruff check app/mcp_server.py tests/test_mcp_server.py
uv run ruff format --check app/mcp_server.py tests/test_mcp_server.py
git diff --check
graphify update .
```

Expected: all tests and checks pass; graphify reports an updated code graph.

- [ ] **Step 6: Commit the membership tools and docs**

```bash
git add app/mcp_server.py tests/test_mcp_server.py README.md graphify-out
git commit -m "feat: complete MCP work management surface"
```

---

## Manual Smoke Test After MCP Reconnect

- [ ] Reconnect the MCP server so the client refreshes its tool list.
- [ ] List and get the existing `mcp-2026-09-18` project.
- [ ] Create a ticket, read it, rename it, change its status, and delete it.
- [ ] Create a future planning sprint, rename it, read it, and list sprints.
- [ ] Read the project's member list. Do not mutate membership without a second
  test account.
- [ ] Do not delete the project or start/close a sprint during the smoke test.
