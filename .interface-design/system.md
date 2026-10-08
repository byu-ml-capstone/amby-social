# Kanban Flow Interface System

## Direction

- Calm, practical work-management UI for both technical and non-technical teams.
- Keep information dense but readable; use plain department-neutral language.
- Prefer native HTML, semantic CSS, and restrained interaction over decorative UI.

## Foundations

- Depth: warm paper canvas and white surfaces; 3px hard shadow only for elevated dialogs and actionable controls, never soft shadows.
- Spacing: 4px base unit using the existing `--space-1` through `--space-6` tokens.
- Corners: shared asymmetric control/card/panel radii.
- Typography: local Kalam headings and Patrick Hand prose/actions; system sans and monospace retain dense metadata and references.
- Controls: 44px minimum height with a visible `:focus-visible` outline.

## Hierarchy

- Lead with the work item title and editable details.
- Keep metadata compact in paired fields on desktop and a single column on mobile.
- Treat conversation as a primary workflow, not a footer.
- Use borders and spacing—not extra cards or color—to separate regions.

## Component Patterns

### Ticket detail modal

- Centered native `<dialog>` with a maximum width of 1120px and 16px viewport gutters.
- Desktop layout: ticket details 60% / comments 40%, with a 20px gap.
- Separate comments with a 1px left border and 20px left padding.
- At widths below 768px, use a full-screen dialog and stack comments below details with a top border.
- On open, focus the modal heading; on close, return focus to the originating ticket.

### Ticket comments

- Keep comment history, edit/delete actions, mention controls, and composer in the right panel.
- Use an inset surface for each comment and muted 12px timestamps.
- Place Edit and Delete at the card's top right; return Edit to normal flow while its form is open.
- Typing `@` in the composer opens a keyboard-accessible project-member picker below the textarea.
- Store comment timestamps as UTC and render them in the browser's locale and time zone.
- Preserve native form submission as a fallback; HTMX may replace only the comments region.

### Ticket development activity

- Keep ticket reference and branch-command copy controls in the left detail pane, using
  native buttons with visible polite status text.
- Render linked GitHub work as border-separated rows below the editable fields; repository
  and timestamp are muted metadata, while the PR or commit title carries the row hierarchy.
- Use compact neutral pills for PR and CI summaries near the header. Keep comments in the
  existing right pane and stack both panes only at the 767px mobile breakpoint.
- Show external text as escaped text. Only configured GitHub HTTPS-origin URLs become links,
  and those links open in a new tab with `noopener noreferrer`.

### Restrained hand-drawn system

- Global shell, auth, dashboard, backlog, history, settings, common forms, dialogs, tables, rows, and empty states use shared paper/pencil tokens and local Kalam/Patrick Hand typography. The canvas is warm paper while cards and dialogs remain white for hierarchy.
- Board dot grid, card rotation, and ticket-detail tape remain limited to their current `.sketch-board` and `.sketch-ticket-detail` scopes.
- Use `#fdfbf7` paper, `#2d2d2d` pencil, `#e5e0d8` erased structure, `#2d5da1` action/focus ink, `#ff4d4d` correction borders for error/destructive/failed states, and `#fff9c4` only for active-filter context and modal tape.
- Use one 24px dot-grid layer, 2px pencil borders, shared asymmetric control/card/panel radii, and one 3px 3px zero-blur pencil shadow. Do not add soft shadows or decorative textures inside the exception.
- Use local Kalam 700 at 32px for the board title, 24px for the modal title, 20px for section headings, 18px for the sprint title, and 16px for lane headings. Use local Patrick Hand 400 at 18px for prose/card titles/inputs and 17px for actions; preserve system sans for dense metadata and monospace for references/branch commands.
- Rotate only board ticket cards in the fixed `-0.25deg`, `0.35deg`, `-0.15deg`, `0.2deg` cycle. Remove rotation at 767px and under reduced motion.
- Keep the native detail dialog centered with a 1120px maximum and 60/40 desktop split; use full-screen stacked panes at 767px. Preserve 44px targets, 3px blue focus outlines, native dialog/focus behavior, and horizontal four-lane scrolling.
