from pathlib import Path


def test_signed_in_shell_has_project_sidebar(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get("/dashboard")

    assert 'data-testid="app-sidebar"' in page.text
    assert f'href="/projects/{project.slug}"' in page.text
    assert 'href="/static/app.css?v=' in page.text
    assert 'href="#main-content">Skip to main content</a>' in page.text


def test_app_stylesheet_is_served(client):
    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert "--canvas: var(--sketch-paper);" in response.text


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
    assert "--sketch-paper: #fdfbf7;" in root_tokens
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


def test_mobile_drawer_is_inert_only_while_closed(client, make_user, login_as):
    owner = make_user(email="ada@example.com")
    login_as(owner.email)

    page = client.get("/dashboard")

    assert "isMobile: window.matchMedia" in page.text
    assert ':inert="isMobile && !navOpen"' in page.text
    assert ':aria-hidden="(isMobile && !navOpen).toString()"' in page.text
    assert '@keydown.escape.window="if (isMobile && navOpen) closeNav()"' in page.text
    assert "navOpener = $el" in page.text


def test_backdrop_hides_and_drawer_closes_when_resized_to_desktop(client, make_user, login_as):
    owner = make_user(email="ada@example.com")
    login_as(owner.email)

    page = client.get("/dashboard")

    assert 'x-show="navOpen && isMobile"' in page.text
    assert "if (!event.matches) navOpen = false" in page.text


def test_mobile_drawer_has_a_complete_focus_lifecycle(client, make_user, login_as):
    """Removing focus entry, containment, return, or background inerting must fail."""
    owner = make_user(email="ada@example.com")
    login_as(owner.email)

    page = client.get("/dashboard")

    assert 'x-ref="mobileNav"' in page.text
    assert "navOpener = $el" in page.text
    assert "$refs.mobileNav.querySelector('a, button')?.focus()" in page.text
    assert '@keydown.tab="if (isMobile && navOpen) trapNav($event)"' in page.text
    assert "navOpener?.focus()" in page.text
    assert ':inert="isMobile && navOpen"' in page.text


def test_shell_uses_local_semantic_css_without_tailwind_runtime():
    source = Path("app/templates/base.html").read_text()
    stylesheet = Path("app/static/app.css").read_text()

    assert "cdn.tailwindcss.com" not in source
    assert ".sr-only" in stylesheet
    assert "--success:" not in stylesheet
    assert "--warning:" not in stylesheet
    assert "--radius-lg:" not in stylesheet


def test_dashboard_has_no_tailwind_utility_contracts():
    source = Path("app/templates/dashboard.html").read_text()

    for utility in (
        "max-w-3xl",
        "mx-auto",
        "space-y-6",
        "items-center",
        "ml-auto",
        "overflow-hidden",
        "gap-3",
        "px-4",
        "py-3",
        "text-slate-500",
        "justify-end",
    ):
        assert utility not in source


def test_dialogs_have_names_escape_handling_and_focus_targets():
    dashboard = Path("app/templates/dashboard.html").read_text()
    board = Path("app/templates/board.html").read_text()
    backlog = Path("app/templates/backlog.html").read_text()
    ticket_modal = Path("app/templates/partials/ticket_modal.html").read_text()
    close_dialog = Path("app/templates/partials/sprint_close.html").read_text()

    assert 'role="dialog" aria-modal="true" aria-labelledby="project-dialog-heading"' in dashboard
    assert "$refs.projectName.focus()" in dashboard
    assert "$refs.projectDialogOpener.focus()" in dashboard
    assert '<dialog id="ticket-modal"' in ticket_modal
    assert 'role="dialog" aria-modal="true" aria-labelledby="ticket-modal-heading"' in ticket_modal
    assert "x-data=" not in ticket_modal
    assert '@cancel="$event.preventDefault(); $el.close()"' in ticket_modal
    assert 'x-ref="ticketTitle"' in ticket_modal
    assert "$refs.ticketModalOpener.focus()" in ticket_modal
    for page in (board, backlog):
        assert "x-data=\"{ ticketError: ''," in page
        assert 'x-ref="ticketModalOpener"' in page
        assert "$refs.ticketModal.showModal()" in page
        assert "$refs.ticketTitle.focus()" in page
    assert "@htmx:after-request.camel=" in ticket_modal
    assert "$event.detail.xhr.status >= 200 && $event.detail.xhr.status < 300" in ticket_modal
    assert "$event.detail.successful" not in ticket_modal
    assert "ticketError = $event.detail.xhr.responseText" in ticket_modal
    assert "ticketError = ''; $el.reset()" in ticket_modal
    assert '@cancel="$event.preventDefault(); $el.close()"' in close_dialog
    assert "$el.querySelector('h2').focus()" in close_dialog
    assert "$refs.closeFormOpener.focus()" in close_dialog


def test_styles_respect_reduced_motion():
    stylesheet = Path("app/static/app.css").read_text()

    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert "transition-duration: 0.01ms !important" in stylesheet
