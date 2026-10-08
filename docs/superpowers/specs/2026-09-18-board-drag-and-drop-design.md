# Board Drag-and-Drop and Ticket Creation

**Date:** 2026-09-18
**Status:** Approved

## Intent

Keep sprint work on the board: filters stay on the board, cards move directly between status lanes, and a new ticket is created in a sprint rather than the separate project backlog queue.

## Behavior

- Submitting empty Assignee, Type, or Priority filters renders the HTML board instead of FastAPI's JSON validation response. Non-empty invalid values remain rejected.
- Ticket cards can be dragged between the existing `BACKLOG`, `TODO`, `IN_PROGRESS`, and `DONE` lanes. A drop uses the existing status-change route and its transition, notification, CSRF, and permission rules.
- Successful drops move the card and update both lane counts. Failed drops leave the board recoverable and show an error; the ticket detail status control remains the keyboard-accessible fallback.
- The New Ticket Destination select lists active and planning sprints only. It keeps the displayed sprint selected, and the ticket starts in that sprint's `BACKLOG` status.
- When no active or planning sprint exists, the backlog page hides New Ticket until a sprint is added.
- Existing unscheduled tickets, API support for `sprint_id=None`, and the sprint-planning project backlog queue remain unchanged for compatibility.

## Implementation Boundary

- Reuse the current FastAPI status form endpoint, HTMX card fragment, CSRF token, and vanilla browser drag events. Add no dependency and no ordering within a lane.
- Normalize blank filter values at the board route boundary before applying enum filters.
- Change only the board/new-ticket template, the smallest vanilla-JavaScript and CSS hooks needed for drag states, and focused route/UI tests.
- Preserve current card click/detail behavior, mobile horizontal scrolling, focus styles, permissions, and notification behavior.

## Interface

- Draggable cards keep their existing visual hierarchy. While dragging, the source card is subdued and eligible lanes receive a restrained outline; the active drop lane uses the existing blue action color.
- Pointer dragging is enhancement only. Existing ticket detail/status editing remains available to keyboard and touch users.
- Empty lanes remain valid drop targets. Reduced-motion mode adds no movement animation.

## Acceptance Criteria

1. A filter request containing blank select values returns the board as `text/html` and applies any non-blank filters.
2. Dropping a card in another lane persists the status through the existing endpoint and updates the visible card placement/counts without a full-page navigation.
3. A failed status update does not silently move the card and exposes an actionable error.
4. New Ticket has no standalone `Backlog` destination; the displayed sprint is selected and receives the ticket in `BACKLOG`, while projects without an open sprint show no New Ticket action.
5. Existing unscheduled-ticket and sprint-planning behavior remains intact.
6. Focused tests and the full test suite pass; the board is smoke-checked in Chromium at desktop and mobile widths.
