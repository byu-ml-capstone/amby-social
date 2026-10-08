# Notion-Style Sprint Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved Notion-style shell, active sprint board, backlog planning, sprint lifecycle controls, ticket detail panel, and closed-sprint history.

**Architecture:** Keep server-rendered Jinja2 pages and HTMX fragments. Add one native CSS file for reusable visual tokens and responsive behavior; Alpine.js remains limited to local presentation state.

**Tech Stack:** FastAPI, Jinja2, HTMX, Alpine.js, Tailwind CDN, native CSS, pytest/TestClient.

**Spec:** `docs/superpowers/specs/2026-09-16-notion-sprint-workflow-design.md`

**Prerequisite:** Complete `docs/superpowers/plans/2026-09-16-sprint-core-implementation.md` first.

## Global Constraints

- Projects remain visible immediately after login and open in one click.
- Project entry opens active sprint, then planning sprint, then Backlog as fallback.
- Tabs are Board, Backlog, Sprint History, and Settings.
- Raw enum strings are never user-facing copy.
- Sprint controls render only for `OWNER`; server authorization remains authoritative.
- Mobile must not compress four board columns into the viewport.
- Do not add drag-and-drop, dark mode, command palette, charts, or a frontend build step.

---

### Task 1: Add the visual foundation and application shell

**Files:**
- Create: `app/static/app.css`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Create: `tests/test_web_shell.py`

**Interfaces:**
- Produces: `/static/app.css`, sidebar shell, mobile drawer, and shared CSS classes used by later templates.

- [ ] **Step 1: Write failing shell tests**

```python
def test_signed_in_shell_has_project_sidebar(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)
    page = client.get("/dashboard")
    assert 'data-testid="app-sidebar"' in page.text
    assert f'href="/projects/{project.slug}"' in page.text
    assert 'href="/static/app.css"' in page.text
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_web_shell.py -v`
Expected: FAIL because the new shell and stylesheet do not exist.

- [ ] **Step 3: Mount static files and pass projects to the shell**

Mount `StaticFiles(directory="app/static")` at `/static`. Add a small shared context helper in `app/routers/web.py` that loads the signed-in user's projects once per full-page render; fragment responses skip the shell list.

- [ ] **Step 4: Implement the shell and CSS tokens**

Define native CSS variables such as `--canvas: #f7f7f5`, `--surface: #ffffff`, `--text: #37352f`, `--muted: #787774`, and `--line: #e9e9e7`. Build a 230 px desktop sidebar, content area, subtle row hover, dialog shadow, `[x-cloak]`, and a mobile drawer breakpoint. Use system fonts and respect `prefers-reduced-motion`.

- [ ] **Step 5: Run shell and existing auth/dashboard tests**

Run: `uv run pytest tests/test_web_shell.py tests/test_web_auth.py tests/test_web_dashboard.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/static/app.css app/main.py app/templates/base.html app/routers/web.py tests/test_web_shell.py
git commit -m "feat: add notion-style application shell"
```

### Task 2: Redesign the project dashboard

**Files:**
- Modify: `app/templates/dashboard.html`
- Modify: `tests/test_web_dashboard.py`

**Interfaces:**
- Produces: compact project rows and modal project creation.

- [ ] **Step 1: Write failing dashboard assertions**

Assert the page has one `Projects` heading, a `New project` button, compact project rows with role labels, and an accessible project dialog. Preserve existing empty and validation states.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_web_dashboard.py -v`
Expected: FAIL on the new dialog and row markers.

- [ ] **Step 3: Replace the two-card layout**

Render project entries as plain rows inside one surface. Move the existing create form into an Alpine-controlled dialog, retain CSRF and field errors, and reopen the dialog when server validation returns an error.

- [ ] **Step 4: Run dashboard tests**

Run: `uv run pytest tests/test_web_dashboard.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/dashboard.html tests/test_web_dashboard.py
git commit -m "feat: simplify project dashboard"
```

### Task 3: Make the project entry sprint-aware

**Files:**
- Modify: `app/routers/web.py`
- Create: `app/templates/partials/project_nav.html`
- Modify: `app/templates/board.html`
- Modify: `tests/test_web_board.py`

**Interfaces:**
- Produces: project entry fallback, project tabs, sprint header, board counts, and server-side filters.
- Consumes: sprint and ticket filters from the core plan.

- [ ] **Step 1: Write failing navigation tests**

```python
@pytest.fixture
def active_sprint_world(make_user, make_project, engine):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        sprint = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship checkout",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
            committed_points=0,
        )
        session.add(sprint)
        session.commit()
        session.refresh(sprint)
    return SimpleNamespace(owner=owner, project=project, sprint=sprint)


def test_project_opens_active_sprint(client, active_sprint_world, login_as):
    login_as(active_sprint_world.owner.email)
    page = client.get(f"/projects/{active_sprint_world.project.slug}")
    assert page.status_code == 200
    assert active_sprint_world.sprint.name in page.text
    assert 'data-testid="project-tabs"' in page.text
```

Also test planning fallback, Backlog fallback, column counts, `mine=1`, assignee/type/priority filters, and human labels such as `In progress`.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_web_board.py -v`
Expected: FAIL on sprint navigation assertions.

- [ ] **Step 3: Load the correct sprint and filtered tickets**

Update `board()` to resolve the user's project membership, choose `ACTIVE` before `PLANNING`, accept the four approved filters, and query only that sprint's tickets. When no sprint exists, redirect to `/projects/{slug}/backlog`.

- [ ] **Step 4: Render the project header and compact board**

Use `project_nav.html` for the four tabs. Render date range, goal, sprint status, column ticket counts, filter form, compact cards, and owner-only sprint actions. Preserve the 200-ticket cap and truncation message.

- [ ] **Step 5: Add mobile column navigation**

Use CSS grid with fixed minimum column widths and horizontal overflow plus labeled scroll regions. Do not add drag-and-drop JavaScript.

- [ ] **Step 6: Run board regression tests**

Run: `uv run pytest tests/test_web_board.py tests/test_web_status.py tests/test_rendering.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/web.py app/templates/board.html app/templates/partials/project_nav.html app/static/app.css tests/test_web_board.py
git commit -m "feat: show active sprint board"
```

### Task 4: Add Backlog and planning UI

**Files:**
- Create: `app/routers/web_sprints.py`
- Modify: `app/main.py`
- Create: `app/templates/backlog.html`
- Create: `app/templates/partials/sprint_form.html`
- Create: `tests/test_web_sprint_planning.py`

**Interfaces:**
- Produces: `GET /projects/{slug}/backlog`, `POST /projects/{slug}/sprints`, and ticket-to-sprint assignment form actions.
- Consumes: core sprint creation and ticket assignment services.

- [ ] **Step 1: Write failing Backlog tests**

Cover member reads, owner-only sprint creation, one-planning conflict, unassigned tickets, multi-select assignment, and valid CSRF.

```python
@pytest.fixture
def backlog_world(make_user, make_project, engine):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        planning = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Ship reports",
            start_date=date(2026, 9, 28),
            end_date=date(2026, 10, 5),
        )
        unassigned = Ticket(
            ticket_number=1,
            project_id=project.id,
            title="Unassigned",
            creator_id=owner.id,
        )
        session.add(planning)
        session.flush()
        assigned = Ticket(
            ticket_number=2,
            project_id=project.id,
            sprint_id=planning.id,
            title="Assigned",
            creator_id=owner.id,
        )
        session.add_all([unassigned, assigned])
        session.commit()
        session.refresh(unassigned)
        session.refresh(assigned)
    return SimpleNamespace(owner=owner, project=project, unassigned=unassigned, assigned=assigned)


def test_backlog_only_lists_unassigned_tickets(client, backlog_world, login_as):
    login_as(backlog_world.owner.email)
    page = client.get(f"/projects/{backlog_world.project.slug}/backlog")
    assert backlog_world.unassigned.title in page.text
    assert backlog_world.assigned.title not in page.text
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_web_sprint_planning.py -v`
Expected: FAIL with route 404.

- [ ] **Step 3: Add sprint web routes**

Create a focused `web_sprints` router. Reuse `render`, `verify_csrf`, `project_reader`, `project_owner`, and core services. Sprint creation returns the Backlog page with field errors on failure and redirects to Backlog on success.

- [ ] **Step 4: Build the Backlog and planning forms**

Render unassigned ticket rows, the single planning sprint summary, owner-only create dialog, ticket checkboxes, and an `Add to sprint` action. Members see the same planning state without mutation controls.

- [ ] **Step 5: Run planning and authorization tests**

Run: `uv run pytest tests/test_web_sprint_planning.py tests/test_authorization_matrix.py -v`
Expected: PASS after adding the new HTML mutations to the authorization matrix.

- [ ] **Step 6: Commit**

```bash
git add app/routers/web_sprints.py app/main.py app/templates/backlog.html app/templates/partials/sprint_form.html tests/test_web_sprint_planning.py tests/test_authorization_matrix.py
git commit -m "feat: add backlog sprint planning"
```

### Task 5: Add sprint start and close workflows

**Files:**
- Modify: `app/routers/web_sprints.py`
- Create: `app/templates/partials/sprint_start.html`
- Create: `app/templates/partials/sprint_close.html`
- Create: `tests/test_web_sprint_lifecycle.py`

**Interfaces:**
- Produces: owner-only start and close forms with preview and errors.
- Consumes: `start_sprint` and `close_sprint` services.

- [ ] **Step 1: Write failing lifecycle tests**

Test owner success, member `403`, CSRF failure, start totals, close preview contents, mandatory planning destination, successful rollover, duplicate-submit conflict, and service error rendering.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_web_sprint_lifecycle.py -v`
Expected: FAIL because lifecycle form routes do not exist.

- [ ] **Step 3: Add start and close routes**

Add `POST /projects/{slug}/sprints/{id}/start`, `GET /projects/{slug}/sprints/{id}/close`, and `POST /projects/{slug}/sprints/{id}/close`. Require owner and CSRF on mutations. The GET preview computes counts only; the POST calls the atomic core service.

- [ ] **Step 4: Build accessible confirmation dialogs**

The start form shows the ticket and point commitment. The close form shows completed points, unfinished ticket count, and the selected planning sprint. Disable repeated submission, autofocus the dialog heading, support Escape, trap focus, and restore focus to the opener.

- [ ] **Step 5: Run lifecycle tests**

Run: `uv run pytest tests/test_web_sprint_lifecycle.py tests/test_authorization_matrix.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routers/web_sprints.py app/templates/partials/sprint_start.html app/templates/partials/sprint_close.html tests/test_web_sprint_lifecycle.py tests/test_authorization_matrix.py
git commit -m "feat: add web sprint lifecycle"
```

### Task 6: Add closed-sprint history

**Files:**
- Modify: `app/routers/web_sprints.py`
- Create: `app/templates/sprint_history.html`
- Create: `app/templates/sprint_history_detail.html`
- Create: `tests/test_web_sprint_history.py`

**Interfaces:**
- Produces: history list and read-only sprint detail pages.

- [ ] **Step 1: Write failing history tests**

Assert newest-first closed sprints, summary totals, close-time status and points, rollover count, current ticket link, no mutation controls, member access, and outsider `404`.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_web_sprint_history.py -v`
Expected: FAIL with route 404.

- [ ] **Step 3: Add history routes and templates**

Add `GET /projects/{slug}/sprints` and `GET /projects/{slug}/sprints/{id}`. Query `SprintTicketHistory` joined to current tickets. Render compact summaries and a read-only ticket list rather than a historical Kanban board.

- [ ] **Step 4: Run history tests**

Run: `uv run pytest tests/test_web_sprint_history.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routers/web_sprints.py app/templates/sprint_history.html app/templates/sprint_history_detail.html tests/test_web_sprint_history.py
git commit -m "feat: show sprint history"
```

### Task 7: Add ticket detail editing panel

**Files:**
- Modify: `app/routers/web.py`
- Modify: `app/templates/partials/ticket_card.html`
- Create: `app/templates/partials/ticket_detail.html`
- Modify: `app/templates/partials/ticket_modal.html`
- Create: `tests/test_web_ticket_detail.py`

**Interfaces:**
- Produces: HTML detail fragment and update action for title, description, type, priority, points, assignee, status, resolution notes, and sprint destination.

- [ ] **Step 1: Write failing detail tests**

Test fragment load, sanitized Markdown, same-project assignees, owner/member edits, foreign assignee rejection, closed-sprint assignment rejection, update errors, and refreshed compact card output.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_web_ticket_detail.py -v`
Expected: FAIL because the detail route and template do not exist.

- [ ] **Step 3: Add fragment and update routes**

Add `GET /projects/{slug}/tickets/{ticket_number}` and CSRF-protected `POST /projects/{slug}/tickets/{ticket_number}`. Reuse the same validation helpers as the JSON API, then return the updated detail fragment with an HTMX event requesting the corresponding card refresh.

- [ ] **Step 4: Replace expanded card descriptions with summary cards**

Cards become buttons that load the right panel. Keep number, title, type, priority, assignee, and points; remove full descriptions from the board. Add explicit destination to the create modal, defaulted by Board or Backlog context.

- [ ] **Step 5: Run ticket web regression tests**

Run: `uv run pytest tests/test_web_ticket_detail.py tests/test_web_ticket_create.py tests/test_web_status.py tests/test_web_board.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routers/web.py app/templates/partials/ticket_card.html app/templates/partials/ticket_detail.html app/templates/partials/ticket_modal.html tests/test_web_ticket_detail.py
git commit -m "feat: add ticket detail panel"
```

### Task 8: Add project settings

**Files:**
- Modify: `app/routers/web_sprints.py`
- Modify: `app/services.py`
- Modify: `app/routers/api_projects.py`
- Create: `app/templates/project_settings.html`
- Create: `tests/test_web_project_settings.py`

**Interfaces:**
- Produces: `GET /projects/{slug}/settings` plus owner-only project, webhook, and membership forms.
- Consumes: existing project and membership rules.

- [ ] **Step 1: Write failing settings tests**

Cover owner/member rendering, member read-only state, project rename, webhook validation, member add/role change/removal, last-owner protection, CSRF, and deletion confirmation.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/test_web_project_settings.py -v`
Expected: FAIL because the settings page does not exist.

- [ ] **Step 3: Share the existing settings logic**

Move project update and membership mutations that currently live only in `api_projects.py` into named functions in `app/services.py`. Call those functions from both JSON and HTML routes; do not issue loopback HTTP requests from the web process to its own API.

- [ ] **Step 4: Build the settings page**

Render project details, chat webhook settings, and member rows. Members see values without owner mutation controls. Put project deletion in a separated danger section requiring the immutable slug.

- [ ] **Step 5: Run settings and API regressions**

Run: `uv run pytest tests/test_web_project_settings.py tests/test_projects.py tests/test_membership.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routers/web_sprints.py app/services.py app/routers/api_projects.py app/templates/project_settings.html tests/test_web_project_settings.py
git commit -m "feat: add project settings page"
```

### Task 9: Verify accessibility, responsiveness, and regressions

**Files:**
- Modify: `tests/test_web_shell.py`
- Modify: `tests/test_web_board.py`
- Modify: `README.md`

**Interfaces:**
- Produces: final web acceptance coverage and usage documentation.

- [ ] **Step 1: Add static accessibility assertions**

Check dialog roles and labels, input labels, active tab state, keyboard-close bindings, focus targets, status text, and reduced-motion CSS. Check that the mobile board stylesheet uses horizontal overflow and minimum column width.

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest`
Expected: all non-benchmark tests PASS.

- [ ] **Step 3: Update README**

Document dashboard, Board, Backlog, Sprint History, Settings navigation, owner-only sprint actions, and mobile behavior.

- [ ] **Step 4: Run final quality checks**

Run: `uv run ruff check . && uv run ruff format --check . && git diff --check`
Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add tests/test_web_shell.py tests/test_web_board.py README.md
git commit -m "test: verify sprint web experience"
```
