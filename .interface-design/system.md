# Kanban Flow Interface System

## Direction

- Courier waybill and parcel tracking: every ticket is a parcel, the board is its tracking page.
- Built for non-technical leads checking in a few times a day; state outranks decoration.
- Native HTML, semantic CSS, system sans only. No handwritten faces, no hard offset shadows.

## Foundations

- Color: white ground, carbon ink `#15171c`, label gray `#5b6068`, rules `#e3e5e9` / `#c9ccd2`.
  Orange `--signal` marks the current stage; `--accent` (darker orange) fills the primary action;
  `--done` green marks delivered work; overdue dates use `--accent-text` orange; `--danger` red is only for destructive actions.
- Navigation: carbon sidebar; the active project gets a `--nav-active` fill (no colored side stripe).
- Spacing: 4px base unit, `--space-1` through `--space-6`.
- Corners: 4px controls and cards, 8px panels and dialogs.
- Controls: 44px minimum height, white fields with a 3:1 `--control-border`, visible `:focus-visible` ring.

## Board

- Columns have no box: an uppercase 14px heading over a 2px carbon rule, count beside it.
- Cards are labels: stamp row (`KEY-12` in 800 weight, type in small caps), dashed tear line,
  15px title, one muted meta line separated by `·`, then a four-segment stage bar.
- Defaults stay quiet: Medium/Low priority are not printed; High/Urgent are.
- "My requests" lists the viewer's own sprint tickets with a labeled four-step tracker,
  under the same filters as the board.
