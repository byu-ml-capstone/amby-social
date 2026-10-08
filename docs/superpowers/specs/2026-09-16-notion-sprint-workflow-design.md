# Notion-Style Sprint Workflow Design

**Date:** 2026-09-16  
**Status:** Approved  
**Scope:** Web information architecture, sprint lifecycle, history, and matching MCP tools. This document defines design only; implementation is not part of this work.

## 1. Goal

Turn the current minimal project board into a readable, low-friction sprint workspace for developers, non-technical teammates, and project owners. A signed-in user must see their projects immediately, reach any project in one click, work from the active sprint by default, and review closed sprints without losing their historical ticket state.

## 2. Design Direction

Use a Notion-inspired visual language without copying Notion's product structure:

- warm off-white page background and white work surfaces;
- dark neutral text with generous line height;
- thin borders, 6–8 px radii, and shadows only for overlays;
- color reserved for status, priority, warnings, and primary actions;
- compact work-item rows and cards rather than large decorative panels;
- subtle hover states and inline actions;
- desktop sidebar that becomes a drawer on mobile;
- no dark mode, command palette, decorative animation, or custom theme system in the first version.

The result should be slightly denser than a document editor so the board remains useful as an operational tool.

## 3. Users and Permissions

The product keeps its existing two project roles.

### OWNER

- Create, start, and close sprints through either the web UI or MCP.
- Plan sprint scope, manage members and project settings, and perform all ticket work.

### MEMBER

- View active, planned, and closed sprints.
- Create, edit, assign, estimate, and transition tickets.
- Cannot create, start, or close a sprint through the web UI, API, or MCP.

No `VIEWER` role or granular sprint permission is added now. MCP authorization must use the same backend permission checks as the web UI and must never become a bypass.

## 4. Information Architecture and Click Targets

### Global shell

The desktop sidebar contains the product name, a Projects entry, the user's project list, and the user/logout control. The main content area owns the current page title and contextual actions. On mobile, the sidebar is a drawer.

### Navigation targets

- After login, the project list is visible with zero additional clicks.
- From any application screen, a project opens from the sidebar in one click.
- Opening a project goes directly to its active sprint board. If there is no active sprint, it opens the planning sprint; if neither exists, it opens Backlog.
- A closed sprint is reachable in one click from the sprint selector while inside a project.

There is no intermediate project overview page.

### Project navigation

Each project exposes four primary tabs:

- **Board:** tickets assigned to the active sprint;
- **Backlog:** tickets with no sprint assignment and the planning workflow;
- **Sprint History:** closed-sprint summaries and ticket snapshots;
- **Settings:** membership, project details, and integrations.

The project header shows project name, sprint selector, sprint status, date range, and goal. Sprint actions appear only to an `OWNER`.

## 5. Screens

### Dashboard

Use a compact database-like project list rather than a grid of large cards. Each row shows project name, the caller's role, recent activity when available, and an open action. `New project` opens a small modal. A cross-project "My work" feed is deferred because it requires a separate aggregate data path.

### Active sprint board

- Fixed columns: `BACKLOG`, `SELECTED`, `IN_PROGRESS`, and `DONE`.
- Human-readable labels replace raw enum strings.
- Each column shows its ticket count.
- Cards show only ticket number, title, type, priority, assignee, and story points.
- Selecting a card opens a right-side detail panel for description and editing.
- Initial filters are My tickets, assignee, type, and priority.
- Desktop uses four columns; mobile uses horizontal column navigation rather than squeezing four columns into the viewport.

### Backlog and sprint planning

The Backlog shows tickets without a sprint assignment. An `OWNER` can create one planning sprint, select backlog tickets for it, assign owners and estimates, and then start it. Starting freezes committed story points and makes the sprint the project's active sprint.

The first version permits at most one `ACTIVE` and one `PLANNING` sprint per project. Planning multiple future sprints is deferred.

### Ticket creation

Creation follows the current screen while keeping the destination visible and editable:

- creating from Board defaults to the active sprint;
- creating from Backlog defaults to no sprint;
- the creation modal allows the destination to be changed before submission.

### Sprint history

Sprint History uses a compact summary list rather than recreating a full read-only board. Each closed sprint shows its name, goal, date range, committed points, completed points, completion count, and rollover count. Opening a sprint shows its tickets with status at close, points at close, completion state, and a link to the current ticket.

Full ticket-field versioning is out of scope. History freezes sprint membership, close-time status, and close-time points; the current ticket remains the source for its title and description.

## 6. Sprint Lifecycle

### Creation

Every sprint is created with:

- name;
- goal;
- start date;
- end date.

Dates are chosen for each sprint rather than inherited from a fixed project cadence. The end date must be after the start date. A new sprint begins in `PLANNING`.

### Starting

Only an `OWNER` may transition `PLANNING → ACTIVE`. A project may have only one active sprint. Starting freezes `committed_points` from the tickets currently assigned to the sprint.

### Closing and rollover

Closing an active sprint requires an explicit next `PLANNING` sprint. This requirement replaces the earlier proposal to auto-create a next sprint with unspecified dates.

The user flow is:

1. Review completed points, unfinished tickets, and the rollover destination.
2. Select the single planning sprint. If it does not exist, create it first with dates.
3. Confirm close.
4. In one database transaction:
   - write one immutable `SprintTicketHistory` record per current sprint ticket;
   - freeze completed points;
   - increment aging/rollover data for unfinished tickets;
   - move unfinished tickets to the selected planning sprint while preserving their workflow status and original creation time;
   - mark the current sprint `CLOSED`.
5. Leave the next sprint in `PLANNING`; starting it remains a separate owner action.

Completed tickets stay associated with the closed sprint. Report generation runs only after the close transaction commits, so report failure cannot roll back or partially close a sprint.

Closing without a next sprint, closing an already closed sprint, or starting while another sprint is active returns a conflict without changing data. Any rollover failure rolls back the entire close transaction.

## 7. Shared API and MCP Design

Web forms and MCP tools are thin clients over the same service functions and authorization rules.

### API operations

- `GET /api/v1/projects/{slug}/sprints` — list planning, active, and closed sprints;
- `POST /api/v1/projects/{slug}/sprints` — create a planning sprint (`OWNER`);
- `PATCH /api/v1/sprints/{id}` — update planning details or start a sprint (`OWNER`);
- `POST /api/v1/sprints/{id}/close` with required `next_sprint_id` — close and roll over (`OWNER`);
- `GET /api/v1/sprints/{id}` — sprint detail and frozen totals;
- `GET /api/v1/sprints/{id}/history` — close-time ticket records.

Project-scoped routes consistently use immutable project slugs.

### MCP tools

- `list_sprints` — find the active, planning, or prior sprint with a lean response;
- `create_sprint` — create a dated planning sprint;
- `start_sprint` — activate the planning sprint;
- `close_sprint` — close the active sprint with a required next sprint ID.

For a request such as "create next week's sprint and close the current one," the MCP client calls `create_sprint` and then `close_sprint`. Keeping these as separate operations makes each tool independently useful and avoids a branching all-in-one close payload.

## 8. Errors and Feedback

- Invalid dates return field-level `422` feedback in the web form and structured errors through MCP.
- State conflicts return `409` and preserve the current sprint state.
- Permission failures return `403` for authenticated non-owners.
- Missing or inaccessible project data follows the existing project visibility rules.
- The web UI disables duplicate submissions and shows progress during start and close operations.
- MCP returns concise next-action guidance, such as creating a planning sprint before closing, without retrying a state-changing operation automatically.

## 9. Verification Criteria

- Login shows projects without an intermediate navigation step.
- A project opens its active sprint in one click from the sidebar.
- Board creation assigns the active sprint by default; Backlog creation remains unassigned by default.
- Only an `OWNER` can create, start, and close sprints through web, API, and MCP paths.
- Web and MCP operations produce identical sprint state and error outcomes.
- Starting a second active sprint fails without mutation.
- Closing without a planning destination fails without mutation.
- A forced error during rollover leaves the current sprint active and moves no tickets.
- Successful close preserves the closed sprint's membership, status-at-close, and point totals after tickets roll forward.
- The next sprint remains planning until explicitly started.
- Report-generation failure does not alter the completed close transaction.
- Mobile navigation exposes every project tab and board column without compressing the four-column board.

## 10. Deferred Work

- `VIEWER` or custom roles;
- multiple future planning sprints;
- fixed project cadence and automatic date calculation;
- complete ticket edit history;
- board/list view switching for closed sprints;
- cross-project My work dashboard;
- drag-and-drop, custom columns, command palette, dark mode, and theme customization.

