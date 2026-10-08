# Board Drag-and-Drop Implementation Plan

**Goal:** Keep filter submissions on the HTML board, move cards between status lanes by drag-and-drop, and remove the unscheduled Backlog choice from New Ticket.

**Architecture:** Normalize web-only filter strings at the board route boundary. Reuse the existing status POST route from a small native drag handler. Keep `sprint_id=None` in the model/API/planning queue, while the visible New Ticket UI lists only active or planning sprints.

**Tech:** FastAPI, Jinja, vanilla JavaScript, CSS, pytest, Playwright/Chromium smoke check.

**Spec:** `docs/superpowers/specs/2026-09-18-board-drag-and-drop-design.md`

## Task 1: Blank board filters

**Files:** `tests/test_web_board.py`, `app/routers/web.py`

- [ ] Add a route test requesting blank `assignee_id`, `type`, and `priority` plus one real filter; assert `200`, `text/html`, and the filtered board.
- [ ] Run the focused test and confirm the current enum binding returns `422` JSON.
- [ ] Bind Type/Priority as optional query strings, normalize blank strings to `None`, and validate non-blank values with the existing enums before building SQL filters.
- [ ] Run the focused test and existing board tests.

## Task 2: Sprint-only New Ticket destination

**Files:** `tests/test_web_ticket_create.py`, `app/templates/partials/ticket_modal.html`, `app/routers/web.py`

- [ ] Change focused tests to assert no empty/Backlog option and hide New Ticket when no open sprint exists.
- [ ] Run the focused tests and confirm they fail.
- [ ] Remove the standalone Backlog option, retain the displayed sprint selection, and hide the backlog-page action when no destination sprint exists.
- [ ] Leave API creation and the sprint-planning unscheduled queue unchanged.
- [ ] Run ticket-creation tests.

## Task 3: Native board drag-and-drop

**Files:** `tests/test_web_board.py`, `app/templates/board.html`, `app/templates/partials/ticket_card.html`, `app/static/app.js`, `app/static/app.css`

- [ ] Add markup/CSS contract tests for draggable cards, status/url data, drop lanes, live status, and restrained drag classes.
- [ ] Run the focused tests and confirm they fail.
- [ ] Add draggable data to cards and drop-target status data to lanes. Expose the existing CSRF value on the board and add one live status message.
- [ ] Add delegated native `dragstart`, `dragend`, `dragover`, `dragleave`, and `drop` handlers. POST `status` and `_csrf` to the existing route; only move the original card after a successful response.
- [ ] Recompute visible lane counts, preserving existing `+` truncation markers, and maintain empty-lane messages. On failure, leave the card in place and announce the error.
- [ ] Add only opacity/outline drag states, with no animation or dependency.
- [ ] Run board/status tests.

## Task 4: Verification and graph refresh

- [ ] Run `uv run pytest -q tests/test_web_board.py tests/test_web_status.py tests/test_web_ticket_create.py`.
- [ ] Run the full `uv run pytest -q` suite.
- [ ] Smoke-check Filter, New Ticket, successful drag, and mobile fallback in Chromium.
- [ ] Run `graphify update .` and `git diff --check`.
