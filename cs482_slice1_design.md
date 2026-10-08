# Kanban Flow — Slice 1 Design

**Date:** 2026-09-14
**Status:** Current. Where this document and `cs482_workflow.md` disagree, **this document wins**. `cs482_workflow.md` is retained as the original product specification and is not being edited; the decision table in §1 names every point it supersedes.
**Scope:** The first implementation slice defined in `cs482_workflow.md` §10, plus the resolution of open specification questions that affect the slice 1 schema and route shape.

---

## 1. Decisions confirmed

These were resolved by review of `cs482_workflow.md`. The final column names what each decision supersedes in that document.

| ID | Decision | Supersedes |
|---|---|---|
| **D-01** | Plan and build **slice 1 only**. Sprints, velocity, rollover, AI reporting, GitHub inbound, and PAT issuance are slice 2. | — (§10 already scoped this) |
| **D-02** | **HTMX drives requests; Alpine.js holds pure UI state only** (modal open/closed). Web HTML routes are **separate** from the `/api/v1/*` JSON routes: the web route returns a rendered HTML fragment, the API route returns JSON, and both call the same service function. | §7, which implied the web UI consumes `/api/v1/*`; §9's undecided "Alpine.js/HTMX" |
| **D-03** | Chat notifications fire on exactly two events: **`TICKET_CREATED`** and **`TICKET_DONE`**. **No duplicate suppression** — a `DONE → IN_PROGRESS → DONE` round trip sends two cards. | Fills a gap; §6/FR-06 never listed trigger events |
| **D-04** | Every project-scoped route is keyed by **`{slug}`**, never `{project_id}`. **`slug` is immutable** — it is derived from `name` at creation and never changes, even when `name` is edited. | §7, which mixed `{slug}` and `{project_id}`; §6, which left rename behavior undefined |
| **D-05** | Sprint close **auto-creates or resolves the rollover target and moves unfinished tickets into it, but leaves that sprint in `PLANNING`**. Promotion to `ACTIVE` stays a manual OWNER action. Rationale: `committed_points` freezes at activation, so auto-activating at close would freeze the baseline against rolled-over tickets alone and permanently exclude anything added during planning, inflating every subsequent velocity reading. What ADR-005 wanted to eliminate was Jira's prompt for a *destination*; that is still eliminated. | §4 Workflow 3 step 2c; §6 Sprint rules; ADR-005's consequence column |
| **D-06** | **No `delayed_days` snapshot** in `SprintTicketHistory`. The live value on the ticket serves the board badge; the historical value survives as prose in the generated report. | Confirms §6 as written; closes the question |
| **D-07** | Report endpoints split: `POST /report/generate` is **server-side generation only** and returns `409` when `ANTHROPIC_API_KEY` is absent. The MCP fallback reads **`GET /api/v1/sprints/{id}/report/payload`** and writes the draft back through the existing `PUT /report` with `generated_by = MCP_CLIENT`. The payload endpoint doubles as the fixture the §8 grounding test compares against. | §8's claim that the MCP path posts to `/report/generate` |
| **D-08** | GitHub inbound webhooks use a **per-project URL**: `POST /api/v1/webhooks/github/{slug}`. The signature is verified against that one project's `github_secret`. | §7's single global `/api/v1/webhooks/github` |
| **D-09** | Slice 1 runs **locally only** (`docker compose up`). Outbound chat webhooks need no public address. Deployment happens in slice 2, when the GitHub inbound webhook and the MCP client first require a public HTTPS endpoint. | Sequences §9's hosting requirement |
| **D-10** | Tooling: **uv** for dependencies, **ruff** for lint/format, **pytest**, **alembic** from the first commit, **pydantic-settings** over a `.env` file. | Fills a gap |
| **D-11** | CSRF: a **session-bound signed token in a hidden form field**, validated on every HTML route `POST`. `/api/v1/*` authenticates by Bearer PAT (slice 2) or session cookie plus an `Origin` check. | Fills a gap; CSRF was unaddressed |
| **D-12** | Sprint-related ticket columns (`sprint_id`, `rollover_count`, `delayed_days`, `first_sprint_entered_at`) and `Project.github_secret` are **not created in slice 1**. They arrive as an alembic migration in slice 2. | Sequences §6 |

---

## 2. Slice 1 scope

### In
- User registration and login with `name`, `email`, `password_hash`; signed session cookie.
- SQLite initialization in WAL mode with foreign keys enforced.
- Project creation; the creator becomes `OWNER` in the same transaction.
- Member management by email (add, change role, remove) — required because authorization is tested in this slice.
- Ticket creation through both the JSON API and the web modal, with atomic per-project `ticket_number` allocation.
- Ticket status transitions (needed by D-03's `TICKET_DONE` event), maintaining `completed_at` and `resolution_notes`.
- Kanban board render: four fixed columns, sanitized Markdown descriptions.
- Background chat webhook dispatch to Slack, Discord, and Teams.

### Out (slice 2)
Sprints and the sprint lifecycle, velocity, `SprintTicketHistory`, rollover, AI sprint reports, GitHub inbound webhooks, `ApiToken` / PAT issuance, the MCP server, deployment, and rate limiting.

---

## 3. Module layout

```
app/
  main.py            FastAPI app, lifespan, router mounting
  config.py          pydantic-settings, reads .env
  db.py              engine, session dependency, per-connection PRAGMA listener
  models.py          SQLModel tables
  schemas.py         request/response models
  auth.py            bcrypt hashing, signed session cookie, CSRF token,
                     current_user / require_member / require_owner dependencies
  services.py        create_ticket(), set_status() — shared by web and API routers
  notifications.py   dispatcher plus the three payload formatters
  rendering.py       markdown-it-py (HTML disabled) -> bleach allow-list, exposed as a Jinja filter
  routers/
    api_auth.py  api_projects.py  api_tickets.py  web.py
  templates/
    base.html  login.html  register.html  dashboard.html  board.html
    partials/ticket_card.html  partials/ticket_modal.html
  static/
alembic/
tests/
docker-compose.yml
```

**Why `services.py` exists.** D-02 gives ticket creation two entry points. `ticket_number` allocation and notification scheduling must exist in exactly one place, so the routers do authentication and serialization only and both delegate to the same function. This is the one shared-logic module in the slice; adding more service modules before there is a second consumer would be premature.

**Why the three formatters live inside `notifications.py`.** Each is a function returning one dict. Splitting them across files costs more to navigate than it saves.

---

## 4. Schema

Four tables. Field lists are complete for slice 1; D-12 columns are absent by design.

### User
`id` UUID PK · `name` VARCHAR(50) · `email` VARCHAR(255) unique, stored lowercased · `password_hash` VARCHAR(255) bcrypt · `created_at` TIMESTAMP UTC

Deleting a user is unsupported. All timestamps across the schema are UTC-aware.

### Project
`id` UUID PK · `name` VARCHAR(100) · `slug` VARCHAR(50) globally unique, **immutable** · `webhook_type` ENUM(`NONE`,`TEAMS`,`SLACK`,`DISCORD`) default `NONE` · `webhook_url` VARCHAR(500) nullable · `next_ticket_number` INTEGER default 1 · `created_at` TIMESTAMP

- `slug` is derived from `name` at creation: lowercased, non-alphanumerics collapsed to `-`, trimmed to 50 characters. A collision returns `409` naming the conflicting slug; no suffix is appended silently.
- Editing `name` later does not change `slug` (D-04).
- `webhook_url` must be present and `https` whenever `webhook_type != NONE`, else `422`.

### ProjectMember
`id` UUID PK · `project_id` UUID FK · `user_id` UUID FK · `role` ENUM(`OWNER`,`MEMBER`) · `joined_at` TIMESTAMP

- Composite unique on `(project_id, user_id)`.
- The creator is inserted as `OWNER` in the project-creation transaction.
- A project must retain at least one `OWNER`; removing or demoting the last one returns `409`.

### Ticket
`id` UUID PK · `ticket_number` INTEGER, per-project sequential · `project_id` UUID FK · `title` VARCHAR(255) non-empty after trimming · `description` TEXT Markdown, max 20,000 chars · `type` ENUM(`STORY`,`BUG`,`DEMO_REQUEST`,`TASK`) · `status` ENUM(`BACKLOG`,`SELECTED`,`IN_PROGRESS`,`DONE`) · `priority` ENUM(`LOW`,`MEDIUM`,`HIGH`,`URGENT`) default `MEDIUM` · `story_points` INTEGER nullable, constrained to `{1,2,3,5,8,13}` · `creator_id` UUID FK · `assignee_id` UUID FK nullable · `resolution_notes` TEXT nullable · `completed_at` TIMESTAMP nullable · `meta` JSON · `created_at` TIMESTAMP immutable

- Status transitions are **unrestricted**; the only validation is enum membership.
- `completed_at` is set when `status` becomes `DONE` and cleared when it moves away. `resolution_notes` is **not** cleared on that move — it is the author's text, and losing it on an accidental status change is worse than leaving a stale note that is editable.
- `assignee_id` must reference a `ProjectMember` of the same project, else `422`.
- `meta` is rejected with `422` if it is not a JSON object, nests deeper than 3 levels, or serializes above 8 KB.
- `description` is stored raw and sanitized at render time only, so the Markdown source stays editable.
- Creation does **not** accept `sprint_id`; sprints do not exist in this slice.

---

## 5. Route contract

### Web routes (HTML)
- `GET /login`, `GET /register` — authentication views.
- `GET /dashboard` — the caller's project list and a project creation form.
- `GET /projects/{slug}` — the board: four columns of rendered ticket cards, plus the `[+ New Ticket]` modal.
- `POST /projects/{slug}/tickets` — modal submission. Returns **one rendered `ticket_card.html` fragment**, which HTMX inserts with `hx-swap="afterbegin"` into the `BACKLOG` column. No full page reload.
- `POST /projects/{slug}/tickets/{ticket_number}/status` — board dropdown. Returns the re-rendered card fragment.

All five `POST` routes require the CSRF hidden field (D-11).

### API routes (JSON, `/api/v1`)
- `POST /auth/register` — `409` on duplicate email. `POST /auth/login` — `401` with identical message and timing for unknown email and wrong password. `POST /auth/logout`.
- `GET /projects` · `POST /projects` (`409` on slug collision) · `GET /projects/{slug}` including the caller's role · `PATCH /projects/{slug}` (name, webhook settings; **OWNER**) · `DELETE /projects/{slug}` (**OWNER**, requires `?confirm=<slug>`).
- `GET /projects/{slug}/members` · `POST /projects/{slug}/members` by email (**OWNER**; `404` unknown user, `409` already a member) · `PATCH /projects/{slug}/members/{user_id}` (**OWNER**; `409` if it would remove the last owner) · `DELETE /projects/{slug}/members/{user_id}` (**OWNER**; their tickets keep `assignee_id = NULL`).
- `GET /projects/{slug}/tickets` — board query, filters `status`, `assignee_id`, `type`, `priority`; `limit` default 50 max 200, plus `cursor`.
- `GET /tickets/{id}` · `POST /tickets` · `PATCH /tickets/{id}` · `PATCH /tickets/{id}/status` (accepts optional `resolution_notes`) · `DELETE /tickets/{id}` (**OWNER**).

### Error conventions
`401` unauthenticated · `403` authenticated but insufficient role · `404` absent **or** the caller is not a project member · `409` state conflict · `422` validation failure.

---

## 6. Authentication, CSRF, authorization

- **Session:** an `itsdangerous`-signed cookie carrying `user_id`, `HttpOnly`, `SameSite=Lax`, and `Secure` when not running locally.
- **CSRF:** a token bound to the session, emitted into every HTML form as a hidden field and validated by a dependency on `routers/web.py`. JSON routes are exempt because slice 2 authenticates them by Bearer PAT; until then a cookie-authenticated JSON request is additionally checked against `Origin`.
- **Authorization:** `require_member(slug)` and `require_owner(slug)` resolve the project and the caller's `ProjectMember` row. A non-member receives `404` on reads and `403` on writes, so project existence never leaks. Slice 1 permissions follow `cs482_workflow.md` §6 restricted to the entities that exist here: every member may create, edit, and transition tickets; only an `OWNER` may edit project settings, manage membership, delete the project, or delete a ticket.

---

## 7. `ticket_number` allocation and SQLite configuration

A SQLAlchemy connection event applies `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, and `busy_timeout=5000` to every connection. `foreign_keys` defaults to OFF in SQLite; without this the foreign keys in §4 are decorative.

Allocation happens in the same transaction as the ticket insert:

```sql
UPDATE project SET next_ticket_number = next_ticket_number + 1
WHERE id = :project_id RETURNING next_ticket_number - 1
```

`MAX(...) + 1` is rejected because two concurrent readers can observe the same maximum. The `UPDATE` takes a write lock that serializes competing creations, and `busy_timeout` absorbs the wait. A failed insert rolls the counter back with it, preserving the no-gaps property.

The application runs under a single Uvicorn worker. SQLite permits one writer; additional workers produce `database is locked` under concurrent writes. Database-touching routes are declared `def`, not `async def`, so the synchronous session runs in FastAPI's threadpool rather than blocking the event loop.

---

## 8. Notification dispatch

```python
def schedule(tasks: BackgroundTasks, project: Project, event: str, ticket: Ticket) -> None
def dispatch(webhook_type: str, webhook_url: str, payload: dict) -> None
```

- `schedule` freezes everything the delivery needs into a **plain dict** at scheduling time. No ORM object crosses into the background task, so a closed session can never trigger a lazy load there, and `dispatch` has no database dependency at all.
- Formatters are a `webhook_type -> callable` mapping producing Slack Block Kit, Discord Embeds, or Teams Adaptive Cards. The transport is one identical `POST` of JSON in all three cases.
- Delivery uses a synchronous `httpx` client with a 5-second per-attempt timeout and three retries at 1s, 2s, and 4s. Final failure writes one `WARNING` naming `project_id` and `ticket_number`.
- `BackgroundTasks` run after the response is sent, therefore after commit. There is no code path by which a chat outage can reverse a ticket mutation.
- Events are `TICKET_CREATED` and `TICKET_DONE` (D-03). Delivery is at-most-once; failures are visible only in logs, and the slice ships no delivery-status UI.

---

## 9. Markdown rendering

`markdown-it-py` with raw HTML disabled, then `bleach` with an allow-list, exposed as a Jinja filter and applied to `description` at render time. Never applied on write.

---

## 10. Validation

Tests run against a **file-backed** SQLite database in `tmp_path`. An in-memory database reproduces neither WAL, nor foreign key enforcement, nor lock contention — precisely the three things this slice needs to demonstrate.

| Test | Covers | Pass condition |
|---|---|---|
| 50 concurrent ticket creations in one project | V-1, FR-12 | Zero `database is locked`; 50 distinct consecutive numbers; measured p95 ~70-125 ms on a MacBook Air (Apple M1, macOS 15.7.3) across repeated runs — budget raised from 50 ms to under 1 s because 50-way contention on SQLite's single writer adds lock queueing plus per-thread scheduling overhead (serial, non-concurrent baseline: ~0.45 ms/write) |
| Ticket creation with `webhook_url` pointed at a black-hole endpoint | V-4, FR-06 | `201` returned under 500 ms; ticket persisted; exactly one `WARNING` after retries |
| (non-member, MEMBER, OWNER) × every mutating endpoint | V-8, FR-10 | Every cell matches the permission matrix |
| Description containing `<script>` and `<img onerror=...>`, rendered on the board | V-9, FR-11 | Rendered as inert text; no execution |
| Duplicate-email registration; successful login | FR-01 | `409`; session cookie issued |
| `meta` above 8 KB; `meta` nested 4 levels | FR-03 | `422` in both cases |
| Board query and render over a seeded 5,000-ticket project | V-2 | Capped board queries (four status queries, 201 rows each) measured p95 4.40 ms (min 4.14 ms, median 4.18 ms, 50 runs), under the 20 ms query budget. The capped board response measured p95 157.97 ms (min 132.81 ms, median 155.91 ms, 20 runs) and 1.24 MB of HTML. |

V-2's figure is recorded rather than assumed, and replaces the unmeasured read-latency claim in `cs482_workflow.md` §2.

---

## 11. Deferred, with the trigger for revisiting

| Deferred | Revisit when |
|---|---|
| `sprint_id`, `rollover_count`, `delayed_days`, `first_sprint_entered_at`, `github_secret` columns | Slice 2 begins; added by alembic migration |
| Public deployment and HTTPS | The GitHub inbound webhook or the MCP client needs a reachable address |
| Rate limiting (`slowapi`) | The service is publicly reachable |
| Notification duplicate suppression | Round-trip status changes actually produce noise a team complains about |
| `delayed_days` history snapshot | A screen exists that would display delay trend |
