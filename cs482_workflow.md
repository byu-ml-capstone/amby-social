## 1. Product vision
*CRISP-DM connection: Business Understanding — define the problem, stakeholders, purpose, and measurable success before choosing a technical solution.*

### Product name
**Kanban Flow**

### Problem statement
Existing project tracking platforms impose a friction cost that small teams pay every day: to update a board, a developer must leave the editor, open a browser, find the card, and curate it by hand. The tools reviewed in §2 are all browser-first — none of them can be driven from inside the environment where the work actually happens — so board state drifts away from repository state until the board stops being trusted. Concurrently, work that is finished in git never makes it back onto the board, non-technical inbound requests (bug reports, customer demo requests) have no low-friction intake path, and sprint closing, velocity accounting, and retrospective reporting remain dreaded manual tasks.

### Product vision
An API-first hybrid engineering workflow platform that bridges developer IDEs and non-technical stakeholders. Developers interact through native IDE MCP tools (Cursor, Claude Desktop) and team chat alerts (Teams, Slack, Discord) without ever opening a web browser, while non-technical teammates and instructors monitor progress, submit tickets, and review AI-generated sprint retrospectives via an ultra-lightweight web dashboard.

### Intended users and stakeholders
| Person or group | Need or responsibility | How the app helps |
|---|---|---|
| **Software Engineers** | Focus on code without context-switching to web project trackers; track assigned tasks. | Manipulate ticket lifecycles and query tasks via natural language directly within Cursor/Claude via lightweight MCP tools. |
| **Non-Technical Leads & Ops** | Submit inbound customer requests, monitor team delivery status, and inspect work. | Access a simple web dashboard to visually inspect Kanban columns and submit tickets via modal forms. |
| **Faculty Reviewers & Leads** | Review sprint performance, evaluate velocity, and unblock stalled stories. | Inspect per-sprint velocity (committed vs. completed story points), automated rollover metrics (`rollover_count`, `delayed_days`), and review AI-drafted sprint retrospectives. |

### Success criteria
Write observable criteria. Avoid statements such as “the app is easy to use” unless you explain how you will recognize that.
- [ ] An engineer can transition a ticket status (e.g., `IN_PROGRESS` to `DONE`) entirely inside Cursor or Claude Desktop via an MCP tool call without opening a web browser.
- [ ] A non-technical user, starting from an already-authenticated `/projects/{slug}` board, can create a ticket through the modal in **5 interactions or fewer** (open modal, title, type, description, submit) and see it appear in the `BACKLOG` column without a full page reload. Measured by a scripted UI walkthrough, not by self-report.
- [ ] A GitHub push or merge whose commit message contains `Closes #<ticket_number>` transitions the matching ticket to `DONE` and delivers a card to the configured team chat webhook. Measured as **p95 ≤ 60s** from webhook receipt to chat delivery, over 20 scripted deliveries.
- [ ] Closing a sprint (`POST /api/v1/sprints/{id}/close`) carries every non-`DONE` ticket into the next sprint — auto-creating that sprint if none exists — preserving the ticket's `status` and `created_at`, incrementing `rollover_count`, and **freezing** `delayed_days` at close time so the value never changes on re-read.
- [ ] The `GET /api/v1/projects/{slug}/velocity` response returns, for each closed sprint, `committed_points` and `completed_points`, and these values remain identical when the endpoint is called again a week later.
- [ ] The web dashboard renders the AI-generated sprint report draft with an editable interface; a reviewer can save the draft any number of times and then finalize it in a separate, OWNER-only action.
- [ ] Every ticket number, story point total, and date appearing in a generated report is present in the JSON input given to the model. Verified by a `pytest` grounding check that extracts `#\d+` references from the draft and asserts set-membership in the input payload.

---

## 2. Product research and decisions
*CRISP-DM connection: Data Understanding — learn from existing products and inspect the patterns, assumptions, and constraints that shape the problem space.*

### How these tools were inspected
Three products from the assignment's list were inspected between 2026-09-04 and 2026-09-10 by working through their public documentation and by creating a throwaway project in each free tier, then walking one story from creation to completion. Jira and GitHub Projects were reviewed in depth because they are the two that actually implement the behaviors this app needs (sprint close, velocity, git-driven status change). Trello was reviewed as the minimal baseline — the floor this app should not fall below in simplicity.

| Tool | What was inspected | Reference |
|---|---|---|
| Jira Software (Scrum board template) | Free-tier cloud site, one Scrum project, one sprint started and completed | <https://www.atlassian.com/software/jira> · docs: <https://support.atlassian.com/jira-software-cloud/> · "Complete a sprint", "View and understand the velocity chart" |
| GitHub Projects (v2) + Issues | Personal project board over a test repository, iteration field, closing keywords | <https://docs.github.com/en/issues/planning-and-tracking-with-projects> · <https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue> |
| Trello | Free board, three lists, Butler rule | <https://trello.com/> · docs: <https://support.atlassian.com/trello/> |

---

### Review 1: Jira Software (Scrum board)

- **Core workflow.** Issues accumulate in a **Backlog** that is a separate screen from the board. A lead creates a sprint container in the backlog, drags issues into it, then clicks **Start sprint**, which opens a dialog demanding a sprint name, a **sprint goal**, and start/end dates. Work happens on the board. The lead clicks **Complete sprint**, which produces a report. Backlog and board are deliberately different surfaces: planning and execution are different activities.
- **Projects and stories/tasks.** A project has a short key (`PAY`) and every issue gets a **project-scoped sequential key** (`PAY-123`) that people say out loud in standups. Issues are typed: Epic, Story, Task, Bug, Sub-task, with Epic → Story → Sub-task forming a three-level hierarchy. Estimation is a first-class `Story Points` field. Grouping is done by **Components** (a per-project enum) and free-form **Labels**, which are different things: components are curated, labels are not.
- **Sprint, board, status workflow.** Board columns are a *mapping* onto statuses, not statuses themselves — several statuses can share a column. The underlying workflow is a configurable directed graph with conditions, validators, and post-functions, so "In Progress → Done" can be made illegal or can fire side effects. By default a board runs **one active sprint at a time**; parallel sprints is an opt-in administrative setting.
- **Reporting and progress tracking.** The richest of the three by a wide margin: **Burndown chart**, **Velocity chart** (paired bars of *committed* vs. *completed* story points per sprint, which is the canonical definition of velocity), **Sprint report**, Cumulative Flow Diagram, Control chart, Epic burndown. The velocity chart is read as a trailing average over the last three to five sprints.
- **Blockers, decisions, notes, risks.** Three distinct mechanisms. (1) **Flagging** an issue marks it visually blocked on the board without changing its status. (2) Typed **issue links** — `blocks` / `is blocked by`, `relates to`, `duplicates` — express dependency as data. (3) Comments and @mentions for narrative. Decisions and retrospectives are *not* in Jira; they live in linked Confluence pages, which means the reasoning behind a decision is one product away from the work it governs.
- **Useful for CS 482.** The **Complete sprint** dialog is the single most directly reusable idea: it refuses to let a sprint end ambiguously and forces an explicit destination for unfinished issues (next sprint, backlog, or a named future sprint). The committed-vs-completed velocity definition. The project-scoped sequential issue key. The sprint goal as a required field. Flagging as a status-orthogonal blocker signal.
- **Unnecessary for the course MVP.** Configurable workflow schemes, permission/notification/screen schemes, custom field administration, the Epic hierarchy, parallel sprints, JQL, and every chart except the velocity numbers. Jira's configurability is the reason it needs an administrator, and a course project has no administrator.

---

### Review 2: GitHub Projects (v2) + GitHub Issues

- **Core workflow.** Work is filed as an **Issue** in a repository. A **Project** is a separate org- or user-level table that issues from any repository are added to. The status change that matters most happens in git, not in the UI: a pull request whose body or commit message says `Closes #12` closes issue 12 when it merges to the default branch, and a project workflow then moves that item to Done. **The board is a consequence of the repository, not a thing maintained beside it.**
- **Projects and stories/tasks.** Issues are numbered **per repository** (`#12`), sharing one counter with pull requests. There is no fixed issue-type enum — type is expressed as a label or a custom field. Hierarchy is thin: task lists and sub-issues, no epics. The interesting move is that **fields belong to the project, not the issue**: `Status`, `Estimate`, `Priority`, `Iteration` are single-select / number / iteration fields defined per project, so the same issue can carry different metadata in two different projects.
- **Sprint, board, status workflow.** There is no "sprint" object. An **Iteration field** supplies time-boxed periods with dates that can be auto-generated forward. `Status` is an ordinary single-select field that the Board view happens to group by — so the column set is data, not schema, and there are **no transition rules at all**: any value can follow any value. Built-in workflows cover "item added → set Status", "issue closed → set Status Done".
- **Reporting and progress tracking.** **Insights** gives a burn-up chart and a current-status chart with grouping and filtering. Thin compared to Jira, and notably there is **no velocity concept** — because there is no sprint object to attribute completed points to, only a date range.
- **Blockers, decisions, notes, risks.** Weakest of the three. No native dependency relation; teams improvise with a `blocked` label or a custom single-select field. Notes live in issue comments. Decisions conventionally live **in the repository as Markdown ADR files**, versioned alongside the code they justify, with GitHub Discussions for open questions. That convention — decisions as a durable, reviewable artifact next to the work — is worth more to this project than the board features.
- **Useful for CS 482.** The **closing-keyword pattern** (`Closes #<n>` parsed from push and pull-request payloads) is adopted essentially verbatim; it is the entire justification for Workflow 2b and FR-09. Per-repository sequential issue numbers map onto per-project `ticket_number`. Status-as-plain-enum with no transition guards. Decisions-as-files informs §11.
- **Unnecessary for the course MVP.** Multiple saved views, the Roadmap view, organization-level projects spanning repositories, the Projects GraphQL API, the automation builder, and Insights charts. Also the per-project custom field system: this app fixes its fields in the schema instead.

---

### Review 3: Trello

- **Core workflow.** Create a board, create lists, create cards, drag cards rightward. That is the whole product. Onboarding cost is close to zero and there is nothing to configure before the first card exists.
- **Projects and stories/tasks.** Strictly three levels: **Board → List → Card**. A card carries a title, a Markdown description, labels, members, a due date, checklists, attachments, and a comment thread. There is **no issue type, no estimate, and no story-point field** — anything structured has to be encoded into a label or a checklist.
- **Sprint, board, status workflow.** A **list is the status** — there is no status field behind it, so columns cannot be renamed without renaming the state itself, and no transition is ever illegal. There is **no sprint object**; teams simulate sprints either with one board per sprint or a "Sprint N" label, and neither survives reporting. Butler rules add if-this-then-that automation on card movement.
- **Reporting and progress tracking.** None natively. Every chart requires a Power-Up (Dashcards, Burndown for Trello), which is the honest signal that Trello is a *board*, not a delivery-tracking system.
- **Blockers, decisions, notes, risks.** No native mechanism whatsoever. The universal workaround is a red `Blocked` label or a dedicated "Blocked" list — the latter being strictly worse, because parking a card in a Blocked list destroys the information about which column it was blocked *in*.
- **Useful for CS 482.** The card model is the right size for a course MVP: title, Markdown description, type-ish label, assignee, and nothing else. Zero-configuration onboarding is a target to match. Most importantly, Trello proves that **an unconstrained status model is survivable** — which is the evidence behind ADR-012's decision to allow free transitions.
- **Unnecessary for the course MVP.** Drag-and-drop as the primary interaction (it demands a client-side library and a card-ordering field this app deliberately excludes), the Power-Up ecosystem, checklists, and attachments. Trello's missing pieces are also instructive: no sprint and no reporting is exactly the gap this app exists to fill.

---

### Adjacent systems inspected (not project-management tools)
These were inspected as integration surfaces rather than as competitors, and are not counted among the products reviewed above.

| System | Pattern observed | Implication |
|---|---|---|
| **Slack / Discord / MS Teams** | Inbound webhooks accept structured card payloads in three different shapes (Block Kit, Embeds, Adaptive Cards) over one identical `POST`-JSON-to-a-URL transport. | One dispatcher interface, three payload formatters. The transport is the same; only serialization differs. |
| **Anthropic MCP** | A JSON-RPC protocol that lets an LLM inside an IDE discover and invoke tools, with tool results consuming the caller's context window. | Primary developer interface. Because results cost tokens, list tools must return a lean projection (ADR-007). |

---

### Cross-tool pattern summary

| Pattern | Jira | GitHub Projects | Trello | Decision for this app |
|---|---|---|---|---|
| Sprint / iteration object | First-class, with goal and dates | Iteration *field* only, no object | Absent | **Adopt Jira's**: a real `Sprint` entity with `goal`, dates, and status |
| Explicit sprint close | **Yes** — dialog forces a destination for unfinished work | No | No | **Adopt and automate**: `POST /sprints/{id}/close` auto-creates the target rather than prompting (ADR-005) |
| Velocity | Committed vs. completed points per sprint | Absent | Absent | **Adopt Jira's definition**: `committed_points` at activation, `completed_points` at close (FR-08) |
| Work item numbering | Project-scoped (`PAY-123`) | Repository-scoped (`#12`) | Not surfaced | **Adopt**: per-project `ticket_number` (FR-12) |
| Estimation field | Story Points, first-class | Optional number field | Absent | **Adopt**, optional: `story_points` on a Fibonacci scale |
| Status transition rules | Configurable graph with validators | None — any value to any value | None — lists are freeform | **Reject Jira's**: free transitions (ADR-012) |
| Board column ordering / rank | Yes | Yes | Yes (drag) | **Reject**: no rank field, no drag-and-drop |
| Git-driven status change | Via a paid app / Smart Commits | **Native closing keywords** | Absent | **Adopt GitHub's** verbatim: `Closes #<n>` (FR-09) |
| Blocker signal | Flag + typed `is blocked by` links | Label convention only | Label convention only | **Simplify**: no blocker field; blockage is inferred from `rollover_count` and `delayed_days` |
| Decision record | External (Confluence) | Markdown ADRs in-repo | Absent | **Adopt GitHub's convention**: §11 decision log lives in this file, versioned with the project |
| Reporting surface | Six chart types | Burn-up + current | None | **Narrow deliberately**: velocity as numbers, plus one AI-written retrospective |

---

### MVP implications
What the three reviews change about this app, stated as commitments:

1. **A sprint must be a real object, not a date range.** GitHub's iteration field cannot answer "what did sprint 3 contain" once items move, and that is precisely the question velocity depends on. This is what forces the `Sprint` entity and `SprintTicketHistory` snapshot (ADR-011).
2. **Sprint close is the product's most important moment, and it should not ask.** Jira gets the moment right and the ergonomics wrong — a modal asking where unfinished work should go is a decision a small team makes the same way every time. Automate it: roll forward, preserve `status`, auto-create the target sprint (ADR-005).
3. **Velocity means committed vs. completed points, and nothing else.** Adopt Jira's definition and stop there. No burndown, no cumulative flow, no control chart — those are the features that make Jira need an administrator.
4. **Git is the status input that matters.** GitHub's closing keywords eliminate the most common board-drift problem for free. One signed webhook endpoint and one regex buys most of the "developers never update the board" problem (FR-09).
5. **Do not build a transition state machine.** Two of three tools have no transition rules at all and their users are fine. Validate the enum, allow any move, and spend the saved effort on the sprint-close transaction (ADR-012).
6. **Keep the card at Trello's size.** Title, Markdown description, type, priority, assignee, optional points. Every additional field is one a course-scale team will leave empty.
7. **Ship no charts.** All three tools are browser-first; this app's differentiator is being driven from the IDE and summarized in chat. A chart is the one artifact that cannot be delivered to either surface, so velocity ships as numbers and the retrospective ships as text.
8. **Keep decisions in the repository.** Jira's split between work and reasoning is a real cost. §11 stays in this Markdown file, reviewed and versioned alongside the code.

---

### Patterns to adopt
- **API-First Dual-Interface Architecture:** Maintain a unified FastAPI backend serving both developer MCP endpoints and a lightweight server-rendered web dashboard.
- **Embedded SQLite with Write-Ahead Logging (WAL):** Zero-install, file-based persistence with no network round-trip on reads. WAL allows concurrent readers alongside a **single** writer; it does not provide concurrent write throughput, which is acceptable at team scale (see ADR-002).
- **Client-Side Distributed AI via MCP (for interactive parsing):** Natural-language intent parsing — turning *“mark #42 done, resolved in abc1234”* into a structured tool call — happens in the client’s existing LLM session, so the server never pays for it. This applies to **interactive** work only; the sprint report is a batch artifact and is generated server-side by default (see ADR-008).

### Patterns to reject or simplify
- **Pure Headless Design:** Reject strict headless CLI architecture to ensure non-technical team members and evaluators have a web GUI for visibility.
- **Complex SPA Frameworks (React/Next.js):** Avoid build-step overhead, state synchronizers, and client-side routing libraries in favor of FastAPI server-rendered templates (Jinja2 + Tailwind + HTMX/Alpine.js).
- **In-App Notification Bell System:** Eliminate read/unread inbox databases; dispatch alerts strictly through configured chat webhooks and direct emails.

### Product decisions
| Decision | Alternatives considered | Choice | Reason |
|---|---|---|---|
| **Backend & UI Framework** | Rust (Actix/Axum), Node.js, Next.js SPA | **Python (FastAPI) + Jinja2 Templates** | Single runtime handles REST API, MCP proxy, and HTML dashboard rendering with zero build pipelines. |
| **Database Engine** | AWS DynamoDB (Single Table), PostgreSQL RDS | **Embedded SQLite (WAL Mode)** | Zero installation, zero infrastructure cost, no network round-trip on reads, and trivial backup via file copy. (Read-latency figures to be measured during the validation plan in §10, not assumed.) |
| **Production Hosting** | AWS Lambda + API Gateway, AWS EC2 | **AWS Lightsail ($5/month Ubuntu instance)** | Predictable flat-rate pricing (IPv4 + 40GB SSD included), no cold-start latency, and instant Docker deployment. |
| **Authentication Model** | OAuth2 Social Login, Magic Links | **Minimal 3-Field Auth (`name`, `email`, `hash`)** | Ultra-fast onboarding; issues browser session cookies for the web UI and, separately, revocable Bearer PATs stored as hashes in an `ApiToken` table for MCP (see ADR-010). |
| **Ticket Modeling** | Class-table inheritance, plain string tags | **Single `Ticket` Entity with JSON `meta` column** | Maximizes board query performance while allowing bounded extension data from external webhooks and LLMs. Named `meta`, not `metadata`, because `metadata` is a reserved attribute on SQLAlchemy/SQLModel declarative classes. |
| **Unfinished Sprint Stories** | Dump back into project backlog, drop completely | **Automatic rollover into the next sprint, auto-created if absent, with `status` preserved** | Ensures ongoing work is never forgotten and that an in-flight ticket stays in-flight across the boundary. Accountability comes from `rollover_count` and a `delayed_days` value frozen at close time (see ADR-005, ADR-009). |

---

## 3. MVP scope
*CRISP-DM connection: Business Understanding → Data Understanding — decide which needs and product patterns belong in the first version and which do not.*

### In scope
- [x] **Projects:** Multi-tenant project boundaries identified by unique slugs; multi-project user associations.
- [x] **Web Dashboard UI (3 Screens + 1 Modal):**
  - Auth Page (`/login`, `/register`): Minimal 3-field account access.
  - Workspace Home (`/dashboard`): Project list, project creator, and personal MCP token copy box.
  - Project Board (`/projects/{slug}`): Tabbed view for Kanban board, AI Sprint Report, and Team Settings.
  - Ticket Creation Modal: Fast manual ticket submission form.
- [x] **User Stories / Tickets:** Single-entity model supporting `STORY`, `BUG`, `DEMO_REQUEST`, and `TASK` types with rich Markdown support and a bounded JSON `meta` column. Estimation via `story_points` on a fixed Fibonacci scale.
- [x] **Sprint Board & Lifecycle:** Fixed 4-state workflow (`BACKLOG`, `SELECTED`, `IN_PROGRESS`, `DONE`) with **free transitions** between any two states; sprint creation, activation, and closure with unfinished ticket rollover that preserves `status`.
- [x] **Velocity Tracking:** Per-sprint `committed_points` / `completed_points`, recorded immutably at close time via `SprintTicketHistory` so historical velocity survives rollover.
- [x] **Inbound GitHub Webhook:** `push` and `pull_request` payload parsing for `Closes #<ticket_number>` to auto-advance tickets to `DONE`.
- [x] **Revocable MCP Tokens:** `ApiToken` entity with hashed storage, prefix display, issue / list / revoke endpoints.
- [x] **Role-Based Access:** `OWNER` performs administrative actions (project settings, membership, sprint create/close, report finalize); `MEMBER` performs work actions (ticket CRUD, status changes, report drafting).
- [x] **Multi-Channel Notification Dispatcher:** Unified webhook delivery abstraction supporting MS Teams (Adaptive Cards), Slack (Block Kit), and Discord (Embeds).
- [x] **Standard Stdio MCP Server:** Client-side MCP interface exposing lean tools (`list_my_tickets`, `get_ticket`, `create_ticket`, `update_ticket_status`) optimized for low token consumption.
- [x] **AI Sprint Reporting:** Automated generation of sprint performance and blocker retrospective drafts upon sprint close.

### Explicitly out of scope
- **Gmail / email inbound triage.** Non-technical intake happens through the web modal only in the MVP. Deferred to Post-MVP.
- Bitbucket and GitLab inbound webhooks (GitHub only).
- Sprint burndown charts, cumulative flow diagrams, and any time-series chart rendering (velocity is exposed as numbers and a table, not a chart).
- WIP limits and board card ordering / ranking within a column.
- In-app notification bell centers and read/unread status management.
- Complex drag-and-drop front-end libraries (replaced with click-to-change status dropdowns).
- Direct binary file upload storage (users link external URLs, commits, or PRs).
- Multi-tier hierarchical RBAC beyond basic `OWNER` and `MEMBER` distinctions.

---

## 4. Key user workflows
*CRISP-DM connection: Business Understanding — describe how a stakeholder will accomplish a meaningful goal and what result would count as success.*

### Workflow 0: Sprint Planning (backlog → sprint)
- **Actor:** Team Lead (`OWNER`)
- **Starting condition:** A project exists with tickets sitting in `BACKLOG` and `sprint_id = NULL`. There is no `ACTIVE` sprint, or the current one is about to be closed.
- **Steps:**
  1. The actor creates a sprint via `POST /api/v1/projects/{project_id}/sprints` with `name`, `goal`, `start_date`, `end_date`. It is created in `PLANNING`.
  2. The actor assigns `story_points` to candidate tickets via `PATCH /api/v1/tickets/{id}`.
  3. The actor pulls tickets into the sprint via `PATCH /api/v1/tickets/{id}` with `{"sprint_id": "...", "status": "SELECTED"}`. The backend stamps `first_sprint_entered_at` on the ticket the first time this happens and never again.
  4. The actor activates the sprint via `PATCH /api/v1/sprints/{id}` with `{"status": "ACTIVE"}`. The backend rejects this with `409` if another sprint in the same project is already `ACTIVE`, and records `committed_points` as the sum of `story_points` over tickets in the sprint at that instant.
- **Expected result:** Exactly one `ACTIVE` sprint per project, with a frozen commitment baseline that velocity can later be measured against.

### Workflow 1: Developer Task Resolution via IDE MCP
- **Actor:** Software Engineer (working in Cursor or Claude Desktop)
- **Starting condition:** The engineer has configured `kanban-flow-mcp` with their PAT; an active ticket (`#42`) is assigned to them.
- **Steps:**
  1. The engineer prompts their editor: *"Mark ticket #42 as DONE and note that it was resolved in commit abc1234."*
  2. The editor invokes the `update_ticket_status` MCP tool.
  3. The FastAPI backend validates that the caller is a member of the ticket's project, updates the SQLite record, stamps `completed_at` because the new status is `DONE`, and **schedules** the notification dispatch as a `BackgroundTask`.
  4. The API returns `200` immediately; the configured chat channel (Teams/Slack/Discord) receives a completion card out-of-band.
- **Expected result:** The ticket moves to `DONE` and synchronizes to team chat without opening a web browser.
- **Failure behavior:** A webhook that times out or errors does **not** fail or reverse the ticket update. The dispatcher retries 3 times with exponential backoff (1s, 2s, 4s) at a 5s per-attempt timeout, then gives up and writes a `WARNING` log line naming the `project_id` and `ticket_number`.

### Workflow 2: Non-Technical User Web Dashboard Triage & Ticket Submission
- **Actor:** Product Manager, Sales Lead, or Faculty Reviewer
- **Starting condition:** User accesses the platform URL on a web browser.
- **Steps:**
  1. User navigates to `/projects/payment-gateway` and views the Kanban board.
  2. User clicks the `[+ New Ticket]` button in the top navigation bar.
  3. User fills out the lightweight modal form (Title, Type: `DEMO_REQUEST`, Description) and submits.
  4. The ticket instantly renders in the `BACKLOG` column (`sprint_id = NULL`) and an announcement card is queued for the team chat channel.
- **Expected result:** Non-technical members successfully introduce tasks into the engineering pipeline via GUI. The ticket is unestimated (`story_points = NULL`) and unscheduled until an `OWNER` handles it in Workflow 0.
- **Failure behavior:** A submitter who is not a member of the project receives `403` and the modal surfaces the message inline rather than closing.

### Workflow 2b: GitHub Commit Closes a Ticket
- **Actor:** GitHub (machine), acting on a Software Engineer's push
- **Starting condition:** The project has a configured GitHub webhook secret; ticket `#42` is `IN_PROGRESS`.
- **Steps:**
  1. An engineer pushes a commit whose message contains `Closes #42`.
  2. GitHub `POST`s to `/api/v1/webhooks/github`; the backend verifies the `X-Hub-Signature-256` HMAC and rejects with `401` on mismatch.
  3. The backend resolves `#42` to a ticket **within the project bound to that webhook secret** (ticket numbers are per-project, so a global lookup would be ambiguous), sets `status = DONE`, `completed_at = NOW()`, and records the commit SHA in `meta.git_commit`.
  4. A completion card is queued for the team chat channel.
- **Expected result:** Board state reflects merged work with no human board interaction.
- **Failure behavior:** An unresolvable or already-`DONE` ticket number is ignored with a `200` and a log line — GitHub must not see a failure and retry indefinitely.

### Workflow 3: Sprint Closure, Story Rollover, and Retrospective Draft
- **Actor:** Team Lead / Faculty Reviewer
- **Starting condition:** Sprint cycle reaches end date; tickets remain in `IN_PROGRESS` while others are `DONE`.
- **Steps:**
  1. The actor (must be `OWNER`; otherwise `403`) clicks `Close Sprint` on the dashboard or invokes `POST /api/v1/sprints/{id}/close`.
  2. **Within a single transaction**, the backend:
     a. Writes one `SprintTicketHistory` row per ticket in the sprint, capturing `status_at_close`, `story_points_at_close`, and `was_completed`. This is what makes past velocity recoverable after `sprint_id` is overwritten.
     b. Records `completed_points` on the sprint as the sum of `story_points` over tickets that are `DONE`.
     c. Resolves the rollover target: the project's next sprint by `start_date` if one exists in `PLANNING`, otherwise **auto-creates** one named `"{previous name} (next)"` with the same duration as the sprint being closed, starting the day after its `end_date`. The target is promoted to `ACTIVE` only after the current sprint flips to `CLOSED`, so the "at most one `ACTIVE` sprint" rule is never violated mid-transaction.
     d. Moves every non-`DONE` ticket to the target sprint, **preserving its `status`** (an `IN_PROGRESS` ticket stays `IN_PROGRESS`), incrementing `rollover_count`, and writing a frozen `delayed_days = (closed sprint's end_date - first_sprint_entered_at).days`, clamped at 0.
     e. Sets the closed sprint's `status = CLOSED` and creates a `SprintReport` row with `is_finalized = False`.
  3. **After** that transaction commits, the AI reporting engine is invoked to fill the report's text fields. See §8 for the dual generation path and failure behavior.
  4. The actor reviews and edits the draft in the web dashboard, saving as many times as needed via `PUT /api/v1/sprints/{id}/report`.
  5. The actor clicks `Finalize Report`, which calls `POST /api/v1/sprints/{id}/report/finalize`.
- **Expected result:** Historical sprint metrics (velocity, per-ticket close state) are preserved immutably, unfinished tasks are carried forward in-flight with frozen aging figures, and the final retrospective report is committed.
- **Failure behavior:** Closing an already-`CLOSED` sprint returns `409` and changes nothing — close is not idempotent by design, because a second run would double-increment `rollover_count`. If step 3 fails, the sprint stays closed and the report row persists with empty text and `generation_status = "FAILED"`; the dashboard shows a **Retry AI draft** button and the report remains hand-editable.

---

## 5. Functional requirements
*CRISP-DM connection: Business Understanding → Modeling — translate stakeholder needs into precise behavior that can later be designed, implemented, and tested.*

| ID | Requirement | Priority | Related workflow | Acceptance evidence |
|---|---|---|---|---|
| **FR-01** | The system must register and log in users with `name`, `email`, and `password_hash`, issuing an HTTP-only session cookie for the web UI and, separately, a revocable Bearer PAT for MCP. | Must | Auth | Successful `POST /auth/register`, `POST /auth/login`, and an authenticated API call using an issued PAT. Duplicate email returns `409`. |
| **FR-02** | The system must provide a web dashboard rendering Kanban columns, the velocity table, AI report drafts, and team settings. | Must | Workflow 2 | Board displays correct ticket states, modal creates tickets, report tab renders sanitized Markdown, velocity tab lists closed sprints. |
| **FR-03** | The system must manage tickets containing a bounded JSON `meta` column and distinct `type` flags. | Must | Core CRUD | Ticket persists key-value `meta` in SQLite; a `meta` payload exceeding 8 KB serialized is rejected with `422`. |
| **FR-04** | The system must expose a standard Stdio MCP server offering lean tools for Cursor and Claude. | Must | Workflow 1 | `list_my_tickets` with the default `limit=20` returns a summary projection measured at **under 500 tokens by `tiktoken` `cl100k_base`**, asserted in `pytest`. |
| **FR-05** | Closing a sprint must move every non-`DONE` ticket into the next sprint — auto-creating it if absent — preserving `status` and `created_at`, incrementing `rollover_count`, and freezing `delayed_days`. | Must | Workflow 3 | Rolled-over ticket shows the new `sprint_id`, unchanged `status`, `rollover_count = previous + 1`, preserved `created_at`, and a `delayed_days` value identical on re-read a day later. |
| **FR-06** | The system must format and dispatch outbound webhook payloads to Slack, Discord, and Teams as background tasks that never block or reverse the triggering mutation. | Must | Workflow 1, 2 | Receipt of formatted cards in target channels; with the webhook URL pointed at a black-hole endpoint, the ticket mutation still returns within 500 ms and succeeds. |
| **FR-07** | The system must draft an AI sprint report summarizing completed tickets, velocity, and aging blockers. | Should | Workflow 3 | AI-generated Markdown draft persisted with `is_finalized = False` and `generation_status = "OK"`. |
| **FR-08** | The system must record `committed_points` at sprint activation and `completed_points` at sprint close, and expose per-sprint velocity that remains stable after tickets roll over. | Must | Workflow 0, 3 | `GET /projects/{slug}/velocity` returns identical figures for a closed sprint before and after subsequent sprints close. |
| **FR-09** | The system must accept authenticated GitHub `push` and `pull_request` webhooks and transition tickets referenced by `Closes #<n>` to `DONE`. | Must | Workflow 2b | Signed payload advances the ticket; an unsigned or wrongly-signed payload returns `401` and changes nothing. |
| **FR-10** | The system must enforce project-scoped authorization on every ticket, sprint, report, and settings operation. | Must | All | A user who is not a `ProjectMember` receives `404` on read and `403` on write for that project's resources; a `MEMBER` receives `403` on sprint create/activate/close, membership changes, project settings, and report finalize. |
| **FR-11** | The system must render all user-supplied Markdown through an allow-list sanitizer before serving it in HTML. | Must | Workflow 2 | A ticket description containing `<script>` and `<img onerror=...>` renders as inert text in the board and report views. |
| **FR-12** | The system must allocate `ticket_number` uniquely and consecutively per project under concurrent creation. | Must | Core CRUD | A `pytest` test issuing 50 concurrent ticket creations in one project yields 50 distinct numbers with no gaps. |

---

## 6. Domain model
*CRISP-DM connection: Data Understanding → Modeling — identify the information the product manages, its relationships, and the rules that govern it.*

### Entity: User
- **Purpose:** Identifies platform users, ticket creators, and assignees.
- **Fields:**
  - `id` — UUID (Primary Key)
  - `name` — VARCHAR(50), display name / nickname
  - `email` — VARCHAR(255), unique login identifier and notification address (stored lowercased; uniqueness is case-insensitive)
  - `password_hash` — VARCHAR(255), bcrypt
  - `created_at` — TIMESTAMP, UTC, immutable
- **Relationships:** Many-to-Many with `Project` via `ProjectMember`; One-to-Many with `Ticket` (as creator and as assignee — two distinct FKs); One-to-Many with `ApiToken`.
- **Rules:** Deleting a user is not supported in the MVP. All timestamps across the schema are stored as UTC-aware values.

### Entity: ApiToken
- **Purpose:** Revocable Bearer credential used by the Stdio MCP server. Separate from the web session cookie so that revoking a developer's IDE access does not log them out of the dashboard.
- **Fields:**
  - `id` — UUID (Primary Key)
  - `user_id` — UUID (Foreign Key referencing `User.id`)
  - `token_hash` — VARCHAR(255), SHA-256 of the token. **The plaintext token is returned exactly once, at issue time, and is never recoverable afterward.**
  - `prefix` — VARCHAR(12), first 8 characters of the token, shown in the UI so a user can tell their tokens apart
  - `label` — VARCHAR(50), e.g., "work laptop"
  - `created_at` — TIMESTAMP
  - `last_used_at` — TIMESTAMP, nullable
  - `revoked_at` — TIMESTAMP, nullable
- **Rules:** Tokens do not expire; access ends only on revocation. A request bearing a token whose `revoked_at` is set returns `401`. `last_used_at` is updated at most once per minute per token to avoid a write on every MCP call.

### Entity: Project
- **Purpose:** Top-level multi-tenant boundary for tasks, sprints, and chat integrations.
- **Fields:**
  - `id` — UUID (Primary Key)
  - `name` — VARCHAR(100)
  - `slug` — VARCHAR(50), **globally** unique URL identifier (e.g., `/projects/payment-gateway`)
  - `webhook_type` — ENUM (`NONE`, `TEAMS`, `SLACK`, `DISCORD`), default `NONE`
  - `webhook_url` — VARCHAR(500), nullable
  - `github_secret` — VARCHAR(255), nullable, HMAC secret for inbound GitHub webhooks
  - `next_ticket_number` — INTEGER, default 1, the per-project ticket counter
  - `created_at` — TIMESTAMP
- **Relationships:** One-to-Many with `Sprint`, `Ticket`, and `ProjectMember`.
- **Rules:**
  - `slug` is auto-derived from `name` (lowercase, non-alphanumerics to `-`, collapsed, trimmed to 50). On collision the API returns `409` with the conflicting slug; it does not silently append a suffix.
  - `webhook_url` must be non-null whenever `webhook_type != NONE`, and must be `https`. Violation returns `422`.
  - Deleting a project cascades to its `Sprint`, `Ticket`, `ProjectMember`, and `SprintTicketHistory` rows. Only an `OWNER` may delete, and only via an explicit confirmation parameter.

### Entity: ProjectMember
- **Purpose:** Defines user membership and access scope within projects.
- **Fields:**
  - `id` — UUID (Primary Key)
  - `project_id` — UUID (Foreign Key referencing `Project.id`)
  - `user_id` — UUID (Foreign Key referencing `User.id`)
  - `role` — ENUM (`OWNER`, `MEMBER`)
  - `joined_at` — TIMESTAMP
- **Rules:**
  - Composite unique constraint on `(project_id, user_id)`.
  - The project creator is inserted as `OWNER` in the same transaction as project creation.
  - A project must retain at least one `OWNER`; removing or demoting the last one returns `409`.
  - **Permission matrix** (a non-member gets `404` on read and `403` on write, so project existence is not leaked):

    | Action | OWNER | MEMBER |
    |---|---|---|
    | View board, tickets, reports, velocity | yes | yes |
    | Create / edit / assign / estimate tickets, change status | yes | yes |
    | Save report draft (`PUT .../report`) | yes | yes |
    | Create, activate, or close a sprint | yes | **no (403)** |
    | Finalize a report | yes | **no (403)** |
    | Add / remove members, change roles | yes | **no (403)** |
    | Edit project settings, webhooks, GitHub secret; delete project | yes | **no (403)** |

### Entity: Sprint
- **Purpose:** Time-boxed development iteration container.
- **Fields:**
  - `id` — UUID (Primary Key)
  - `project_id` — UUID (Foreign Key referencing `Project.id`)
  - `name` — VARCHAR(100), e.g., "Sprint 14"
  - `goal` — TEXT, statement of target delivery
  - `status` — ENUM (`PLANNING`, `ACTIVE`, `CLOSED`), default `PLANNING`
  - `start_date` — TIMESTAMP
  - `end_date` — TIMESTAMP
  - `committed_points` — INTEGER, nullable; frozen at activation as the sum of `story_points` over the sprint's tickets. `NULL` while `PLANNING`.
  - `completed_points` — INTEGER, nullable; frozen at close as the sum of `story_points` over tickets that were `DONE`.
  - `closed_at` — TIMESTAMP, nullable
- **Rules:**
  - A project has **at most one** `ACTIVE` sprint at any point in time, enforced by a partial unique index on `(project_id)` where `status = 'ACTIVE'`.
  - `end_date` must be strictly after `start_date` (`422` otherwise). Date ranges of sprints within a project **may** overlap — the single-`ACTIVE` rule is the real constraint, not the calendar.
  - Allowed transitions: `PLANNING → ACTIVE → CLOSED`. `PLANNING → CLOSED` is allowed (abandoning an unstarted sprint, which performs no rollover). No transition out of `CLOSED`; reopening is not supported and returns `409`.
  - Closing is **manual**. Reaching `end_date` changes nothing on its own; the MVP ships no scheduler. The dashboard surfaces an overdue badge when `NOW() > end_date` and `status = ACTIVE`.
  - A sprint with tickets cannot be hard-deleted; delete the project instead.

### Entity: Ticket
- **Purpose:** Primary unit of work representing user stories, bugs, demo inquiries, or internal tasks.
- **Fields:**
  - `id` — UUID (Primary Key)
  - `ticket_number` — INTEGER, sequential identifier scoped per project
  - `project_id` — UUID (Foreign Key referencing `Project.id`)
  - `sprint_id` — UUID (Foreign Key referencing `Sprint.id`, Nullable) — the ticket's **current** sprint only. Historical membership lives in `SprintTicketHistory`.
  - `title` — VARCHAR(255), non-empty after trimming
  - `description` — TEXT, Markdown formatted, max 20,000 characters
  - `type` — ENUM (`STORY`, `BUG`, `DEMO_REQUEST`, `TASK`)
  - `status` — ENUM (`BACKLOG`, `SELECTED`, `IN_PROGRESS`, `DONE`)
  - `priority` — ENUM (`LOW`, `MEDIUM`, `HIGH`, `URGENT`), default `MEDIUM`
  - `story_points` — INTEGER, nullable, constrained to `{1, 2, 3, 5, 8, 13}`. `NULL` means unestimated and contributes 0 to velocity.
  - `creator_id` — UUID (Foreign Key referencing `User.id`)
  - `assignee_id` — UUID (Foreign Key referencing `User.id`, Nullable) — must be a `ProjectMember` of the same project, else `422`
  - `resolution_notes` — TEXT, nullable; the free-text note supplied when a ticket is moved to `DONE`, and the "resolution notes" consumed by the AI report in §8
  - `rollover_count` — INTEGER, default 0
  - `created_at` — TIMESTAMP, immutable creation date
  - `first_sprint_entered_at` — TIMESTAMP, nullable; stamped the **first** time `sprint_id` goes from `NULL` to a value, never overwritten. Basis for `delayed_days`.
  - `completed_at` — TIMESTAMP, nullable; set when `status` becomes `DONE`, cleared when it moves away from `DONE`
  - `delayed_days` — INTEGER, nullable; **frozen at sprint close**, not computed on read
  - `meta` — JSON, bounded extension attributes (sender email, git commit hash, meeting preferences)
- **Rules:**
  - **Status transitions are unrestricted.** Any of the four states may move to any other, in either direction, so an accidental move is trivially undone. The only validation is that the value is a member of the enum.
  - `status` and `sprint_id` are **independent**. A ticket in an `ACTIVE` sprint may sit in `BACKLOG`; a ticket with `sprint_id = NULL` may be `IN_PROGRESS` (unplanned work). The board renders the two dimensions separately and never infers one from the other.
  - Moving a ticket to `BACKLOG` does **not** clear `sprint_id`. Removing a ticket from a sprint is an explicit `sprint_id = null` update.
  - `created_at` is never overwritten on sprint rollover.
  - `delayed_days` is written only by the sprint-close routine as `(closed sprint's end_date − first_sprint_entered_at).days`, clamped at 0. It is never recomputed from `NOW()`, so a historical report always reads the same.
  - `ticket_number` is allocated by an atomic `UPDATE project SET next_ticket_number = next_ticket_number + 1 ... RETURNING` inside the creating transaction, not by `MAX(...) + 1`.
  - `meta` is rejected with `422` if it is not a JSON object, nests deeper than 3 levels, or serializes to more than 8 KB.
  - `description` is stored raw and sanitized at render time (see FR-11), never sanitized on write, so the Markdown source stays editable.

### Entity: SprintTicketHistory
- **Purpose:** Immutable record of what each sprint actually contained at the moment it closed. Exists because `Ticket.sprint_id` is overwritten by rollover, which would otherwise make past velocity and past sprint composition unrecoverable.
- **Fields:**
  - `id` — UUID (Primary Key)
  - `sprint_id` — UUID (Foreign Key referencing `Sprint.id`)
  - `ticket_id` — UUID (Foreign Key referencing `Ticket.id`)
  - `status_at_close` — ENUM, the ticket's status when the sprint closed
  - `story_points_at_close` — INTEGER, nullable
  - `was_completed` — BOOLEAN, `status_at_close == DONE`
  - `recorded_at` — TIMESTAMP
- **Rules:** Composite unique constraint on `(sprint_id, ticket_id)`. Rows are written once by the close routine and are never updated or deleted while the project exists.

### Entity: SprintReport
- **Purpose:** Post-sprint evaluation and retrospective artifact compiled from ticket history.
- **Fields:**
  - `id` — UUID (Primary Key)
  - `sprint_id` — UUID (Foreign Key referencing `Sprint.id`, Unique)
  - `goal_summary` — TEXT
  - `completed_summary` — TEXT
  - `blockers_and_rollovers` — TEXT
  - `velocity_summary` — TEXT, narrative around `committed_points` vs. `completed_points`
  - `faculty_notes` — TEXT, reserved for reviewer / instructor evaluation
  - `generation_status` — ENUM (`PENDING`, `OK`, `FAILED`), default `PENDING`
  - `generated_by` — ENUM (`SERVER`, `MCP_CLIENT`, `HUMAN`), nullable
  - `is_finalized` — BOOLEAN, default False
  - `finalized_by` — UUID (Foreign Key referencing `User.id`, Nullable)
  - `finalized_at` — TIMESTAMP, nullable
  - `created_at` — TIMESTAMP
- **Rules:**
  - Exactly one report per sprint, created by the close routine. Because close is rejected on an already-`CLOSED` sprint, a duplicate report can never be created.
  - Any project member may `PUT` the draft while `is_finalized = False`. Once finalized, `PUT` returns `409`.
  - Only an `OWNER` may finalize. Un-finalizing is **not** supported — the point of finalization is that the artifact stops moving.
  - `faculty_notes` is a plain text field with no special permission: any project member may fill it before finalization. A "faculty reviewer" is modeled as an ordinary `MEMBER` invited to the project, not as a distinct role.

---

## 7. API & Web Route Contract
*CRISP-DM connection: Modeling — define the executable boundary between the product behavior, backend services, and future implementation.*

### 0) Conventions
- All `/api/v1/*` routes accept **either** a session cookie or an `Authorization: Bearer <PAT>` header. The inbound GitHub webhook uses HMAC signature verification instead.
- Standard error codes: `401` unauthenticated or revoked token, `403` authenticated but insufficient role, `404` resource absent **or** caller is not a project member, `409` state conflict (duplicate slug/email, second `ACTIVE` sprint, re-closing a sprint, editing a finalized report), `422` validation failure.
- Every list endpoint takes `limit` (default 50, max 200) and `cursor`. MCP list tools default to `limit=20` to hold the token budget.

### 1) Web UI Routes (HTML Serving)
- `GET /login`, `GET /register` : Render the minimal authentication views.
- `GET /dashboard` : Renders the user's project list and personal MCP configuration snippet.
- `GET /projects/{slug}` : Renders the project board view with Kanban, Velocity, AI Report, and Settings tabs. Tabs the caller's role cannot use are rendered read-only rather than hidden.

### 2) Auth & Tokens
- `POST /api/v1/auth/register` : Registration. Returns a session cookie. `409` on duplicate email.
- `POST /api/v1/auth/login` : Login. Returns a session cookie. `401` on bad credentials, with an identical response time and message for unknown-email and wrong-password.
- `POST /api/v1/auth/logout` : Clears the session cookie.
- `GET /api/v1/tokens` : Lists the caller's `ApiToken` rows (`prefix`, `label`, `created_at`, `last_used_at`, `revoked_at`). Never returns plaintext.
- `POST /api/v1/tokens` : Issues a new PAT. **The plaintext token appears in this response only.** Backs the dashboard's token copy box.
- `DELETE /api/v1/tokens/{token_id}` : Revokes a token. Idempotent.

### 3) Projects & Membership
- `GET /api/v1/projects` : Projects the caller is a member of.
- `POST /api/v1/projects` : Project creation; the creator becomes `OWNER`. `409` on slug collision.
- `GET /api/v1/projects/{slug}` : Project detail including the caller's role.
- `PATCH /api/v1/projects/{project_id}` : Update name, `webhook_type`, `webhook_url`, `github_secret`. **OWNER only.**
- `DELETE /api/v1/projects/{project_id}` : Cascading delete. **OWNER only**, requires `?confirm=<slug>`.
- `GET /api/v1/projects/{project_id}/members` : List members and roles.
- `POST /api/v1/projects/{project_id}/members` : Add a member by email. **OWNER only.** `404` if no such user, `409` if already a member.
- `PATCH /api/v1/projects/{project_id}/members/{user_id}` : Change role. **OWNER only.** `409` if it would remove the last `OWNER`.
- `DELETE /api/v1/projects/{project_id}/members/{user_id}` : Remove a member. **OWNER only.** Their assigned tickets are left in place with `assignee_id` set to `NULL`.

### 4) Sprints
- `GET /api/v1/projects/{project_id}/sprints` : List sprints, filterable by `status`.
- `POST /api/v1/projects/{project_id}/sprints` : Create a sprint in `PLANNING`. **OWNER only.** `422` if `end_date <= start_date`.
- `GET /api/v1/sprints/{id}` : Sprint detail with point totals.
- `PATCH /api/v1/sprints/{id}` : Update `name`, `goal`, dates, or `status`. **OWNER only.** Activating freezes `committed_points`; `409` if another sprint in the project is already `ACTIVE`, or on any transition out of `CLOSED`.
- `POST /api/v1/sprints/{id}/close` : Close, snapshot history, roll over, and trigger the report draft. **OWNER only.** `409` if already `CLOSED`. Not idempotent by design.

### 5) Tickets & Board
- `GET /api/v1/projects/{project_id}/tickets` : Board query. Filters: `sprint_id` (including the literal `null`), `status`, `assignee_id`, `type`, `priority`. This is what backs both the board render and the MCP `list_my_tickets` tool, with `?view=summary` selecting the lean projection.
- `GET /api/v1/tickets/{id}` : Full ticket, including `description` and `meta`. Backs MCP `get_ticket`.
- `POST /api/v1/tickets` : Ticket creation (web modal, MCP tool, GitHub handler). Allocates `ticket_number` atomically.
- `PATCH /api/v1/tickets/{id}` : Update `title`, `description`, `type`, `priority`, `story_points`, `assignee_id`, **`sprint_id`**, `resolution_notes`, `meta`. This is the endpoint sprint planning uses to pull a backlog ticket into a sprint; it stamps `first_sprint_entered_at` on the first such move.
- `PATCH /api/v1/tickets/{id}/status` : Status-only transition, kept as a separate narrow endpoint for MCP and board dropdowns. Accepts an optional `resolution_notes`. Maintains `completed_at`.
- `DELETE /api/v1/tickets/{id}` : Hard delete. **OWNER only.** `409` if the ticket appears in any `SprintTicketHistory` row, since deleting it would corrupt a closed sprint's velocity.

### 6) Reports & Velocity
- `GET /api/v1/sprints/{id}/report` : Retrieve the report, including `generation_status`.
- `PUT /api/v1/sprints/{id}/report` : Save the draft. Any member. `409` if already finalized.
- `POST /api/v1/sprints/{id}/report/generate` : (Re)run the AI draft. Used by the dashboard's **Retry AI draft** button and by the MCP fallback path in §8.
- `POST /api/v1/sprints/{id}/report/finalize` : Mark `is_finalized = True` and stamp `finalized_by` / `finalized_at`. **OWNER only.** No un-finalize endpoint exists.
- `GET /api/v1/projects/{slug}/velocity` : Per-closed-sprint `committed_points`, `completed_points`, completed ticket count, and the rolling 3-sprint average, computed from `Sprint` totals and `SprintTicketHistory` — never from live `Ticket.sprint_id`.

### 7) Inbound Integrations
- `POST /api/v1/webhooks/github` : Accepts `push` and `pull_request` events. Verifies `X-Hub-Signature-256` against the project's `github_secret` and returns `401` on mismatch. Resolves `Closes #<n>` within that project only. Returns `200` for unmatched or already-`DONE` references so GitHub does not retry.

### 8) MCP Tool → Endpoint Mapping
The Stdio MCP server runs on the developer's machine and has **no direct access to the server's SQLite file**; every tool is a thin HTTPS client over the endpoints above, authenticating with the user's PAT.

| MCP tool | Calls |
|---|---|
| `list_my_tickets` | `GET /api/v1/projects/{project_id}/tickets?assignee_id=me&view=summary&limit=20` |
| `get_ticket` | `GET /api/v1/tickets/{id}` |
| `create_ticket` | `POST /api/v1/tickets` |
| `update_ticket_status` | `PATCH /api/v1/tickets/{id}/status` |

---

## 8. AI sprint-report behavior
*CRISP-DM connection: Modeling → Evaluation — specify how project data becomes an AI-assisted product behavior and how people will check its quality.*

### Where generation runs
The MVP supports **two generation paths**, both writing to the same `SprintReport` row and both recorded in `generated_by`:

1. **Server-side (default).** On sprint close, the backend calls the Claude Messages API with the assembled JSON payload. Requires `ANTHROPIC_API_KEY` in the environment. One call per sprint close, so cost scales with sprint count, not with user activity — this is why it does not contradict the client-side-AI principle in §2, which governs *interactive* parsing.
2. **MCP client fallback.** If `ANTHROPIC_API_KEY` is unset, close leaves the report with `generation_status = "PENDING"` and the dashboard shows **Draft via MCP**. A developer then asks their IDE to run the `generate_sprint_report` MCP tool, which fetches the payload, drafts in the client's own LLM session, and writes back through `POST /api/v1/sprints/{id}/report/generate`.

A team that configures no key still gets reports; a team that configures one requires no developer in the loop.

### Inputs provided to the model
- **Sprint Target:** Sprint goal, start date, end date, and `committed_points`.
- **Completed Work:** Array of tickets whose `SprintTicketHistory.was_completed` is true — `ticket_number`, `title`, `type`, `story_points_at_close`, `resolution_notes`.
- **Rollover & Blocker Array:** Array of non-completed tickets with `status_at_close`, `rollover_count`, and the frozen `delayed_days`.
- **Velocity Context:** `committed_points`, `completed_points`, and the previous three sprints' `completed_points` for trend framing.
- **System Instructions:** Directives enforcing strict factual grounding on the provided JSON — no ticket, number, date, or name may appear in the output unless it appears in the input, and the model must state "insufficient data" rather than infer a cause for a blocker.

### Output sections
- **Sprint goal:** Assessment of whether the stated goal was met, expressed against `completed_points` vs. `committed_points`.
- **Completed work:** Bulleted digest of resolved tickets **grouped by `type`** (`STORY`, `BUG`, `TASK`, `DEMO_REQUEST`). There is no `component` field in the domain model, so grouping by component is not available.
- **Velocity:** Committed vs. completed points, the delta, and the direction of the 3-sprint trend.
- **Next sprint goals:** Suggested focus areas derived from rollover items only.
- **Blockers:** Enumeration of stalled tickets, emphasizing those with `rollover_count >= 2` **or** `delayed_days >= 14` — the two thresholds the dashboard also uses to render an aging warning badge.
- **Faculty notes:** Dedicated section with prompts for reviewer feedback, left empty by the model.

### Failure behavior
- Generation runs **after** the close transaction commits. An API error, timeout (30s), or malformed response leaves the sprint closed and the report row present with `generation_status = "FAILED"` and empty text fields. The close call itself still returns `200`; the response body includes the generation status so the caller knows.
- The report is always hand-editable, so a permanently failing model never blocks a team from finishing a retrospective.

### Human review
- The report draft is rendered inside the web dashboard under the `AI Report` tab with `is_finalized = False`.
- Any project member may edit and save the draft, including `faculty_notes`, any number of times.
- An `OWNER` clicks `Finalize Report`, which stamps `finalized_by` and `finalized_at`. After that the report is read-only; there is no un-finalize.

### How quality is checked
- **Grounding test (automated):** a `pytest` case extracts every `#\d+` reference, every integer point value, and every ISO date from a generated draft and asserts each is present in the input payload. Any extra reference fails the build.
- **Refusal test (automated):** a payload with zero completed tickets must produce a report that says so rather than inventing completions.
- **Human spot-check:** for the first three sprints, an `OWNER` diffs the draft against the board before finalizing and records disagreements in `faculty_notes`. Three consecutive clean drafts is the bar for trusting the default path.

---

## 9. Non-functional and platform requirements
*CRISP-DM connection: Modeling → Deployment — define the operational conditions that allow the system to run, integrate, and remain maintainable.*

- [x] **Backend Framework:** Python 3.11+ with FastAPI and Uvicorn, run with **a single worker** (`--workers 1`). SQLite permits one writer; additional workers would produce `database is locked` under concurrent writes. Vertical scale only — see ADR-002.
- [x] **Concurrency model:** Routes that touch the database are defined `def` (not `async def`) so SQLAlchemy's synchronous session runs in FastAPI's threadpool instead of blocking the event loop. Outbound HTTP (webhooks, LLM) uses `httpx` inside `BackgroundTasks`.
- [x] **Web Frontend:** Jinja2 server-side rendering + Tailwind CSS (via CDN) + Alpine.js/HTMX.
- [x] **Database:** Embedded SQLite with Write-Ahead Logging (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;`) managed via SQLModel/SQLAlchemy. `foreign_keys` is off by default in SQLite and must be enabled per connection or every FK in §6 is decorative.
- [x] **Schema migrations:** Alembic from the first commit. The JSON `meta` column reduces migration frequency but does not eliminate it — `story_points`, `completed_at`, and `SprintTicketHistory` are themselves schema changes.
- [x] **Output sanitization:** Markdown rendered through `markdown-it-py` with HTML disabled, then `bleach` with an allow-list, applied to `description`, `goal`, and every report text field. Applied at render time, never at write time.
- [x] **Backups:** Nightly `sqlite3 .backup` to a timestamped file with 7-day retention. A naive `cp` of a WAL-mode database mid-write is not a consistent snapshot.
- [x] **Hosting Infrastructure:** AWS Lightsail ($5/month Ubuntu instance, 1GB RAM, 40GB SSD, static IPv4 included).
- [x] **Deployment:** Single container or local process running via Docker Compose (`docker compose up -d`). The SQLite file lives on a bind-mounted host volume so a container rebuild cannot destroy it.
- [x] **Secrets:** `ANTHROPIC_API_KEY`, `SESSION_SECRET`, and per-project `github_secret` supplied via environment / database, never committed. Absent `ANTHROPIC_API_KEY` degrades to the MCP fallback path in §8 rather than crashing at startup.
- [x] **Standard Stdio MCP Server:** Exposes lightweight tools (`list_my_tickets`, `get_ticket`, `create_ticket`, `update_ticket_status`, `generate_sprint_report`) as HTTPS clients over the §7 endpoints, authenticated with the user's PAT.
- [x] **Token Budgeting:** Two-tier projection schemas. `view=summary` returns `ticket_number`, `title` (truncated to 60 chars), `status`, `priority`, `story_points` only, with `limit` defaulting to 20 and capped at 200. The under-500-token claim is asserted in CI against `tiktoken` `cl100k_base`, not assumed.
- [x] **Rate limiting:** 60 requests/minute per token or session on `/api/v1/*`, and 10/minute on auth routes, via `slowapi`. Returns `429`.
- [x] **Automated Tests:** `pytest` suite. Most tests use file-backed SQLite in a `tmp_path` so WAL, `foreign_keys`, and the `ticket_number` allocation race are actually exercised; `sqlite:///:memory:` is used only for pure-unit schema tests where none of those apply. Every FR in §5 maps to at least one named test.

---

## 10. Initial validation plan & First slice
*CRISP-DM connection: Evaluation — decide what evidence will show that the product behavior and engineering claims are credible.*

### First implementation slice
- **Selected slice:** Registration and login, SQLite initialization in WAL mode with `foreign_keys=ON`, project creation, ticket creation through both the API and the web modal, and background webhook notification to Microsoft Teams / Slack / Discord.
- **Why this slice:** Proves the end-to-end value loop immediately — a user creates a ticket and observes feedback in their team chat room. It also forces the two riskiest primitives (per-project `ticket_number` allocation and non-blocking webhook dispatch) into the very first commit, where they are cheap to get wrong.
- **Deliberately deferred to slice 2:** sprints, velocity, rollover, AI reporting, GitHub inbound, PAT issuance.

### Validation plan
Each claim in this document that could be wrong, and the evidence that settles it:

| # | Claim under test | Method | Pass condition |
|---|---|---|---|
| V-1 | SQLite handles the team's write load | `pytest` harness issuing 50 concurrent ticket creations against a file-backed DB | Zero `database is locked` errors, 50 distinct consecutive `ticket_number` values, p95 write under 50 ms |
| V-2 | Read latency is adequate (replaces the unmeasured "0.1 ms" claim in §2) | Benchmark a board query over a seeded 5,000-ticket project | p95 under 20 ms; record the actual number and correct §2 with it |
| V-3 | The MCP list projection fits the token budget | `tiktoken` assertion over a 20-ticket summary response | Under 500 tokens with realistic 60-char titles |
| V-4 | Webhook failure cannot damage the product | Point `webhook_url` at a black-hole endpoint, then create a ticket | Creation returns under 500 ms with `201`; ticket exists; one `WARNING` logged after retries |
| V-5 | Close is transactionally safe | Force an exception between rollover and sprint status update | No partial state: either everything committed or nothing; no double-incremented `rollover_count` |
| V-6 | Velocity survives rollover | Close three sprints in sequence, then re-read sprint 1's velocity | Identical figures before and after sprints 2 and 3 close |
| V-7 | The AI draft is grounded | Automated grounding and refusal tests from §8 over 10 synthetic sprints | Zero ungrounded ticket references; the empty-sprint case reports zero completions rather than inventing them |
| V-8 | Authorization holds | Parameterized test matrix of (non-member, MEMBER, OWNER) × every mutating endpoint | Every cell matches the permission matrix in §6 |
| V-9 | Rendered Markdown is inert | Ticket description containing `<script>` and `<img onerror=...>`, rendered on board and report | No script execution; payload visible as text |

### Known open risks
- **No scheduler.** If a team never clicks `Close Sprint`, nothing closes and velocity is never recorded. Mitigated only by an overdue badge in the UI. Revisit if teams actually forget.
- **Single instance, single worker.** No horizontal scale path without migrating off SQLite. Accepted for the MVP; the SQLModel layer keeps a PostgreSQL migration mechanical.
- **Story points are optional.** A team that never estimates gets `completed_points = 0` and a meaningless velocity. The dashboard should show "unestimated: n tickets" next to any velocity figure rather than implying zero throughput.

---

## 11. Decision log
*CRISP-DM connection: Improve — preserve the reasoning behind changes so later iterations can build on evidence instead of repeating old uncertainty.*

| ID | Decision | Alternatives considered | Reason | Consequence |
|---|---|---|---|---|
| **ADR-001** | Python FastAPI Backend | Rust, Node.js | Fast development cycle, native async I/O, Pydantic validation, and rich AI/MCP ecosystem. | Slightly higher memory footprint than Rust, but negligible for MVP. |
| **ADR-002** | Embedded SQLite in WAL Mode | PostgreSQL RDS, DynamoDB | No network round-trip on reads, zero installation overhead, and no monthly DB hosting bills. | WAL gives concurrent **readers**, not concurrent writers. Forces a single Uvicorn worker and a `busy_timeout`; caps the product at one instance. Backups must use `sqlite3 .backup`, not `cp`. |
| **ADR-003** | Server-Rendered Jinja2 + Tailwind UI | Next.js SPA, React | Eliminates frontend build tooling and client-server sync code; allows building the 3 core screens in hours. | Less client-side state interactivity compared to a full SPA. |
| **ADR-004** | AWS Lightsail ($5/mo) Hosting | AWS EC2 (t4g.small), AWS Lambda | Flat-rate pricing with IPv4, SSD, and traffic bundled; avoids Lambda cold-start latency and EC2 hidden costs. | Single instance limits auto-scaling (sufficient for team MVP). |
| **ADR-005** | Automatic Sprint Rollover with Aging, `status` preserved | Return to Backlog, reset to SELECTED, discard | Uncompleted work stays visible **and stays in-flight** — an `IN_PROGRESS` ticket crossing a sprint boundary is still in progress. Accountability via `rollover_count` and `delayed_days`. | Close must auto-create the target sprint when none exists, and must promote it to `ACTIVE` only after the closing sprint flips to `CLOSED` to respect the single-`ACTIVE` invariant. Close is therefore non-idempotent and returns `409` on re-run. |
| **ADR-006** | Single Table with a JSON `meta` column | Class-table inheritance | Flexible capture of external webhook and AI attributes without a migration per integration. | JSON fields require SQLite JSON operators for deep querying. Alembic is still required for first-class fields. Named `meta` because `metadata` is reserved on SQLAlchemy declarative classes. `meta` is capped at 8 KB and 3 levels to keep it from becoming a shadow schema. |
| **ADR-007** | Two-Tier MCP Output Projections | Full-payload ticket dumps | Unconstrained ticket list dumps exhaust developer context windows and inflate token costs. | List tools return lean summary tuples; full descriptions require explicit single-ticket queries. |
| **ADR-008** | Server-side AI report by default, MCP client as fallback | Server only, client only | A retrospective must be readable by non-technical stakeholders who have no IDE, so it cannot depend on a developer running a tool. But a team without an API key should still get drafts. | Two code paths to maintain and a `generated_by` field to disambiguate. Server cost is one call per sprint close, which is bounded and small. |
| **ADR-009** | `delayed_days` measured from `first_sprint_entered_at` and frozen at close | `NOW() - created_at`, live computation | `NOW() - created_at` charges a ticket for time it spent unscheduled in the backlog, and makes a closed sprint's report change every time it is opened. | Requires a `first_sprint_entered_at` field stamped exactly once. A ticket that never entered a sprint has `delayed_days = NULL`, which the UI must render as "—" rather than 0. |
| **ADR-010** | Separate `ApiToken` table with hashed, revocable tokens | Single `pat_hash` on `User`, stateless JWT | Revoking IDE access must not log a user out of the dashboard, and a leaked token must be killable without rotating everything. | One extra table and a token-lookup on every MCP request, mitigated by indexing `token_hash` and throttling `last_used_at` writes. Plaintext is shown exactly once; a lost token must be reissued. |
| **ADR-011** | `SprintTicketHistory` snapshot at close | Recompute velocity from live `Ticket.sprint_id`, immutable `original_sprint_id` | Rollover overwrites `sprint_id`, so live data cannot answer "what did sprint 3 actually contain". Velocity that silently rewrites history is worse than no velocity. | One row per ticket per sprint closed. Blocks hard-deleting a ticket that appears in a closed sprint (`409`). |
| **ADR-012** | Free status transitions; `status` and `sprint_id` independent | Enforced sequential workflow, `BACKLOG` implying `sprint_id = NULL` | Transition guards mostly generate `422`s when someone fixes a mis-click, and coupling the two fields makes unplanned in-flight work unrepresentable. | The board must render sprint membership and column position as separate dimensions. Nothing prevents a nonsensical combination; correctness is a team convention, not a constraint. |
| **ADR-013** | Background, retrying webhook dispatch | Synchronous dispatch, transactional dispatch | A chat-server outage must never become a product outage or a lost ticket. | Delivery is at-most-once after 3 retries and can be silently lost; failures are visible only in server logs. No delivery-status UI in the MVP. |
| **ADR-014** | Gmail intake deferred; GitHub inbound in MVP | Both in MVP, neither in MVP | GitHub parsing is one signed endpoint and one regex; Gmail intake needs OAuth, polling or push infrastructure, and an address-to-project mapping model — a second product. | The §1 problem statement's inbound-email pain is unaddressed in v1. Non-technical intake runs entirely through the web modal. |