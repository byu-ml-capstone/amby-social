# Restrained Hand-Drawn Board Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the active sprint board and only its board-opened ticket-detail dialog a restrained paper-and-pencil treatment while preserving every existing workflow and all other screens.

**Architecture:** Keep the existing FastAPI/Jinja/HTMX/Alpine structure and add two opt-in CSS hooks: `.sketch-board` on the active board root and `.sketch-ticket-detail` on the dialog branch of the ticket detail partial. Self-host two official Google Fonts WOFF2 files, define shared theme tokens under those two hooks, and layer scoped CSS after the existing global system so removing the hooks is the complete rollback.

**Tech Stack:** FastAPI, Jinja, HTMX, Alpine.js, semantic HTML, one vanilla CSS file, native `<dialog>`, pytest/TestClient, Playwright Chromium

**Spec:** `docs/superpowers/specs/2026-09-17-restrained-hand-drawn-board-theme-design.md`

## Global Constraints

- Theme only the active board header, sprint selector, actions, project tabs, filters, four-lane workspace, cards, empty lanes, and the centered ticket-detail dialog opened from that board.
- Keep the global sidebar/mobile navigation, auth, dashboard, settings, backlog, sprint history, standalone ticket detail, new-ticket dialog, and sprint dialogs on the current visual system.
- Do not change routes, models, services, migrations, data, permissions, copy, filtering, form submission, HTMX targets/swaps, Alpine state, `app/static/app.js`, or `app/templates/base.html`.
- Do not add React, Tailwind, Lucide, another framework, JavaScript, dependency, build step, runtime font service, drag-and-drop, random placement, animation flourish, theme switcher, dark mode, or new ticket fields.
- Keep all theme custom properties off `:root`; define them only on `.sketch-board` and `.sketch-ticket-detail`.
- Anchor board overrides to `.sketch-board > .project-header`, `.sketch-board > .project-navigation`, `.sketch-board > .board-filters`, or `.sketch-board .board-workspace`. Never add broad `.sketch-board .app-dialog`, `.sketch-board .app-form`, or `.sketch-board .app-primary-button` rules.
- Keep native semantic controls, native dialog centering/cancel behavior, current focus management, accessible names, inline SVG, horizontal scroll semantics, clipboard/local-time behavior, and every existing HTMX boundary.
- Use the existing 4px spacing scale. Interactive targets in the two theme scopes are at least 44px by 44px, and `:focus-visible` is a 3px blue-ink outline with a 2px offset.
- Correction red never appears as small red text on paper or as white text on red; pair it with a border, underline, icon, or pale field so the state is not color-only.
- Verification stays focused. Do not run the full pytest suite for this CSS/markup slice.

## File Map

- Create `app/static/fonts/Kalam-Bold.woff2`, `PatrickHand-Regular.woff2`, `Kalam-OFL.txt`, `PatrickHand-OFL.txt`, and `ATTRIBUTION.md`: immutable local font payload, licenses, and exact provenance.
- Modify `app/static/app.css`: global `@font-face` declarations, two-scope theme tokens, board overrides, dialog overrides, and scoped responsive/reduced-motion rules.
- Modify `app/templates/board.html`: add only `.sketch-board` to the existing `.project-board` root.
- Modify `app/templates/partials/ticket_detail.html`: add `.sketch-ticket-detail` only to the `<dialog>` branch and state classes only where text alone cannot be selected safely.
- Modify `tests/test_web_shell.py`: exercise the served CSS/font/license routes rather than reading source files.
- Modify `tests/test_web_board.py`: exercise rendered board/dialog hooks and the served board CSS contract.
- Modify `tests/test_web_ui_refresh.py`: exercise dialog-versus-standalone scoping and desktop/mobile CSS contracts.
- Modify `tests/test_web_ticket_development.py`: assert failed development state has a non-color semantic hook while visible status text remains.
- Modify `tests/playwright_ticket_drawer_focus.py`: retain the disposable-app smoke and make its desktop/mobile, reduced-motion, keyboard, font, and capture checks exact.
- Modify `.interface-design/system.md`: after Chromium verification, record the approved board-only exception to the calm global system.

---

### Task 1: Self-hosted fonts and scoped token foundation

**Files:**

- Create: `app/static/fonts/Kalam-Bold.woff2`
- Create: `app/static/fonts/PatrickHand-Regular.woff2`
- Create: `app/static/fonts/Kalam-OFL.txt`
- Create: `app/static/fonts/PatrickHand-OFL.txt`
- Create: `app/static/fonts/ATTRIBUTION.md`
- Modify: `app/static/app.css:1-27`
- Modify: `tests/test_web_shell.py:1-24`

**Interfaces:**

- Consumes: the existing `/static` mount, `app/static/app.css`, and the existing `client` TestClient fixture.
- Produces: local `/static/fonts/Kalam-Bold.woff2` and `/static/fonts/PatrickHand-Regular.woff2` URLs; `--sketch-*` properties available only on `.sketch-board` and `.sketch-ticket-detail`; the exact `var(--sketch-font-heading)` and `var(--sketch-font-hand)` names consumed by Tasks 2 and 3.

- [ ] **Step 1: Add one failing HTTP/CSS contract test**

Add this test to `tests/test_web_shell.py`; it checks the actual served artifacts, not source-file text:

```python
def test_sketch_fonts_and_tokens_are_local_served_artifacts(client):
    css_response = client.get("/static/app.css")
    assert css_response.status_code == 200
    css = css_response.text

    assert 'font-family: "Kalam";' in css
    assert 'url("/static/fonts/Kalam-Bold.woff2") format("woff2")' in css
    assert "font-weight: 700;" in css
    assert 'font-family: "Patrick Hand";' in css
    assert 'url("/static/fonts/PatrickHand-Regular.woff2") format("woff2")' in css
    assert "font-weight: 400;" in css
    assert css.count("font-display: swap;") >= 2
    assert "fonts.googleapis.com" not in css
    assert "fonts.gstatic.com" not in css

    root_tokens = css.split(":root {", 1)[1].split("}", 1)[0]
    assert "--sketch-" not in root_tokens
    scope = css.split(".sketch-board,\n.sketch-ticket-detail {", 1)[1].split("}", 1)[0]
    for declaration in (
        "--sketch-paper: #fdfbf7;",
        "--sketch-pencil: #2d2d2d;",
        "--sketch-erased: #e5e0d8;",
        "--sketch-correction: #ff4d4d;",
        "--sketch-blue-ink: #2d5da1;",
        "--sketch-post-it: #fff9c4;",
        "--sketch-dot-size: 24px;",
        "--sketch-border: 2px solid var(--sketch-pencil);",
        "--sketch-radius-control: 6px 9px 7px 5px / 7px 5px 9px 6px;",
        "--sketch-radius-card: 8px 12px 7px 10px / 10px 8px 11px 7px;",
        "--sketch-radius-panel: 12px 9px 14px 10px / 10px 13px 9px 12px;",
        "--sketch-shadow: 3px 3px 0 var(--sketch-pencil);",
        '--sketch-font-heading: "Kalam", cursive;',
        '--sketch-font-hand: "Patrick Hand", cursive;',
    ):
        assert declaration in scope

    for filename in ("Kalam-Bold.woff2", "PatrickHand-Regular.woff2"):
        response = client.get(f"/static/fonts/{filename}")
        assert response.status_code == 200
        assert response.content[:4] == b"wOF2"

    for filename, copyright_line in (
        ("Kalam-OFL.txt", "Copyright (c) 2014, Indian Type Foundry"),
        ("PatrickHand-OFL.txt", "Copyright (c) 2010-2012 Patrick Wagesreiter"),
    ):
        response = client.get(f"/static/fonts/{filename}")
        assert response.status_code == 200
        assert copyright_line in response.text
        assert "SIL OPEN FONT LICENSE Version 1.1" in response.text

    attribution = client.get("/static/fonts/ATTRIBUTION.md")
    assert attribution.status_code == 200
    assert "Kalam Bold 700" in attribution.text
    assert "Patrick Hand Regular 400" in attribution.text
    assert "github.com/google/fonts/tree/main/ofl/" in attribution.text
```

- [ ] **Step 2: Run the new test to prove RED**

Run:

```bash
uv run pytest -q tests/test_web_shell.py::test_sketch_fonts_and_tokens_are_local_served_artifacts
```

Expected: FAIL because the `@font-face` declarations, scoped token block, and `/static/fonts/...` assets do not exist.

- [ ] **Step 3: Download only the approved official font artifacts and retain their licenses**

Use the exact Google Fonts CSS API requests to confirm the family/weight responses, then use the pinned Latin WOFF2 URLs returned by those responses. Do not copy an HTML `<link>` into the application and do not reference either Google host from application CSS or templates.

```bash
font_user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
curl -fsSL -A "$font_user_agent" 'https://fonts.googleapis.com/css2?family=Kalam:wght@700&display=swap'
curl -fsSL -A "$font_user_agent" 'https://fonts.googleapis.com/css2?family=Patrick+Hand:wght@400&display=swap'
mkdir -p app/static/fonts
curl -fsSL --proto '=https' 'https://fonts.gstatic.com/s/kalam/v18/YA9Qr0Wd4kDdMtDqHTLMkiQ.woff2' -o app/static/fonts/Kalam-Bold.woff2
curl -fsSL --proto '=https' 'https://fonts.gstatic.com/s/patrickhand/v25/LDI1apSQOAYtSuYWp8ZhfYe8XsLL.woff2' -o app/static/fonts/PatrickHand-Regular.woff2
curl -fsSL --proto '=https' 'https://raw.githubusercontent.com/google/fonts/main/ofl/kalam/OFL.txt' -o app/static/fonts/Kalam-OFL.txt
curl -fsSL --proto '=https' 'https://raw.githubusercontent.com/google/fonts/main/ofl/patrickhand/OFL.txt' -o app/static/fonts/PatrickHand-OFL.txt
```

Verify both downloads are WOFF2 and exactly match the pinned artifacts:

```bash
file app/static/fonts/Kalam-Bold.woff2 app/static/fonts/PatrickHand-Regular.woff2 | rg 'Web Open Font Format \(Version 2\)'
test "$(xxd -p -l 4 app/static/fonts/Kalam-Bold.woff2)" = 774f4632
test "$(xxd -p -l 4 app/static/fonts/PatrickHand-Regular.woff2)" = 774f4632
printf '%s  %s\n' \
  252063af6ade8b9a744cde4ddad0fc21ea53b8ba711eed121a0c2e8610ea9c93 app/static/fonts/Kalam-Bold.woff2 \
  ac5bc9033b2572bf84d39f7150c1634e37ed16e8dbee632d6d0bceac0bbf0199 app/static/fonts/PatrickHand-Regular.woff2 \
  | shasum -a 256 -c -
rg -n 'SIL OPEN FONT LICENSE Version 1.1' app/static/fonts/Kalam-OFL.txt app/static/fonts/PatrickHand-OFL.txt
```

Create `app/static/fonts/ATTRIBUTION.md` with this exact provenance:

```markdown
# Font attribution

- Kalam Bold 700 — Google Fonts CSS API: `https://fonts.googleapis.com/css2?family=Kalam:wght@700&display=swap`; pinned Latin WOFF2: `https://fonts.gstatic.com/s/kalam/v18/YA9Qr0Wd4kDdMtDqHTLMkiQ.woff2`; upstream family: `https://github.com/google/fonts/tree/main/ofl/kalam`; license: `Kalam-OFL.txt`.
- Patrick Hand Regular 400 — Google Fonts CSS API: `https://fonts.googleapis.com/css2?family=Patrick+Hand:wght@400&display=swap`; pinned Latin WOFF2: `https://fonts.gstatic.com/s/patrickhand/v25/LDI1apSQOAYtSuYWp8ZhfYe8XsLL.woff2`; upstream family: `https://github.com/google/fonts/tree/main/ofl/patrickhand`; license: `PatrickHand-OFL.txt`.

The application serves these checked-in files locally. It makes no runtime request to Google Fonts.
```

- [ ] **Step 4: Add the two font faces and exact two-scope token block**

Insert before the current `:root` block in `app/static/app.css`:

```css
@font-face {
  font-family: "Kalam";
  src: url("/static/fonts/Kalam-Bold.woff2") format("woff2");
  font-style: normal;
  font-weight: 700;
  font-display: swap;
}

@font-face {
  font-family: "Patrick Hand";
  src: url("/static/fonts/PatrickHand-Regular.woff2") format("woff2");
  font-style: normal;
  font-weight: 400;
  font-display: swap;
}
```

Insert this foundation after the current non-media global rules and immediately before the existing first `@media` block so desktop contract tests see it without changing application tokens:

```css
.sketch-board,
.sketch-ticket-detail {
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
}
```

- [ ] **Step 5: Run the focused test to prove GREEN**

Run:

```bash
uv run pytest -q tests/test_web_shell.py::test_sketch_fonts_and_tokens_are_local_served_artifacts
git diff --check
```

Expected: PASS; both font responses begin with WOFF2 magic, license/attribution routes are readable, the served CSS contains only local font URLs, and the global `:root` contains no theme tokens.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/static/fonts app/static/app.css tests/test_web_shell.py
git commit -m "feat: self-host sketch board fonts"
```

---

### Task 2: Scoped board paper treatment and deterministic cards

**Files:**

- Modify: `app/templates/board.html:6`
- Modify: `app/static/app.css:after the Task 1 theme token block`
- Modify: `tests/test_web_board.py:95-165,318-339`

**Interfaces:**

- Consumes: Task 1's `.sketch-board` token scope and both local font variables; existing direct children `.project-header`, `.project-navigation`, `.board-filters`, and descendant `.board-workspace`.
- Produces: `class="project-board sketch-board"` on active boards; deterministic four-card CSS selectors; no dialog, route, script, or partial interface changes. Task 3 relies only on the independent `.sketch-ticket-detail` scope, not DOM inheritance from this root.

- [ ] **Step 1: Add failing rendered-page and served-CSS tests**

Add these tests to `tests/test_web_board.py`, using the existing `active_sprint_world` fixture:

```python
def test_board_enables_the_sketch_scope_without_theming_nested_dialogs(
    client, active_sprint_world, login_as
):
    login_as(active_sprint_world.owner.email)
    response = client.get(f"/projects/{active_sprint_world.project.slug}?mine=1")

    assert response.status_code == 200
    assert 'class="project-board sketch-board"' in response.text
    assert 'class="board-filter-summary">Filters active</p>' in response.text
    assert '<dialog id="ticket-modal"' in response.text
    assert 'id="ticket-modal" x-ref="ticketModal" class="app-dialog"' in response.text
    assert "sketch-ticket-detail" not in response.text


def test_board_sketch_css_is_scoped_deterministic_and_responsive(client):
    response = client.get("/static/app.css")
    assert response.status_code == 200
    css = response.text

    root_tokens = css.split(":root {", 1)[1].split("}", 1)[0]
    assert "--sketch-" not in root_tokens
    for selector in (
        ".sketch-board > .project-header",
        ".sketch-board > .project-navigation",
        ".sketch-board > .board-filters",
        ".sketch-board .board-workspace",
    ):
        assert selector in css
    for forbidden_selector in (
        ".sketch-board .app-dialog",
        ".sketch-board .app-form",
        ".sketch-board .app-primary-button",
    ):
        assert forbidden_selector not in css

    assert "radial-gradient(circle, var(--sketch-erased) 1px, transparent 1px)" in css
    assert "background-size: var(--sketch-dot-size) var(--sketch-dot-size);" in css
    assert "grid-template-columns: repeat(4, minmax(240px, 1fr));" in css
    assert ".sketch-board .board-workspace .board-scroll {" in css
    assert "overflow-x: auto;" in css
    for index, angle in enumerate(("-0.25deg", "0.35deg", "-0.15deg", "0.2deg"), 1):
        rule = css.split(
            f".sketch-board .ticket-card:nth-child(4n + {index}) {{", 1
        )[1].split("}", 1)[0]
        assert f"transform: rotate({angle});" in rule
    assert ".sketch-board .ticket-card:hover {" in css
    hover_rule = css.split(".sketch-board .ticket-card:hover {", 1)[1].split("}", 1)[0]
    assert "border-color: var(--sketch-blue-ink);" in hover_rule
    assert "transform:" not in hover_rule

    mobile = css.split("@media (max-width: 767px)", 1)[1]
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".sketch-board { padding: var(--space-4); }" in mobile
    assert ".sketch-board .ticket-card { transform: none; }" in mobile
    assert ".sketch-board .ticket-card { transform: none; }" in reduced
```

- [ ] **Step 2: Run the two tests to prove RED**

Run:

```bash
uv run pytest -q \
  tests/test_web_board.py::test_board_enables_the_sketch_scope_without_theming_nested_dialogs \
  tests/test_web_board.py::test_board_sketch_css_is_scoped_deterministic_and_responsive
```

Expected: FAIL first on the missing `.sketch-board` class and then on missing scoped board rules.

- [ ] **Step 3: Add the board hook and root canvas**

Change only the existing root opening tag in `app/templates/board.html`:

```html
<div class="project-board sketch-board" x-data="{ ticketError: '', ticketOpener: null }" @ticket-drawer-closed.window="$nextTick(() => document.getElementById(`ticket-${ticketOpener}`)?.querySelector('button')?.focus())">
```

Do not add the hook to `app/templates/ticket_detail.html`, backlog/history/settings templates, `ticket_modal.html`, or sprint-dialog partials.

Start the board CSS with the paper canvas and open header treatment:

```css
.sketch-board {
  background-color: var(--sketch-paper);
  background-image: radial-gradient(circle, var(--sketch-erased) 1px, transparent 1px);
  background-size: var(--sketch-dot-size) var(--sketch-dot-size);
  color: var(--sketch-pencil);
  padding: var(--space-6);
}

.sketch-board > .project-header {
  background: transparent;
}

.sketch-board > .project-header .project-title {
  color: var(--sketch-pencil);
  font-family: var(--sketch-font-heading);
  font-size: 32px;
  font-weight: 700;
  letter-spacing: 0;
  line-height: 1.15;
}

.sketch-board > .project-header .sprint-title {
  font-family: var(--sketch-font-heading);
  font-size: 18px;
  font-weight: 700;
  line-height: 1.25;
}

.sketch-board > .project-header .sprint-meta,
.sketch-board > .project-header .sprint-status {
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 12px;
  line-height: 1.4;
}
```

- [ ] **Step 4: Style only the named navigation/filter/control regions and every control state**

Use these exact selector boundaries and values:

```css
.sketch-board > .project-header .sprint-selector,
.sketch-board > .board-filters {
  background: var(--sketch-paper);
  border: var(--sketch-border);
  border-radius: var(--sketch-radius-panel);
  padding: var(--space-2) var(--space-3);
}

.sketch-board > .project-navigation .project-tabs {
  border-bottom: 2px solid var(--sketch-pencil);
}

.sketch-board > .project-navigation .project-tab {
  color: var(--sketch-pencil);
  font-family: var(--sketch-font-hand);
  font-size: 17px;
  font-weight: 400;
  line-height: 1.2;
  min-height: 44px;
}

.sketch-board > .project-navigation .project-tab.is-active {
  border-bottom: 2px solid var(--sketch-blue-ink);
  color: var(--sketch-blue-ink);
}

.sketch-board > .board-filters .board-filter-summary {
  background: var(--sketch-post-it);
  border: 2px solid var(--sketch-pencil);
  border-radius: var(--sketch-radius-control);
  color: var(--sketch-pencil);
  padding: var(--space-1) var(--space-2);
}

.sketch-board > .project-header .sprint-selector select,
.sketch-board > .board-filters select {
  background: var(--sketch-erased);
  border: var(--sketch-border);
  border-radius: var(--sketch-radius-control);
  color: var(--sketch-pencil);
  font-family: ui-sans-serif, system-ui, sans-serif;
  min-height: 44px;
}

.sketch-board > .project-header .app-primary-button,
.sketch-board > .project-header .app-secondary-button,
.sketch-board > .project-header .app-ghost-button,
.sketch-board > .project-header .app-danger-button,
.sketch-board > .board-filters .app-primary-button,
.sketch-board > .board-filters .app-secondary-button,
.sketch-board > .board-filters .app-ghost-button,
.sketch-board > .board-filters .app-danger-button {
  border-radius: var(--sketch-radius-control);
  font-family: var(--sketch-font-hand);
  font-size: 17px;
  font-weight: 400;
  line-height: 1.2;
  min-height: 44px;
  min-width: 44px;
}
```

For those same direct-region selectors, implement this state matrix without introducing unscoped aliases:

| State | Exact treatment |
|---|---|
| primary | blue-ink background and 2px blue-ink border; paper text |
| secondary | paper background, 2px pencil border, pencil text |
| ghost | transparent background and transparent 2px border; erased-gray field on hover |
| danger | pale `color-mix(in srgb, var(--sketch-correction) 12%, var(--sketch-paper))` field, 2px correction-red border, pencil text |
| disabled / `[aria-disabled="true"]` | erased-gray field and pencil label; retain the label and native disabled semantics |
| hover | blue-ink border or underline; never add or change rotation |
| active | retain the current card angle; use border/color only |
| focus-visible | `outline: 3px solid var(--sketch-blue-ink); outline-offset: 2px` |

Keep form labels, filter status, and lane counts in system sans at `12px / 1.4`, weight `600`; keep sprint-selector eyebrow text at `11px / 1.35`, weight `700`. Preserve native select option rendering and the existing visible `Filters active` text.

- [ ] **Step 5: Style the workspace, lanes, cards, empty states, wrapping, and fixed angle pattern**

Append scoped rules with these exact selectors and type roles:

```css
.sketch-board .board-workspace .board-scroll {
  overflow-x: auto;
}

.sketch-board .board-workspace .board-columns {
  grid-template-columns: repeat(4, minmax(240px, 1fr));
  min-width: 1000px;
}

.sketch-board .board-workspace .board-column {
  background: var(--sketch-erased);
  border: var(--sketch-border);
  border-radius: var(--sketch-radius-panel);
  box-shadow: none;
}

.sketch-board .board-workspace .board-column h2 {
  color: var(--sketch-pencil);
  font-family: var(--sketch-font-heading);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0;
  line-height: 1.2;
  text-transform: none;
}

.sketch-board .board-workspace .board-column h2 span {
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  line-height: 1.4;
}

.sketch-board .board-workspace .ticket-card {
  background: var(--sketch-paper);
  border: var(--sketch-border);
  border-radius: var(--sketch-radius-card);
  box-shadow: var(--sketch-shadow);
}

.sketch-board .board-workspace .ticket-card:hover {
  border-color: var(--sketch-blue-ink);
}

.sketch-board .board-workspace .ticket-card-title {
  font-family: var(--sketch-font-hand);
  font-size: 18px;
  font-weight: 400;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.sketch-board .board-workspace .ticket-number {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
}

.sketch-board .board-workspace .ticket-card-meta {
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.4;
}

.sketch-board .board-workspace .board-empty-state {
  border: 2px dashed var(--sketch-pencil);
  border-radius: var(--sketch-radius-panel);
  color: var(--sketch-pencil);
  min-width: 100%;
  padding: var(--space-4);
}

.sketch-board .ticket-card:nth-child(4n + 1) { transform: rotate(-0.25deg); }
.sketch-board .ticket-card:nth-child(4n + 2) { transform: rotate(0.35deg); }
.sketch-board .ticket-card:nth-child(4n + 3) { transform: rotate(-0.15deg); }
.sketch-board .ticket-card:nth-child(4n + 4) { transform: rotate(0.2deg); }
```

Also set `.ticket-card-open` to a 44px minimum target and the same blue `:focus-visible` outline. Keep `.board-scroll`, `.board-columns`, headings, buttons, and text untransformed. Keep `.board-truncated` as a separate explicit system-sans message. Add `overflow-wrap: anywhere` only to ticket title/metadata content that can grow; do not hide or truncate content.

- [ ] **Step 6: Add mobile and reduced-motion overrides without changing order or horizontal scrolling**

Place these inside the existing matching media queries, after current global rules:

```css
@media (max-width: 767px) {
  .sketch-board { padding: var(--space-4); }
  .sketch-board > .project-header,
  .sketch-board > .board-filters,
  .sketch-board > .board-filters .board-filter-controls {
    align-items: stretch;
  }
  .sketch-board .ticket-card { transform: none; }
}

@media (prefers-reduced-motion: reduce) {
  .sketch-board .ticket-card { transform: none; }
}
```

The mobile selectors may wrap/stack through the existing flex rules but must not use `order`. Do not change `.board-columns` minimum width or `.board-scroll` overflow, so four lanes remain horizontally scrollable at 390px.

- [ ] **Step 7: Run focused GREEN and leakage checks**

Run:

```bash
uv run pytest -q \
  tests/test_web_board.py::test_board_enables_the_sketch_scope_without_theming_nested_dialogs \
  tests/test_web_board.py::test_board_sketch_css_is_scoped_deterministic_and_responsive \
  tests/test_web_board.py::test_board_accessibility_uses_labeled_filters_and_human_status_text \
  tests/test_web_board.py::test_board_css_keeps_columns_horizontally_scrollable_on_mobile \
  tests/test_web_shell.py::test_sketch_fonts_and_tokens_are_local_served_artifacts
git diff --check
```

Expected: PASS. As a rollback audit, remove only `sketch-board` from the rendered opening tag in a local diff and confirm no theme selector matches the active board; restore it before committing. Confirm `git diff -- app/templates/partials/ticket_modal.html app/templates/partials/sprint_form.html app/templates/partials/sprint_start.html app/templates/partials/sprint_close.html app/templates/base.html app/static/app.js` is empty.

- [ ] **Step 8: Commit Task 2**

```bash
git add app/templates/board.html app/static/app.css tests/test_web_board.py
git commit -m "feat: theme active sprint board"
```

---

### Task 3: Scoped ticket-detail paper sheet, state coverage, and verified system record

**Files:**

- Modify: `app/templates/partials/ticket_detail.html:1-3,31-36,128-133`
- Modify: `app/static/app.css:118-183 and scoped theme section/media queries`
- Modify: `tests/test_web_board.py:117-158`
- Modify: `tests/test_web_ui_refresh.py:110-158`
- Modify: `tests/test_web_ticket_detail.py:91-115`
- Modify: `tests/test_web_ticket_development.py:159-196`
- Modify: `tests/playwright_ticket_drawer_focus.py:65-180`
- Modify: `.interface-design/system.md:after Ticket development activity`

**Interfaces:**

- Consumes: Task 1's `.sketch-ticket-detail` token scope; the existing `detail_drawer: bool` branch; Task 2's board opener/focus-return behavior; existing `data-description-*`, comment, mention, local-time, copy-status, and HTMX hooks.
- Produces: `class="app-dialog ticket-detail-modal sketch-ticket-detail"` only when `detail_drawer` is true; `.is-failed` only on visible failed-CI text; unchanged standalone `.ticket-detail-page`; a 1440x900/390x844 Chromium smoke that preserves all behavior.

- [ ] **Step 1: Add failing rendered/HTTP/CSS/state assertions**

Update `tests/test_web_board.py::test_board_ticket_detail_fragment_uses_a_centered_native_dialog` to expect:

```python
assert (
    '<dialog id="ticket-detail-panel" '
    'class="app-dialog ticket-detail-modal sketch-ticket-detail"'
    in fragment.text
)
assert 'aria-labelledby="ticket-detail-heading"' in fragment.text
assert '@cancel="$event.preventDefault(); $el.close()"' in fragment.text
assert "$el.querySelector('#ticket-detail-heading').focus()" in fragment.text
assert 'class="ticket-detail-layout"' in fragment.text
```

Extend `tests/test_web_ui_refresh.py::test_ticket_detail_partial_is_an_accessible_centered_modal` to render both values of `detail_drawer` and assert the hook is exclusive to the dialog branch:

```python
assert 'class="app-dialog ticket-detail-modal sketch-ticket-detail"' in dialog_source
assert 'class="ticket-detail-page"' in page_source
assert "sketch-ticket-detail" not in page_source
```

Add served-CSS assertions to `tests/test_web_ui_refresh.py::test_remaining_workspace_views_collapse_to_one_column_on_mobile`:

```python
assert ".sketch-ticket-detail {" in desktop_css
assert "max-width: 1120px;" in desktop_css
assert "grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr);" in desktop_css
assert "border-left: 2px dashed var(--sketch-erased);" in desktop_css
assert ".sketch-ticket-detail::before {" in desktop_css
assert "height: 22px;" in desktop_css
assert "width: 88px;" in desktop_css
assert ".sketch-ticket-detail .ticket-comment {" in desktop_css
assert ".sketch-ticket-detail .ticket-mention-menu {" in desktop_css
assert ".sketch-ticket-detail .ticket-description-fields[hidden] { display: none; }" in desktop_css
assert ".sketch-ticket-detail { border-width: 0;" in mobile_css
assert ".sketch-ticket-detail::before { display: none; }" in mobile_css
assert "border-left: 0;" in mobile_css
assert "border-top: 2px dashed var(--sketch-erased);" in mobile_css
```

In `tests/test_web_ticket_development.py::test_ticket_detail_renders_safe_active_development_for_every_reader_and_mode`, retain the visible `CI Failed` assertion and add:

```python
assert '<span class="ticket-development-badge is-failed">CI Failed</span>' in response.text
assert '<span class="ticket-development-state is-failed">Failed</span>' in response.text
```

Also add an HTTP assertion using the existing `ticket_world` fixture that a non-HX GET renders the standalone page with neither scope hook:

```python
standalone = client.get(f"/projects/{ticket_world.project.slug}/tickets/1")
assert standalone.status_code == 200
assert 'class="ticket-detail-page"' in standalone.text
assert "sketch-ticket-detail" not in standalone.text
assert 'class="project-board sketch-board"' not in standalone.text
```

- [ ] **Step 2: Run the named tests to prove RED**

Run:

```bash
uv run pytest -q \
  tests/test_web_board.py::test_board_ticket_detail_fragment_uses_a_centered_native_dialog \
  tests/test_web_ui_refresh.py::test_ticket_detail_partial_is_an_accessible_centered_modal \
  tests/test_web_ui_refresh.py::test_remaining_workspace_views_collapse_to_one_column_on_mobile \
  tests/test_web_ticket_development.py::test_ticket_detail_renders_safe_active_development_for_every_reader_and_mode
```

Expected: FAIL on the missing dialog hook, failed-state classes, tape/scoped modal rules, and mobile scoped divider.

- [ ] **Step 3: Add only the dialog and failed-state semantic hooks**

Change the first branch of `app/templates/partials/ticket_detail.html`; leave the standalone `<section>` line unchanged:

```jinja2
{% if detail_drawer %}
<dialog id="ticket-detail-panel" class="app-dialog ticket-detail-modal sketch-ticket-detail" aria-labelledby="ticket-detail-heading" x-init="$nextTick(() => { $el.showModal(); $el.querySelector('#ticket-detail-heading').focus() })" @cancel="$event.preventDefault(); $el.close()" @close="$dispatch('ticket-drawer-closed'); $el.remove()">
{% else %}
<section id="ticket-detail-panel" class="ticket-detail-page" aria-labelledby="ticket-detail-heading">
{% endif %}
```

CSS cannot select text content, so add a class only to the two existing visible failed-CI spans:

```jinja2
{% if row.ci_state.value != 'NONE' %}<span class="ticket-development-badge{% if row.ci_state.value == 'FAILED' %} is-failed{% endif %}">CI {{ row.ci_state.value|title }}</span>{% endif %}
```

```jinja2
{% if row.ci_state.value != 'NONE' %}<span class="ticket-development-state{% if row.ci_state.value == 'FAILED' %} is-failed{% endif %}">{{ row.ci_state.value|title }}</span>{% endif %}
```

Do not change dialog events, heading tabindex, Close button, form actions, IDs, `hidden`, `role=status`, `aria-live`, link safety attributes, HTMX targets/swaps, or either comments/template file.

- [ ] **Step 4: Implement the centered sheet, 60/40 divider, hierarchy, and controls under the dialog hook**

Insert these scoped desktop overrides after the global ticket-detail rules and before the existing first `@media` block:

```css
.sketch-ticket-detail {
  background: var(--sketch-paper);
  border: var(--sketch-border);
  border-radius: var(--sketch-radius-panel);
  box-shadow: var(--sketch-shadow);
  color: var(--sketch-pencil);
  max-width: 1120px;
}

.sketch-ticket-detail::before {
  background: var(--sketch-post-it);
  content: "";
  height: 22px;
  left: 50%;
  position: absolute;
  top: -2px;
  transform: translateX(-50%);
  width: 88px;
}

.sketch-ticket-detail .ticket-detail-layout {
  display: grid;
  gap: var(--space-5);
  grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr);
}

.sketch-ticket-detail .ticket-comments {
  border-left: 2px dashed var(--sketch-erased);
  border-top: 0;
  margin-top: 0;
  padding-left: var(--space-5);
  padding-top: 0;
}

.sketch-ticket-detail .ticket-detail-header h2 {
  font-family: var(--sketch-font-heading);
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}

.sketch-ticket-detail .ticket-development h3,
.sketch-ticket-detail .ticket-comments-header h3,
.sketch-ticket-detail .ticket-description-label {
  font-family: var(--sketch-font-heading);
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
}

.sketch-ticket-detail .ticket-description,
.sketch-ticket-detail .ticket-comment-body,
.sketch-ticket-detail .ticket-development-title {
  font-family: var(--sketch-font-hand);
  font-size: 18px;
  font-weight: 400;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.sketch-ticket-detail code,
.sketch-ticket-detail .ticket-number {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
```

Apply system sans `12px / 1.4` weight `600` to form labels, statuses, counts, repository names, dates, comment authors/actions, and development metadata; use `11px / 1.35` weight `700` for compact badges. Apply Patrick Hand `17px / 1.2` weight `400` and a 44px minimum in both dimensions to `.app-primary-button`, `.app-secondary-button`, `.app-ghost-button`, `.app-danger-button`, and `.app-icon-button` under `.sketch-ticket-detail`. Apply Patrick Hand `18px / 1.35` only to text inputs and textareas; keep selects/options in system sans.

Use the Task 2 button state matrix under `.sketch-ticket-detail`, including pencil text plus correction-red border/pale field for danger. Inputs/textareas/selects use erased background, 2px pencil border, control radius, pencil text/caret, 44px minimum height, and 3px blue focus outline with 2px offset. `.form-error` uses pencil text on the pale correction field with a 2px red left border; `.form-status` keeps visible text and a 2px blue left border. Never replace the existing label or status text with color.

- [ ] **Step 5: Implement development, comments, mentions, wrapping, and mobile states without behavior changes**

Add only scoped selectors:

```css
.sketch-ticket-detail .ticket-development-row + .ticket-development-row {
  border-top: 1px solid var(--sketch-erased);
}

.sketch-ticket-detail .ticket-development-badge,
.sketch-ticket-detail .ticket-development-state {
  background: var(--sketch-erased);
  color: var(--sketch-pencil);
}

.sketch-ticket-detail .ticket-development-badge.is-failed,
.sketch-ticket-detail .ticket-development-state.is-failed {
  border: 2px solid var(--sketch-correction);
  color: var(--sketch-pencil);
  text-decoration: underline;
  text-decoration-color: var(--sketch-correction);
}

.sketch-ticket-detail .ticket-comment {
  background: var(--sketch-erased);
  border: 2px solid var(--sketch-pencil);
  border-radius: var(--sketch-radius-card);
  box-shadow: none;
  transform: none;
}

.sketch-ticket-detail .ticket-mention-menu {
  background: var(--sketch-paper);
  border: var(--sketch-border);
  border-radius: var(--sketch-radius-control);
  box-shadow: var(--sketch-shadow);
  transform: none;
}

.sketch-ticket-detail .ticket-mention-menu button:hover,
.sketch-ticket-detail .ticket-mention-menu button[aria-selected="true"],
.sketch-ticket-detail .ticket-mention-menu button:focus-visible {
  background: var(--sketch-erased);
  color: var(--sketch-blue-ink);
}

.sketch-ticket-detail .ticket-description-fields[hidden] { display: none; }
```

Keep comment edit/delete forms in normal flow through the existing `:has(details[open])` rule. Add `overflow-wrap: anywhere` to long Markdown, branch commands, repository names, development titles, comments, and mention labels; never rotate comment cards, development rows, menus, controls, headings, modal content, or the dialog itself.

Place these after the existing mobile ticket-detail rules:

```css
@media (max-width: 767px) {
  .sketch-ticket-detail { border-width: 0; }
  .sketch-ticket-detail::before { display: none; }
  .sketch-ticket-detail .ticket-detail-layout { display: block; }
  .sketch-ticket-detail .ticket-comments {
    border-left: 0;
    border-top: 2px dashed var(--sketch-erased);
    margin-top: var(--space-6);
    padding-left: 0;
    padding-top: var(--space-5);
  }
}
```

Retain the global `height: 100dvh`, full width, single-column field grid, and desktop `inset: 0; margin: auto`. Do not add backdrop-click close; the backdrop remains visual only.

- [ ] **Step 6: Extend and run the focused Chromium smoke at exact viewports**

In `tests/playwright_ticket_drawer_focus.py`, set the initial context to `browser.new_page(viewport={"width": 1440, "height": 900})`. Keep the disposable database/server workflow and extend its existing checks to:

```python
assert page.evaluate("document.fonts.check('700 32px Kalam')")
assert page.evaluate("document.fonts.check('400 18px \\"Patrick Hand\\"')")
assert page.locator(".project-board").evaluate(
    "el => getComputedStyle(el).backgroundSize === '24px 24px'"
)
assert page.locator(".board-scroll").evaluate("el => el.scrollWidth >= el.clientWidth")

card.focus()
card.press("Enter")
assert panel.get_attribute("class") == "app-dialog ticket-detail-modal sketch-ticket-detail"
assert page.locator("#ticket-detail-heading").evaluate(
    "el => el === document.activeElement"
)
page.get_by_role("button", name="Edit details").click()
assert page.locator("[data-description-fields]").is_visible()
page.get_by_role("button", name="Cancel", exact=True).click()
assert page.locator("[data-description-fields]").is_hidden()
```

Keep the existing save/card-swap, mention keyboard selection, comment HTMX swap, local-time, centered modal, desktop comments-to-the-right, and mobile comments-below assertions. Save non-repository artifacts to `/tmp/kanbanflow-hand-drawn-smoke/board-1440x900.png`, `modal-1440x900.png`, and `modal-390x844.png` and print the directory. Close the desktop modal with `page.keyboard.press("Escape")`, assert focus returns to the refreshed ticket button, then reopen it and retain the explicit Close-button check. At `390x844`, assert the dialog box is exactly full-screen and the board remains horizontally scrollable. Finally call `page.emulate_media(reduced_motion="reduce")` and assert every `.ticket-card` has computed `transform == "none"`.

Run:

```bash
/opt/homebrew/opt/python@3.11/bin/python3.11 tests/playwright_ticket_drawer_focus.py
```

Expected: PASS at 1440x900 and 390x844, with locally loaded fonts, centered desktop 60/40 modal, full-screen stacked mobile modal, visible keyboard focus, Escape and explicit Close behavior, focus return, description edit/cancel, mention/comment swap, no card rotation under reduced motion, no clipped/wrapped content, and the three screenshots available for inspection.

Inspect the captures for the 24px dot scale, restrained single-depth pencil shadows, title hierarchy, four-lane scroll, post-it tape, dashed pane divider, empty state, error/saved/development/comment states, and absence of theme styling on sidebar/new-ticket/sprint dialogs. Use Chromium's Network panel with a `font` filter and confirm the only font requests are `/static/fonts/Kalam-Bold.woff2` and `/static/fonts/PatrickHand-Regular.woff2`; there are no Google Fonts requests.

- [ ] **Step 7: Record the verified board-only exception in the interface system**

Only after Step 6 passes, append this section to `.interface-design/system.md`:

```markdown
### Board-only restrained hand-drawn exception

- Scope the warm paper treatment only with `.sketch-board` on the active sprint board and `.sketch-ticket-detail` on its opened native dialog. The global calm system remains the fallback and removing those classes is the complete visual rollback.
- Use `#fdfbf7` paper, `#2d2d2d` pencil, `#e5e0d8` erased structure, `#2d5da1` action/focus ink, `#ff4d4d` correction borders for error/destructive/failed states, and `#fff9c4` only for active-filter context and modal tape.
- Use one 24px dot-grid layer, 2px pencil borders, the shared asymmetric control/card/panel radii, and one 3px by 3px zero-blur pencil shadow. Do not add soft shadows or decorative textures inside this exception.
- Use local Kalam 700 for the 32px board title, 24px modal title, 20px section headings, 18px sprint title, and 16px lane headings. Use local Patrick Hand 400 for 18px prose/card titles/inputs and 17px actions; preserve system sans for dense metadata and monospace for references/branch commands.
- Rotate only board ticket cards with the fixed `-0.25deg`, `0.35deg`, `-0.15deg`, `0.2deg` cycle. Remove rotation at 767px and under reduced motion.
- Keep the native detail dialog centered at a 1120px maximum and 60/40 desktop split; use full-screen stacked panes at 767px. Preserve 44px targets, 3px blue focus outlines, native dialog/focus behavior, and horizontal four-lane scrolling.
```

- [ ] **Step 8: Run final named checks, scope audit, Graphify update, and commit Task 3**

Run only the named UI tests; do not run all of pytest:

```bash
uv run pytest -q \
  tests/test_web_shell.py::test_sketch_fonts_and_tokens_are_local_served_artifacts \
  tests/test_web_board.py::test_board_enables_the_sketch_scope_without_theming_nested_dialogs \
  tests/test_web_board.py::test_board_sketch_css_is_scoped_deterministic_and_responsive \
  tests/test_web_board.py::test_board_ticket_detail_fragment_uses_a_centered_native_dialog \
  tests/test_web_board.py::test_board_accessibility_uses_labeled_filters_and_human_status_text \
  tests/test_web_ui_refresh.py::test_ticket_detail_partial_is_an_accessible_centered_modal \
  tests/test_web_ui_refresh.py::test_remaining_workspace_views_collapse_to_one_column_on_mobile \
  tests/test_web_ticket_detail.py::test_detail_fragment_renders_sanitized_markdown_and_project_members \
  tests/test_web_ticket_comments.py::test_ticket_detail_includes_the_comment_composer_and_project_members \
  tests/test_web_ticket_comments.py::test_comment_permissions_render_accessible_icon_actions \
  tests/test_web_ticket_development.py::test_ticket_detail_renders_safe_active_development_for_every_reader_and_mode
/opt/homebrew/opt/python@3.11/bin/python3.11 tests/playwright_ticket_drawer_focus.py
uv run ruff check tests/test_web_shell.py tests/test_web_board.py tests/test_web_ui_refresh.py tests/test_web_ticket_detail.py tests/test_web_ticket_development.py tests/playwright_ticket_drawer_focus.py
git diff --check
git diff --exit-code -- app/routers app/models.py app/services.py app/static/app.js app/templates/base.html app/templates/partials/ticket_card.html app/templates/partials/ticket_comments.html app/templates/partials/ticket_modal.html
graphify update .
git status --short
```

Expected acceptance assertions:

- Board and board-opened dialog have exactly their two scope hooks; standalone detail, new-ticket/sprint dialogs, sidebar, auth, dashboard, settings, backlog, and history do not.
- All theme declarations are under `.sketch-board`/`.sketch-ticket-detail`; `:root` and the named out-of-scope files are unchanged. Removing the hooks reveals the existing global system with no route or behavior rollback.
- Font requests are local WOFF2 only, match the recorded checksums, use `font-display: swap`, keep their two OFL texts and attribution, and remain usable through cursive fallbacks.
- Cards alone use the fixed four-angle cycle within ±0.35deg; hover/active do not increase it, and mobile/reduced-motion compute to no rotation.
- Blue marks action/focus/link states, red is paired with a non-color cue only for error/destructive/failed states, yellow is limited to filter/tape context, and every label/status remains text.
- Native dialog focus, Escape, explicit Close, opener focus return, description edit/view/cancel/save, local time, copy announcements, comments/mentions, safe development links, and HTMX swaps behave unchanged.
- Desktop is centered at 60/40; mobile is 390x844 full-screen with comments below details. Four lanes retain keyboard-focusable horizontal scrolling, 44px targets do not overlap, and long text wraps without page-level horizontal overflow.

Then commit exactly the Task 3 files:

```bash
git add app/templates/partials/ticket_detail.html app/static/app.css .interface-design/system.md \
  tests/test_web_board.py tests/test_web_ui_refresh.py tests/test_web_ticket_detail.py tests/test_web_ticket_development.py \
  tests/playwright_ticket_drawer_focus.py
git commit -m "feat: theme board ticket detail"
```
