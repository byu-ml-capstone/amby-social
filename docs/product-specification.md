# Kanban Flow — Product Specification

## 1. Product scope

Kanban Flow is a collaborative project and sprint tracker exposed through a
server-rendered web app, a JSON API, and MCP tools. Projects contain members,
tickets, sprints, sprint history, and sprint reports. `OWNER`s administer the
project and sprint lifecycle. `MEMBER`s work with tickets and may plan tickets
into any `PLANNING` sprint.

This document is the canonical public contract. Historical design records under
`docs/superpowers/` are intentionally not amended.

## 2. Roles and authorization

- Project readers are members of that project; outsiders receive the existing
  not-found/forbidden behavior for read/write boundaries.
- Owners create projects, add generated sprints, edit planning goals, start and
  close sprints, manage membership, and delete projects.
- Members and owners may create and edit tickets and assign unscheduled tickets
  to any `PLANNING` sprint.
- Web mutations require CSRF validation. JSON and MCP mutations retain their
  existing session/token scope checks.

## 3. Project workflow

### Create a project

The web form displays a required native date input, defaults it to today, and
allows any valid past or future date. JSON API and MCP callers must provide an
ISO calendar `start_date`.

Creation is one transaction that writes:

1. the project with its immutable `start_date`;
2. an `OWNER` membership for the creator; and
3. one empty `PLANNING` sprint.

The initial sprint begins on the first Monday on or after the project date and
ends 13 days later on Sunday. A Monday project date remains unchanged. The
initial sprint is named from the application naming pattern, has a blank goal,
and has no tickets. Any failure rolls back all three records.

Project create, list, detail, and update responses include `start_date`.
Project updates expose no `start_date` field and reject unknown fields.

### Existing projects

The migration sets each existing project's date to `DATE(created_at)`, makes
the column non-null, and does not insert, rename, or redate any sprint.

## 4. Sprint planning workflow

An owner adds between one and five sprints at a time. The default count is one.
The operation is atomic and returns a list in JSON and MCP even when one sprint
is created. Legacy custom name, goal, and date inputs are rejected.

Generated sprints:

- are named with `SPRINT_NAME_PATTERN`, default `Sprint {number:02d}`;
- are numbered after the total count of existing sprint rows;
- have a blank goal and no tickets;
- last 14 calendar days, ending 13 days after their start; and
- start 14 days after the chronologically latest sprint's start.

For a legacy project with no sprint, generation starts on the first Monday on
or after the project's date. Invalid counts and naming configuration create no
rows.

Multiple `PLANNING` sprints may coexist. Canonical chronology everywhere is
`(start_date, end_date, id)` ascending. This includes anomalous legacy plans
whose dates precede an active sprint. JSON collections, lifecycle selection,
web fallback/current selection, planning cards, and destination lists use this
same order. The shell groups statuses `ACTIVE`, `PLANNING`, then `CLOSED` while
preserving canonical order inside each group.

Owners may edit only a planning sprint's goal. Blank goals are valid while
planning. Names and dates are immutable. Members may assign tickets to any
planning sprint.

## 5. Sprint start workflow

Starting uses `PATCH /api/v1/sprints/{id}` with `{"status":"ACTIVE"}` or the
owner-only web control. It succeeds only when:

- the sprint is `PLANNING`;
- its trimmed goal is nonblank;
- it is the canonical earliest planning sprint; and
- no sprint in the project is already `ACTIVE`.

The committed point total is frozen from the sprint's tickets when it starts.
A failed or conflicting start leaves the sprint in `PLANNING`. Only one sprint
can be active in a project at a time.

## 6. Sprint close workflow

Closing needs no destination identifier. JSON accepts `{}`; MCP accepts only
the sprint id; the web dialog contains no destination picker. Legacy
`next_sprint_id` JSON input is rejected with `422`.

Closing is atomic. It selects the canonical earliest planning sprint as the
destination, or creates the next generated sprint when no plan exists—even if
no ticket needs rollover. It snapshots the closing sprint's tickets, moves each
non-`DONE` ticket to the destination, freezes point totals and close review
fields, creates its pending report, and returns the closed sprint.

If closing fails or conflicts with another close, neither the destination,
ticket movement, history, report, nor sprint state is partially changed. A
repeated close receives a conflict.

## 7. Data model rules

### Project

- `id`, `name`, `slug`, and `key` retain their existing constraints.
- `start_date` is a non-null date and immutable through public updates.
- `next_ticket_number` remains the atomic per-project ticket allocator.

### Sprint

- `project_id` references `Project`.
- `name` is nonblank generated text up to 100 characters for new sprints.
- `goal` is non-null text up to 2,000 characters and may be blank only while
  planning.
- `status` is `PLANNING`, `ACTIVE`, or `CLOSED`.
- `end_date` must be after `start_date`.
- Multiple planning sprints are allowed; only one sprint may be active at a
  time.
- Committed/completed points and close review fields retain their existing
  lifecycle semantics.

### Ticket and sprint history

Tickets may reference an `ACTIVE` or `PLANNING` sprint. Their first sprint entry
and rollover/delay fields retain existing behavior. Close-time history is
immutable and unique per sprint/ticket pair.

## 8. Public JSON contracts

### Projects

- `POST /api/v1/projects`
  - request: `{"name":"Payments","start_date":"2026-09-23"}`
  - response: project including immutable `start_date`
  - missing/invalid date or unknown fields: `422`
- `GET /api/v1/projects` and `GET /api/v1/projects/{slug}` include
  `start_date`.
- `PATCH /api/v1/projects/{slug}` accepts the existing mutable project fields
  only; `start_date` and unknown fields are rejected.

### Sprints

- `GET /api/v1/projects/{slug}/sprints`
  - returns all statuses in canonical ascending chronology.
- `POST /api/v1/projects/{slug}/sprints`
  - request: `{"count":1}` where count is a strict integer from 1 through 5;
  - response: `list[SprintOut]`;
  - custom names/goals/dates and unknown fields: `422`.
- `GET /api/v1/sprints/{id}` returns the sprint.
- `PATCH /api/v1/sprints/{id}`
  - `{"goal":"..."}` edits a planning goal, including `""`;
  - `{"status":"ACTIVE"}` starts the sprint;
  - names, dates, other statuses, unknown fields, or mixed activation/detail
    changes are rejected.
- `POST /api/v1/sprints/{id}/close`
  - request: `{}`;
  - response: the closed sprint;
  - `next_sprint_id` and unknown fields: `422`.
- Existing history and report endpoints retain their response contracts.

## 9. Web contracts

- New-project form: name plus required editable date defaulting to today.
- Backlog: every planning sprint appears in canonical order with tickets and
  totals. Owners always see count-based Add sprint controls and per-plan goal
  controls. Only the earliest plan displays a start action.
- Owners edit goals through CSRF-protected forms; server validation enforces the
  same 2,000-character limit as JSON.
- Members and owners can bulk-assign selected unscheduled tickets to any plan.
- Close preview explains automatic destination selection/creation and accepts
  no destination id.

## 10. MCP contracts

MCP provides the same validation, authorization, and lifecycle behavior as the
JSON API.

- `create_project(name, start_date)` requires a date.
- `create_sprint(slug, count=1)` returns a list.
- `update_sprint(sprint_id, goal)` is goal-only.
- `start_sprint(sprint_id)` activates under the chronological rules.
- `close_sprint(sprint_id)` chooses or creates the destination automatically.
- Legacy sprint name/date creation and close destination parameters are absent
  from tool schemas.

## 11. Configuration and transaction guarantees

`SPRINT_NAME_PATTERN` is an application-wide setting. It accepts the integer
`{number}` placeholder and standard integer formatting; the default is
`Sprint {number:02d}`. Invalid patterns, blank names, and names over 100
characters are rejected without creating sprints.

Project creation, sprint batches, sprint start, and sprint close are atomic.
Invalid input, failures, and competing lifecycle requests do not leave partial
projects, sprint batches, rollovers, or duplicate active sprints.
