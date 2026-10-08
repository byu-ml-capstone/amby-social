# Core Work Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add optional ticket due dates, a cross-project My Work dashboard, and live active-sprint summary cards.

**Architecture:** Add one nullable `due_date` column and carry it through the existing ticket service, API, web, and MCP paths. Build My Work with two bounded cross-project queries on the existing dashboard, and compute active-sprint metrics from current ticket rows without storing another report model.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLAlchemy, SQLite/Alembic, Jinja2, HTMX, Alpine.js, pytest.

**Spec:** `docs/superpowers/specs/2026-09-18-core-work-visibility-design.md`

## Global Constraints

- Add no dependency.
- Keep the fixed four-status workflow and OWNER/MEMBER permission model.
- Use native `<input type="date">`; add no date-picker JavaScript.
- Do not add saved views, a dashboard model, a reporting table, charts, or a cross-project API.
- A DONE ticket is never overdue, even when its due date is in the past.
- Run `graphify update .` after code changes.

---

### Task 1: Persist and expose ticket due dates

**Files:**

- Create: `alembic/versions/7a9c2d1e4f80_add_ticket_due_date.py`
- Modify: `app/models.py:155-180`
- Modify: `app/schemas.py:113-164`
- Modify: `app/services.py` (`create_ticket` and `update_ticket`)
- Modify: `app/routers/api_tickets.py:68-95`
- Test: `tests/test_migrations.py`
- Test: `tests/test_ticket_api.py`
- Test: `tests/test_ticket_create.py`

**Interfaces:**

- Produces: `Ticket.due_date: date | None`
- Produces: `TicketCreate.due_date`, `TicketUpdate.due_date`, and `TicketOut.due_date`
- Preserves: omitted update leaves the value unchanged; explicit JSON `null` clears it

- [ ] **Step 1: Add failing API tests for create, update, and clear**

Add tests equivalent to:

```python
def test_ticket_due_date_create_update_and_clear(client, project_world):
    created = client.post(
        "/api/v1/tickets",
        json={
            "slug": project_world.project.slug,
            "title": "Ship billing",
            "due_date": "2026-09-30",
        },
    )
    assert created.status_code == 201
    assert created.json()["due_date"] == "2026-09-30"

    ticket_id = created.json()["id"]
    assert client.patch(
        f"/api/v1/tickets/{ticket_id}", json={"due_date": "2026-10-02"}
    ).json()["due_date"] == "2026-10-02"
    assert client.patch(
        f"/api/v1/tickets/{ticket_id}", json={"due_date": None}
    ).json()["due_date"] is None
```

Also assert malformed values such as `"09/30/2026"` return 422 and do not
change a previously stored value.

- [ ] **Step 2: Run the focused API tests and verify failure**

Run:

```bash
uv run pytest tests/test_ticket_api.py tests/test_ticket_create.py -q
```

Expected: FAIL because `due_date` is absent from the response and persistence
model.

- [ ] **Step 3: Add the nullable model field and Alembic migration**

Add to `Ticket`:

```python
due_date: date | None = Field(default=None, index=True)
```

The migration must use revision `7a9c2d1e4f80`, down revision
`5d0b32a81e77`, add nullable `DATE` column `ticket.due_date`, create the named
non-unique index `ix_ticket_due_date`, and drop the index before the column on
downgrade. No backfill is needed.

- [ ] **Step 4: Add a migration round-trip test**

In `tests/test_migrations.py`, migrate a temporary database from
`5d0b32a81e77` to head, assert that `due_date` exists and is nullable, downgrade
to `5d0b32a81e77`, and assert the column and index are gone.

- [ ] **Step 5: Run migration tests and verify they pass**

Run:

```bash
uv run pytest tests/test_migrations.py -q
```

Expected: PASS.

- [ ] **Step 6: Carry due_date through schemas, service, and API create**

Import `date` where needed. Add `due_date: date | None = None` to
`TicketCreate`, `TicketOut`, and `TicketUpdate`. Add the same keyword-only
argument to `create_ticket`, assign it on `Ticket(...)`, and pass
`body.due_date` from `post_ticket`. Do not add a future-only validator.
`update_ticket` already applies fields from `TicketUpdate`; retain that shared
path rather than adding route-specific assignment.

- [ ] **Step 7: Run focused tests and verify they pass**

Run:

```bash
uv run pytest tests/test_ticket_api.py tests/test_ticket_create.py tests/test_models.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit the persistence/API slice**

```bash
git add alembic/versions/7a9c2d1e4f80_add_ticket_due_date.py app/models.py app/schemas.py app/services.py app/routers/api_tickets.py tests/test_migrations.py tests/test_ticket_api.py tests/test_ticket_create.py
git commit -m "feat: add ticket due dates"
```

### Task 2: Add due dates to web forms and board cards

**Files:**

- Modify: `app/routers/web.py` (`_ticket_detail`, `board`, ticket update/create/status handlers)
- Modify: `app/templates/partials/ticket_modal.html`
- Modify: `app/templates/partials/ticket_detail.html`
- Modify: `app/templates/partials/ticket_card.html`
- Modify: `app/static/app.css`
- Test: `tests/test_web_ticket_create.py`
- Test: `tests/test_web_ticket_detail.py`
- Test: `tests/test_web_board.py`

**Interfaces:**

- Consumes: `Ticket.due_date: date | None` from Task 1
- Produces: native date inputs named `due_date`
- Produces: card `<time datetime="YYYY-MM-DD">` and `is-overdue` presentation

- [ ] **Step 1: Add failing web tests**

Cover all four behaviors:

```python
def test_web_ticket_create_accepts_due_date(client, active_sprint_world, login_as):
    login_as(active_sprint_world.owner.email)
    response = client.post(
        f"/projects/{active_sprint_world.project.slug}/tickets",
        data={
            "_csrf": csrf_for(active_sprint_world.owner),
            "title": "Deadline",
            "type": "TASK",
            "sprint_id": active_sprint_world.sprint.id,
            "due_date": "2026-09-30",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
```

Add assertions that the create and detail forms contain
`type="date" name="due_date"`, editing persists a new value, and submitting an
empty value clears it. In the board test, create one open past-due ticket and
one DONE past-due ticket; assert only the open card has `is-overdue` while both
render their `<time>` value.

- [ ] **Step 2: Run focused web tests and verify failure**

Run:

```bash
uv run pytest tests/test_web_ticket_create.py tests/test_web_ticket_detail.py tests/test_web_board.py -q
```

Expected: FAIL because the forms and cards do not render due dates.

- [ ] **Step 3: Wire native inputs into create and edit flows**

Add this control to the create form:

```html
<label for="ticket-due-date">Due date</label>
<input id="ticket-due-date" type="date" name="due_date">
```

Add the populated equivalent to the detail form:

```html
<label for="detail-due-date">Due date</label>
<input id="detail-due-date" type="date" name="due_date"
       value="{{ form_values['due_date'] }}">
```

Add `due_date: date | None = Form(None)` to
`create_ticket_form` and pass it to `create_ticket`. Add the same form parameter
to the update handler and include it when constructing `TicketUpdate`; the
empty form value must become `None`. Add `due_date` to `_ticket_detail`'s
`form_values` as `ticket.due_date.isoformat()` or an empty string so validation
errors preserve the submitted value.

- [ ] **Step 4: Render semantic due-date badges consistently**

Pass `today=date.today()` to the full board render and the single-card status
refresh render. In `ticket_card.html`, render:

```html
{% if ticket.due_date %}
<time datetime="{{ ticket.due_date.isoformat() }}"
      class="ticket-due-date{% if ticket.status.value != 'DONE' and ticket.due_date < today %} is-overdue{% endif %}">
  Due {{ ticket.due_date.strftime('%b %-d') }}
</time>
{% endif %}
```

Keep the date inside `.ticket-card-meta`; do not add client-side date parsing.
Add a restrained `.is-overdue` color/weight rule and preserve existing focus
and responsive rules.

- [ ] **Step 5: Run focused web tests and verify they pass**

Run:

```bash
uv run pytest tests/test_web_ticket_create.py tests/test_web_ticket_detail.py tests/test_web_board.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the web due-date slice**

```bash
git add app/routers/web.py app/templates/partials/ticket_modal.html app/templates/partials/ticket_detail.html app/templates/partials/ticket_card.html app/static/app.css tests/test_web_ticket_create.py tests/test_web_ticket_detail.py tests/test_web_board.py
git commit -m "feat: show due dates on tickets"
```

### Task 3: Expose due dates through MCP

**Files:**

- Modify: `app/mcp_server.py:167-193`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: POST `/api/v1/tickets` accepting ISO `due_date` from Task 1
- Produces: `create_ticket(..., due_date: date | None = None)`
- Preserves: generic `update_ticket(ticket_id, changes)` for update and clear

- [ ] **Step 1: Add a failing MCP forwarding test**

Extend the existing fake transport test to call:

```python
await create_ticket(
    "payments",
    "Ship billing",
    due_date=date(2026, 9, 30),
)
```

Assert the outgoing JSON body contains `"due_date": "2026-09-30"`. Extend the
tool schema assertion to require the optional `due_date` property and retain all
existing required inputs.

- [ ] **Step 2: Run the focused MCP tests and verify failure**

Run:

```bash
uv run pytest tests/test_mcp_server.py -q
```

Expected: FAIL because `create_ticket` does not accept `due_date`.

- [ ] **Step 3: Add the optional MCP parameter**

Import `date`, add `due_date: date | None = None` after the existing optional
ticket fields, and send `due_date.isoformat() if due_date else None` in the JSON
body. Do not add another update tool: callers clear the field with
`update_ticket(ticket_id, {"due_date": None})`.

- [ ] **Step 4: Run MCP tests and verify they pass**

Run:

```bash
uv run pytest tests/test_mcp_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the MCP slice**

```bash
git add app/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: support ticket due dates in MCP"
```

### Task 4: Add cross-project My Work to the dashboard

**Files:**

- Modify: `app/routers/web.py:672-680`
- Modify: `app/templates/dashboard.html`
- Modify: `app/static/app.css`
- Test: `tests/test_web_dashboard.py`

**Interfaces:**

- Consumes: `Ticket.due_date` from Task 1 and existing `ProjectMember` access
- Produces: template lists `overdue_tickets`, `in_progress_tickets`,
  `next_tickets`, and `recently_completed_tickets`, each containing
  `(Ticket, Project)` rows
- Keeps: `/dashboard` as the sole page and canonical ticket URLs as row links

- [ ] **Step 1: Add failing dashboard grouping and access tests**

Build two projects with tickets assigned to the same user. Assert:

- an open ticket due yesterday appears only under `Overdue`;
- a non-overdue `IN_PROGRESS` ticket appears only under `In progress`;
- `BACKLOG` and `SELECTED` tickets appear under `Next`;
- only the 10 newest DONE tickets appear under `Recently completed`;
- unassigned tickets and tickets belonging to a project from which the user was
  removed do not appear;
- every row links to `/projects/{slug}/tickets/{ticket_number}` and shows the
  project key;
- a user with no assigned work sees the empty state.

- [ ] **Step 2: Run dashboard tests and verify failure**

Run:

```bash
uv run pytest tests/test_web_dashboard.py -q
```

Expected: FAIL because the dashboard only lists projects.

- [ ] **Step 3: Add the two bounded dashboard queries**

In `dashboard`, calculate `today = date.today()`. Query `(Ticket, Project)` by
joining `Project` and `ProjectMember`, with both
`Ticket.assignee_id == user.id` and `ProjectMember.user_id == user.id`.

The open query must filter `Ticket.status != TicketStatus.DONE`, order dated
rows before undated rows, then by `due_date` ascending and `created_at`
descending, and limit to 100. The completed query must filter DONE, order
`completed_at` descending, and limit to 10. Partition open rows once in Python:

```python
overdue = []
in_progress = []
next_up = []
for row in open_rows:
    ticket = row[0]
    if ticket.due_date is not None and ticket.due_date < today:
        overdue.append(row)
    elif ticket.status is TicketStatus.IN_PROGRESS:
        in_progress.append(row)
    else:
        next_up.append(row)
```

Pass the four named lists and `today` to the existing dashboard render. Do not
introduce a helper or repository class for these two queries.

- [ ] **Step 4: Render an accessible compact My Work section**

Add the section above Projects using one heading and four small subsections.
Each ticket appears once and uses a normal anchor to the canonical detail page.
Use `<time datetime>` for due dates, plain text for project key/status/priority,
and the same overdue treatment as board cards. Keep Projects and the New
project dialog unchanged.

- [ ] **Step 5: Add minimal responsive styles**

Reuse `.surface`, `.app-row-list`, and `.app-row` where possible. Add only the
grid/wrapping rules needed for four groups and narrow screens. Do not create a
dashboard card component abstraction or JavaScript interaction.

- [ ] **Step 6: Run dashboard tests and verify they pass**

Run:

```bash
uv run pytest tests/test_web_dashboard.py tests/test_web_shell.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit My Work**

```bash
git add app/routers/web.py app/templates/dashboard.html app/static/app.css tests/test_web_dashboard.py
git commit -m "feat: add My Work dashboard"
```

### Task 5: Add active-sprint summary cards

**Files:**

- Modify: `app/routers/web.py:708-823`
- Modify: `app/templates/board.html`
- Modify: `app/static/app.css`
- Test: `tests/test_web_board.py`

**Interfaces:**

- Consumes: selected `Sprint` and all current `Ticket` rows in that sprint
- Produces: `sprint_summary` mapping for ACTIVE sprints, otherwise `None`
- Preserves: closed history values and planning sprint behavior

- [ ] **Step 1: Add failing active/planning sprint summary tests**

Create an ACTIVE sprint with `committed_points=13` and tickets representing:
DONE with 5 points, DONE unestimated, open with 3 points, one rollover, and one
at-risk ticket. Assert the board contains:

```text
Completed
2 / 5 · 40%
Points
5 / 13
Rollover
2
At risk
1
```

Add an empty ACTIVE sprint assertion for `0 / 0 · 0%`, and a PLANNING sprint
assertion that the summary element is absent.

- [ ] **Step 2: Run board tests and verify failure**

Run:

```bash
uv run pytest tests/test_web_board.py -q
```

Expected: FAIL because no sprint summary is rendered.

- [ ] **Step 3: Compute live metrics in the existing board route**

For an ACTIVE selected sprint, load all current tickets with one simple
`select(Ticket).where(Ticket.sprint_id == sprint.id)` query. Compute:

```python
done = [ticket for ticket in summary_tickets if ticket.status is TicketStatus.DONE]
total = len(summary_tickets)
sprint_summary = {
    "completed_count": len(done),
    "total_count": total,
    "completion_percent": round(len(done) * 100 / total) if total else 0,
    "completed_points": sum(ticket.story_points or 0 for ticket in done),
    "committed_points": sprint.committed_points or 0,
    "rollover_count": sum(ticket.rollover_count > 0 for ticket in summary_tickets),
    "at_risk_count": sum(
        ticket.rollover_count >= 2 or (ticket.delayed_days or 0) >= 14
        for ticket in summary_tickets
    ),
}
```

Set `sprint_summary = None` for PLANNING. Do not write the derived values back
to `Sprint` or add a service abstraction with one caller.

- [ ] **Step 4: Render numeric cards with semantic markup**

Place a `<dl data-testid="sprint-summary">` between project navigation and
board filters when `sprint_summary` is not None. Use four `<div>` entries with
`<dt>` and `<dd>`. Render raw point values even when completed exceeds
committed. Do not add progress bars, canvas, SVG, or JavaScript.

- [ ] **Step 5: Add compact responsive styles**

Use a four-column CSS grid at desktop and two columns on small screens. Reuse
existing surface colors, borders, type scale, and spacing tokens. Ensure the
definition list remains understandable without color.

- [ ] **Step 6: Run board tests and verify they pass**

Run:

```bash
uv run pytest tests/test_web_board.py tests/test_web_sprint_lifecycle.py tests/test_web_sprint_history.py -q
```

Expected: PASS, including unchanged frozen closed-sprint reporting.

- [ ] **Step 7: Commit sprint summary**

```bash
git add app/routers/web.py app/templates/board.html app/static/app.css tests/test_web_board.py
git commit -m "feat: summarize active sprint progress"
```

### Task 6: Verify the complete change and refresh Graphify

**Files:**

- Modify: `graphify-out/**` through the Graphify CLI
- Modify: `README.md` only if its ticket/MCP field descriptions are now
  demonstrably incomplete during review

**Interfaces:**

- Verifies: migration, API, web, MCP, formatting, and graph consistency

- [ ] **Step 1: Run the complete test suite**

```bash
uv run pytest
```

Expected: PASS with the benchmark marker deselected by project configuration.

- [ ] **Step 2: Run lint and formatting checks**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: both commands exit 0. If formatting fails, run
`uv run ruff format .`, inspect the diff, and repeat both checks.

- [ ] **Step 3: Smoke-test the migration chain**

```bash
tmp_db="$(mktemp -d)/kanbanflow.db"
DATABASE_URL="sqlite:///$tmp_db" uv run alembic upgrade head
DATABASE_URL="sqlite:///$tmp_db" uv run alembic downgrade 5d0b32a81e77
DATABASE_URL="sqlite:///$tmp_db" uv run alembic upgrade head
```

Expected: all three commands exit 0. The temporary directory is left to the OS
for cleanup; do not point migration tests at `data/kanbanflow.db`.

- [ ] **Step 4: Refresh and inspect the knowledge graph**

```bash
graphify update .
graphify query "Where are due dates, My Work, and active sprint summaries implemented and tested?"
```

Expected: the scoped graph names the model/schema/service/API/MCP paths for due
dates, the dashboard route/template for My Work, and the board route/template
for sprint summaries.

- [ ] **Step 5: Review the final diff for scope**

```bash
git status --short
git diff --stat HEAD~5..HEAD
git diff HEAD~5..HEAD
```

Confirm there is no new dependency, dashboard/reporting table, custom date
picker, cross-project API, chart, or unrelated refactor.

- [ ] **Step 6: Commit graph/documentation updates if any**

```bash
git add graphify-out README.md
git commit -m "docs: refresh work visibility documentation"
```

Skip this commit when neither tracked Graphify output nor README changed.
