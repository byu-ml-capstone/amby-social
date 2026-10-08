import re
from datetime import date
from pathlib import Path

from sqlmodel import Session

from app.models import Sprint, SprintStatus


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
        "--surface: #ffffff;",
        "--focus: var(--sketch-blue-ink);",
    ):
        assert declaration in root

    global_theme = css.split("/* Shared sketch system.", 1)[1].split(
        ".sketch-board,\n.sketch-ticket-detail {", 1
    )[0]
    for declaration in (
        "background: var(--sketch-paper);",
        "border: var(--sketch-border);",
        "border-radius: var(--sketch-radius-panel);",
        "font-family: var(--sketch-font-heading);",
        "font-family: var(--sketch-font-hand);",
        "color: var(--sketch-blue-ink);",
        "box-shadow: var(--sketch-shadow);",
    ):
        assert declaration in global_theme
    assert ".ticket-detail-header h2" in global_theme
    assert ".ticket-development h3" in global_theme
    assert ".ticket-comments-header h3" in global_theme
    assert ".integration-card .integration-status" in global_theme
    assert "font-family: ui-sans-serif, system-ui, sans-serif;" in global_theme

    controls = global_theme.split(".app-primary-button, .app-secondary-button", 1)[1]
    assert "min-height: 44px;" in controls
    for selector in (
        ".app-projects-link, .app-project-link, .app-sign-out",
        ".project-tab, .project-settings-link",
        ".integration-card input, .integration-card button",
        ".ticket-copy-row .app-ghost-button",
        ".ticket-mentions summary, .ticket-mention-menu button, .ticket-mentions label",
        ".github-repository-option, .board-filter-check",
    ):
        rule = global_theme.split(f"{selector} {{", 1)[1].split("}", 1)[0]
        assert "min-height: 44px;" in rule

    danger_hover = global_theme.split(".app-danger-button:hover {", 1)[1].split("}", 1)[0]
    for declaration in (
        "background: color-mix(in srgb, var(--sketch-correction) 20%, var(--sketch-paper));",
        "border-color: var(--sketch-correction);",
        "color: var(--sketch-pencil);",
        "text-decoration: underline;",
    ):
        assert declaration in danger_hover

    mention_menu = global_theme.split(".ticket-mention-menu {", 1)[1].split("}", 1)[0]
    assert "border: var(--sketch-border);" in mention_menu
    assert "border-radius: var(--sketch-radius-control);" in mention_menu
    assert "box-shadow: var(--sketch-shadow);" in mention_menu

    global_focus = css.split(":focus-visible {", 1)[1].split("}", 1)[0]
    assert "outline: 3px solid var(--sketch-blue-ink);" in global_focus
    assert "outline-offset: 2px;" in global_focus
    mobile = css.split("@media (max-width: 767px)", 1)[1]
    assert ".app-mobile-header { border-bottom: 2px solid var(--sketch-pencil); }" in mobile
    drawer = mobile.rsplit(".app-sidebar.is-open {", 1)[1].split("}", 1)[0]
    assert "box-shadow: var(--sketch-shadow);" in drawer
    assert ".sketch-ticket-detail::before" in css
    assert ".sketch-board .ticket-card:nth-child(4n + 2)" in css

    system = Path(".interface-design/system.md").read_text()
    assert "warm paper canvas and white surfaces" in system
    assert "3px hard shadow" in system
    assert "asymmetric control/card/panel radii" in system
    assert "44px minimum" in system


def test_project_shell_marks_the_open_project(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog")

    assert page.status_code == 200
    assert f'href="/projects/{project.slug}" class="app-project-link is-active"' in page.text


def test_project_navigation_keeps_settings_as_a_secondary_utility(
    client, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog")
    navigation = re.search(
        r'<nav class="project-tabs".*?</nav>', page.text, re.DOTALL
    ).group()

    assert f'/projects/{project.slug}/settings' not in navigation
    assert (
        f'<a class="project-settings-link" href="/projects/{project.slug}/settings">Settings</a>'
        in page.text
    )


def test_sprint_selector_contains_only_sprint_destinations(
    client, engine, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        session.add(
            Sprint(
                project_id=project.id,
                name="Sprint 1",
                goal="Ship it",
                status=SprintStatus.PLANNING,
                start_date=date(2026, 9, 21),
                end_date=date(2026, 9, 28),
            )
        )
        session.commit()
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog")
    selector = re.search(r'<div class="sprint-selector">.*?</div>', page.text, re.DOTALL).group()

    assert "Backlog" not in selector
    assert "Sprint history" not in selector
    assert "Settings" not in selector


def test_sprint_selector_is_absent_without_sprints(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog")

    assert 'class="sprint-selector"' not in page.text


def test_settings_link_has_current_location_state(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/settings")

    assert (
        f'<a class="project-settings-link is-active" href="/projects/{project.slug}/settings" '
        'aria-current="page">Settings</a>'
    ) in page.text
    css = client.get("/static/app.css").text
    assert ".project-settings-link.is-active {" in css
    active_style = css.split(".project-settings-link.is-active {", 1)[1].split("}", 1)[0]

    assert "border-bottom: 2px solid var(--text)" in active_style
    assert "color: var(--text)" in active_style


def test_backlog_has_one_primary_owner_action_without_a_planning_sprint(
    client, make_user, make_project, login_as
):
    """Making both top-level backlog actions primary must make this fail."""
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog")
    header = re.search(r'<header class="project-header">.*?</header>', page.text, re.DOTALL).group()

    assert 'x-ref="ticketModalOpener"' not in header
    assert 'class="app-primary-button" type="button" x-ref="sprintFormOpener"' in header


def test_ticket_detail_partial_is_an_accessible_centered_modal():
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="app/templates")
    template = templates.get_template("partials/ticket_detail.html")
    context = dict(
        ticket=type("Ticket", (), {"ticket_number": 1})(),
        project=type("Project", (), {"slug": "project"})(),
        form_values={
            "title": "Ticket",
            "description": "",
            "type": "TASK",
            "priority": "MEDIUM",
            "story_points": "",
            "assignee_id": "",
            "status": "BACKLOG",
            "sprint_id": "",
            "resolution_notes": "",
        },
        ticket_types=[],
        priorities=[],
        columns=[],
        members=[],
        sprints=[],
        csrf_token="token",
        error=None,
    )
    dialog_source = template.render(**context, detail_drawer=True)
    page_source = template.render(**context, detail_drawer=False)

    assert (
        'class="app-dialog ticket-detail-modal sketch-ticket-detail"' in dialog_source
    )
    assert "$el.showModal()" in dialog_source
    assert "ticket-drawer-closed" in dialog_source
    assert 'class="ticket-detail-page"' in page_source
    assert "sketch-ticket-detail" not in page_source


def test_remaining_workspace_views_collapse_to_one_column_on_mobile(client):
    css = client.get("/static/app.css")

    assert css.status_code == 200
    desktop_css = css.text.split("@media (max-width: 767px)", 1)[0]
    mobile_css = css.text.split("@media (max-width: 767px)", 1)[1]
    assert (
        "grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr)"
        in desktop_css
    )
    assert ".ticket-detail-modal .ticket-comments { border-left:" in desktop_css
    assert ".sketch-ticket-detail {" in desktop_css
    assert "max-width: 1120px;" in desktop_css
    assert "grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr);" in desktop_css
    assert "border-left: 2px dashed var(--sketch-erased);" in desktop_css
    assert ".sketch-ticket-detail::before {" in desktop_css
    assert "height: 22px;" in desktop_css
    assert "width: 88px;" in desktop_css
    assert ".sketch-ticket-detail .ticket-comment {" in desktop_css
    assert ".sketch-ticket-detail .ticket-mention-menu {" in desktop_css
    assert ".sketch-ticket-detail :focus-visible {" in desktop_css
    focus_rule = desktop_css.split(
        ".sketch-ticket-detail :focus-visible {", 1
    )[1].split("}", 1)[0]
    assert "outline: 3px solid var(--sketch-blue-ink);" in focus_rule
    assert "outline-offset: 2px;" in focus_rule
    assert (
        ".sketch-ticket-detail .ticket-description-fields[hidden] { display: none; }"
        in desktop_css
    )
    assert ".ticket-detail-modal .ticket-detail-layout { display: block; }" in mobile_css
    assert ".ticket-detail-modal .ticket-comments { border-left: 0; border-top:" in mobile_css
    assert ".sketch-ticket-detail { border-width: 0;" in mobile_css
    assert ".sketch-ticket-detail::before { display: none; }" in mobile_css
    assert "border-left: 0;" in mobile_css
    assert "border-top: 2px dashed var(--sketch-erased);" in mobile_css
    assert ".sprint-history-row" in mobile_css
    assert ".settings-member" in mobile_css
    assert ".auth-card" in mobile_css


def test_project_board_shares_chrome_without_leaking_board_effects(client):
    css = client.get("/static/app.css").text
    project_board = css.split(".project-board {", 1)[1].split("}", 1)[0]
    mobile_css = css.split("@media (max-width: 767px)", 1)[1]

    assert "padding: var(--space-6);" in project_board
    assert ".project-board { padding: var(--space-4); }" in mobile_css
    for selector in (
        ".project-board > .project-header .project-title",
        ".project-board > .project-header .sprint-selector",
        ".project-board > .project-navigation .project-tabs",
        ".project-board > .project-navigation .project-tab",
        ".project-board > .project-navigation .project-settings-link",
    ):
        assert selector in css

    assert ".sketch-board {\n  background-color: var(--sketch-paper);" in css
    assert (
        "background-image: radial-gradient(circle, var(--sketch-erased) 1px, transparent 1px);"
        in css
    )
    assert ".sketch-board .ticket-card:nth-child(4n + 1)" in css
    assert ".project-board .ticket-card:nth-child" not in css


def test_app_assets_are_versioned_by_content_so_deploys_bust_the_browser_cache(client):
    page = client.get("/login").text
    for asset in ("app.js", "app.css"):
        version = re.search(rf'/static/{asset}\?v=(\w+)"', page)
        assert version, f"{asset} is not versioned"
        assert client.get(f"/static/{asset}?v={version[1]}").status_code == 200
