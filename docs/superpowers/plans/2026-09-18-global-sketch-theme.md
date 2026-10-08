# Global Sketch Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the approved restrained paper-and-pencil visual language to every existing product surface without changing behavior or disturbing the already-approved board and board-opened ticket dialog.

**Architecture:** Keep one CSS file and the existing semantic template classes. Promote the already self-hosted font families and `--sketch-*` values into the global token foundation, map the existing global component selectors to those values, and leave the board/dialog's scoped overrides in place so their dot grid, tape, deterministic rotations, and responsive detail layout remain exactly as they are. No template hook is needed: `.app-shell`, `.auth-*`, `.app-*`, `.project-*`, backlog/history/settings classes, and native dialog/form classes already partition every requested surface.

**Tech Stack:** FastAPI, Jinja, semantic HTML, one vanilla CSS file, existing HTMX/Alpine, native `<dialog>`, pytest/TestClient, Chromium

**Spec:** `docs/superpowers/specs/2026-09-17-restrained-hand-drawn-board-theme-design.md` (approved board/ticket-detail behavior to preserve)

## Global Constraints

- Do not change routes, models, services, migrations, data, authorization, labels, form submission, HTMX targets/swaps, Alpine state, `app/static/app.js`, dependencies, framework, or build tooling.
- Reuse `Kalam` 700, `Patrick Hand` 400, checked-in WOFF2 files, and the exact existing `--sketch-*` values; make no runtime font request.
- Apply warm paper, pencil borders, asymmetric radii, one 3px hard pencil shadow, blue ink action/focus/link treatment, erased-gray structure, and correction-red non-color cues consistently. Do not add soft shadows, texture beyond the existing board dot grid, animation, dark mode, theme switching, icon packages, or JavaScript.
- Preserve the board-only dot grid, card-only fixed rotation sequence, horizontal four-lane scroll, ticket-detail `<dialog>` class, 60/40 desktop layout, full-screen mobile layout, tape, native focus/Escape/return-focus behavior, and standalone ticket-detail structure.
- Keep meaningful status, error, and destructive text visible; pair correction red with a border, underline, icon, or pale field. Keep controls at least 44px high and use a 3px blue `:focus-visible` outline with 2px offset.
- At `max-width: 767px`, retain the current mobile drawer and dialog behavior, use 16px content gutters, stack/wrap controls without reordering, and prevent page-level horizontal overflow. Preserve the existing reduced-motion rule.
- Use only focused selector/markup contract tests and desktop/mobile Chromium smoke checks. Do not run the full suite unless a focused failure indicates a regression.

## File Map

- Modify `app/static/app.css`: promote existing sketch tokens to `:root`, remap the global shell and shared component system, and add responsive global overrides before the existing scoped board/dialog rules.
- Modify `tests/test_web_shell.py`: update the served stylesheet contract from the calm palette to global sketch tokens and prove fonts remain local.
- Modify `tests/test_web_ui_refresh.py`: add one served-CSS contract covering shell, auth, shared controls/dialogs/forms/rows/empty states, project non-board surfaces, responsive rules, and preserved scoped detail selectors.
- Modify `tests/test_web_board.py`: change the obsolete assertion that prohibits sketch tokens at `:root`; keep assertions that board behavior is scoped and new-ticket/sprint dialogs retain their existing markup/behavior.
- Modify `.interface-design/system.md`: replace the board-only exception with the confirmed global foundation while explicitly retaining board-only dot-grid/card-angle/tape treatments.

---

### Task 1: Promote the approved sketch system through the existing global CSS seams

**Files:**

- Modify: `app/static/app.css:15-892`
- Modify: `tests/test_web_shell.py:17-71`
- Modify: `tests/test_web_ui_refresh.py:120-190`
- Modify: `tests/test_web_board.py:131-148`
- Modify: `.interface-design/system.md:5-40`

**Interfaces:**

- Consumes: the current `:root` variables and global selectors; checked-in `/static/fonts/Kalam-Bold.woff2` and `/static/fonts/PatrickHand-Regular.woff2`; the existing `.sketch-board` and `.sketch-ticket-detail` override blocks.
- Produces: a global sketch visual foundation consumed by existing semantic classes only. The board continues to consume its own identical scoped values and the dialog retains `class="app-dialog ticket-detail-modal sketch-ticket-detail"` only on its native-dialog branch.

- [ ] **Step 1: Add one focused failing global-theme contract test**

  Add this test to `tests/test_web_ui_refresh.py`. It proves the public CSS contract and prevents a broad rewrite from leaking into the board detail behavior:

  ```python
  def test_global_sketch_theme_covers_shared_and_non_board_product_surfaces(client):
      css = client.get("/static/app.css").text
      root = css.split(":root {", 1)[1].split("}", 1)[0]

      for declaration in (
          "--sketch-paper: #fdfbf7;",
          "--sketch-pencil: #2d2d2d;",
          "--sketch-erased: #e5e0d8;",
          "--sketch-correction: #ff4d4d;",
          "--sketch-blue-ink: #2d5da1;",
          "--sketch-post-it: #fff9c4;",
          "--canvas: var(--sketch-paper);",
          "--surface: var(--sketch-paper);",
          "--focus: var(--sketch-blue-ink);",
      ):
          assert declaration in root

      for selector in (
          ".app-shell", ".app-sidebar", ".app-mobile-header", ".auth-header",
          ".auth-card", ".app-surface, .surface", ".app-row", ".app-empty-state",
          ".app-dialog", ".app-form", ".app-input", ".app-primary-button",
          ".app-secondary-button", ".app-ghost-button", ".app-danger-button",
          ".backlog-planning-sprint", ".backlog-list", ".sprint-history-row",
          ".settings-section", ".integration-card", ".settings-member",
      ):
          assert selector in css

      global_focus = css.split(":focus-visible {", 1)[1].split("}", 1)[0]
      assert "outline: 3px solid var(--sketch-blue-ink);" in global_focus
      assert "outline-offset: 2px;" in global_focus
      assert "@media (max-width: 767px)" in css
      assert ".sketch-ticket-detail::before" in css
      assert ".sketch-board .ticket-card:nth-child(4n + 2)" in css
  ```

  In `tests/test_web_shell.py`, change the current `--canvas: #f7f7f5` assertion to `--canvas: var(--sketch-paper);`. In `tests/test_web_board.py`, replace `assert "--sketch-" not in root_tokens` with `assert "--sketch-paper: #fdfbf7;" in root_tokens`; leave the existing selector and native-dialog non-leakage assertions unchanged.

- [ ] **Step 2: Run the focused tests to prove RED**

  Run:

  ```bash
  uv run pytest -q tests/test_web_shell.py tests/test_web_ui_refresh.py tests/test_web_board.py
  ```

  Expected: the new global-theme contract fails because sketch tokens remain scoped and global selectors still use the calm palette/40px controls.

- [ ] **Step 3: Apply the smallest CSS-only global cascade layer**

  In the existing `:root` block, define the same existing `--sketch-*` declarations, then map global variables to them. Preserve the board/dialog scope block (it may retain the same declarations for explicit rollback and top-layer dialog ownership).

  ```css
  :root {
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
    --canvas: var(--sketch-paper);
    --surface: var(--sketch-paper);
    --surface-inset: var(--sketch-erased);
    --text: var(--sketch-pencil);
    --text-secondary: var(--sketch-pencil);
    --muted: #625f59;
    --line: var(--sketch-erased);
    --line-strong: var(--sketch-pencil);
    --focus: var(--sketch-blue-ink);
    --control: var(--sketch-erased);
    --danger: var(--sketch-correction);
  }
  ```

  Add global overrides adjacent to the existing shared components, ahead of the `.sketch-board` block so the current scoped rules win unchanged. Use existing selectors only: give shell/sidebar/mobile/auth containers paper plus pencil separators; Kalam headings; Patrick Hand buttons, links, input/textarea prose, section headings, rows/cards; 2px pencil borders and shared asymmetric radii for surfaces/dialogs/controls; and 44px minimum targets. Use a single `var(--sketch-shadow)` only for native dialogs, not list rows, panels, or cards.

  Keep system sans for labels, metadata, counts, select/options, and monospace for `code`. Make primary buttons blue with paper text; secondary buttons paper/pencil; ghosts unboxed until hover; disabled controls erased/pencil; and danger buttons pale red with a red border plus pencil text. Make `.form-error` and destructive settings surfaces pale red with a red border/underline while retaining pencil text. Set links, active tabs, and focus to blue ink. Add no rotations, tape, or dot grid outside the existing board/dialog selectors.

  Use the existing selectors for non-board pages: `.auth-card`, `.app-page`, `.app-row`, `.app-empty-state`, `.backlog-planning-sprint`, `.backlog-list`, `.backlog-ticket`, `.sprint-history*`, `.settings-*`, `.integration-*`, and `.project-navigation`. At the current 767px media query, give global main/auth content 16px side padding; let header/project actions, filters, settings member controls, integration forms, backlog selection, and sprint metrics wrap/stack through their current flex/grid selectors; retain the existing drawer transform and dialog mobile rules. Do not edit any template because these hooks already exist.

- [ ] **Step 4: Update the interface-system record after the CSS contract is in place**

  Replace the `Board-only restrained hand-drawn exception` section in `.interface-design/system.md` with a concise global rule: global shell, auth, dashboard, backlog, history, settings, common forms/dialogs/tables/rows/empty states use the shared paper/pencil tokens and local fonts; board dot grid, card rotation, and ticket-detail tape remain limited to their current scoped classes. Retain the existing native dialog and 767px responsive requirements verbatim where they describe behavior.

- [ ] **Step 5: Run focused automated verification**

  Run:

  ```bash
  uv run pytest -q tests/test_web_shell.py tests/test_web_auth.py tests/test_web_ui_refresh.py tests/test_web_board.py tests/test_web_ticket_create.py tests/test_web_sprint_history.py tests/test_web_project_settings.py
  uv run ruff check app tests
  uv run ruff format --check app tests
  git diff --check
  ```

  Expected: PASS. The focused web flows still render unchanged semantics while their shared CSS contract reflects the global theme.

- [ ] **Step 6: Perform proportional Chromium smoke checks**

  Start the existing app with `uv run uvicorn app.main:app --reload --workers 1`, then use Chromium at 1440×900 and 390×844. Register/sign in and inspect dashboard (including new-project dialog), backlog (new-ticket and sprint dialogs), sprint history/detail, settings/integrations/member/danger forms, and standalone ticket detail. Confirm warm paper, 2px pencil structure, handwriting hierarchy only where specified, blue focus/action/link ink, readable error/destructive cues, wrapping, 44px targets, local fonts, and no horizontal page overflow.

  On the active board, open a card with the keyboard; verify dot grid, fixed card angles only on desktop, horizontal four-lane scrolling, centered 60/40 dialog, and focus return after Escape are unchanged. At 390×844, verify mobile drawer, 16px gutters, wrapped actions, full-screen stacked detail dialog, no card rotation, and all dialogs/forms remain usable. Repeat the board check with `prefers-reduced-motion: reduce`; angles remain absent and no added transition is visible.

- [ ] **Step 7: Refresh the graph and commit the one reviewable change**

  Run:

  ```bash
  graphify update .
  git add app/static/app.css tests/test_web_shell.py tests/test_web_ui_refresh.py tests/test_web_board.py .interface-design/system.md graphify-out
  git commit -m "feat: extend sketch theme globally"
  ```

  Expected: one commit containing the global CSS/system/test contract only; no route, template, JavaScript, dependency, or font-file changes.

## Self-Review

- Spec preservation: the only approved scoped behaviors—board grid/angles and board-opened ticket dialog tape/layout—are explicitly retained; standalone ticket detail remains unscoped.
- Surface coverage: shell/sidebar/mobile header, auth, dashboard, backlog, history/detail, settings/integrations, dialogs, forms, rows, tables/metrics, and empty states all map to existing selectors in Task 1.
- YAGNI: no markup class, component, framework, dependency, runtime font service, or JavaScript change is proposed because all necessary semantic hooks and local fonts already exist.
- Verification: focused HTTP/CSS contracts plus desktop/mobile/reduced-motion browser smoke cover styling and preserved native dialog workflows without a full suite.
