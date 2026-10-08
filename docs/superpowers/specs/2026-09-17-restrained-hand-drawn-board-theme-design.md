# Restrained Hand-Drawn Board Theme

**Date:** 2026-09-17
**Status:** Approved
**Scope:** Visual treatment for the active project board and its centered ticket-detail modal only.

## Intent

- **Human:** a teammate scanning and updating sprint work throughout the day.
- **Task:** understand the sprint, filter work, scan four lanes, open a ticket, edit details, inspect development activity, and comment without losing board context.
- **Feel:** a working paper board annotated by a careful team—warm and human, but restrained enough for dense operational use.
- **Signature:** lightly irregular paper cards on a dot-grid board, with blue-ink actions and correction-red exceptions. The handwriting effect comes from typography, borders, and deterministic card angles, never random behavior.

## Scope and Non-Goals

Theme only:

- the board project header, sprint selector, actions, project tabs, filter bar, columns, empty lanes, and ticket cards inside the board root;
- the centered ticket-detail `<dialog>` opened from a themed board, including details, development activity, comments, edit states, and mention controls.

Keep the current visual system for the global sidebar and mobile navigation, authentication, dashboard, project settings, backlog, sprint history, standalone ticket-detail page, new-ticket dialog, and sprint dialogs. A future global theme is explicitly out of scope.

Do not change data, models, routes, permissions, labels, filtering, form submissions, HTMX targets/swaps, Alpine state, clipboard/local-time behavior, or dialog behavior. Do not add drag-and-drop, animation flourishes, random card placement, a theme switcher, dark mode, or new ticket fields.

## Technical Constraints and Scoped Architecture

Keep the current FastAPI/Jinja stack, semantic HTML, HTMX, existing Alpine, one `app/static/app.css`, existing vanilla JavaScript, and inline SVG. Do not add React, Tailwind, a Lucide package, another framework, a build step, or a runtime font service.

Add one board scope class, `.sketch-board`, to the existing `.project-board` in `app/templates/board.html`. Define theme custom properties on that root and write component overrides below `.sketch-board`; do not mutate `:root` application tokens. The root supplies the paper canvas and variables, while typography is applied only to named board components.

Anchor board selectors to the approved regions: `.sketch-board > .project-header`, `.sketch-board > .project-navigation`, `.sketch-board > .board-filters`, and `.sketch-board .board-workspace`. Do not write broad rules such as `.sketch-board .app-dialog`, `.sketch-board .app-form`, or `.sketch-board .app-primary-button`: the new-ticket and sprint dialogs also live under the board root and must retain the global system. Style detail controls separately through `.sketch-ticket-detail`.

The HTMX ticket-detail response remains under `#ticket-detail-root`, but its existing dialog receives a dedicated `.sketch-ticket-detail` class when `detail_drawer` is true. That class owns the same local theme properties and modal selectors so the top-layer dialog is explicitly themed. The non-dialog `.ticket-detail-page` used by the standalone route does not receive the class. Removing `.sketch-board` and `.sketch-ticket-detail` must restore the current UI without reverting routes or behavior.

Prefer existing elements and classes. Add markup only for a necessary semantic hook; render decoration with CSS pseudo-elements or existing inline SVG. Decorative SVG must have `aria-hidden="true"` and `focusable="false"`; pseudo-elements are inherently absent from the accessibility tree.

## Theme Tokens

Define these under both theme scopes, using the existing 4px spacing scale:

```css
--sketch-paper: #fdfbf7;
--sketch-pencil: #2d2d2d;
--sketch-erased: #e5e0d8;
--sketch-correction: #ff4d4d;
--sketch-blue-ink: #2d5da1;
--sketch-post-it: #fff9c4;
--sketch-dot-size: 24px;
--sketch-border: 2px solid var(--sketch-pencil);
--sketch-radius-control: 6px 9px 7px 5px / 7px 5px 9px 6px;
--sketch-radius-card: 8px 12px 7px 10px / 10px 8px 11px 7px;
--sketch-radius-panel: 12px 9px 14px 10px / 10px 13px 9px 12px;
--sketch-shadow: 3px 3px 0 var(--sketch-pencil);
--sketch-font-heading: "Kalam", cursive;
--sketch-font-hand: "Patrick Hand", cursive;
```

The board background is warm paper with a quiet 1px erased-color dot every 24px, implemented as one CSS radial-gradient layer with `background-size: var(--sketch-dot-size) var(--sketch-dot-size)`. Use 2px pencil borders, the reusable asymmetric radii above, and 3px hard offset shadows with zero blur. Do not add soft shadows, multiple shadow depths, freehand SVG paths, noisy textures, or per-component radius inventions.

Color is semantic:

- blue ink marks primary actions, links, active tabs, and focus;
- correction red marks validation errors, failed states, and destructive actions only;
- post-it yellow marks active-filter context and the small tape accent;
- erased gray separates lanes, inset fields, disabled controls, and secondary structure;
- workflow completion remains legible through its text/column label rather than introducing another accent color.

`#ff4d4d` is not used for small text on paper or with white text. Error and danger labels remain pencil-colored and pair the red with a border, icon, underline, or pale field treatment so state never depends on color alone.

## Fonts and Type Hierarchy

During implementation, self-host only these WOFF2 files under `app/static/fonts/`:

- `Kalam-Bold.woff2`, weight 700;
- `PatrickHand-Regular.woff2`, weight 400;
- `Kalam-OFL.txt` and `PatrickHand-OFL.txt`, retaining the SIL Open Font License text for each family;
- `ATTRIBUTION.md`, recording each family name and upstream source URL beside the font files.

Declare local `@font-face` rules in `app.css` with `font-style: normal` and `font-display: swap`. Font URLs must be `/static/fonts/...`; there must be no Google Fonts, `fonts.googleapis.com`, or `fonts.gstatic.com` runtime request. The declarations may be global, but the families are applied only inside the two theme scopes.

Use this hierarchy:

| Role | Family | Size / line-height | Weight |
|---|---|---:|---:|
| Board project title | Kalam | 32px / 1.15 | 700 |
| Detail modal title | Kalam | 24px / 1.2 | 700 |
| Section heading | Kalam | 20px / 1.25 | 700 |
| Sprint title | Kalam | 18px / 1.25 | 700 |
| Lane heading | Kalam | 16px / 1.2 | 700 |
| Card title / body / Markdown | Patrick Hand | 18px / 1.35 | 400 |
| Buttons | Patrick Hand | 17px / 1.2 | 400 |
| Text inputs and textareas | Patrick Hand | 18px / 1.35 | 400 |
| Dates and card metadata | system sans | 12px / 1.4 | 400 |
| Form labels, status, and counts | system sans | 12px / 1.4 | 600 |
| Eyebrows and compact badges | system sans | 11px / 1.35 | 700 |
| Ticket references and branch commands | system monospace | 12px / 1.5 | 400 |

Preserve system sans for dense metadata, status, dates, form labels, and native select option legibility. Preserve monospace for ticket references, numeric identifiers, and branch commands. Do not simulate handwriting with letter-by-letter rotation or transforms.

## Component Mapping

| Existing seam | Treatment |
|---|---|
| `.project-header` | Open paper area rather than another card. Kalam project title leads; sprint state/date/goal stay compact system metadata. Actions keep their existing order and permissions. |
| `.project-tabs`, `.project-tab` | Pencil baseline with a blue-ink 2px active underline and pencil text. Hover may use erased gray; active state retains `aria-current` behavior. |
| `.sprint-selector`, `.board-filters` | Compact paper strips with 2px borders and wobbly panel radius. Controls are inset erased gray. Active-filter summary gets a post-it-yellow field plus explicit text; no color-only state. |
| `.board-scroll`, `.board-columns` | Preserve the focusable horizontal scrolling region and four-column minimum width. The scroll container is never rotated or shadow-transformed. |
| `.board-column` | Erased-gray lane surface, 2px pencil border, panel radius, and no competing shadow. Lane title and count remain explicit; the count uses tabular system numerals. |
| `.ticket-card` | Paper surface, 2px border, card radius, one hard 3px shadow. Title is Patrick Hand; reference and metadata remain monospace/system. Hover strengthens the blue-ink edge; focus remains on the inner native button. |
| `.board-empty-state` | Full-width dashed 2px pencil outline with neutral text, no illustration, shadow, or fake ticket. Truncation text remains separate and explicit. |
| Buttons inside named board regions or `.sketch-ticket-detail` | Minimum 44px target. Primary uses blue ink with readable paper text; secondary is paper with pencil border; ghost is unboxed until hover/focus; danger uses correction red as a border/field with pencil text. Disabled state uses erased gray and keeps its label readable. |
| Inputs/selects/textareas inside named board regions or `.sketch-ticket-detail` | Minimum 44px height, erased inset surface, 2px pencil border, control radius, pencil caret/text, and blue focus ring. Error state adds correction-red border/supporting text without removing the label. No rotation or text transform. |
| Status/badge elements | Neutral pencil/erased by default. Blue denotes active/linkable development state; correction red is limited to failed/error states. Every badge retains visible status text. |

Existing inline edit/delete SVG icons remain; do not introduce an icon library. Hover, active, focus, disabled, empty, validation-error, saved, and HTMX-swapped states must all remain readable.

## Controlled Irregularity

Only `.ticket-card` may rotate, as a whole component, using a repeating four-card selector pattern: `-0.25deg`, `0.35deg`, `-0.15deg`, and `0.2deg`. Use fixed CSS selectors, not random JavaScript, server-generated numbers, or inline styles. Do not rotate headings, buttons independently, input text, menus, modal content, comment cards, development rows, or any scroll container.

At `max-width: 767px`, remove ticket-card rotation. Also remove it under `prefers-reduced-motion: reduce` to present the calmest version. No hover or press interaction may increase the angle.

## Centered Ticket-Detail Modal

Retain the existing native `<dialog>`, centered top-layer placement, 1120px maximum width, viewport gutters, and desktop `3fr / 2fr` (60/40) detail/comments grid. Replace the soft modal shadow only within `.sketch-ticket-detail` with the 3px hard pencil offset.

The dialog reads as one paper sheet:

- paper background, 2px pencil outline, panel radius, and an 88px by 22px centered post-it-yellow tape pseudo-element overlapping the top edge;
- no transform on the dialog or any modal text;
- a dashed 2px erased-gray divider between the details and comments panes;
- Kalam modal and section headings, Patrick Hand prose/actions, and system/monospace exceptions from the hierarchy;
- details Markdown view and existing edit/cancel/save state remain visually distinct without changing `hidden` behavior;
- GitHub development rows remain compact ruled rows: repository/time as system metadata, PR or commit title as the hierarchy anchor, and safe external links unchanged;
- comment cards use paper/erased surfaces without rotation; author/actions/time retain their positions, mentions stay explicit, and edit/delete forms stay in normal flow when opened;
- the mention listbox remains a stable, unrotated overlay with visible selected, hover, and keyboard-focus states.

Preserve initial focus on `#ticket-detail-heading`, native cancel/Escape close, the explicit Close button, focus return to the originating ticket, local-time rendering, clipboard status announcements, form errors/saved messages, and every existing HTMX target and swap boundary. The backdrop remains visual only; no backdrop-click close behavior is added. The new-ticket dialog is not themed.

## Responsive and Accessibility Requirements

- All scoped interactive targets are at least 44px by 44px without overlapping hit areas.
- Use a 3px blue-ink `:focus-visible` outline with 2px offset inside the theme scopes. Do not remove native semantics or accessible names.
- Pencil on paper is the default text contrast. Blue ink may be used for normal text/actions; correction red is paired with a non-color cue as described above.
- Preserve the board's keyboard-focusable horizontal scroll at desktop and mobile widths; never squeeze four lanes into one viewport.
- At 767px and below, use 16px board padding, remove card rotation, keep horizontal lane scrolling, and let filters/header actions wrap or stack without reordering.
- At 767px and below, retain the existing full-screen detail dialog and stack comments below details. Hide the tape accent if it consumes space; replace the desktop dashed side divider with a dashed top divider.
- Long ticket titles, Markdown, branch commands, repository names, comments, and mentions wrap without forcing page-level horizontal overflow.
- Under `prefers-reduced-motion: reduce`, keep the existing near-zero transition rule and remove card rotation. Do not add decorative animation.
- Decorative marks are pseudo-elements or `aria-hidden`; meaningful status and actions remain text or accessible inline SVG.

## File-Level Implementation Boundary

- `app/static/app.css`: local font faces, scoped theme tokens, component overrides, responsive and reduced-motion rules.
- `app/templates/board.html`: `.sketch-board` root hook only, plus a semantic hook only if an existing selector cannot express an approved state.
- `app/templates/partials/ticket_detail.html`: `.sketch-ticket-detail` on the dialog branch only; preserve the standalone section branch.
- `app/templates/partials/ticket_card.html` and `ticket_comments.html`: keep current semantic/HTMX structure; add classes only if CSS cannot target an existing semantic state safely.
- `app/static/fonts/`: two WOFF2 files and their license/source attribution.

No changes are expected in routers, services, models, migrations, `base.html`, or `app.js`. If implementation proves one is necessary, stop and justify the behavioral or trust-boundary need before expanding scope.

Do not update `.interface-design/system.md` in the spec commit. During implementation, update it only after desktop and mobile visual verification confirms the scoped pattern; record that this is a board-only exception to the global calm system.

## Acceptance Criteria

1. The active board header, tabs, filters, four lanes, cards, empty lanes, and board-opened ticket-detail dialog match the tokens and component mapping above.
2. Sidebar, auth, dashboard, settings, backlog, history, standalone ticket detail, new-ticket dialog, and sprint dialogs retain the current visual system.
3. Removing `.sketch-board` and `.sketch-ticket-detail` restores current styling; no application-level custom property changes are required.
4. Kalam 700 and Patrick Hand 400 load from local WOFF2 files with attribution and no runtime Google Fonts requests; fallback fonts keep content usable.
5. Ticket angles are deterministic, card-only, no greater than ±0.35deg, and removed on mobile/reduced-motion. No randomization code exists.
6. Color is semantic: blue for action/focus/links, red for errors/destructive/failed states, yellow for active-filter context/tape, and no decorative status rainbow.
7. Native dialog focus, cancel/Escape close, the explicit Close button, opener focus return, Markdown edit/view, local time, copy announcements, comment/mention actions, and HTMX swaps behave exactly as before; the backdrop remains visual only.
8. Controls meet 44px targets, focus is visible, labels and status are not color-only, content wraps, and the board remains keyboard-scrollable horizontally.
9. Desktop modal remains centered at 60/40; mobile modal is full-screen and stacked without rotated or clipped text.
10. No route, model, data, authorization, label, dependency, framework, or build-step change is introduced.

## Verification

Keep verification proportional to this CSS/markup-only change:

- Extend focused assertions in `tests/test_web_board.py` and/or `tests/test_web_ui_refresh.py` for the two scope classes, unchanged native-dialog semantics, local font references, scoped selectors, deterministic rotation bounds, and mobile/reduced-motion overrides.
- Run the focused board, ticket-detail, comment, and UI-refresh tests touched by the selectors/markup. Do not run the full suite unless a relevant regression appears.
- Use Chromium at 1440×900 and 390×844 to capture/smoke-check the board and open detail modal. Confirm font loading, dot scale, hierarchy, four-lane horizontal scroll, wrapping, full-screen mobile stacking, and absence of theme leakage.
- In the same smoke pass, keyboard-open a ticket, edit/cancel details, exercise a comment/mention swap, close with Escape, and verify focus returns to its ticket. Check normal and reduced-motion media settings.

## Rollout and Fallback

Ship as scoped CSS and two explicit markup hooks. No feature flag or migration is needed because removing those hooks is the complete visual rollback and leaves the existing UI underneath. Expanding the theme to global navigation or other project screens requires a separate approved design.
