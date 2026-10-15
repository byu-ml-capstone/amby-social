---
name: Kanban Flow
description: An engineering tracker whose web board reads like a courier tracking page.
colors:
  label-white: "#ffffff"
  inset-gray: "#f4f5f7"
  carbon-ink: "#15171c"
  ink-secondary: "#3d4148"
  label-gray: "#5b6068"
  rule-light: "#e3e5e9"
  rule-strong: "#c9ccd2"
  control-border: "#8a9099"
  signal-orange: "#d9531e"
  accent-orange: "#c2410c"
  accent-orange-deep: "#a63a0b"
  accent-text: "#b8410f"
  delivered-green: "#1a7f4b"
  danger-red: "#c62828"
  highlight-peach: "#fdf0e9"
  nav-carbon: "#15171c"
  nav-text: "#e8eaee"
  nav-muted: "#a3a8b0"
  nav-active: "#2a2d34"
typography:
  headline:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  title-field:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1.3
  section:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "18px"
    fontWeight: 700
  title:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 700
  stamp:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 800
    fontFeature: "\"tnum\""
  card-title:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.4
  body:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  meta:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
  label:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 650
  column-head:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 800
    letterSpacing: "0.03em"
  type-stamp:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    letterSpacing: "0.04em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
rounded:
  sm: "4px"
  md: "8px"
spacing:
  space-1: "4px"
  space-2: "8px"
  space-3: "12px"
  space-4: "16px"
  space-5: "20px"
  space-6: "24px"
components:
  button-primary:
    backgroundColor: "{colors.accent-orange}"
    textColor: "{colors.label-white}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.accent-orange-deep}"
  button-secondary:
    backgroundColor: "{colors.label-white}"
    textColor: "{colors.carbon-ink}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
    height: "44px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
    height: "44px"
  button-ghost-hover:
    backgroundColor: "{colors.inset-gray}"
    textColor: "{colors.carbon-ink}"
  button-danger:
    backgroundColor: "{colors.label-white}"
    textColor: "{colors.danger-red}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
    height: "44px"
  button-danger-hover:
    backgroundColor: "{colors.danger-red}"
    textColor: "{colors.label-white}"
  button-icon:
    backgroundColor: "transparent"
    textColor: "{colors.label-gray}"
    rounded: "{rounded.sm}"
    size: "44px"
  input:
    backgroundColor: "{colors.label-white}"
    textColor: "{colors.carbon-ink}"
    rounded: "{rounded.sm}"
    padding: "8px"
    height: "44px"
  label-card:
    backgroundColor: "{colors.label-white}"
    textColor: "{colors.carbon-ink}"
    rounded: "{rounded.sm}"
  panel:
    backgroundColor: "{colors.label-white}"
    rounded: "{rounded.md}"
  dialog:
    backgroundColor: "{colors.label-white}"
    rounded: "{rounded.md}"
    padding: "20px"
    width: "448px"
  nav-sidebar:
    backgroundColor: "{colors.nav-carbon}"
    textColor: "{colors.nav-muted}"
    width: "240px"
  nav-link-active:
    backgroundColor: "{colors.nav-active}"
    textColor: "{colors.nav-text}"
    rounded: "{rounded.sm}"
    height: "44px"
---

# Design System: Kanban Flow

## Overview

**Creative North Star: "The Waybill Board"**

Every ticket is a parcel and the board is its tracking page. A lead who checks in a few times a day reads "which stage is my request at" the way they read courier tracking: a bold reference stamp, a short description, and a four-segment bar that shows how far along it is. The world comes from thermal shipping labels: a white ground, carbon ink, gray rules, dashed tear lines, and one signal orange that marks where a parcel is right now.

The interface is dense and quiet. Columns have no box around them, only an uppercase heading over a heavy carbon rule. Cards are flat labels with a 1px rule, not floating tiles. Hierarchy comes from weight (800 stamps, 700 headings, 500 titles), tabular numerals, and rules, never from shadow or tinted fills. Color is spent on state: orange for the current stage and the primary action, green for delivered work, red for destructive actions.

This world replaced an earlier hand-drawn sketch/paper theme. Handwritten faces, wobbly borders, and offset shadows from that theme are rejected.

**Key Characteristics:**
- White label ground, carbon ink, gray rules; a carbon sidebar frames the workspace.
- One signal orange for "here now" and the primary action; green only for done.
- System sans throughout, bold tabular reference stamps (`DEV-3`).
- Flat: depth comes from rules and borders, not shadows.
- Four-segment stage bars on cards and labeled four-step trackers in "My requests".
- Plain-language labels everywhere: Not planned, Ready, In progress, Done.

## Colors

A near-monochrome label palette with one warm signal and one delivered green.

### Primary
- **Signal Orange** (signal-orange): the current-stage segment on stage bars and trackers, drag-over outlines, and the focus ring. Used as a fill or line, never as body text.
- **Accent Orange** (accent-orange): fills the primary button (New ticket, Save, Create) with white text; deepens to **Accent Orange Deep** (accent-orange-deep) on hover. Also the checked fill of custom checkboxes and the drag-over border on attachments.
- **Accent Text** (accent-text): orange used as text: the current-step label in trackers, overdue due dates, inline links in status messages, hovered backlog rows.

### Secondary
- **Delivered Green** (delivered-green): every segment of a Done ticket's stage bar and the Done label in trackers. Nothing else.

### Tertiary
- **Danger Red** (danger-red): destructive buttons (outlined at rest, filled on hover), form errors, the secret warning, and the danger-zone border.

### Neutral
- **Label White** (label-white): page canvas, card, panel, dialog, and field background.
- **Inset Gray** (inset-gray): row hover, ghost-button hover, comments, code chips, disabled fields.
- **Carbon Ink** (carbon-ink): primary text, passed stage segments, column-head rules, the active tab underline, card hover border.
- **Ink Secondary** (ink-secondary): field labels, descriptions, ghost-button text.
- **Label Gray** (label-gray): meta lines, counts, empty states, ticket type stamps, table cells after the first.
- **Rule Light** (rule-light): row dividers, panel borders, unreached stage segments.
- **Rule Strong** (rule-strong): card borders, dashed tear lines, dialog and menu borders, empty-column dashed boxes.
- **Control Border** (control-border): field and secondary-button borders (3:1 against white).
- **Highlight Peach** (highlight-peach): drop-target fill, text selection, selected rows, the one-time issued-token panel.
- **Nav Carbon / Nav Text / Nav Muted / Nav Active** (nav-carbon, nav-text, nav-muted, nav-active): the sidebar, mobile header, and auth header.

### Named Rules
**The Here-Now Rule.** Signal orange marks exactly one thing per parcel: the stage it is at now. Passed stages are carbon, unreached stages are rule gray, and a delivered parcel turns entirely green.

**The Ink-Not-Paint Rule.** Neutrals and white carry every surface. Color never fills a card, column, or panel; it appears only on state marks, the primary action, and destructive actions.

## Typography

**Display Font:** none (no display face)
**Body Font:** system UI sans (ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif)
**Label/Mono Font:** ui-monospace, SFMono-Regular, Menlo, monospace (for branch commands, tokens, code only)

**Character:** One utilitarian sans, like label printer output. Weight and tabular numerals do the work that a display face would do elsewhere.

### Hierarchy
- **Headline** (700, 28px, 1.2, -0.025em, balanced wrap): page and project titles.
- **Title field** (700, 22px, 1.3): the editable ticket title in the detail view, borderless until hover/focus.
- **Section** (700, 18px): dialog headings, panel headings (backlog, history, settings), the ticket reference in the detail header (800 there).
- **Title** (700, 15px): sprint name, "My requests", Development and Comments headings.
- **Stamp** (800, 15px, tabular): the ticket reference `KEY-n` on cards and trackers; 14px in lists and rows.
- **Card title** (500, 15px, 1.4): the ticket title on a label card.
- **Body** (400, 14px, 1.5): base text, buttons (650), nav links, table first column.
- **Meta** (400, 13px): card meta lines, sprint date line, filter bar, stats labels.
- **Label** (650, 12px): form labels and small secondary text.
- **Column head** (800, 14px, 0.03em, uppercase): board column and My Work group headings only.
- **Type stamp** (700, 12px, 0.04em, uppercase): the ticket type in a card's stamp row; history metric terms use the same voice at 0.06em.

### Named Rules
**The Stamp Rule.** A ticket is always named by its bold tabular reference (`DEV-3`) first; the title follows. Never lead with the title alone.

**The No-Hand Rule.** No handwritten, script, or display faces. The system sans is the only voice.

## Layout

A fixed 240px carbon sidebar on the left, the workspace on the right. The board page runs to 1440px max with 24px padding; content pages (dashboard, settings) cap at 768px; auth pages center a 400px card 64px from the top. The ticket detail opens as a dialog up to 1120px wide, split 3:2 between the form and a comments column divided by a 1px rule.

Spacing is a 4px base scale (space-1 through space-6: 4 to 24px), with 28px and 32px used for sidebar and main-area offsets. The board is a four-column grid (each at least 240px, 20px gaps, 1000px minimum) that scrolls horizontally rather than stacking. Rows are 56px minimum; every interactive target is 44px minimum.

At 767px and below the sidebar becomes an off-canvas drawer behind a 56px carbon header with a menu button; the detail dialog goes full-screen and single-column; grids collapse to one column; board lanes still scroll sideways.

## Elevation & Depth

Flat. No box-shadows exist anywhere in the build. Depth is told with rules and borders: 1px light rules between rows, 1px strong borders on cards and dialogs, a 2px carbon rule under column heads, and a 40% carbon backdrop (rgb(21 23 28 / 40%)) behind dialogs and the mobile drawer. Hover darkens a border to carbon ink or fills a row with inset gray; nothing lifts.

### Named Rules
**The Flat Label Rule.** Labels lie on the table. A surface earns separation from a border or rule, never from a shadow.

## Shapes

Two radii only: slightly softened corners (4px) on controls, cards, chips, and menus; gently rounded corners (8px) on panels, dialogs, filter bars, and attachment tiles. Borders are 1px solid; dashed lines are reserved for the card tear line, empty-column placeholders, and drop targets. Stage segments are square 4px bars with 3px gaps.

### Named Rules
**The One Box Rule.** A box never sits inside another box. Columns, groups, and modal sections divide with rules; only cards, panels, and dialogs get borders.

## Components

### Buttons
Plain and firm: solid rectangles with a 650-weight label.
- **Shape:** slightly softened corners (4px), 44px minimum height, 8px x 12px padding.
- **Primary:** accent-orange fill and border, white text. One per view (New ticket, Save, Create).
- **Hover / Focus:** primary deepens to accent-orange-deep; secondary border darkens to carbon; all controls show a 3px signal-orange outline offset 2px on `:focus-visible`.
- **Secondary:** white with a control-border outline, carbon text (Sprint actions, Filter tickets summary).
- **Ghost:** transparent, ink-secondary text, inset-gray on hover (Copy, Cancel).
- **Danger:** white with a red outline and red text; fills red with white text on hover.
- **Icon:** 44px square, transparent, label-gray inline SVG at 16px; inset-gray on hover. Always has an `aria-label`.
- **Disabled:** inset-gray fill, strong-rule border, label-gray text.

### Label Card (signature)
A ticket on the board is a shipping label.
- **Corner Style:** 4px.
- **Background / Border:** label-white with a 1px rule-strong border; border goes carbon on hover (120ms). No shadow.
- **Structure:** stamp row (reference in Stamp 800 left, type in uppercase Type stamp right, 8px x 12px padding), a 1px dashed rule-strong tear line, the title (Card title, 12px sides), one meta line in label gray separated by `·` (owner, then High/Urgent priority in carbon 700, points, due date; overdue in accent-text 700), then the stage bar.
- **Quiet defaults:** Medium and Low priority are not printed.
- **Drag:** grab cursor; 45% opacity while dragging; the target column fills highlight-peach with a 2px dashed signal-orange outline.

### Stage Bar (signature)
Four square 4px segments, 3px apart, 12px padding: rule-light for unreached, carbon for passed, signal-orange for current, all delivered-green when done. Decorative (`aria-hidden`); the stage is conveyed by the column.

### Tracking Row ("My requests")
A full-width 8px-radius panel with a 1px light border above the board. Each row is a button: 72px reference stamp, a 260px truncated title, then a four-step tracker where each step is a 4px top rule over its plain-language name (12px label gray). Passed steps are carbon, the current step has a signal-orange rule and accent-text 700 label, done turns green. A visually hidden "Stage: ..." line carries the state for screen readers. On mobile the tracker wraps below the stamp and title.

### Column and Group Heads
No box: a Column head (uppercase 800, 14px) over a 2px carbon rule, with the count beside it in label gray 500 tabular. Used for board columns and My Work groups. Empty columns show a dashed 4px-radius placeholder ("No tickets here.").

### Navigation
- **Carbon sidebar:** 240px nav-carbon, 20px x 12px padding. Brand in 800 nav-text; "Projects" in 700 nav-text; project and settings links 44px tall in nav-muted 14px, filling nav-active with nav-text on hover and when active (650). User name and Sign out sit at the bottom above a nav-active rule.
- **Tabs:** text tabs 44px tall with a 3px bottom border; label gray at rest, carbon 700 with a carbon underline when active. Settings sits at the far right in the same style.
- **Mobile:** 56px carbon header with a 44px menu button; the sidebar slides in over 160ms (cubic-bezier(.23, 1, .32, 1)) over a 40% backdrop.

### Inputs / Fields
- **Style:** white, 1px control-border, 4px radius, 44px tall, 8px padding; placeholder in label gray.
- **Labels:** 12px 650 ink-secondary above the field.
- **Hover / Focus:** border darkens to carbon; focus adds the signal-orange ring.
- **Disabled:** inset-gray fill, label-gray text.
- **Error:** 12px danger-red message above the form.

### Panels and Rows
- **Panels:** white, 1px rule-light border, 8px radius; headed by a Section heading over a 1px rule.
- **Rows:** 56px minimum, 12px x 16px padding, divided by 1px rule-light, inset-gray on hover. Lists show the stamp in carbon 800 before the title.

### Dialogs
Native `<dialog>`: white, 1px rule-strong border, 8px radius, 20px padding, 448px max (600px for Gmail labels, 1120px for ticket detail), 40% carbon backdrop. Header row holds a Section heading and a close icon button at the right. In ticket detail the Save bar sticks to the bottom over a 1px rule.

## Do's and Don'ts

### Do:
- **Do** name every ticket by its bold tabular stamp (`KEY-n`) before its title.
- **Do** show stage with the four-segment bar: carbon passed, signal-orange current, rule-light ahead, all green when done.
- **Do** use the plain-language labels from `app/labels.py` everywhere: types Initiative, Issue, Review request, Task; stages Not planned, Ready, In progress, Done.
- **Do** divide columns, groups, and modal sections with rules (2px carbon under heads, 1px light between rows).
- **Do** keep every target at 44px minimum and keep the 3px signal-orange focus ring.
- **Do** keep one accent-orange primary button per view.

### Don't:
- **Don't** use handwritten, script, or display faces; system sans only.
- **Don't** use box-shadows or hard offset shadows; the system is flat.
- **Don't** nest boxes: no bordered card inside a bordered panel inside a bordered column.
- **Don't** add kickers or eyebrows above headings; the page title stands alone.
- **Don't** use glyph or emoji icons; icons are inline SVG with `aria-label` on their button.
- **Don't** use colored pills or tinted card fills for status or type; status lives in the stage bar, type in the stamp row.
- **Don't** print raw enum values (BACKLOG, STORY, DEMO_REQUEST) on screen.
