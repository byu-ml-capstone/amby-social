# Core Work Visibility Design

## Goal

Add three small visibility improvements inspired by ClickUp without adding a
configurable dashboard or reporting subsystem:

1. Optional ticket due dates with overdue treatment.
2. A cross-project My Work section on the existing dashboard.
3. Live numeric summary cards for the active sprint.

## Due Dates

`Ticket.due_date` is a nullable date. Past dates are valid, JSON `null` and an
empty web form value clear the date, and no timezone conversion is needed
because the value represents a calendar day rather than an instant.

The field is accepted by ticket create and update operations in the JSON API,
the web create and edit forms, and the explicit MCP `create_ticket` tool. The
existing generic MCP `update_ticket` tool forwards the ISO date string or null
through `TicketUpdate`.

Board cards display the date using a semantic `<time>` element. A non-DONE
ticket is overdue when `due_date < date.today()` on the application server.
DONE tickets retain their due date but are never styled as overdue.

## My Work

The existing `/dashboard` remains the route and continues listing projects.
Above the project list it gains a My Work section containing tickets assigned
to the signed-in user in projects where that user is still a member.

Open tickets are partitioned into mutually exclusive groups:

- **Overdue:** non-DONE tickets whose due date is before today.
- **In progress:** non-overdue tickets with status `IN_PROGRESS`.
- **Next:** non-overdue tickets with status `BACKLOG` or `SELECTED`.

The route loads at most 100 open assigned tickets, ordered by due date with
undated tickets last and then newest first. It separately loads the 10 most
recent DONE tickets by `completed_at`. Each row links to the existing canonical
ticket page and shows project key, ticket number, title, status, priority, and
due date. An empty assignment state is rendered instead of hiding the section.

No new cross-project JSON endpoint, saved views, pagination UI, or persisted
dashboard configuration is introduced.

## Active Sprint Summary

The existing project board shows a semantic `<dl>` summary only when the
selected sprint is ACTIVE. The route derives the values from the current
tickets in that sprint:

- **Completed:** DONE ticket count divided by total ticket count, plus a rounded
  percentage. An empty sprint is 0%.
- **Points:** sum of story points on current DONE tickets divided by the
  sprint's frozen `committed_points`. This may exceed 100% when scope is added
  after sprint start, so the UI shows raw values rather than clamping.
- **Rollover:** number of current sprint tickets with `rollover_count > 0`.
- **At risk:** number of current sprint tickets with `rollover_count >= 2` or
  `delayed_days >= 14`, matching the existing stale-work rule.

These values are computed on read and are not stored. Closed sprint reporting
continues using its frozen history values.

## Constraints

- Keep FastAPI, SQLModel, SQLite, Jinja2, HTMX, and Alpine.js; add no dependency.
- Keep the fixed four-status workflow and OWNER/MEMBER permission model.
- Reuse existing routes, services, templates, and ticket navigation.
- Use native `<input type="date">`; add no date-picker JavaScript.
- Preserve server-rendered accessibility and mobile behavior.
- Update Graphify after the implementation.

