# Quiet Sprint Workspace UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply a coherent Notion-like product UI across the existing Kanban Flow web screens without changing the backend feature set.

**Architecture:** Keep the current server-rendered Jinja, HTMX, Alpine, and native dialog architecture. Consolidate visual decisions in `app/static/app.css`, replace page-specific utility strings with semantic classes, and keep route and authorization behavior unchanged.

**Tech Stack:** FastAPI, Jinja2, HTMX, Alpine.js, native HTML/CSS, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-ui-refresh-design.md`

## Global Constraints

- Do not introduce a frontend dependency or build step.
- Preserve existing route behavior, authorization boundaries, Jinja rendering, HTMX, and Alpine usage.
- Project navigation contains Board, Backlog, and History only; Settings is a secondary utility link.
- The sprint selector contains sprints only.
- Use native buttons, links, form controls, and dialogs.
- Use a four-pixel spacing base and the primary, secondary, ghost, and danger control hierarchy.
- Maintain existing test IDs and server-visible copy where tests or route contracts depend on them, except for the approved labels `History` and `Unscheduled tickets`.

---

### Task 1: Application shell and shared design system

**Files:**
- Modify: `app/static/app.css`
- Modify: `app/templates/base.html`
- Modify: `app/templates/dashboard.html`
- Modify: `app/templates/partials/project_nav.html`
- Modify: `app/templates/partials/sprint_selector.html`
- Modify: `tests/test_web_shell.py`
- Modify: `tests/test_web_dashboard.py`
- Create: `tests/test_web_ui_refresh.py`

**Interfaces:**
- Consumes: existing `user`, `project`, `active_tab`, `sprints`, and `selected_sprint_id` Jinja context.
- Produces: semantic shell, page, navigation, control, surface, form, status, and responsive classes used by Tasks 2 and 3.

- [ ] **Step 1: Write failing rendered-HTML tests**

Add tests proving the rendered shell includes a skip link and active project context, project navigation omits Settings, the sprint selector omits non-sprint destinations, and dashboard rows have one project navigation link.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_web_ui_refresh.py tests/test_web_shell.py tests/test_web_dashboard.py`

Expected: failures identify the missing skip link, duplicated navigation, and old dashboard row structure.

- [ ] **Step 3: Implement the shared visual system and shell**

Define semantic color, text, spacing, radius, border, focus, control, surface, list, dialog, and responsive rules in `app.css`. Update the shell, dashboard, project navigation, and sprint selector to use those rules while retaining the existing Jinja and Alpine contracts.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest -q tests/test_web_ui_refresh.py tests/test_web_shell.py tests/test_web_dashboard.py`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: refresh application shell and design system`

### Task 2: Board and backlog workspaces

**Files:**
- Modify: `app/templates/board.html`
- Modify: `app/templates/backlog.html`
- Modify: `app/templates/partials/ticket_card.html`
- Modify: `app/templates/partials/ticket_detail.html`
- Modify: `app/templates/partials/ticket_modal.html`
- Modify: `app/templates/partials/sprint_form.html`
- Modify: `app/templates/partials/sprint_start.html`
- Modify: `app/templates/partials/sprint_close.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_web_ui_refresh.py`
- Modify: `tests/test_web_board.py`
- Modify: `tests/test_web_sprint_planning.py`

**Interfaces:**
- Consumes: Task 1 semantic layout, control, form, surface, status, and navigation classes.
- Produces: full-width board with ticket drawer, empty lane states, compact filters, planning sprint strip, and contextual backlog selection action.

- [ ] **Step 1: Write failing rendered-HTML tests**

Add tests proving the board exposes a drawer, every empty lane renders an empty state, active filters expose a Clear filters link, a planning sprint hides Add sprint, and zero ticket selection cannot submit.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_web_ui_refresh.py tests/test_web_board.py tests/test_web_sprint_planning.py`

Expected: failures identify the fixed detail rail, missing empty states and clear action, and competing backlog actions.

- [ ] **Step 3: Implement board and backlog hierarchy**

Use semantic components from Task 1. Keep the board as the focal surface, place ticket detail in an overlay drawer/sheet, add empty states, make filters compact, label the queue Unscheduled tickets, and reveal Add selected only when checkboxes are selected.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest -q tests/test_web_ui_refresh.py tests/test_web_board.py tests/test_web_sprint_planning.py`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: organize board and backlog workspaces`

### Task 3: History, settings, authentication, and responsive polish

**Files:**
- Modify: `app/templates/sprint_history.html`
- Modify: `app/templates/sprint_history_detail.html`
- Modify: `app/templates/project_settings.html`
- Modify: `app/templates/login.html`
- Modify: `app/templates/register.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_web_ui_refresh.py`
- Modify: `tests/test_web_sprint_history.py`
- Modify: `tests/test_web_project_settings.py`
- Modify: `tests/test_web_auth.py`

**Interfaces:**
- Consumes: Task 1 semantic layout, list, form, control, and status classes.
- Produces: aligned history rows, structured settings sections, danger action styling, member-specific accessible labels, and consistent authentication pages.

- [ ] **Step 1: Write failing rendered-HTML tests**

Add tests proving history uses row semantics and metric labels, Settings is linked as a utility action rather than a project tab, member controls include the member name in their accessible labels, and deletion uses the danger control.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_web_ui_refresh.py tests/test_web_sprint_history.py tests/test_web_project_settings.py tests/test_web_auth.py`

Expected: failures identify the old card-like history and undifferentiated settings controls.

- [ ] **Step 3: Implement remaining screens and responsive states**

Apply the shared page hierarchy to history, settings, and authentication. Align history metrics, separate the danger zone, add member-specific labels, and complete responsive one-column and full-width sheet behavior.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest -q tests/test_web_ui_refresh.py tests/test_web_sprint_history.py tests/test_web_project_settings.py tests/test_web_auth.py`

Expected: all focused tests pass.

- [ ] **Step 5: Run the full suite and visually verify desktop and mobile**

Run: `uv run pytest -q`

Then run the app with Playwright-backed screenshots at desktop and mobile widths and inspect hierarchy, overflow, empty states, drawer behavior, focus styling, and legibility.

- [ ] **Step 6: Commit**

Commit message: `feat: finish quiet workspace ui refresh`
