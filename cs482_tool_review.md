# C04 Product Research & Specification: Kanban Flow

## 1. Intended Users & Stakeholders

Kanban Flow is built for a small engineering team that has to stay legible to people who are not on it. The tools reviewed in §2 are all browser-first, which serves the second group well and the first group poorly.

| Person or group | Need or responsibility | How the app helps |
|---|---|---|
| **Software Engineers** | Track and update assigned work without leaving the development environment; avoid the context switch that makes boards go stale. | Query tickets and change status through MCP tools inside Cursor or Claude Desktop, and receive completion cards in the team chat channel. A `Closes #<n>` in a commit message updates the board with no interaction at all. |
| **Team Lead / Project Owner** | Plan a sprint, keep exactly one iteration in flight, close it cleanly, and account for what did not finish. | Sprint create / activate / close restricted to the `OWNER` role, with closure automatically rolling unfinished work forward, freezing velocity figures, and producing a retrospective draft. |
| **Non-Technical Leads & Ops** | File bug reports and customer demo requests, and see delivery status without an IDE, a terminal, or a git account. | A server-rendered dashboard showing the Kanban board and velocity table, plus a `[+ New Ticket]` modal that files work directly into the backlog. |
| **Faculty Reviewers** | Evaluate sprint performance and velocity, and judge whether reported progress is real. | Per-sprint committed-vs-completed story points that never change retroactively, aging metrics (`rollover_count`, `delayed_days`), and an AI-drafted retrospective that a human finalizes. Invited as an ordinary project `MEMBER`; no separate reviewer role exists. |

**Non-users, stated explicitly:** multi-team organizations, external customers (they reach the team through a lead, never directly), and anyone requiring SSO or audit compliance. Every one of those is a reason the reviewed tools carry weight this project does not need.

---

## 2. Product Research & Tool Reviews

To design an effective Engineering Workflow application for CS 482, four project tracking tools were inspected: **Linear**, **Trello**, **GitHub Projects**, and **Jira Software**.

Each tool was inspected between 2026-09-04 and 2026-09-12 by reading its public product documentation and by creating a throwaway project in its free tier, then walking a single story from creation through completion. Linear and Jira were examined most closely because they are the two that actually implement sprint closure and velocity; Trello was examined as the minimal baseline, and GitHub Projects as the version-control-native case.

---

### Tool 1: Linear
* **Inspected Tool / Reference:** [Linear Official Product Documentation](https://linear.app/docs)
* **Core Workflow:**
  * Highly keyboard-driven, minimal-latency issue tracking tailored for fast-paced engineering teams. 
  * Linear organizes execution through continuous development cycles (sprints) that automatically roll over unfinished tasks into the next cycle.
* **Projects & Stories/Tasks Representation:**
  * **Projects:** Time-bound initiatives containing roadmaps, target dates, project leads, and child issues.
  * **Issues/Tasks:** Minimalist single-entity model. Each issue carries an auto-generated **team-scoped** identifier (e.g., `ENG-101`, where `ENG` is the team key, not the project), markdown description, assignee, priority (`Urgent`, `High`, `Medium`, `Low`), and story estimates (points or t-shirt sizes). Note that Linear's `Project` is a cross-team initiative, a separate concept from the identifier prefix.
* **Sprint, Board, or Status Workflow:**
  * Uses fixed-cadence **Cycles** (equivalent to Sprints). Default workflow states are `Backlog`, `Todo`, `In Progress`, `Done`, `Canceled`; teams commonly add custom states such as `In Review`. States are configurable, but Linear imposes **no transition graph** — an issue may be moved to any state directly.
  * Issues assigned to a cycle appear on a responsive Kanban board.
* **Reporting & Progress-Tracking Features:**
  * Real-time burn-up charts, team velocity tracking (completed estimates per cycle), and cycle completion percentage progress bars.
* **How Blockers, Decisions, Notes, or Risks Appear:**
  * **Blockers:** First-class issue relations (`blocks` / `blocked by`). A blocked issue visually renders a persistent dependency badge.
  * **Decisions/Notes:** Threaded comments, issue activity audit logs, and project status updates (`On Track`, `At Risk`, `Off Track`).
* **What seems useful for CS 482:**
  * Automatic rollover of uncompleted work into the subsequent cycle.
  * Lightweight keyboard-first interactions and minimal database entity complexity.
  * Explicit tracking of blocked dependencies.
* **What seems unnecessary for the course MVP:**
  * Multi-team workspace hierarchies, enterprise SSO, SLA tracking, and complex multi-project roadmap timelines.

---

### Tool 2: Trello
* **Inspected Tool / Reference:** [Trello Guide & Product Tour](https://trello.com/guide)
* **Core Workflow:**
  * Visual, freeform Kanban workflow based on Boards, Lists (columns), and Cards. Users manually drag and drop cards across columns to represent progress.
* **Projects & Stories/Tasks Representation:**
  * **Projects:** Represented broadly as a single "Board" or grouped under a "Workspace".
  * **Tasks:** "Cards" containing a title, markdown description, color-coded labels, member assignments, checklists, and due dates.
* **Sprint, Board, or Status Workflow:**
  * Trello has no native concept of a "Sprint" or "Cycle" out of the box. Teams emulate sprints by manually creating lists such as `Sprint Backlog`, `Doing`, and `Done`, or by adding third-party Power-Ups.
* **Reporting & Progress-Tracking Features:**
  * Minimal native velocity or burndown reporting. Progress tracking is strictly checklist completion bars (`3/5 completed`) unless external reporting plugins are subscribed to.
* **How Blockers, Decisions, Notes, or Risks Appear:**
  * Unstructured. Teams must use red color labels, card comments, or custom text fields to signal blockers.
* **What seems useful for CS 482:**
  * Clean, universally understood 4-column visual board layout (`Backlog`, `Selected`, `In Progress`, `Done`).
  * Checklists and subtasks inside work items.
* **What seems unnecessary for the course MVP:**
  * Unrestricted freeform list creation (which leads to inconsistent process states).
  * Power-Up ecosystem, custom background themes, and complex card cover designs.

---

### Tool 3: GitHub Projects (Issues & Projects v2)
* **Inspected Tool / Reference:** [GitHub Projects Documentation](https://docs.github.com/en/issues/planning-and-tracking-with-projects)
* **Core Workflow:**
  * Deeply coupled with version control (Git). Development progress is driven by commit messages (e.g., `Closes #42`), pull requests, and code review lifecycles.
* **Projects & Stories/Tasks Representation:**
  * **Projects:** Spreadsheet-style or board-style views grouping issues across repositories.
  * **Stories/Tasks:** "Issues" containing unique sequential IDs (`#12`), markdown bodies, labels, milestones, assignees, and pull request links.
* **Sprint, Board, or Status Workflow:**
  * Milestones and Iteration fields enable sprint tracking. Column statuses move automatically via built-in workflow triggers (e.g., moving to `Done` when a linked PR is merged).
* **Reporting & Progress-Tracking Features:**
  * Milestone progress bars, issue closed vs. open counts, and iteration burn-up charts.
* **How Blockers, Decisions, Notes, or Risks Appear:**
  * Commit references and PR discussion threads record decisions. Issue cross-references (e.g., "duplicate of #5", "blocked by #8") visualize risks.
* **What seems useful for CS 482:**
  * Git commit / PR message linking to close tickets automatically.
  * Direct table and Kanban dual projections.
* **What seems unnecessary for the course MVP:**
  * Complex cross-repository aggregation, enterprise SAML access controls, and custom GraphQL workflow automation rules.

---

### Tool 4: Jira Software (Scrum board template)
* **Inspected Tool / Reference:** [Jira Software](https://www.atlassian.com/software/jira) · [Jira Software Cloud Documentation](https://support.atlassian.com/jira-software-cloud/)
* **Core Workflow:**
  * Issues accumulate in a **Backlog**, which is a screen separate from the board. A lead creates a sprint container there, drags issues into it, then clicks **Start sprint**, which opens a dialog requiring a sprint name, a **sprint goal**, and start/end dates.
  * Execution happens on the board. The lead ends the iteration with **Complete sprint**, which generates a report. Planning and execution are deliberately different surfaces.
* **Projects & Stories/Tasks Representation:**
  * **Projects:** Each project has a short key (`PAY`) and every issue receives a **project-scoped sequential key** (`PAY-123`) that people say aloud in standups.
  * **Stories/Tasks:** Typed issues — Epic, Story, Task, Bug, Sub-task — forming an Epic → Story → Sub-task hierarchy. Estimation is a first-class `Story Points` field. Grouping uses **Components** (a curated per-project enum) and free-form **Labels**, which are intentionally different mechanisms.
* **Sprint, Board, or Status Workflow:**
  * Board columns are a *mapping onto* statuses rather than statuses themselves, so several statuses can share one column.
  * The underlying workflow is a configurable directed graph with conditions, validators, and post-functions, so a transition can be made illegal or can fire side effects. Of the four tools reviewed, **Jira is the only one that can forbid a status change.**
  * A board runs **one active sprint at a time** by default; parallel sprints is an opt-in administrative setting.
* **Reporting & Progress-Tracking Features:**
  * The richest of the four by a wide margin: **Burndown chart**, **Velocity chart** (paired bars of *committed* vs. *completed* story points per sprint — the canonical definition of velocity), **Sprint report**, Cumulative Flow Diagram, Control chart, and Epic burndown.
  * The velocity chart is read as a trailing average over the last three to five sprints.
* **How Blockers, Decisions, Notes, or Risks Appear:**
  * **Blockers:** two separate mechanisms. **Flagging** marks an issue visually blocked on the board *without changing its status*, and typed **issue links** (`blocks` / `is blocked by`, `relates to`, `duplicates`) express dependency as queryable data.
  * **Decisions:** notably absent from Jira itself. Decision records and retrospective write-ups live in linked Confluence pages, which places the reasoning behind a decision one product away from the work it governs.
* **What seems useful for CS 482:**
  * The **Complete sprint** dialog, which refuses to let a sprint end ambiguously and forces an explicit destination for unfinished issues.
  * The committed-vs-completed definition of velocity, adopted verbatim.
  * Project-scoped sequential issue keys, and the sprint goal as a required field rather than an optional note.
  * Flagging as a blocker signal that is **orthogonal to status**, which avoids a "Blocked" column that destroys information about where the work actually stalled.
* **What seems unnecessary for the course MVP:**
  * Configurable workflow schemes, permission / notification / screen schemes, custom field administration, the Epic hierarchy, parallel sprints, JQL, and every chart except the velocity numbers.
  * Jira's configurability is precisely why it requires a dedicated administrator, and a course project has none.

---

## 3. Product Patterns to Adopt and Reject

### Patterns to Adopt
1. **Linear's Cycle Rollover & Aging Mechanism:**
   * Automatically carry unfinished stories forward on sprint closure, **preserving each ticket's `status`** so an in-flight issue stays in flight across the boundary. Linear does this without asking; Jira's equivalent stops to prompt for a destination, and the prompt is a decision a small team makes identically every time.
   * Delay is measured from **`first_sprint_entered_at`**, not `created_at`, so a ticket is not charged for the weeks it sat unscheduled in the backlog, and the value is **frozen at close time** so a historical sprint report reads the same a month later.
2. **Linear & GitHub's Developer-First Interface:**
   * Instead of forcing developers into a web browser, provide an MCP (Model Context Protocol) tool interface for Cursor / Claude Desktop and webhook push notifications to team chat (Teams, Slack, Discord).
3. **GitHub's Git-Driven Status Change:**
   * Adopt the closing-keyword pattern verbatim. A signed inbound webhook parses `push` and `pull_request` payloads for `Closes #<ticket_number>` and advances the matching ticket to `DONE`, so the board follows the repository instead of being maintained beside it.
4. **Trello's Intuitive 4-Column Board Layout — columns fixed, transitions free:**
   * Fix the **column set** to `BACKLOG` → `SELECTED` → `IN_PROGRESS` → `DONE` so every project reads the same way, but impose **no transition rules**: any state may move to any other, in either direction. Three of the four tools reviewed — Trello, GitHub Projects, and Linear — ship with no transition graph at all, and their users are fine; only Jira can forbid a move, and a guard mostly produces a `422` when someone undoes a mis-click.
5. **Single-Table Entity with JSON Extensibility:**
   * Consolidate stories, bugs, and inbound tasks into a single `Ticket` entity with a JSON `meta` column for variable fields (commit hashes, chat message IDs). Named `meta`, not `metadata`, because `metadata` is a reserved attribute on SQLAlchemy / SQLModel declarative classes. The column is capped at 8 KB and 3 levels of nesting so it cannot become a shadow schema.

### Patterns to Reject or Simplify
1. **Manual Drag-and-Drop Front-End Overhead:**
   * Reject heavy client-side drag-and-drop SPA frameworks (React/Next.js). Use server-rendered HTML (FastAPI + Jinja2 + Tailwind) with simple status dropdown buttons.
2. **Unbounded Freeform Columns:**
   * Reject Trello's arbitrary list creation. The four column names are schema, not data, so a board is legible without reading its configuration. This constrains the **column set only** — see Adopt #4 on why transitions stay unconstrained.
3. **Configurable Workflow Engines:**
   * Reject Jira-style transition graphs with conditions, validators, and post-functions, and Linear's per-team workflow customization. Configurability is the reason those tools need an administrator, and a course project has none.
4. **Complex In-App Notification Center:**
   * Eliminate read/unread inbox databases. Route notifications directly to where the team already communicates (Teams/Slack/Discord webhooks). Dispatch runs as a background task with a 5s timeout and three retries, and a failed delivery never reverses the ticket change that triggered it.
5. **Multi-Tenant Enterprise RBAC:**
   * Reject complex permission trees in favor of two project-level roles mapped by user email: `OWNER` for administrative actions (project settings, membership, sprint create/activate/close, report finalize) and `MEMBER` for work actions (ticket CRUD, status changes, report drafting).
6. **Rich Reporting Surfaces:**
   * Reject Linear's burn-up charts and Jira's six report types (burndown, velocity chart, sprint report, cumulative flow, control chart, epic burndown). Velocity ships as numbers and a table; the retrospective ships as text. A chart is the one artifact that can be delivered to neither of this product's two surfaces — the IDE and the chat channel.

---

## 4. Proposed MVP Capabilities for Kanban Flow

### 1. Projects, Membership & Access
- Minimal user account registration using 3 fields: `name`, `email`, and `password_hash`.
- Project boundaries identified by unique URL slugs (e.g., `/projects/payment-gateway`).
- Direct member mapping via registered email address without multi-step invite links.
- Two roles per project: `OWNER` (project settings, membership, sprint create/activate/close, report finalize) and `MEMBER` (ticket work, report drafting). A non-member receives `404` on read and `403` on write, so project existence is never leaked.

### 2. User Stories & Tickets
- Single relational `Ticket` entity supporting types: `STORY`, `BUG`, `DEMO_REQUEST`, and `TASK`.
- Project-scoped sequential ticket numbers (e.g., `#101`), allocated by an atomic counter increment rather than `MAX(...) + 1`, so concurrent creation cannot collide.
- Priority levels: `LOW`, `MEDIUM`, `HIGH`, `URGENT`.
- Story points estimation field (`story_points: INT`, Fibonacci scale) to support velocity tracking. Optional — an unestimated ticket contributes 0.
- Extensible `meta` (JSON) field for external context (commit hashes, chat message IDs), bounded at 8 KB and 3 levels of nesting.
- Markdown descriptions, rendered through an allow-list sanitizer so a ticket body submitted by a non-technical user cannot inject script into the dashboard.

### 3. Sprint Lifecycle & Kanban Board
- Fixed 4-column board: `BACKLOG`, `SELECTED`, `IN_PROGRESS`, `DONE`, with **free transitions** between any two states in either direction.
- `status` and sprint membership are **independent axes**: a ticket in an active sprint may sit in `BACKLOG`, and unplanned work may be `IN_PROGRESS` with no sprint at all.
- Projects enforce a maximum of **one active sprint** at any time.
- **Sprint Closure & Automated Rollover:** Closing a sprint moves every unfinished item (`status != 'DONE'`) into the next sprint — **auto-creating that sprint if none exists** — while **preserving each ticket's `status`**, incrementing `rollover_count`, and freezing `delayed_days`. The whole move runs in one transaction.
- Warning tags (`STALE_BLOCKER`) visually triggered on cards with `rollover_count >= 2` **or** `delayed_days >= 14`.

### 4. Progress & Velocity Tracking
- Sprint-level velocity following Linear's and Jira's shared definition: `committed_points` frozen at sprint activation vs. `completed_points` frozen at sprint closure.
- A `SprintTicketHistory` snapshot row is written per ticket at close, recording `status_at_close` and `story_points_at_close`. This exists because rollover overwrites a ticket's `sprint_id` — without the snapshot, "what did Sprint 3 actually contain" becomes unanswerable and historical velocity would silently rewrite itself.

### 5. Git-Driven Ticket Closure (GitHub Inbound Webhook)
- A single signed endpoint accepts GitHub `push` and `pull_request` events, verifying `X-Hub-Signature-256` against a per-project secret.
- Commit messages containing `Closes #<ticket_number>` advance the matching ticket to `DONE` and queue a chat notification, closing the gap between repository state and board state without any human board interaction.
- Ticket numbers are project-scoped, so resolution is constrained to the project bound to that webhook secret.

### 6. Multi-Interface Accessibility (IDE MCP + Web Dashboard)
- **Developer Interface (MCP):** Stdio MCP Server allowing developers to query assigned tickets and update statuses from Cursor / Claude Desktop without opening a browser. The server runs on the developer's machine and reaches the backend over HTTPS, authenticating with a revocable personal access token.
- **Revocable tokens:** a dedicated `ApiToken` record per token, stored as a SHA-256 hash with only a display prefix retained. Plaintext is shown exactly once at issue time; revoking a developer's IDE access does not log them out of the dashboard.
- **Stakeholder / Non-Technical Interface (Web UI):** Server-rendered dashboard (FastAPI + Jinja2) displaying the visual Kanban board, the velocity table, and an instant `[+ New Ticket]` submission modal.

### 7. AI Sprint Retrospective Draft
- On sprint close, a structured Markdown draft is generated covering the sprint goal, completed work grouped by ticket type, velocity (committed vs. completed), rollover blockers, and a reviewer notes section.
- **Two generation paths.** Server-side by default when an API key is configured; otherwise the draft is left pending and a developer can produce it through an MCP tool using their own IDE LLM session. A team with no key still gets reports; a team with one needs no developer in the loop.
- The draft is always hand-editable. Any member may save it; only an `OWNER` may finalize, after which it becomes read-only.
- Generation runs **after** the close transaction commits, so a model outage can never leave a sprint half-closed.

---

## 5. Deliberately Excluded Capabilities

The following features are intentionally excluded to protect project velocity and prioritize course requirements:

1. **Complex Authentication & Recovery Flows:**
   * Excluded: OAuth2 social logins, magic links, email verification loops, and password recovery workflows.
   * Reason: 3-field authentication is sufficient for course evaluation and internal team use.
2. **Email / Gmail Inbound Triage:**
   * Excluded: mailbox polling or push ingestion that converts inbound customer email into tickets.
   * Reason: GitHub inbound parsing is one signed endpoint and one regular expression; email intake needs OAuth, delivery infrastructure, and an address-to-project mapping model — effectively a second product. Non-technical intake runs entirely through the web modal in v1.
3. **Reporting Charts:**
   * Excluded: burndown charts, burn-up charts, cumulative flow diagrams, control charts, and epic burndowns — the reporting surfaces Linear and Jira lead with.
   * Reason: this product's two delivery surfaces are the IDE and the chat channel, and a chart renders in neither. Velocity is delivered as numbers; the retrospective as text.
4. **Configurable Workflow & Transition Rules:**
   * Excluded: per-project status configuration, transition guards, conditions, validators, and post-functions.
   * Reason: three of the four tools reviewed impose no transition rules at all; only Jira does, and it pays for that flexibility with an administrator. The engineering effort belongs in the sprint-close transaction instead.
5. **Board Card Ordering & WIP Limits:**
   * Excluded: within-column ranking, manual card ordering, and work-in-progress caps.
   * Reason: ranking requires a rank field and a reordering interaction, which is exactly the drag-and-drop machinery excluded below.
6. **In-App Notification Bell System:**
   * Excluded: In-app notification centers, read/unread states, and badge counters.
   * Reason: Replaced entirely by push webhooks to team chat channels.
7. **Direct File Storage & Attachment Management:**
   * Excluded: S3 integration, binary chunking, and file previewers.
   * Reason: Team members can link external URLs, PRs, or logs directly in markdown descriptions.
8. **Drag-and-Drop SPA Frontend:**
   * Excluded: React/Vue drag-and-drop state synchronizers.
   * Reason: Replaced by lightweight server-side rendered HTML with click-to-move status dropdowns.
9. **First-Class Blocker Relations:**
   * Excluded: Linear's `blocks` / `blocked by` issue relations and Jira's flagging, both of which the reviews above flagged as genuinely useful.
   * Reason: a dependency graph needs its own entity, cycle detection, and a board affordance to be worth anything, and at course-team scale the information it carries is already available more cheaply. A ticket that is stuck reveals itself through `rollover_count >= 2` and `delayed_days >= 14`, which the sprint close computes for free and the AI retrospective already surfaces. Revisit if a team reports a blocker the aging metrics failed to catch.
10. **Checklists & Sub-Tasks Inside Work Items:**
   * Excluded: Trello-style checklists, Jira sub-tasks, and any parent/child ticket hierarchy.
   * Reason: sub-structure inside a ticket splits progress across two incompatible units — a ticket is `IN_PROGRESS` while its checklist says `3/5` — and velocity would then need a rule for which one counts. A ticket too large to track as one unit should be split into two tickets, which the flat model already supports.
11. **Keyboard-First Navigation:**
   * Excluded: Linear's command palette and global keyboard shortcuts.
   * Reason: the shortcuts exist to make a *browser* fast, and this product's answer to browser friction is to leave the browser entirely. A developer's fast path is the MCP tool inside the IDE; investing in dashboard shortcuts would optimize the surface the product is trying to make optional.
12. **Server-Side LLM Calls for Routine Interactive Work:**
   * Excluded: server-side model calls for natural-language parsing, triage, or any per-interaction task.
   * Reason: interactive parsing — turning *"mark #42 done, resolved in abc1234"* into a structured tool call — happens in the developer's existing Cursor / Claude session at no cost to the server. The one server-side model call in the product is the sprint retrospective, which fires **once per sprint close**, so its cost scales with sprint count rather than with user activity.

---

## 6. Success Criteria

Observable checks, each stated so that it can be failed. "Easy to use" is deliberately absent; where usability is claimed, the measurement is named.

- [ ] An engineer can move a ticket from `IN_PROGRESS` to `DONE` entirely inside Cursor or Claude Desktop through an MCP tool call, with no browser open at any point in the interaction.
- [ ] Starting from an authenticated board, a non-technical user can file a ticket in **5 interactions or fewer** (open modal, title, type, description, submit) and see it appear in `BACKLOG` without a full page reload. Verified by a scripted UI walkthrough, not by asking the user whether it felt easy.
- [ ] A commit message containing `Closes #<ticket_number>` moves the matching ticket to `DONE` and delivers a card to the configured chat channel, at **p95 ≤ 60 seconds** from webhook receipt to delivery over 20 scripted runs.
- [ ] Closing a sprint carries every non-`DONE` ticket into the next sprint — creating that sprint if none exists — preserving each ticket's `status`, incrementing `rollover_count`, and freezing `delayed_days`. Re-reading the closed sprint a week later returns identical figures.
- [ ] Per-sprint `committed_points` and `completed_points` remain unchanged after two subsequent sprints have closed. This is the check that the `SprintTicketHistory` snapshot actually works; without it, velocity would silently rewrite itself.
- [ ] Pointing a project's chat webhook at a black-hole endpoint does not prevent, delay beyond 500 ms, or reverse a ticket update.
- [ ] Every ticket number, point total, and date appearing in a generated retrospective is present in the JSON payload given to the model, asserted by an automated grounding test. A sprint with zero completed tickets produces a report saying so rather than inventing completions.
- [ ] A user who is not a project member receives `404` on read and `403` on write for that project's resources; a `MEMBER` receives `403` on sprint closure, membership changes, and report finalization.
- [ ] `list_my_tickets` returns its default 20-ticket summary in **under 500 tokens**, measured with a real tokenizer in CI rather than estimated.

---

## 7. Short List of MVP Implications for Kanban Flow

1. **A sprint must be a real object, not a date range.**
   * GitHub Projects tracks iterations as a *field* on an item, which cannot answer "what did Sprint 3 contain" once items move — and that is precisely the question velocity depends on. Linear's Cycle is a real container, and this app follows it: a `Sprint` entity plus a `SprintTicketHistory` snapshot written at close.
2. **Sprint close is the product's most important moment, and it should not ask.**
   * Jira gets the timing right and the ergonomics wrong. Adopt Linear's automatic behavior and go one step further: if no next sprint exists, create it rather than failing or dumping work back to the backlog.
3. **Velocity Metric Grounding:**
   * Requires a `story_points` integer field on each ticket so sprint closure can output credible numeric velocity, and requires the close-time snapshot so those numbers never change retroactively.
4. **Git is the status input that matters.**
   * GitHub's closing keywords eliminate the most common source of board drift for the cost of one signed endpoint and one regular expression. This is the highest value-to-effort ratio item in the MVP.
5. **Do not build a transition state machine.**
   * Three of the four tools reviewed impose no transition rules whatsoever. Validate that the status is a member of the enum, allow any move, and spend the saved effort on making sprint close transactionally correct.
6. **Two-Tier Output Projection for MCP:**
   * To prevent high token consumption in Cursor / Claude contexts, `list_my_tickets` returns compact summaries (`number`, `title`, `status`, `priority`, `points`) with a default limit of 20, leaving full descriptions to explicit `get_ticket` requests. The under-500-token claim is asserted in CI against a real tokenizer, not assumed.
7. **Notifications must never be able to damage data.**
   * Chat delivery runs as a background task with a timeout and bounded retries. A Slack outage degrades visibility, never correctness — the ticket change stands regardless.
8. **Technical Stack Selection:**
   * **Backend:** Python (FastAPI) for native async I/O, Pydantic validation, and seamless MCP integration. Run with a single worker, since SQLite permits one writer.
   * **Database:** Embedded **SQLite with WAL mode** (`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON`), giving zero-install, zero-cost persistence with no network round-trip on reads. WAL provides concurrent *readers*, not concurrent writers — acceptable at team scale. Read-latency figures will be measured during validation rather than assumed.
   * **Hosting:** Single $5/month **AWS Lightsail** Ubuntu instance running via `docker-compose`.
9. **Non-Technical Usability:**
   * A web view and ticket-creation modal ensure faculty evaluators, PMs, and non-technical stakeholders can inspect work and file requests without an IDE or terminal — the audience none of the three reviewed tools' developer-facing strengths serve.
