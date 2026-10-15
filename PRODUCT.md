# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary web users are non-technical leads and operations people. They check in a few times a day: file bug reports and demo requests, see where work stands, and follow a sprint without an IDE, terminal, or git account. Engineers mostly work through the MCP server and JSON API; faculty reviewers join as ordinary members to judge sprint performance.

## Product Purpose

An engineering workflow tracker with two first-class interfaces onto one domain model: a server-rendered web workspace for people without an IDE, and an MCP server plus JSON API for developers in Cursor or Claude Desktop. Success means the board stays trusted because it matches repository reality, and a non-technical user can file and follow work without help.

## Positioning

One source of truth driven from both sides: tickets move from MCP tool calls and `Closes #<n>` commits as well as from the web board, so the web view reflects what engineers actually did rather than what someone remembered to drag.

## Operating Context

Small engineering team, one active sprint at a time. Web workspace: dashboard (My Work, projects), project Board (Backlog / Selected / In progress / Done), Backlog, List, Sprint History, Settings, ticket detail with comments and attachments. Integrations: GitHub PR sync, Gmail intake, chat webhooks, AI-drafted sprint reports.

## Capabilities and Constraints

- FastAPI + Jinja server-rendered templates, one stylesheet (`app/static/app.css`), minimal JS.
- Only project OWNERs run sprint lifecycle actions; members work tickets.
- Mobile: sidebar becomes a drawer; board columns scroll horizontally.
- Korean-specific typography is not required.

## Brand Commitments

Name "Kanban Flow". No visual commitments: the current sketch/paper look may be replaced entirely.

## Evidence on Hand

Real sample data lives in the local SQLite database (project "Dev", 11 tickets). No customers, testimonials, or metrics to cite.

## Product Principles

1. The board must be readable at a glance by someone who did not write the tickets.
2. State over decoration: status, owner, and urgency outrank identity.
3. Same words everywhere: board, modal, and API vocabulary match.
4. A few-times-a-day visitor must re-orient in seconds, without learning anything.

## Accessibility & Inclusion

WCAG 2.1 AA contrast and keyboard operability; keep 44px targets and visible focus.
