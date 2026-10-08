# Sprint Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dated sprint planning, activation, transactional close/rollover, immutable history, and shared JSON APIs.

**Architecture:** Extend the existing SQLModel domain and Alembic schema, keep lifecycle rules in `app/services.py`, and expose thin FastAPI routes. Web and MCP plans consume these APIs and services later.

**Tech Stack:** Python 3.11, FastAPI, SQLModel/SQLAlchemy, SQLite, Alembic, Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-notion-sprint-workflow-design.md`

## Global Constraints

- A project has at most one `ACTIVE` and one `PLANNING` sprint.
- Sprint dates are entered per sprint; `end_date` must be after `start_date`.
- Only `OWNER` may create, start, or close a sprint.
- Closing requires `next_sprint_id`; the destination must be the same project's `PLANNING` sprint.
- Close snapshots, totals, rollover, and state transition commit atomically.
- The destination remains `PLANNING` after rollover.
- AI report generation remains a separate follow-up subsystem; when added, it must start only after this plan's close transaction commits.
- Do not add a scheduler, reopen operation, full ticket versioning, or multiple planning sprints.

---

### Task 1: Persist sprint state and history

**Files:**
- Modify: `app/models.py`
- Create: `alembic/versions/6c25b12d1a91_add_sprint_workflow.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `SprintStatus`, `Sprint`, `SprintTicketHistory`, and sprint fields on `Ticket`.
- Consumed by: every later task in this plan.

- [ ] **Step 1: Write failing model tests**

Add tests proving the model accepts one planning sprint and records close-time ticket state:

```python
def test_sprint_and_history_models(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    sprint = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add(sprint)
    session.commit()
    assert sprint.status == SprintStatus.PLANNING
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `uv run pytest tests/test_models.py::test_sprint_and_history_models -v`
Expected: FAIL because `Sprint` and `SprintStatus` do not exist.

- [ ] **Step 3: Add the models**

Implement:

```python
class SprintStatus(StrEnum):
    PLANNING = "PLANNING"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class Sprint(SQLModel, table=True):
    __tablename__ = "sprint"
    __table_args__ = (
        Index("uq_sprint_active_project", "project_id", unique=True,
              sqlite_where=text("status = 'ACTIVE'")),
        Index("uq_sprint_planning_project", "project_id", unique=True,
              sqlite_where=text("status = 'PLANNING'")),
    )
    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    name: str = Field(max_length=100)
    goal: str = Field(max_length=2000)
    status: SprintStatus = Field(default=SprintStatus.PLANNING, index=True)
    start_date: date
    end_date: date
    committed_points: int | None = None
    completed_points: int | None = None
    closed_at: datetime | None = None


class SprintTicketHistory(SQLModel, table=True):
    __tablename__ = "sprint_ticket_history"
    __table_args__ = (UniqueConstraint("sprint_id", "ticket_id"),)
    id: str = Field(default_factory=new_id, primary_key=True)
    sprint_id: str = Field(foreign_key="sprint.id", index=True)
    ticket_id: str = Field(foreign_key="ticket.id", index=True)
    status_at_close: TicketStatus
    story_points_at_close: int | None = None
    was_completed: bool
    recorded_at: datetime = Field(default_factory=utcnow)
```

Extend `Ticket` with nullable `sprint_id`, `first_sprint_entered_at`, and `delayed_days`, plus `rollover_count = 0`.

- [ ] **Step 4: Add the Alembic migration**

Create the sprint and history tables, both partial unique indexes, the four ticket columns, and their foreign keys/indexes. Set `down_revision = "89398913f6cb"`; downgrade reverses those changes in dependency order.

- [ ] **Step 5: Verify models and migration agree**

Run: `uv run pytest tests/test_models.py tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/models.py alembic/versions/6c25b12d1a91_add_sprint_workflow.py tests/test_models.py tests/test_migrations.py
git commit -m "feat: add sprint persistence"
```

### Task 2: Define sprint request and response schemas

**Files:**
- Modify: `app/schemas.py`
- Create: `tests/test_sprint_schemas.py`

**Interfaces:**
- Produces: `SprintCreate`, `SprintUpdate`, `SprintClose`, `SprintOut`, `SprintHistoryOut`.
- Consumed by: `app/routers/api_sprints.py`.

- [ ] **Step 1: Write failing schema tests**

```python
def test_sprint_create_rejects_reversed_dates():
    with pytest.raises(ValidationError):
        SprintCreate(
            name="Sprint 1",
            goal="Ship checkout",
            start_date=date(2026, 9, 28),
            end_date=date(2026, 9, 21),
        )
```

Also assert names and goals are stripped and blank values fail.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_sprint_schemas.py -v`
Expected: FAIL because the schemas do not exist.

- [ ] **Step 3: Add minimal schemas**

```python
class SprintCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=2000)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        self.name = self.name.strip()
        self.goal = self.goal.strip()
        if not self.name or not self.goal:
            raise ValueError("name and goal must not be blank")
        return self


class SprintUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    goal: str | None = Field(default=None, min_length=1, max_length=2000)
    start_date: date | None = None
    end_date: date | None = None
    status: Literal[SprintStatus.ACTIVE] | None = None


class SprintClose(BaseModel):
    next_sprint_id: str
```

`SprintOut` mirrors every public `Sprint` field. `SprintHistoryOut` contains `ticket_id`, `ticket_number`, current `title`, `status_at_close`, `story_points_at_close`, and `was_completed`.

- [ ] **Step 4: Run schema tests**

Run: `uv run pytest tests/test_sprint_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas.py tests/test_sprint_schemas.py
git commit -m "feat: define sprint API schemas"
```

### Task 3: Implement sprint creation and activation services

**Files:**
- Modify: `app/services.py`
- Create: `tests/test_sprint_services.py`

**Interfaces:**
- Produces: `create_sprint(session, project, *, name, goal, start_date, end_date) -> Sprint`, `update_sprint(session, sprint, **changes) -> Sprint`, and `start_sprint(session, sprint) -> Sprint`.
- Consumed by: sprint API and web routes.

- [ ] **Step 1: Write failing service tests**

Cover creation, second-planning rejection, date validation, activation, committed-point freezing, and second-active rejection.

```python
def test_start_sprint_freezes_committed_points(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    planning_sprint = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add(planning_sprint)
    session.flush()
    session.add(Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=planning_sprint.id,
        title="Complete checkout",
        story_points=5,
        creator_id=owner.id,
    ))
    session.commit()
    started = start_sprint(session, planning_sprint)
    assert started.status == SprintStatus.ACTIVE
    assert started.committed_points == 5
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_sprint_services.py -v`
Expected: FAIL because the service functions do not exist.

- [ ] **Step 3: Implement creation**

`create_sprint` checks for an existing planning sprint, constructs `Sprint`, commits, and translates the partial-index `IntegrityError` into `409 "A planning sprint already exists"`. `update_sprint` permits name, goal, and date changes only while `PLANNING`, validates the merged start/end pair, and commits once.

- [ ] **Step 4: Implement activation**

`start_sprint` requires `PLANNING`, rejects another active sprint, sums non-null story points for assigned tickets, sets `ACTIVE`, commits, and translates index races into `409 "An active sprint already exists"`.

- [ ] **Step 5: Run service tests**

Run: `uv run pytest tests/test_sprint_services.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services.py tests/test_sprint_services.py
git commit -m "feat: create and start sprints"
```

### Task 4: Implement atomic close and rollover

**Files:**
- Modify: `app/services.py`
- Modify: `tests/test_sprint_services.py`

**Interfaces:**
- Produces: `close_sprint(session, sprint, next_sprint) -> Sprint`.
- Consumed by: API, web, and MCP close operations.

- [ ] **Step 1: Write failing close tests**

Test successful snapshots, completed-point freezing, status preservation on rollover, planning destination preservation, `rollover_count`, frozen `delayed_days`, wrong-project destination, non-planning destination, repeated close, and rollback after an injected flush failure.

```python
def test_close_rolls_unfinished_ticket_to_planning_sprint(session, active, planning, ticket):
    closed = close_sprint(session, active, planning)
    session.refresh(ticket)
    history = session.exec(select(SprintTicketHistory)).one()
    assert closed.status == SprintStatus.CLOSED
    assert planning.status == SprintStatus.PLANNING
    assert ticket.sprint_id == planning.id
    assert history.status_at_close == TicketStatus.IN_PROGRESS
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_sprint_services.py -k close -v`
Expected: FAIL because `close_sprint` does not exist.

- [ ] **Step 3: Implement close without intermediate commits**

Validate both sprints before mutation. Load all current sprint tickets, add one `SprintTicketHistory` per ticket, compute completed points, move non-`DONE` tickets, update aging values, mark the current sprint closed, then call `session.commit()` exactly once. Roll back and re-raise on any exception.

- [ ] **Step 4: Run close and service tests**

Run: `uv run pytest tests/test_sprint_services.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services.py tests/test_sprint_services.py
git commit -m "feat: close and roll over sprints atomically"
```

### Task 5: Expose sprint JSON APIs

**Files:**
- Create: `app/routers/api_sprints.py`
- Modify: `app/routers/__init__.py`
- Modify: `app/main.py`
- Create: `tests/test_sprint_api.py`

**Interfaces:**
- Produces: list, create, detail, update/start, close, and history endpoints from the design spec.
- Consumes: sprint schemas and services from Tasks 2–4.

- [ ] **Step 1: Write failing API tests**

Cover owner success, member `403`, outsider visibility rules, list ordering, detail, activation, required `next_sprint_id`, close output, and history output.

```python
def test_owner_can_create_sprint(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)
    response = client.post(
        f"/api/v1/projects/{project.slug}/sprints",
        json={"name": "Sprint 1", "goal": "Ship", "start_date": "2026-09-21", "end_date": "2026-09-28"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "PLANNING"
```

- [ ] **Step 2: Run and verify 404 failures**

Run: `uv run pytest tests/test_sprint_api.py -v`
Expected: FAIL because the router is not registered.

- [ ] **Step 3: Implement the thin router**

Use `project_reader` for reads and `project_owner` for project-scoped creation. For sprint-ID routes, load the sprint and project membership once, apply read/owner rules, then call the shared service. Do not duplicate lifecycle rules in route handlers.

- [ ] **Step 4: Register the router and run tests**

Run: `uv run pytest tests/test_sprint_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routers/api_sprints.py app/routers/__init__.py app/main.py tests/test_sprint_api.py
git commit -m "feat: expose sprint APIs"
```

### Task 6: Add sprint assignment to tickets

**Files:**
- Modify: `app/schemas.py`
- Modify: `app/services.py`
- Modify: `app/routers/api_tickets.py`
- Modify: `tests/test_ticket_api.py`
- Modify: `tests/test_ticket_create.py`

**Interfaces:**
- Produces: `sprint_id` on ticket create/update/output and `sprint_id` list filtering.
- Consumed by: sprint web and MCP plans.

- [ ] **Step 1: Write failing ticket assignment tests**

Assert create/update accepts a same-project planning or active sprint, rejects a foreign/closed sprint, stamps `first_sprint_entered_at` only once, allows explicit `null` to return a ticket to Backlog, filters `sprint_id=<id>` or `sprint_id=null`, and returns `409` when deleting a ticket recorded in sprint history.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_ticket_api.py tests/test_ticket_create.py -k sprint -v`
Expected: FAIL because sprint fields are not exposed.

- [ ] **Step 3: Add shared validation and field handling**

Implement `validate_sprint_assignment(session, project, sprint_id) -> Sprint | None` and `update_ticket(session, ticket, project, **changes) -> Ticket`. Move the current JSON PATCH mutation logic into `update_ticket`, then call it from the API route and later from the web detail form. Stamp `first_sprint_entered_at = utcnow()` only when its current value is `None` and a non-null sprint is assigned. Before hard deletion, reject any ticket referenced by `SprintTicketHistory` with `409 "Ticket belongs to closed sprint history"`.

- [ ] **Step 4: Add query filtering**

Accept the string query parameter `sprint_id`; treat the literal `null` as `Ticket.sprint_id.is_(None)` and any other value as an exact ID.

- [ ] **Step 5: Run ticket and sprint tests**

Run: `uv run pytest tests/test_ticket_api.py tests/test_ticket_create.py tests/test_sprint_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas.py app/services.py app/routers/api_tickets.py tests/test_ticket_api.py tests/test_ticket_create.py
git commit -m "feat: assign tickets to sprints"
```

### Task 7: Complete authorization and regression verification

**Files:**
- Modify: `tests/test_authorization_matrix.py`
- Modify: `app/routers/api_projects.py`
- Modify: `tests/test_projects.py`
- Modify: `README.md`

**Interfaces:**
- Produces: permission-matrix coverage and documented sprint API behavior.

- [ ] **Step 1: Add every sprint mutation to the authorization matrix**

Add create, start, and close rows as `OWNER_ONLY`, with valid setup IDs and before/after snapshots that include sprint state and ticket sprint assignment.

- [ ] **Step 2: Preserve project deletion with sprint data**

Add a regression that creates active, planning, and closed sprint data, deletes the project as owner, and asserts every project-owned history, ticket, sprint, and membership row is gone. Update `delete_project` to delete history first, then tickets and sprints, then memberships and the project, with one flush before deleting the project.

- [ ] **Step 3: Run authorization and full tests**

Run: `uv run pytest tests/test_authorization_matrix.py -v`
Expected: PASS.
Run: `uv run pytest`
Expected: all non-benchmark tests PASS.

- [ ] **Step 4: Update README API scope**

Document sprint status flow, explicit next-sprint requirement, and the commands used to verify it. Do not describe web or MCP support until their later plans land.

- [ ] **Step 5: Run final quality checks**

Run: `uv run ruff check . && uv run ruff format --check . && git diff --check`
Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add tests/test_authorization_matrix.py app/routers/api_projects.py tests/test_projects.py README.md
git commit -m "test: verify sprint permissions and lifecycle"
```
