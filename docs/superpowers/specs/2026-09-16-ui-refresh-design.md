# Quiet Sprint Workspace UI Refresh

## Intent

- Human: a project owner or member opening the product repeatedly during a sprint to find work, plan the next sprint, or inspect prior delivery.
- Task: move between a project, its current sprint, unscheduled work, and sprint history without re-learning the page hierarchy.
- Feel: quiet like a well-kept working notebook; compact enough for daily use, warm rather than clinical, and explicit about state.

## Domain exploration

- Domain: project workspace, sprint cadence, backlog queue, ticket flow, ownership, history.
- Color world: warm paper, graphite, pencil gray, muted planning amber, completion green, blocker red.
- Signature: a compact sprint strip that keeps sprint name, state, dates, goal, and sprint-only switching together.
- Rejected defaults:
  - Colorful kanban columns: use neutral lanes and reserve color for state.
  - A permanent detail rail: use a right drawer so the board remains the focal surface.
  - Duplicate destinations in tabs and sprint selector: use one project navigation and a sprint-only selector.

## Information architecture

- The sidebar identifies Projects and highlights the current project when available.
- Project navigation contains Board, Backlog, and History only.
- Settings is a visually secondary utility link in the project header.
- The sprint selector contains sprints only. Backlog, History, and Settings are not selector options.
- Every project screen uses the same hierarchy: project eyebrow, page/sprint title, context metadata, one primary action, project navigation.

## Visual system

- Four-pixel spacing base with compact daily-work density.
- Warm paper canvas and white work surfaces. Text uses graphite; muted text must meet WCAG AA for normal text.
- Typography uses the native system stack, with hierarchy created primarily by weight and tone: 28px page title, 14px body, 12px metadata, 11px tracked labels.
- Depth strategy is borders-only for the application frame and work surfaces. Soft shadow is reserved for dialogs and drawers that genuinely overlay content.
- Controls use four variants: primary, secondary, ghost, and danger.
- Color communicates status, action, or risk only.

## Screen behavior

### Dashboard

- The project list is a quiet row list, not a grid of cards.
- The project name is the single navigation target; the duplicate Open link is removed.
- New project is the only primary action.

### Board

- The board occupies the working width; the ticket detail is a right drawer on desktop and a full-width sheet on mobile.
- Each lane has a clear empty state.
- Filters sit in one compact toolbar, show an active-filter summary, and offer Clear filters when filtering is active.
- Ticket cards prioritize number and title, with type, priority, assignee, and points as metadata.

### Backlog

- A planning sprint exists as a compact sprint strip above the unscheduled queue.
- When a planning sprint exists, Start sprint is the dominant owner action; Add sprint is not shown.
- Selecting tickets reveals the contextual Add selected action. Zero selection must not submit.
- The visible section name is Unscheduled tickets.

### History

- Closed sprints are rows with aligned dates, points, completion, rollover, and delay values.
- The sprint name is the row's navigation target.

### Settings

- Sections are Project details, Members, and Danger zone.
- Member controls have member-specific accessible names.
- Destructive actions use the danger variant and remain visually separated.

## Interaction and accessibility

- Include a skip link and visible focus indicators.
- Interactive hit areas are at least 40px, with 44px on mobile where space permits.
- Drawers and dialogs restore focus to their opener and support Escape.
- Empty, error, disabled, and saved states must be legible without relying only on color.
- Reuse native buttons, links, form controls, and dialogs. Do not introduce a frontend dependency or build step.
- Preserve existing route behavior, authorization boundaries, Jinja rendering, HTMX, and Alpine usage.

## Responsive behavior

- At 767px and below, the sidebar becomes an overlay drawer.
- Project headers stack without changing navigation order.
- The board may scroll horizontally, while ticket detail becomes a fixed full-width sheet.
- Settings forms and history metrics collapse to one column.
