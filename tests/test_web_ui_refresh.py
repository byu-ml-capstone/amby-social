import re
from datetime import date
from pathlib import Path

from sqlmodel import Session

from app.models import Sprint, SprintStatus


def test_global_waybill_theme_tokens_targets_and_focus(client):
    css = client.get("/static/app.css").text
    root = css.split(":root {", 1)[1].split("}", 1)[0]

    for declaration in (
        "--canvas: #ffffff;",
        "--text: #15171c;",
        "--accent: #c2410c;",
        "--signal: #d9531e;",
        "--done: #1a7f4b;",
        "--danger: #c62828;",
        "--nav: #15171c;",
        "--focus: var(--signal);",
    ):
        assert declaration in root

    buttons = css.split(
        ".app-primary-button, .app-secondary-button, .app-ghost-button, .app-danger-button {", 1
    )[1].split("}", 1)[0]
    assert "min-height: 44px;" in buttons
    primary = css.split(".app-primary-button {", 1)[1].split("}", 1)[0]
    assert "background: var(--accent);" in primary
    fields = css.split(".board-filters select, .sprint-selector select {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid var(--control-border);" in fields
    assert "background: var(--surface);" in fields
    global_focus = css.split(":focus-visible {", 1)[1].split("}", 1)[0]
    assert "outline: 3px solid var(--focus);" in global_focus
    assert "box-shadow: 3px 3px 0" not in css

    system = Path(".interface-design/system.md").read_text()
    assert "waybill" in system
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
    navigation = re.search(r'<nav class="project-tabs".*?</nav>', page.text, re.DOTALL).group()

    assert f"/projects/{project.slug}/settings" not in navigation
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


def test_new_project_sprint_selector_contains_initial_sprint(
    client, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog")

    assert 'class="sprint-selector"' in page.text
    assert "Sprint 01" in page.text


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

    assert "border-bottom-color: var(--text)" in active_style
    assert "color: var(--text)" in active_style


def test_backlog_has_one_primary_owner_action_with_initial_planning_sprint(
    client, make_user, make_project, login_as
):
    """Making both top-level backlog actions primary must make this fail."""
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog")
    header = re.search(r'<header class="project-header">.*?</header>', page.text, re.DOTALL).group()

    assert 'class="app-secondary-button" type="button" x-ref="ticketModalOpener"' in header
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

    assert 'class="app-dialog ticket-detail-modal"' in dialog_source
    assert "$el.showModal()" in dialog_source
    assert "ticket-drawer-closed" in dialog_source
    assert 'class="ticket-detail-page"' in page_source
    assert "ticket-detail-modal" not in page_source


def test_remaining_workspace_views_collapse_to_one_column_on_mobile(client):
    css = client.get("/static/app.css")

    assert css.status_code == 200
    desktop_css = css.text.split("@media (max-width: 767px)", 1)[0]
    mobile_css = css.text.split("@media (max-width: 767px)", 1)[1]
    assert "grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr)" in desktop_css
    assert ".ticket-detail-modal .ticket-comments { border-left:" in desktop_css
    assert "max-width: 1120px;" in desktop_css
    assert ".ticket-detail-modal .ticket-detail-layout { display: block; }" in mobile_css
    assert ".ticket-detail-modal .ticket-comments { border-left: 0; border-top:" in mobile_css
    assert ".sprint-history-row" in mobile_css
    assert ".settings-member" in mobile_css
    assert ".auth-card" in mobile_css


def test_project_board_shares_chrome_without_leaking_board_effects(client):
    css = client.get("/static/app.css").text
    project_board = css.split(".project-board {", 1)[1].split("}", 1)[0]
    mobile_css = css.split("@media (max-width: 767px)", 1)[1]

    assert "padding: var(--space-6);" in project_board
    assert ".project-board { padding: var(--space-4); }" in mobile_css
    assert "radial-gradient" not in css
    # Subtask disclosure arrows and the companion animation intentionally rotate;
    # the board chrome itself should not introduce decorative rotation.
    assert "rotate(" not in project_board
    assert ".ticket-card:nth-child" not in css


def test_app_assets_are_versioned_by_content_so_deploys_bust_the_browser_cache(client):
    page = client.get("/login").text
    for asset in ("app.js", "app.css"):
        version = re.search(rf'/static/{asset}\?v=(\w+)"', page)
        assert version, f"{asset} is not versioned"
        assert client.get(f"/static/{asset}?v={version[1]}").status_code == 200
