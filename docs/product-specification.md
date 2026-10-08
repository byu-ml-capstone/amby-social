# Kanban Flow — Product Specification

*Sections 1–7, per the C06 assignment. Sections 8–12 of the course template
(AI sprint-report behavior, non-functional/platform requirements, risks and
open questions, validation plan, decision log) are intentionally deferred to
later lectures, per the assignment's own instructions — they are not
attempted here beyond the forward-looking notes already required inside
Section 7 for the AI sprint-report draft endpoint.*

*This document is written against the actual, already-implemented
`kanbanflow` codebase (FastAPI + SQLModel + SQLite), not a from-scratch
proposal. Where the shipped code differs from an earlier planning document
(`cs482_workflow.md`) or from this template's own default assumptions, that
is called out explicitly as a recorded decision — never silently reconciled.
Marked **Assumption** and **Open question** call-outs appear inline, in the
section where they arise, rather than deferred to a section this assignment
does not require.*

---

## 1. Product vision

### Product name
**Kanban Flow**

### Problem statement
Existing project tracking platforms impose a friction cost that a small
engineering team pays every day: to update a board, a developer must leave
the editor, open a browser, find the card, and curate it by hand. Every tool
reviewed in §2 is browser-first — none of them can be driven from inside the
environment where the work actually happens — so board state drifts away
from repository state until the board stops being trusted. Separately, work
finished in git often never makes it back onto the board, non-technical
inbound requests (bug reports, demo requests) have no low-friction intake
path, and sprint closing, rollover accounting, and retrospective reporting
remain manual, easy-to-skip tasks.

### Product vision
A FastAPI-backed engineering workflow tracker with two first-class
interfaces onto one shared domain model: a server-rendered web dashboard for
anyone without an IDE (non-technical leads, faculty reviewers), and a Stdio
MCP server plus a JSON API for developers working from Cursor or Claude
Desktop. A ticket created from either interface, a status changed from
either interface, and a ticket closed by a `Closes #<n>` commit message all
converge on the same board — there is exactly one source of truth, not a
web copy and an IDE copy that can disagree.

### Intended users and stakeholders

| Person or group | Need or responsibility | How the app helps |
|---|---|---|
| **Software Engineers** | Track and update assigned work without leaving the development environment; avoid the context switch that makes boards go stale. | Query tickets and change status through MCP tools inside Cursor or Claude Desktop, or through the JSON API directly. A `Closes #<n>` in a commit message advances the board with no manual interaction. |
| **Team Lead / Project Owner** | Plan a sprint, keep exactly one iteration in flight, close it cleanly, and account for what did not finish. | Sprint create / activate / close is restricted to the `OWNER` role; closing rolls unfinished work forward into an already-prepared next sprint and freezes the velocity figures for the sprint that just ended. |
| **Non-Technical Leads & Ops** | File bug reports and demo requests, and see delivery status without an IDE, a terminal, or a git account. | A server-rendered dashboard shows the Kanban board, the backlog, and sprint history; a `[+ New Ticket]` modal files work directly into the backlog. |
| **Faculty Reviewers** | Evaluate sprint performance and velocity, and judge whether reported progress is real. | Per-sprint committed-vs-completed story points that never change retroactively, aging signals (`rollover_count`, `delayed_days`), and — once §6/§7's `SprintReport` ships — an AI-drafted retrospective a human finalizes. Invited as an ordinary project `MEMBER`; no separate reviewer role exists. |

**Non-users, stated explicitly:** multi-team organizations, external
customers (they reach the team through a lead, never directly), and anyone
requiring SSO or audit compliance. Every one of those is a reason the tools
reviewed in §2 carry weight this project does not need.

### Success criteria
Each item is written so it can be observed to fail, not just asserted as a
feeling.

- [x] An engineer can move a ticket from `IN_PROGRESS` to `DONE` entirely
  inside Cursor or Claude Desktop through an MCP tool call
  (`update_ticket_status`), with no browser open at any point. *Implemented.*
- [x] A commit message containing `Closes #<ticket_number>` transitions the
  matching ticket to `DONE` through the signed GitHub webhook, without a
  human touching the board. *Implemented* (`POST
  /integrations/github/webhook`, verified against
  `app/github_sync.py::process_github_delivery`).
- [x] Starting from an authenticated project page, a non-technical user can
  file a ticket through the modal in **5 interactions or fewer** (open
  modal, title, type, description, submit) and see it appear in `BACKLOG`.
  *Implemented*; not yet verified by an automated scripted walkthrough —
  **Open question:** worth a Playwright check before relying on this figure
  in a demo.
- [x] Closing a sprint moves every non-`DONE` ticket into the sprint named
  as `next_sprint_id`, preserving each ticket's `status`, incrementing
  `rollover_count`, and freezing `delayed_days`. Re-reading the closed
  sprint later returns identical figures — `completed_points` is written
  once, at close, and never recomputed. *Implemented*
  (`app/services.py::close_sprint`).
- [ ] Per-sprint `committed_points` and `completed_points` are visible
  through the API (`GET /api/v1/sprints/{id}` and the project's sprint
  list) and remain unchanged after later sprints close. *Implemented as
  data*; there is no dedicated `/velocity` aggregation endpoint — see §7's
  note on why one is not needed.
- [ ] The web dashboard renders an AI-generated sprint report draft with an
  editable interface; a reviewer can save the draft any number of times and
  finalize it in a separate, `OWNER`-only action. *Not yet implemented* —
  this is the `SprintReport` entity and endpoint specified fresh in §6/§7.
- [ ] Every ticket number, story-point total, and date appearing in a
  generated report is present in the JSON input given to the model,
  verified by an automated grounding test. *Not yet implemented*, tracked
  against the same not-yet-built feature.

---

## 2. Product research and decisions

### How these tools were inspected
Four tools from the assignment's list were inspected by reading each
product's public documentation and, for Linear, Trello, and GitHub
Projects, walking one story from creation to completion in a free-tier
project. Linear and Jira were examined most closely because they are the
two that actually implement sprint closure and velocity in the sense this
app needed; Trello was the minimal baseline, and GitHub Projects the
version-control-native case.

| Tool | What was inspected | Reference |
|---|---|---|
| Linear | Free workspace, one team, one cycle | <https://linear.app/docs> |
| Trello | Free board, three lists, a Butler rule | <https://trello.com/guide> |
| GitHub Projects (v2) + Issues | A personal project board over a test repository, closing keywords | <https://docs.github.com/en/issues/planning-and-tracking-with-projects> |
| Jira Software (Scrum template) | Free-tier cloud site, one Scrum project, one sprint started and completed | <https://www.atlassian.com/software/jira>, <https://support.atlassian.com/jira-software-cloud/> |

### Tool 1: Linear
- **Core workflow.** Keyboard-driven issue tracking organized into
  fixed-cadence **Cycles** that automatically roll unfinished issues into
  the next cycle — no prompt, no confirmation.
- **Projects & stories.** Issues carry a **team-scoped** identifier
  (`ENG-101`); `Project` is a separate, cross-team initiative concept, not
  the identifier prefix.
- **Sprint/board/status.** Default states `Backlog → Todo → In Progress →
  Done/Canceled`, teams may add custom states; **no transition graph** — any
  state may be entered directly from any other.
- **Reporting.** Burn-up charts and cycle-completion percentage; velocity is
  completed estimates per cycle.
- **Blockers/decisions.** First-class `blocks`/`blocked by` issue relations
  with a persistent badge; decisions live in comments and project status
  updates (`On Track` / `At Risk` / `Off Track`).
- **Useful for CS 482.** Automatic, non-interactive rollover; minimal entity
  complexity; the idea of a blocker signal (later simplified — see §3).
- **Unnecessary for the course MVP.** Multi-team hierarchies, enterprise
  SSO, SLA tracking, multi-project roadmaps.

### Tool 2: Trello
- **Core workflow.** Boards → Lists → Cards; the whole product is
  drag-a-card-rightward.
- **Projects & stories.** No issue type, no estimate field; structure is
  encoded into labels or checklists if a team wants it.
- **Sprint/board/status.** A **list is the status** — no field behind it, no
  sprint object at all; teams fake sprints with a "Sprint N" label that
  does not survive reporting.
- **Reporting.** None natively; requires a Power-Up.
- **Blockers/decisions.** Unstructured — a red label or a "Blocked" list,
  the latter destroying the information about which column the work
  actually stalled in.
- **Useful for CS 482.** The 4-column board is the right visual size; proof
  that an unconstrained status model is survivable.
- **Unnecessary for the course MVP.** Drag-and-drop as the primary
  interaction, Power-Ups, checklists as a first-class concept.

### Tool 3: GitHub Projects (v2) + Issues
- **Core workflow.** The status change that matters happens in git: a pull
  request or commit saying `Closes #12` closes the issue on merge. The
  board is a *consequence* of the repository, not maintained beside it.
- **Projects & stories.** Issues are numbered per repository; fields
  (`Status`, `Estimate`, `Iteration`) belong to the *project*, not the
  issue, so the same issue can carry different metadata in two projects.
- **Sprint/board/status.** No sprint object — an Iteration *field* only.
  `Status` is a plain single-select the board groups by; **no transition
  rules at all**.
- **Reporting.** A burn-up and a current-status chart; **no velocity
  concept**, because there is no sprint object to attribute points to.
- **Blockers/decisions.** Weakest of the four — label convention only.
  Decisions conventionally live as Markdown files in the repository.
- **Useful for CS 482.** The closing-keyword pattern, adopted essentially
  verbatim. Status-as-plain-enum with no guard rails.
- **Unnecessary for the course MVP.** Multiple saved views, cross-repository
  aggregation, the automation builder.

### Tool 4: Jira Software (Scrum board template)
- **Core workflow.** Backlog and board are separate screens. **Start
  sprint** requires a name, a goal, and dates; **Complete sprint** forces an
  explicit destination for unfinished issues — it refuses to end
  ambiguously.
- **Projects & stories.** Project-scoped sequential key (`PAY-123`); typed
  issues (Epic/Story/Task/Bug/Sub-task); first-class `Story Points`.
- **Sprint/board/status.** Board columns *map onto* statuses (several
  statuses can share a column); the transition graph is configurable with
  conditions and post-functions — **the only one of the four that can
  forbid a move.**
- **Reporting.** The richest by far: burndown, **velocity chart (paired
  committed-vs-completed story points per sprint — the canonical definition
  this app adopts)**, sprint report, cumulative flow, control chart.
- **Blockers/decisions.** Flagging marks an issue blocked *without* changing
  its status; typed issue links express dependency as data. Decisions live
  in linked Confluence pages, one product away from the work.
- **Useful for CS 482.** The Complete-sprint dialog's refusal to end
  ambiguously (automated further, see §3); the committed-vs-completed
  velocity definition, adopted verbatim; the project-scoped sequential key
  (mirrored today as `Project.key`, e.g. `KAN`).
- **Unnecessary for the course MVP.** Configurable workflow schemes, the
  Epic hierarchy, parallel sprints, every chart except the velocity
  numbers.

### Patterns to adopt
1. **Rollover that preserves in-flight status, not Jira's prompt.**
   Automatically carry unfinished tickets forward on close, preserving each
   ticket's `status` so `IN_PROGRESS` stays `IN_PROGRESS` across the
   boundary. **Decision, sharpened past the original plan:** rather than
   auto-creating the destination sprint invisibly, the implemented design
   requires the next sprint to already exist in `PLANNING` and be named
   explicitly at close time (`next_sprint_id`) — see §6/§7. This keeps
   Jira's "never end ambiguously" property while making the destination an
   explicit, inspectable choice instead of a side effect.
2. **GitHub's git-driven status change**, adopted essentially verbatim via
   a signed GitHub App webhook and `Closes #<n>` parsing.
3. **Trello's fixed column set, Linear/GitHub's free transitions.** Four
   column names as schema (`BACKLOG`, `SELECTED`, `IN_PROGRESS`, `DONE`),
   but no transition graph — any status may move to any other. Three of the
   four tools reviewed ship with no transition graph at all and their users
   are fine; only Jira can forbid a move, and that flexibility is the
   reason it needs an administrator.
4. **Single-table ticket with a bounded JSON extension column**, so
   webhook- and integration-sourced attributes don't force a migration per
   integration.

### Patterns to reject or simplify
1. **Configurable workflow engines** (Jira's transition graph, Linear's
   per-team workflow customization) — rejected; a course project has no
   administrator to configure them.
2. **Rich charting** (burndown, cumulative flow, control charts) —
   rejected; velocity is delivered as numbers, not a chart, because neither
   of this product's two delivery surfaces (IDE, chat) can render one.
3. **First-class blocker relations** (Linear's `blocks`/`blocked by`, Jira's
   flagging) — simplified, not fully rejected. A dependency graph needs its
   own entity and a board affordance to be worth the weight; at
   course-team scale, a single free-text `blocked_reason` field on the
   ticket (added later — see §6) captures the same signal cheaply, backed
   by `rollover_count`/`delayed_days` for the aging half of the story.
4. **Drag-and-drop as the primary interaction** — rejected in favor of
   status dropdowns/click-to-move, to avoid a client-side ordering model.
5. **Multi-tier RBAC** — rejected in favor of two roles, `OWNER` and
   `MEMBER`.
6. **In-app notification centers** — rejected in favor of outbound chat
   webhooks (Slack/Teams/Discord) as background tasks.

### Product decisions

| Decision | Alternatives considered | Choice | Reason |
|---|---|---|---|
| Backend & UI framework | Rust (Actix/Axum), Node.js, Next.js SPA | **Python (FastAPI) + Jinja2 templates** | One runtime for the REST API, the MCP server, and HTML rendering, with zero frontend build pipeline. |
| Database engine | PostgreSQL, DynamoDB | **Embedded SQLite (WAL mode)** | No network round-trip on reads, zero installation, trivial backup via file copy; WAL gives concurrent readers, a single writer — acceptable at team scale, forces a single Uvicorn worker. |
| Production hosting | AWS Lightsail, AWS Lambda | **Docker Compose on an AWS EC2 instance behind nginx**, deployed by `deploy/deploy.sh` | Matches the actual deployment scripts in this repository (`deploy/deploy.sh`, `deploy/nginx/kanbanflow.conf`); container rebuilds bind-mount the SQLite file so data survives a redeploy. |
| Authentication model | OAuth2 social login, magic links | **3-field password auth** (`name`/`email`/`password_hash`) for the web session, plus a separately issued, hashed, **expiring** (90-day) Bearer PAT for the API/MCP | Fast onboarding for a course team; PATs are revocable and time-boxed independent of the web session. |
| Ticket modeling | Class-table inheritance per ticket type | **Single `Ticket` table with a bounded JSON `meta` column** (≤ 8 KB, ≤ 3 levels deep) | One query serves the whole board regardless of type; `meta` absorbs integration data (e.g. a linked GitHub artifact) without a migration per source. |
| Unfinished sprint tickets | Return to backlog, auto-create the next sprint invisibly, drop entirely | **Roll into an existing `PLANNING` sprint, named explicitly by the closing request** | Keeps exactly one sprint in each of `ACTIVE` and `PLANNING` per project as an enforced database invariant, and makes "what will unfinished work roll into" an inspectable choice rather than a hidden side effect. |
| Project identity | UUID-only | **Slug** (URL identity, e.g. `/projects/payment-gateway`) **and a short `key`** (Jira-style, e.g. `KAN`, auto-derived and de-duplicated) | The slug supports readable URLs; the key exists for a future ticket-reference format and standup shorthand, mirroring Jira's `PAY-123`. |
| Board interaction | Drag-and-drop only | **Both** a status dropdown/detail-view transition **and** drag-and-drop with a persisted `backlog_rank` | Drag-and-drop was reconsidered after the initial "reject drag-and-drop" decision (§2 Patterns to reject) once the backlog needed a stable manual order; the dropdown remains for anyone who prefers it or is on a small screen. **This supersedes the earlier blanket rejection — recorded here rather than left as a silent contradiction.** |

---

## 3. MVP scope

### In scope
- [x] **Projects** — multi-project boundaries identified by a unique slug
  and a short key; membership via `OWNER`/`MEMBER` roles.
- [x] **User stories** (implemented as `Ticket`) — typed
  (`STORY`/`BUG`/`DEMO_REQUEST`/`TASK`), Markdown description, optional
  Fibonacci story points, priority, due date, assignee, free-text
  `blocked_reason`, bounded JSON `meta`.
- [x] **Sprint board** — four fixed columns (`BACKLOG`, `SELECTED`,
  `IN_PROGRESS`, `DONE`) with unrestricted transitions between them, plus a
  manually orderable backlog (`backlog_rank`, drag-and-drop **and** a
  status control).
- [x] **Sprint lifecycle** — create (`PLANNING`) → activate (`ACTIVE`,
  freezes `committed_points`) → close (`CLOSED`, rolls unfinished tickets
  into a pre-existing `PLANNING` sprint, freezes `completed_points`). At
  most one `ACTIVE` and one `PLANNING` sprint per project, enforced by a
  database constraint, not just application logic.
- [~] **Sprint reporting** — a lightweight, human-authored version ships
  today (`Sprint.goal_achieved`, `Sprint.review_notes`, captured at close).
  The AI-drafted retrospective (`SprintReport`, editable draft, `OWNER`-only
  finalize) is **specified in §6/§7 but not yet implemented** — this is the
  one required capability this document designs ahead of the code.
- [x] **Velocity / progress tracking** — `committed_points` frozen at
  activation, `completed_points` frozen at close, surfaced through the
  sprint list and sprint-detail endpoints (see §7 for why no separate
  aggregation endpoint exists).
- [x] **Other:** GitHub-driven ticket closure (signed webhook, `Closes
  #<n>`), a Stdio MCP server mirroring the JSON API for IDE use, outbound
  chat notifications (Slack/Teams/Discord) as retried background tasks,
  revocable/expiring API tokens, ticket comments with @mentions.

### Explicitly out of scope
- Email/Gmail inbound triage — GitHub inbound is one signed endpoint and a
  parser; email intake needs OAuth, delivery infrastructure, and an
  address-to-project mapping model, effectively a second product.
- Bitbucket/GitLab inbound integration (GitHub only).
- Burndown charts, cumulative flow diagrams, control charts, epic
  burndowns. Velocity is numbers and a table.
- Work-in-progress (WIP) column limits.
- In-app notification centers / read-unread inboxes.
- Direct binary file/attachment storage (link external URLs, commits, PRs
  instead).
- Multi-tier hierarchical RBAC beyond `OWNER`/`MEMBER`.
- A first-class dependency graph between tickets (`blocks`/`blocked by`
  relations) — the simplified `blocked_reason` free-text field stands in
  for it; revisit if a team reports a blocker the aging metrics
  (`rollover_count`, `delayed_days`) fail to surface.
- Sub-tasks/checklists inside a ticket — a ticket too large to track as one
  unit should be split into two tickets.

### Deferred or optional ideas
- A dedicated `/velocity` aggregation endpoint, if a client needs the
  series pre-computed rather than derived from the sprint list.
- Exposing chat-webhook configuration (`ProjectChatWebhook`) through the
  JSON API — it exists today only behind the web settings page.
- A scripted UI walkthrough proving the "5 interactions or fewer" ticket
  creation claim in §1, rather than relying on manual testing.

---

## 4. Key user workflows

### Workflow 1: Create a project and invite a team
- **Actor:** Any authenticated user, becoming `OWNER`.
- **Starting condition:** User is registered and logged in.
- **Steps:** (1) `POST /api/v1/projects` with a `name`. (2) The service
  derives a unique `slug` and a short `key`, and inserts the caller as
  `OWNER` in the same transaction. (3) The owner adds teammates by email via
  `POST /api/v1/projects/{slug}/members`.
- **Expected result:** A project exists with exactly one `OWNER`, reachable
  at `/projects/{slug}`.
- **Failure/edge cases:** A blank or over-length name → `422`. A name that
  derives an already-taken slug → `409` naming the conflicting slug. Adding
  a member whose email has no account → `404`; adding an existing member
  again → `409`.

### Workflow 2: Author and estimate a user story
- **Actor:** Any project `MEMBER` or `OWNER`.
- **Starting condition:** Project exists; actor is a member.
- **Steps:** (1) `POST /api/v1/tickets` with `slug`, `title`, and
  optionally `type`, `description`, `priority`, `story_points`, `due_date`,
  `assignee_id`. (2) A per-project sequential `ticket_number` is allocated
  atomically. (3) The ticket is created in `BACKLOG` with `sprint_id =
  null` unless one was supplied.
- **Expected result:** The ticket appears in the project's backlog,
  unscheduled, optionally estimated.
- **Failure/edge cases:** Blank title → `422`. `story_points` outside
  `{1,2,3,5,8,13}` → `422`. `assignee_id` who is not a project member →
  `422`. `meta` deeper than 3 levels or over 8 KB serialized → `422`.

### Workflow 3: Sprint planning (prepare the next sprint before it starts)
- **Actor:** `OWNER`.
- **Starting condition:** No `PLANNING` sprint exists yet for the project
  (at most one is allowed at a time).
- **Steps:** (1) `POST /api/v1/projects/{slug}/sprints` with `name`,
  `goal`, `start_date`, `end_date` — created in `PLANNING`. (2) Backlog
  tickets are pulled in via `PATCH /api/v1/tickets/{id}` with `sprint_id`
  set to the planning sprint's id (this also stamps
  `first_sprint_entered_at` the first time it happens, never again). (3)
  When ready, `PATCH /api/v1/sprints/{id}` with `{"status": "ACTIVE"}` —
  this freezes `committed_points` as the sum of story points on tickets
  currently in the sprint.
- **Expected result:** Exactly one `ACTIVE` sprint with a frozen
  commitment baseline.
- **Failure/edge cases:** A second `PLANNING` sprint request → `409`. A
  second `ACTIVE` sprint request → `409`. `sprint_id` pointing at a
  `CLOSED` sprint, or a sprint in another project → `422`.

### Workflow 4: Move a story through the board
- **Actor:** Any project member.
- **Starting condition:** Ticket exists in the caller's project.
- **Steps:** `PATCH /api/v1/tickets/{id}/status` with the new `status` and
  an optional `resolution_notes`.
- **Expected result:** Status changes to any of the four values, in either
  direction, with no transition graph to violate. Entering `DONE` stamps
  `completed_at`; leaving it clears that stamp; re-affirming `DONE` does not
  restamp it. A completion event schedules a chat notification as a
  background task.
- **Failure/edge cases:** Non-member on a project write → `403`.
  **Explicit design decision (deviates from a naive "backlog removes from
  sprint" assumption — see §5 Workflow rules):** moving status to
  `BACKLOG` does **not** clear `sprint_id`; a ticket can be `BACKLOG` and
  still belong to the active sprint, or `IN_PROGRESS` with no sprint at
  all. The two are independent axes by design.

### Workflow 5: Developer resolves a ticket via IDE MCP
- **Actor:** Software Engineer, working in Cursor or Claude Desktop.
- **Starting condition:** The engineer has issued a PAT
  (`POST /api/v1/tokens`) and configured the Stdio MCP server with it.
- **Steps:** (1) The engineer prompts the editor to mark a ticket done. (2)
  The MCP tool `update_ticket_status` calls the same JSON endpoint as
  Workflow 4, authenticated with `Authorization: Bearer <PAT>`. (3) The
  chat channel receives a completion card, dispatched out-of-band.
- **Expected result:** The board updates with no browser open at any point.
- **Failure/edge cases:** A webhook delivery failure never reverses the
  ticket update — outbound chat dispatch is a retried background task, not
  part of the request's transaction.

### Workflow 6: Non-technical stakeholder files a ticket via the web
- **Actor:** Product Manager, Sales Lead, or Faculty Reviewer.
- **Starting condition:** Logged into the web dashboard.
- **Steps:** (1) Navigate to `/projects/{slug}`. (2) Open the `[+ New
  Ticket]` modal. (3) Fill title, type (e.g. `DEMO_REQUEST`), description;
  submit.
- **Expected result:** Ticket appears in `BACKLOG`, unscheduled
  (`sprint_id = null`, `story_points = null`) until an `OWNER` handles it in
  Workflow 3.
- **Failure/edge cases:** A submitter who is not a project member is
  rejected before the modal can post.

### Workflow 7: GitHub commit closes a ticket
- **Actor:** GitHub (machine), on behalf of an engineer's push.
- **Starting condition:** The project's repository is connected through the
  GitHub App installation flow (`GitHubInstallation` /
  `ProjectGitHubRepository`).
- **Steps:** (1) An engineer pushes a commit containing `Closes #42`. (2)
  GitHub `POST`s to `/integrations/github/webhook`; the signature
  (`X-Hub-Signature-256`) is verified against the app-level webhook secret.
  (3) `app/github_sync.py::process_github_delivery` resolves the ticket
  within the connected project and advances it, recording the linked
  artifact (`GitHubArtifact`/`TicketGitLink`).
- **Expected result:** Board state reflects merged work with zero human
  board interaction.
- **Failure/edge cases:** An invalid signature → `401`. A delivery already
  processed (`IntegrationDelivery` de-duplication by `delivery_id`) is a
  no-op, so GitHub's automatic retries cannot double-apply an event.

### Workflow 8: Close a sprint, preserving history and rolling over
- **Actor:** `OWNER`.
- **Starting condition:** An `ACTIVE` sprint exists; a `PLANNING` sprint has
  already been prepared (Workflow 3) as the rollover destination.
- **Steps:** (1) `POST /api/v1/sprints/{id}/close` with
  `{"next_sprint_id": "<planning sprint id>"}`. (2) Within one transaction:
  a `SprintTicketHistory` row is written per ticket (`status_at_close`,
  `story_points_at_close`, `was_completed`); `completed_points` is summed
  over `DONE` tickets and frozen on the closing sprint; every non-`DONE`
  ticket is moved to the named next sprint with its `status` preserved,
  `rollover_count` incremented, and `delayed_days` computed from
  `first_sprint_entered_at` and frozen; the sprint flips to `CLOSED`.
- **Expected result:** Velocity and per-ticket close state are preserved
  immutably; unfinished work is visibly carried forward in-flight.
- **Failure/edge cases:** Closing an already-`CLOSED` sprint, or naming a
  `next_sprint_id` that is not `PLANNING` or not in the same project → `409`.
  Close is **not idempotent by design** — re-running it would
  double-increment `rollover_count`.

### Workflow 9 (specified, not yet implemented): Generate, review, and finalize an AI sprint report
- **Actor:** `OWNER` (finalize) / any member (draft, edit).
- **Starting condition:** A sprint has just closed (Workflow 8 completed).
- **Steps:** See §7's `POST /api/v1/sprints/{id}/report/generate`,
  `PUT .../report`, `POST .../report/finalize`.
- **Expected result:** A structured, editable retrospective draft is
  produced and, once an `OWNER` is satisfied, finalized and made
  read-only.
- **Failure/edge cases:** See §7. Detailed AI behavior, grounding checks,
  and the evaluation plan are explicitly deferred to a later lecture per the
  assignment; only the API surface is committed to here.

### Workflow checklist
- [x] Create and view a project
- [x] Create and manage a user story
- [x] Select a story for the current sprint
- [x] Move a story through the workflow
- [x] Close a sprint while preserving history
- [ ] Generate, review, and edit a sprint report — **specified in §6/§7,
  not yet implemented**
- [x] View planned and completed work (sprint list + sprint history +
  `GET /api/v1/tickets` filters)

---

## 5. Functional requirements

| ID | Requirement | Priority | Related workflow | Acceptance evidence |
|---|---|---|---|---|
| FR-01 | The system must register and log in users with `name`, `email`, `password_hash`, issuing an HTTP-only session cookie. | Must | Auth | `POST /api/v1/auth/register` and `/login` succeed; duplicate email → `409`; wrong password → `401` with an identical message for unknown-email and wrong-password. |
| FR-02 | The system must issue, list, and revoke API tokens (PATs) independent of the web session, expiring after 90 days. | Must | Workflow 5 | `POST /api/v1/tokens` returns plaintext exactly once; `DELETE /api/v1/tokens/{id}` is idempotent; a request bearing a revoked or expired token → `401`. |
| FR-03 | The system must manage tickets with a bounded JSON `meta` extension field and a fixed `type` enum. | Must | Workflow 2 | `meta` over 8 KB or nested past 3 levels → `422`. |
| FR-04 | The system must allocate `ticket_number` uniquely and gaplessly per project under concurrent creation. | Must | Workflow 2 | An atomic `UPDATE ... RETURNING` inside the creating transaction, not `MAX(...) + 1`. |
| FR-05 | Closing a sprint must move every non-`DONE` ticket into an explicitly named, existing `PLANNING` sprint in the same project, preserving `status` and `created_at`, incrementing `rollover_count`, and freezing `delayed_days`. | Must | Workflow 8 | Rolled ticket shows the new `sprint_id`, unchanged `status`, `rollover_count + 1`, and a `delayed_days` value identical on re-read. Naming a non-`PLANNING` sprint or one from another project → `409`/`422`. |
| FR-06 | The system must enforce at most one `ACTIVE` and at most one `PLANNING` sprint per project, at the database level. | Must | Workflow 3 | A partial unique index on `(project_id)` filtered by status; a second attempt of either → `409`, including under a concurrency race (`IntegrityError` fallback). |
| FR-07 | The system must dispatch outbound chat notifications (Slack/Teams/Discord) as background tasks that never block or reverse the triggering mutation. | Must | Workflow 4, 5, 6 | Pointing the configured webhook URL at an unreachable endpoint still returns the ticket mutation successfully. |
| FR-08 | The system must expose `committed_points` (frozen at sprint activation) and `completed_points` (frozen at sprint close) so that velocity for a closed sprint never changes on re-read. | Must | Workflow 3, 8 | `GET /api/v1/sprints/{id}` returns identical figures before and after later sprints in the same project close. |
| FR-09 | The system must accept authenticated GitHub webhook deliveries and advance tickets referenced by `Closes #<n>`, de-duplicating by delivery id. | Must | Workflow 7 | An invalid signature → `401`; a re-delivered event id is a no-op, not a double-apply. |
| FR-10 | The system must enforce project-scoped authorization on every ticket, sprint, and membership operation: `404` on read for a non-member, `403` on write for a non-member, `403` on an `OWNER`-only action performed by a `MEMBER`. | Must | All | A parameterized test matrix over (non-member, `MEMBER`, `OWNER`) × every mutating endpoint matches this table. |
| FR-11 | The system must render user-supplied Markdown (ticket descriptions, sprint goals) through an allow-list sanitizer at render time, never at write time. | Must | Workflow 2, 6 | A description containing `<script>` or an `onerror` handler renders as inert text. |
| FR-12 | Status transitions between the four board states are unrestricted in either direction; the only validation is enum membership. | Must | Workflow 4 | `BACKLOG → DONE` and `DONE → BACKLOG` both succeed; no `422` from a "protected" transition. |
| FR-13 *(specified, not implemented)* | The system must generate an AI sprint-report draft after a sprint closes, editable by any member and finalizable only by an `OWNER`, with generation failure leaving the sprint closed and the draft retriable. | Should | Workflow 9 | See §7's `SprintReport` endpoints. |

### Workflow rules
The course template lists five default rules an assistant might otherwise
invent. Kanban Flow's actual, shipped behavior **deliberately differs from
three of them** — recorded here explicitly rather than silently overwritten,
per this document's own instruction to surface contradictions rather than
hide them.

- **Stories begin in Backlog.** ✅ Matches: `create_ticket` defaults every
  new ticket to `status = BACKLOG`.
- **Moving a story to Selected for Sprint assigns it to the displayed
  sprint.** ⚠️ **Modified.** In the shipped design, `status` and `sprint_id`
  are independent fields updated independently — setting `status =
  SELECTED` does not by itself assign `sprint_id`, and assigning
  `sprint_id` does not by itself change `status`. A UI can choose to change
  both together in one request (`PATCH /tickets/{id}` accepts both fields
  at once), but the domain model does not couple them. **Reason:** coupling
  them makes unplanned in-flight work (e.g. an urgent bug worked on before
  it is ever "in a sprint") unrepresentable, and Trello/GitHub Projects/
  Linear all demonstrate that an unconstrained status model is survivable
  (§2).
- **The active board shows unassigned project backlog stories.** ✅
  Matches: `GET /api/v1/projects/{slug}/tickets?sprint_id=null` and the
  dedicated `/projects/{slug}/backlog` web view both surface tickets with
  no current sprint.
- **Moving a story back to Backlog removes it from the active sprint while
  preserving closed-sprint history.** ⚠️ **Modified, same reasoning as
  above.** Setting `status = BACKLOG` does **not** clear `sprint_id` as a
  side effect; removing a ticket from its sprint is the separate, explicit
  act of setting `sprint_id = null` via `PATCH /tickets/{id}`. Closed-sprint
  history is preserved regardless, because it lives in the append-only
  `SprintTicketHistory` table, not by inference from the live `status`/
  `sprint_id` pair.
- **Closing a sprint preserves its planned and completed story history.**
  ✅ Matches, with one gap flagged as an **Open question**: the history row
  captures `status_at_close` and `was_completed` (the *ending* state) but
  not the ticket's status at the moment it *entered* the sprint, so "this
  was planned as `SELECTED` and finished `DONE`" cannot be reconstructed
  from history alone today — only the final state. Acceptable for velocity
  (which only needs committed vs. completed point totals), but would need a
  new field if a future report wants to narrate the journey, not just the
  outcome.
- **Additional rule, not in the template's default list:** a ticket that
  appears in any `SprintTicketHistory` row cannot be hard-deleted (`DELETE
  /api/v1/tickets/{id}` → `409`), because deleting it would silently
  corrupt a closed sprint's frozen velocity figures.

---

## 6. Domain model

*The template names the user-story entity `UserStory`; the shipped schema
names it `Ticket`. The mapping is noted once here and used throughout: a
"story" in course terms is a `Ticket` row of any `type`.*

### Entity: Project
- **Purpose:** Top-level boundary for tickets, sprints, and membership.
- **Fields:**
  - `id` — string UUID, primary key
  - `name` — string, ≤ 100 chars, required
  - `slug` — string, ≤ 50 chars, globally unique, auto-derived from `name`
  - `key` — string, ≤ 10 chars, globally unique, auto-derived (e.g. `KAN`),
    Jira-style shorthand
  - `next_ticket_number` — integer, default 1, the per-project ticket
    counter (computed/internal, not client-writable)
  - `created_at` — timestamp
- **Relationships:** one-to-many with `Sprint`, `Ticket`, `ProjectMember`,
  `ProjectChatWebhook`.
- **Rules:** `slug` and `key` collisions are resolved server-side (`key`
  retries with a numeric suffix; `slug` collisions return `409`, they are
  never silently suffixed). Deleting a project cascades to its sprints,
  tickets, comments, sprint history, memberships, chat webhooks, and any
  connected GitHub integration rows, and requires the caller to be `OWNER`
  and to pass `?confirm=<slug>`.
- **Open question (template's suggested field):** the template lists a
  `description` field on `Project`; the shipped schema has none.
  **Assumption:** not needed for the course MVP, since the project's `name`
  and its board contents already carry that context; add it if a real
  onboarding flow needs longer project-level context later.

### Entity: UserStory (implemented as `Ticket`)
- **Purpose:** The primary unit of work — a story, bug, demo request, or
  internal task.
- **Fields:**
  - `id` — string UUID, primary key
  - `ticket_number` — integer, sequential per project (not globally unique)
  - `project_id` — FK → `Project.id`
  - `title` — string, ≤ 255 chars, required, non-blank after trimming
  - `description` — Markdown text, ≤ 20,000 chars, default empty
  - `type` — enum `STORY | BUG | DEMO_REQUEST | TASK`
  - `status` — enum `BACKLOG | SELECTED | IN_PROGRESS | DONE` (this is
    "where story status is stored" — see Domain questions below)
  - `priority` — enum `LOW | MEDIUM | HIGH | URGENT`, default `MEDIUM`
  - `backlog_rank` — integer, default = `ticket_number` at creation; drives
    manual backlog ordering
  - `story_points` — integer, optional, one of `{1,2,3,5,8,13}`; `null`
    means unestimated and contributes 0 to velocity (owner: `assignee_id`)
  - `due_date` — date, optional
  - `sprint_id` — FK → `Sprint.id`, nullable; the ticket's **current**
    sprint only
  - `first_sprint_entered_at` — timestamp, nullable, stamped the first time
    `sprint_id` goes from null to a value, never overwritten again
    (computed)
  - `delayed_days` — integer, nullable, written only by sprint close
    (computed, frozen)
  - `rollover_count` — integer, default 0 (computed)
  - `creator_id` — FK → `User.id`
  - `assignee_id` — FK → `User.id`, nullable; must be a member of the same
    project (the "owner" field from the template)
  - `resolution_notes` — text, nullable; free-form note on completion
    (validation evidence, post-hoc — see Domain questions)
  - `blocked_reason` — string, ≤ 500 chars, nullable (blocker/dependency
    notes, the simplified stand-in for §2's rejected blocker-relation
    pattern)
  - `completed_at` — timestamp, nullable; set entering `DONE`, cleared
    leaving it (computed)
  - `meta` — JSON object, bounded to 8 KB / 3 levels deep
  - `created_at` — timestamp, immutable
- **Relationships:** many-to-one with `Project` and `Sprint` (current);
  many-to-one with `User` (twice — creator and assignee); one-to-many with
  `TicketComment`; the historical sprint relationship is described under
  `SprintStory` below.
- **Rules:** Status transitions are unrestricted (FR-12). `status` and
  `sprint_id` are independent (§5 Workflow rules). `ticket_number` is
  allocated atomically inside the creating transaction. A ticket that
  appears in `SprintTicketHistory` cannot be hard-deleted.

### Entity: Sprint
- **Purpose:** Time-boxed iteration container.
- **Fields:**
  - `id` — string UUID, primary key
  - `project_id` — FK → `Project.id`
  - `name` — string, ≤ 100 chars, required
  - `goal` — text, ≤ 2000 chars, required
  - `status` — enum `PLANNING | ACTIVE | CLOSED`, default `PLANNING`
  - `start_date`, `end_date` — dates; `end_date` strictly after `start_date`
  - `committed_points` — integer, nullable; frozen at activation (computed)
  - `completed_points` — integer, nullable; frozen at close (computed)
  - `goal_achieved` — boolean, nullable; the shipped, human-authored
    sprint-review signal, set at close
  - `review_notes` — text, ≤ 4000 chars, nullable; same, human-authored
  - `closed_at` — timestamp, nullable
- **Relationships:** one-to-many with `Ticket` (current membership) and
  `SprintTicketHistory` (frozen membership); one-to-one with `SprintReport`
  (§ below, not yet implemented).
- **Rules:** At most one `ACTIVE` and at most one `PLANNING` sprint per
  project, each enforced by a partial unique index — a real database
  constraint, not just an application check. Allowed transitions:
  `PLANNING → ACTIVE → CLOSED`, one-directional; there is no un-close.
  Closing is manual — there is no scheduler that closes a sprint when
  `end_date` passes.

### Entity: SprintStory or equivalent association
- **Purpose:** Records a ticket's membership and outcome within one
  sprint. **Split into two structures by design**, because a single mutable
  join row cannot both track a ticket's *current* sprint and preserve what
  a *closed* sprint contained once the ticket rolls over into the next one.
  - **Live half — `Ticket.sprint_id`** (on the entity above): which sprint
    a ticket currently belongs to. Mutable; overwritten by rollover.
  - **Frozen half — `SprintTicketHistory`** (new table):
    - `id` — string UUID, primary key
    - `sprint_id` — FK → `Sprint.id`
    - `ticket_id` — FK → `Ticket.id`
    - `status_at_close` — enum, the ticket's `status` at the instant the
      sprint closed (**this is the template's `completed_status`**)
    - `story_points_at_close` — integer, nullable
    - `was_completed` — boolean, `status_at_close == DONE`
    - `recorded_at` — timestamp
    - Composite unique constraint `(sprint_id, ticket_id)`; rows are
      written once, at close, and never updated or deleted.
- **Template fields mapped, with two explicit gaps:**
  - `story_id` → `SprintTicketHistory.ticket_id`
  - `sprint_id` → `SprintTicketHistory.sprint_id`
  - `owner` → **not captured on the history row.** **Open question:** if a
    ticket is reassigned after its sprint closes, "who owned this in Sprint
    3" cannot be reconstructed from history — only from the ticket's
    current `assignee_id`, which may have since changed. **Assumption:**
    acceptable for the course MVP, since reassignment after close is rare;
    add an `assignee_id_at_close` column if this turns out to matter.
  - `planned_status` → **not captured.** Only the ending state
    (`status_at_close`) is recorded, not the status at the moment the
    ticket entered the sprint. Same open question as the Workflow-rules
    note in §5.
  - `completed_status` → `status_at_close` / `was_completed`.
  - `carryover_status` → not a field on the history row at all; represented
    on the **live** `Ticket` via `rollover_count` (how many times it has
    carried over) and `delayed_days` (frozen aging, computed at the close
    that produced the rollover).

### Entity: SprintReport *(specified here; not yet implemented in code)*
- **Purpose:** The AI-drafted retrospective, distinct from the
  already-shipped, human-authored `Sprint.goal_achieved`/`review_notes`
  pair. Where those two fields are a quick structured judgment an `OWNER`
  fills in at close, `SprintReport` is the fuller narrative artifact a
  model drafts and a human edits before finalizing (§4 Workflow 9).
- **Fields (proposed):**
  - `id` — string UUID, primary key
  - `sprint_id` — FK → `Sprint.id`, unique (exactly one report per sprint)
  - `goal_summary` — text
  - `completed_summary` — text
  - `blockers_and_rollovers` — text
  - `velocity_summary` — text
  - `faculty_notes` — text, reserved for reviewer feedback; any member may
    write it, not a distinct role (mirrors the stakeholder decision in §1
    that a "Faculty Reviewer" is an ordinary `MEMBER`)
  - `generation_status` — enum `PENDING | OK | FAILED`
  - `generated_by` — enum `SERVER | MCP_CLIENT | HUMAN`, nullable
  - `is_finalized` — boolean, default `False`
  - `finalized_by` — FK → `User.id`, nullable
  - `finalized_at` — timestamp, nullable
  - `created_at` — timestamp
- **Relationships:** one-to-one with `Sprint`.
- **Rules (proposed):** Created empty by the close routine, once, the
  moment a sprint closes (so a duplicate report can never exist, because
  close itself is not idempotent — FR-05). Any member may `PUT` the draft
  while unfinalized; an `OWNER` alone may finalize; there is no
  un-finalize. Detailed AI generation behavior, grounding checks, and
  failure handling are deferred to a later lecture per the assignment —
  only the entity shape and the API surface (§7) are committed to now.

### Domain questions to resolve
1. **What fields are required vs. optional?** Marked per-entity above;
   the recurring optional set is `story_points`, `due_date`, `assignee_id`,
   `resolution_notes`, `blocked_reason`, `meta` on `Ticket`, and
   `goal_achieved`/`review_notes` on `Sprint`.
2. **Which fields are computed, not client-supplied?**
   `Project.slug`/`key`/`next_ticket_number`; `Ticket.ticket_number`,
   `first_sprint_entered_at`, `delayed_days`, `rollover_count`,
   `completed_at`, `backlog_rank` (initial value); `Sprint.committed_points`,
   `completed_points`, `closed_at`.
3. **Where is story status stored?** Only on the live `Ticket.status`
   field. The historical value at the moment a sprint closed is a separate,
   append-only copy in `SprintTicketHistory.status_at_close` — the two are
   never the same column, by design, so rollover can overwrite the live
   value without destroying the historical one.
4. **Can a story belong to more than one sprint over time?** Yes,
   sequentially, never concurrently — `Ticket.sprint_id` is a single
   nullable FK, reassigned at each rollover; every sprint it has passed
   through leaves one permanent `SprintTicketHistory` row.
5. **What happens to unfinished stories when a sprint closes?** They move
   to the sprint named in the close request (which must already exist, in
   `PLANNING`, in the same project — FR-05), keeping their current
   `status`, with `rollover_count` incremented and `delayed_days` frozen.
6. **Where is an AI-generated report draft saved?** In the new
   `SprintReport` table specified above — not on `Sprint` itself, and not
   the same fields as the already-shipped `goal_achieved`/`review_notes`.
7. **Where are story owners, blockers, and validation evidence recorded?**
   Owner → `Ticket.assignee_id`. Blocker → `Ticket.blocked_reason`
   (free text; no relation graph — §2/§3). Validation evidence →
   `Ticket.resolution_notes`, captured **after** the fact at completion.
   **Open question:** there is no distinct, pre-work `acceptance_evidence`
   field the template's `UserStory` entity names; `resolution_notes` is the
   closest analog but is written at close, not agreed up front. Worth a
   dedicated `acceptance_criteria` field if the course later grades
   against stated-in-advance acceptance criteria rather than after-the-fact
   notes.

---

## 7. API contract

The backend API is implemented with **FastAPI**, using SQLModel/Pydantic
request and response schemas. All `/api/v1/*` routes accept either a
session cookie or an `Authorization: Bearer <PAT>` header; the GitHub
webhook route uses HMAC signature verification instead of either. Standard
error codes throughout: `401` unauthenticated/revoked/expired token, `403`
authenticated but insufficient role or non-member on a write, `404`
resource absent **or** caller is not a project member (a read never leaks
project existence), `409` a state conflict, `422` a validation failure.

### Project endpoints

#### `POST /api/v1/projects`
**Purpose:** Create a project; the creator becomes `OWNER`.
**Related requirement/workflow:** FR-01 (auth precondition), Workflow 1.
**Request body:**
```json
{ "name": "Payment Gateway" }
```
**Response body:** `201 Created`
```json
{
  "id": "b6b8...", "name": "Payment Gateway", "slug": "payment-gateway",
  "key": "PAY", "created_at": "2026-09-01T00:00:00Z", "role": "OWNER"
}
```
**Validation rules:** `name` 1–100 chars after trimming.
**Error cases:** `422` blank/over-length name or a name that derives an
empty slug; `409` if the derived slug collides with an existing project
(includes the conflicting slug in the message, never silently suffixed).

#### `GET /api/v1/projects`
**Purpose:** List projects the caller belongs to, with their role in each.
**Related requirement/workflow:** Workflow 1, 6.
**Request body:** none. **Response body:** `200 OK`, array of the shape
above. **Validation rules:** none (auth only). **Error cases:** `401` if
unauthenticated.

#### `GET /api/v1/projects/{slug}`
**Purpose:** Project detail including the caller's role.
**Related requirement/workflow:** FR-10.
**Response body:** same shape as create. **Error cases:** `404` if the
project doesn't exist or the caller is not a member.

#### `PATCH /api/v1/projects/{slug}`
**Purpose:** Rename a project. **OWNER only.**
**Related requirement/workflow:** FR-10.
**Request body:** `{ "name": "New Name" }` (partial; only supplied fields
change). **Response body:** updated project. **Validation rules:** same as
create. **Error cases:** `403` if caller is `MEMBER`; `422` if the new name
strips to blank.

#### `DELETE /api/v1/projects/{slug}?confirm={slug}`
**Purpose:** Cascading delete of the project and everything under it.
**OWNER only.**
**Related requirement/workflow:** FR-10.
**Response body:** `204 No Content`. **Validation rules:** the query
parameter `confirm` must exactly equal the slug. **Error cases:** `422` if
`confirm` doesn't match; `403` if caller is `MEMBER`.

#### `GET /api/v1/projects/{slug}/members` · `POST .../members` · `PATCH .../members/{user_id}` · `DELETE .../members/{user_id}`
**Purpose:** List, add (by email), change role, and remove membership.
Add/change/remove are **OWNER only**.
**Related requirement/workflow:** FR-10, Workflow 1.
**Request body (add):** `{ "email": "lead@example.com", "role": "MEMBER" }`.
**Response body:** member row(s) with `user_id`, `name`, `email`, `role`,
`joined_at`.
**Validation rules:** the target user must already have an account (add);
role must be `OWNER` or `MEMBER`.
**Error cases:** `404` no such user (add); `409` already a member (add), or
removing/demoting the project's last `OWNER`; `403` non-owner caller.

### User story (ticket) endpoints

#### `POST /api/v1/tickets`
**Purpose:** Create a ticket in `BACKLOG`.
**Related requirement/workflow:** FR-03, FR-04, Workflow 2.
**Request body:**
```json
{
  "slug": "payment-gateway", "title": "Handle refund webhook",
  "description": "...", "type": "BUG", "priority": "HIGH",
  "story_points": 5, "due_date": "2026-10-01",
  "sprint_id": null, "assignee_id": null, "meta": {}
}
```
**Response body:** `201 Created`, the full ticket (see `TicketOut` shape in
§6).
**Validation rules:** `title` 1–255 chars after trimming;
`description` ≤ 20,000 chars; `story_points` ∈ `{1,2,3,5,8,13}` or null;
`meta` ≤ 8 KB serialized, ≤ 3 levels deep; `assignee_id` must be a member
of the target project; if `sprint_id` is supplied it must belong to the
same project and be `PLANNING` or `ACTIVE`.
**Error cases:** `422` for each rule above; `403` if the caller is not a
member of the named project.

#### `GET /api/v1/projects/{slug}/tickets`
**Purpose:** Board/backlog query, paginated. Backs both the web board and
the MCP `list_tickets`/`list_my_tickets` tools.
**Related requirement/workflow:** Workflow 4, 6, FR-12.
**Request body:** none; query params `status`, `assignee_id`, `type`,
`priority`, `sprint_id` (pass the literal string `"null"` for
unassigned-to-any-sprint), `limit` (default 50, max 200), `cursor`.
**Response body:** `200 OK`
```json
{ "items": [ /* TicketOut[] */ ], "next_cursor": 41 }
```
**Validation rules:** `limit` 1–200. **Error cases:** `404` if the caller
is not a project member.

#### `GET /api/v1/tickets/{ticket_id}`
**Purpose:** Full ticket detail. **Response body:** `TicketOut` (see §6
`Ticket` fields). **Error cases:** `404` ticket absent or caller not a
project member.

#### `PATCH /api/v1/tickets/{ticket_id}`
**Purpose:** Update any subset of ticket fields, including reassigning a
sprint (this endpoint is how Workflow 3's sprint planning actually moves a
backlog ticket into a sprint).
**Related requirement/workflow:** Workflow 2, 3; §5 Workflow rules.
**Request body (all optional, unset fields are left unchanged):**
```json
{
  "title": "...", "description": "...", "type": "STORY",
  "priority": "MEDIUM", "story_points": 8, "due_date": "2026-10-10",
  "sprint_id": "3f9a...", "assignee_id": "u_12", "resolution_notes": "...",
  "blocked_reason": "waiting on design sign-off", "meta": {}
}
```
**Response body:** the updated `TicketOut`.
**Validation rules:** same field-level rules as create; a field explicitly
set to `null` is only accepted for the nullable set (`story_points`,
`due_date`, `sprint_id`, `assignee_id`, `resolution_notes`,
`blocked_reason`) — any other field set to `null` is rejected. The first
time `sprint_id` moves from null to a value, `first_sprint_entered_at` is
stamped and never touched again.
**Error cases:** `422` invalid field value or disallowed null; `403` if
caller is not a project member.

#### `PATCH /api/v1/tickets/{ticket_id}/status`
**Purpose:** Board-transition endpoint — the one place a status change
happens, kept narrow for MCP and the board's dropdown/click controls.
**Related requirement/workflow:** FR-12, Workflow 4, 5.
**Request body:**
```json
{ "status": "DONE", "resolution_notes": "Fixed in commit abc1234" }
```
**Response body:** updated `TicketOut`.
**Validation rules:** `status` must be one of the four enum values — that
is the *only* rule; any value may follow any other (FR-12).
**Error cases:** `403` non-member write. No "illegal transition" error
exists by design.

#### `DELETE /api/v1/tickets/{ticket_id}`
**Purpose:** Hard delete. **OWNER only.**
**Response body:** `204 No Content`.
**Error cases:** `403` non-owner; `409` if the ticket appears in any
`SprintTicketHistory` row (would corrupt a closed sprint's velocity).

### Sprint endpoints

#### `GET /api/v1/projects/{slug}/sprints`
**Purpose:** List a project's sprints, most recent first. This is also how
velocity is read as a series — **no separate `/velocity` endpoint exists**,
because each row already carries `committed_points`/`completed_points`; a
client filters by `status=CLOSED` to get the closed-sprint velocity
history.
**Related requirement/workflow:** FR-08.
**Response body:** `200 OK`, array of `SprintOut` (see §6 `Sprint` fields).
**Error cases:** `404` non-member.

#### `POST /api/v1/projects/{slug}/sprints`
**Purpose:** Create a sprint in `PLANNING` — the first half of Workflow 3.
**OWNER only.**
**Related requirement/workflow:** FR-06, Workflow 3.
**Request body:**
```json
{
  "name": "Sprint 15", "goal": "Ship refund handling",
  "start_date": "2026-10-01", "end_date": "2026-10-14"
}
```
**Response body:** `201 Created`, `SprintOut` with `status: "PLANNING"`,
`committed_points: null`.
**Validation rules:** `end_date` strictly after `start_date`; `name`/`goal`
non-blank after trimming.
**Error cases:** `422` bad dates; `409` if the project already has a
`PLANNING` sprint (including a race caught by the database's unique
index, not just a pre-check); `403` non-owner.

#### `GET /api/v1/sprints/{sprint_id}`
**Purpose:** Sprint detail with point totals.
**Response body:** `SprintOut`. **Error cases:** `404` sprint absent or
caller not a member of its project.

#### `PATCH /api/v1/sprints/{sprint_id}`
**Purpose:** Two distinct operations behind one endpoint: (a) edit
`name`/`goal`/dates while still `PLANNING`, or (b) activate
(`{"status": "ACTIVE"}`) — **not both in the same request.** **OWNER
only.**
**Related requirement/workflow:** Workflow 3, FR-06.
**Request body (edit):**
```json
{ "goal": "Ship refund handling and retries" }
```
**Request body (activate):**
```json
{ "status": "ACTIVE" }
```
**Response body:** updated `SprintOut`; activating populates
`committed_points` as the sum of `story_points` over tickets currently in
the sprint.
**Validation rules:** edit is only permitted while `status == PLANNING`;
`status` in the body may not be combined with any other field in the same
request.
**Error cases:** `409` if the sprint is not `PLANNING` (edit), or if
another sprint in the project is already `ACTIVE` (activate); `422` if
`status` is combined with other fields, or if edited dates would make
`end_date <= start_date`; `403` non-owner.

### Sprint planning and closing endpoints

*(Planning is covered by `POST .../sprints`, `PATCH /tickets/{id}`
`sprint_id`, and `PATCH /sprints/{id}` activation above — grouped here per
the assignment's checklist as a single view of the full planning-to-close
path.)*

#### `POST /api/v1/sprints/{sprint_id}/close`
**Purpose:** Close the active sprint: snapshot history, roll over
unfinished tickets, freeze velocity — Workflow 8.
**Related requirement/workflow:** FR-05, FR-06, FR-08.
**Request body:**
```json
{ "next_sprint_id": "a1c4..." }
```
**Response body:** the closed `SprintOut` (`status: "CLOSED"`,
`completed_points` populated, `closed_at` set).
**Validation rules:** the closing sprint must currently be `ACTIVE`;
`next_sprint_id` must reference a sprint that is `PLANNING` **and** in the
same project.
**Error cases:** `409` sprint not `ACTIVE`; `409` named next sprint not
`PLANNING` or not the same project; `404` `next_sprint_id` does not exist;
`409` on re-close (not idempotent by design — a second call would
double-increment every ticket's `rollover_count`).
**Assumption, recorded rather than silently added:** the service layer
already supports optional `goal_achieved`/`review_notes` at close (shipped
today only through the web form); this JSON contract should be extended to
accept them too, so the JSON API and the web UI close a sprint with the
same capability. Tracked as a small, low-risk follow-up, not yet made.

#### `GET /api/v1/sprints/{sprint_id}/history`
**Purpose:** Per-ticket frozen state at the moment a sprint closed.
**Response body:** array of
```json
{
  "ticket_id": "...", "ticket_number": 42, "title": "...",
  "status_at_close": "DONE", "story_points_at_close": 5,
  "was_completed": true
}
```
**Error cases:** `404` non-member.

### Board transition behavior between backlog and sprint states

Covered by two independent endpoints, by design (§5 Workflow rules):
- **Status column** — `PATCH /api/v1/tickets/{id}/status`: moves a ticket
  between `BACKLOG`/`SELECTED`/`IN_PROGRESS`/`DONE` with no transition
  guard.
- **Sprint membership** — `PATCH /api/v1/tickets/{id}` with `sprint_id`:
  moves a ticket into a `PLANNING`/`ACTIVE` sprint, or out of any sprint
  (`sprint_id: null`), independent of its status column.
- A backlog view is simply `GET .../tickets?sprint_id=null`; there is no
  separate "backlog" resource — it is a filter over the same ticket table.

### AI sprint-report draft endpoint *(specified; not yet implemented)*

#### `GET /api/v1/sprints/{sprint_id}/report`
**Purpose:** Retrieve the report, including `generation_status`.
**Related requirement/workflow:** FR-13, Workflow 9.
**Response body:**
```json
{
  "sprint_id": "...", "goal_summary": "", "completed_summary": "",
  "blockers_and_rollovers": "", "velocity_summary": "",
  "faculty_notes": "", "generation_status": "PENDING",
  "generated_by": null, "is_finalized": false,
  "finalized_by": null, "finalized_at": null
}
```
**Error cases:** `404` no report yet (sprint not closed) or non-member.

#### `POST /api/v1/sprints/{sprint_id}/report/generate`
**Purpose:** (Re)run AI generation of the draft's text fields. Detailed AI
behavior, inputs, grounding checks, and failure handling are deferred to a
later lecture (course template §8); this endpoint's contract is committed
to now because it is part of the required API surface.
**Request body:** none. **Response body:** the report, with
`generation_status` updated to `OK` or `FAILED`.
**Error cases:** `409` if already finalized; `503`/`generation_status:
"FAILED"` on a model or timeout error, without failing the HTTP call
itself, since the sprint must not be left in a half-closed state.

#### `PUT /api/v1/sprints/{sprint_id}/report`
**Purpose:** Save a human-edited draft. Any project member.
**Request body:** any subset of the text fields shown above.
**Response body:** the updated report.
**Error cases:** `409` if `is_finalized` is already `true`.

#### `POST /api/v1/sprints/{sprint_id}/report/finalize`
**Purpose:** Lock the report. **OWNER only.**
**Request body:** none. **Response body:** the report with
`is_finalized: true`, `finalized_by`, `finalized_at` set.
**Error cases:** `403` non-owner; `409` if already finalized (no
un-finalize exists).

### Endpoint checklist
- [x] Project endpoints
- [x] User story endpoints
- [x] Sprint endpoints
- [x] Sprint planning and closing endpoints
- [x] Board transition behavior
- [ ] AI sprint-report draft endpoint — **contract specified above;
  implementation deferred, as the assignment allows**
