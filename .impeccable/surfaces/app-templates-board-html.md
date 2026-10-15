---
version: 1
slug: "app-templates-board-html"
primary_target: "app/templates/board.html"
related_targets: ["app/templates/backlog.html","app/templates/dashboard.html","app/templates/partials/ticket_detail.html","app/templates/base.html"]
---

# Web workspace (board, backlog, ticket detail, dashboard)

Scope: the whole server-rendered web workspace, replacing the sketch/paper world. Visitor mode: Operate.
Audience: non-technical leads and ops checking in a few times a day to file requests and see where work stands.
Constraints: Jinja templates + one stylesheet; WCAG AA; 44px targets; no Korean-specific typography needed; no handwritten faces.

## Direction contract

THESIS: Every ticket is a parcel and the board is its tracking page: a lead reads "which stage is my request at" the way they read courier tracking. Refuses the category default of gray SaaS cards with colored pills, and refuses the incumbent sketch look.

OWN-WORLD: White thermal-label ground (#ffffff), carbon ink #15171c, label gray #5b6068, rule gray #e3e5e9 / #c9ccd2, one signal orange #d9531e (text #b8410f) for the current stage and primary action, delivered green #1a7f4b. Black sidebar. System sans only, tabular numerals; bold ticket number (DEV-3) is the label's stamp. Cards are labels: number + type header, dashed tear rule, title, four-segment stage bar. No shadows, no nested boxes.

STORY: The lead sees their own requests' stages first, scans the board by stage, opens a label to read or edit it, and leaves knowing what moved.

FIRST VIEWPORT: Project title, sprint selector and date line top-left, New ticket (orange) top-right; tabs; one-line sprint stats; "My requests" tracking strip (number, title, 4-step bar) full width; four columns with uppercase headers over a 2px carbon rule, label cards beneath. Tabs sit above the strip so they stay in the same place on every project tab (Product Principle 4).

FORM: Courier waybill & parcel tracking, IMPECCABLE'S PICK (rank 1 of 7) chosen over assigned wall planner; seed key 19ea02a9.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Resolved
- Kickers removed from every surface; ticket references read KEY-n everywhere.
- Ticket modal: reference stamp + category header, title as heading, 4-step tracker, Save pinned while the form scrolls, branch command under Development.
- Vocabulary unified on plain-language labels (app/labels.py), chosen by the user.

## Unresolved
- STORY's "leaves knowing what moved" has no carrier yet: tickets have no status-change timestamp, so tracking rows cannot show scan times.
- Ceiling ideas: priority as a service class in the label header, a stage-fill moment on drop, label-header treatment for backlog rows and auth card.
- Mobile board still scrolls lanes horizontally (one lane at a time not built).
- Project role "Owner" (dashboard, settings) shares a word with the ticket Owner field; role names left as domain terms.
