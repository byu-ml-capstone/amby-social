from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.models import Priority, Sprint, SprintStatus, Ticket, TicketStatus, TicketType


@pytest.fixture
def active_sprint_world(make_user, make_project, engine):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        sprint = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship checkout",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        session.add(sprint)
        session.commit()
        session.refresh(sprint)
    return SimpleNamespace(owner=owner, project=project, sprint=sprint)


def test_board_renders_four_fixed_columns(client, active_sprint_world, login_as):
    login_as(active_sprint_world.owner.email)
    page = client.get(f"/projects/{active_sprint_world.project.slug}").text
    for column in ["BACKLOG", "SELECTED", "IN_PROGRESS", "DONE"]:
        assert f'id="column-{column}"' in page


def test_board_shows_tickets_in_their_columns(client, active_sprint_world, login_as):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    login_as(owner.email)
    created = client.post(
        "/api/v1/tickets",
        json={
            "slug": project.slug,
            "title": "Card declines",
            "sprint_id": active_sprint_world.sprint.id,
        },
    ).json()
    client.patch(f"/api/v1/tickets/{created['id']}/status", json={"status": "IN_PROGRESS"})
    page = client.get(f"/projects/{project.slug}").text
    assert "Card declines" in page
    assert f'id="ticket-{created["id"]}"' in page


def test_board_keeps_markdown_descriptions_out_of_compact_cards(
    client, active_sprint_world, login_as
):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    login_as(owner.email)
    client.post(
        "/api/v1/tickets",
        json={
            "slug": project.slug,
            "title": "XSS attempt",
            "sprint_id": active_sprint_world.sprint.id,
            "description": "<script>alert('xss')</script> and <img src=x onerror=\"alert(1)\">",
        },
    )
    page = client.get(f"/projects/{project.slug}").text
    # The panel owns descriptions. A compact board card must not carry the
    # rendered body, whether the original markdown is benign or hostile.
    card = page.split('<article id="ticket-', 1)[1].split("</article>", 1)[0]
    assert "<script>" not in card
    assert "<img" not in card
    assert "alert" not in card


def test_board_marks_only_open_past_due_tickets_overdue(
    client, active_sprint_world, engine, login_as
):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    with Session(engine) as session:
        session.add_all(
            [
                Ticket(
                    ticket_number=1,
                    project_id=project.id,
                    title="Open overdue",
                    status=TicketStatus.BACKLOG,
                    sprint_id=active_sprint_world.sprint.id,
                    creator_id=owner.id,
                    due_date=date(2026, 9, 17),
                ),
                Ticket(
                    ticket_number=2,
                    project_id=project.id,
                    title="Done overdue",
                    status=TicketStatus.DONE,
                    sprint_id=active_sprint_world.sprint.id,
                    creator_id=owner.id,
                    due_date=date(2026, 9, 17),
                ),
            ]
        )
        session.commit()
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}").text
    open_card = page.split("Open overdue", 1)[1].split("</article>", 1)[0]
    done_card = page.split("Done overdue", 1)[1].split("</article>", 1)[0]

    assert '<time datetime="2026-09-17" class="ticket-due-date is-overdue">' in open_card
    assert '<time datetime="2026-09-17" class="ticket-due-date">' in done_card


def test_non_member_gets_404_for_the_board(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    assert client.get(f"/projects/{project.slug}").status_code == 404


def test_anonymous_visitor_is_redirected(client, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    response = client.get(f"/projects/{project.slug}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_empty_project_renders_four_empty_columns(client, active_sprint_world, login_as):
    project = active_sprint_world.project
    login_as(active_sprint_world.owner.email)
    response = client.get(f"/projects/{project.slug}")
    assert response.status_code == 200
    for column in ["BACKLOG", "SELECTED", "IN_PROGRESS", "DONE"]:
        assert f'id="column-{column}"' in response.text
        assert f'data-testid="column-count-{column}">0' in response.text
        assert f'data-testid="empty-lane-{column}"' in response.text


def test_active_sprint_summary_uses_current_ticket_metrics(
    client, active_sprint_world, engine, login_as
):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    with Session(engine) as session:
        sprint = session.get(Sprint, active_sprint_world.sprint.id)
        sprint.committed_points = 13
        session.add_all(
            [
                Ticket(
                    ticket_number=1,
                    project_id=project.id,
                    title="Completed estimate",
                    status=TicketStatus.DONE,
                    story_points=5,
                    sprint_id=sprint.id,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=2,
                    project_id=project.id,
                    title="Completed unestimated",
                    status=TicketStatus.DONE,
                    sprint_id=sprint.id,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=3,
                    project_id=project.id,
                    title="Open estimate",
                    story_points=3,
                    rollover_count=1,
                    sprint_id=sprint.id,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=4,
                    project_id=project.id,
                    title="Rollover at risk",
                    rollover_count=2,
                    sprint_id=sprint.id,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=5,
                    project_id=project.id,
                    title="Delay at risk",
                    delayed_days=14,
                    sprint_id=sprint.id,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=6,
                    project_id=project.id,
                    title="Blocked now",
                    blocked_reason="Waiting for security review",
                    sprint_id=sprint.id,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=7,
                    project_id=project.id,
                    title="Overdue now",
                    due_date=date(2020, 1, 1),
                    sprint_id=sprint.id,
                    creator_id=owner.id,
                ),
            ]
        )
        session.commit()

    login_as(owner.email)
    page = client.get(f"/projects/{project.slug}").text

    assert 'data-testid="sprint-summary"' in page
    assert "<dt>Completed</dt><dd>2 / 7 · 29%</dd>" in page
    assert "<dt>Points</dt><dd>5 / 8</dd>" in page
    assert "<dt>Rollover</dt><dd>2</dd>" in page
    assert "<dt>At risk</dt><dd>4</dd>" in page


def test_empty_active_sprint_summary_is_zero(client, active_sprint_world, login_as):
    login_as(active_sprint_world.owner.email)

    page = client.get(f"/projects/{active_sprint_world.project.slug}").text

    assert 'data-testid="sprint-summary"' in page
    assert "0 / 0 · 0%" in page


def test_board_uses_a_drawer_and_exposes_clear_filters(client, active_sprint_world, login_as):
    project = active_sprint_world.project
    login_as(active_sprint_world.owner.email)

    page = client.get(f"/projects/{project.slug}?mine=1").text

    assert 'id="ticket-detail-root" class="ticket-detail-root"' in page
    assert 'class="board-filter-summary"' in page
    assert f'href="/projects/{project.slug}">Clear filters</a>' in page


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
    assert "--sketch-paper: #fdfbf7;" in root_tokens
    for selector in (
        ".project-board > .project-header",
        ".project-board > .project-navigation",
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
        rule = css.split(f".sketch-board .ticket-card:nth-child(4n + {index}) {{", 1)[1].split(
            "}", 1
        )[0]
        assert f"transform: rotate({angle});" in rule
    assert ".sketch-board .ticket-card:hover {" in css
    hover_rule = css.split(".sketch-board .ticket-card:hover {", 1)[1].split("}", 1)[0]
    assert "border-color: var(--sketch-blue-ink);" in hover_rule
    assert "transform:" not in hover_rule

    mobile = css.split("@media (max-width: 767px)", 1)[1]
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".project-board { padding: var(--space-4); }" in mobile
    assert ".sketch-board .ticket-card { transform: none; }" in mobile
    assert ".sketch-board .ticket-card { transform: none; }" in reduced


def test_board_ticket_detail_fragment_uses_a_centered_native_dialog(
    client, active_sprint_world, login_as
):
    project = active_sprint_world.project
    login_as(active_sprint_world.owner.email)
    ticket = client.post(
        "/api/v1/tickets",
        json={
            "slug": project.slug,
            "title": "Open in dialog",
            "sprint_id": active_sprint_world.sprint.id,
        },
    ).json()

    fragment = client.get(
        f"/projects/{project.slug}/tickets/{ticket['ticket_number']}",
        headers={"HX-Request": "true"},
    )

    assert fragment.status_code == 200
    assert (
        '<dialog id="ticket-detail-panel" '
        'class="app-dialog ticket-detail-modal sketch-ticket-detail"' in fragment.text
    )
    assert 'aria-labelledby="ticket-detail-heading"' in fragment.text
    assert '@cancel="$event.preventDefault(); $el.close()"' in fragment.text
    assert "$el.querySelector('#ticket-detail-heading').focus()" in fragment.text
    assert 'class="ticket-detail-layout"' in fragment.text

    css = client.get("/static/app.css").text
    modal_css = css.split(".ticket-detail-modal {", 1)[1].split("}", 1)[0]
    assert "margin: auto" in modal_css
    assert "max-width: 1120px" in modal_css
    layout_css = css.split(".ticket-detail-modal .ticket-detail-layout {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr)" in layout_css
    comments_css = css.split(".ticket-detail-modal .ticket-comments {", 1)[1].split("}", 1)[0]
    assert "border-left: 1px solid var(--line)" in comments_css


def test_board_filter_checkbox_has_a_40px_hit_target():
    stylesheet = Path("app/static/app.css").read_text()

    assert ".board-filter-check" in stylesheet
    assert "min-height: 40px" in stylesheet.split(".board-filter-check", 1)[1].split("}", 1)[0]


def test_blank_select_filters_render_the_html_board(client, active_sprint_world, login_as):
    login_as(active_sprint_world.owner.email)

    response = client.get(
        f"/projects/{active_sprint_world.project.slug}",
        params={"assignee_id": "", "type": "", "priority": ""},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'class="project-board sketch-board"' in response.text


def test_board_exposes_native_drag_and_drop_contract():
    board = Path("app/templates/board.html").read_text()
    card = Path("app/templates/partials/ticket_card.html").read_text()
    script = Path("app/static/app.js").read_text()
    stylesheet = Path("app/static/app.css").read_text()

    assert 'data-drop-status="{{ column.value }}"' in board
    assert 'data-board-csrf="{{ csrf_token }}"' in board
    assert "data-board-drag-status" in board
    assert 'draggable="true"' in card
    assert "data-ticket-status-url" in card
    for event_name in ("dragstart", "dragend", "dragover", "dragleave", "drop"):
        assert f'addEventListener("{event_name}"' in script
    assert "fetch(" in script
    assert "window.location.reload();" in script
    assert ".is-dragging" in stylesheet
    assert ".is-drag-over" in stylesheet


def test_board_caps_each_column_and_shows_a_truncation_notice(
    client, engine, active_sprint_world, login_as
):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    with Session(engine) as session:
        session.add_all(
            [
                Ticket(
                    ticket_number=n,
                    project_id=project.id,
                    title=f"Ticket {n}",
                    type=TicketType.TASK,
                    status=TicketStatus.BACKLOG,
                    sprint_id=active_sprint_world.sprint.id,
                    creator_id=owner.id,
                )
                for n in range(1, 202)
            ]
        )
        session.commit()
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}").text

    assert page.count('<article id="ticket-') == 200
    assert "Showing the first 200 tickets." in page


def test_ticket_card_partial_renders_standalone():
    from fastapi.templating import Jinja2Templates

    from app.models import Priority, Ticket, TicketStatus, TicketType
    from app.rendering import render_markdown

    templates = Jinja2Templates(directory="app/templates")
    templates.env.filters["markdown"] = render_markdown

    class FakeProject:
        slug = "payment-gateway"

    ticket = Ticket(
        ticket_number=1,
        project_id="proj-1",
        title="Card declines",
        description="**bold**",
        type=TicketType.BUG,
        status=TicketStatus.BACKLOG,
        priority=Priority.HIGH,
        creator_id="user-1",
    )
    html = templates.get_template("partials/ticket_card.html").render(
        ticket=ticket,
        project=FakeProject(),
        columns=[
            TicketStatus.BACKLOG,
            TicketStatus.SELECTED,
            TicketStatus.IN_PROGRESS,
            TicketStatus.DONE,
        ],
        csrf_token="tok",
    )
    assert f'id="ticket-{ticket.id}"' in html
    assert "Card declines" in html
    assert "<strong>bold</strong>" not in html


def test_project_opens_active_sprint(client, active_sprint_world, login_as):
    login_as(active_sprint_world.owner.email)

    page = client.get(f"/projects/{active_sprint_world.project.slug}")

    assert page.status_code == 200
    assert active_sprint_world.sprint.name in page.text
    assert active_sprint_world.sprint.goal in page.text
    assert "Active" in page.text
    assert 'data-testid="project-tabs"' in page.text
    for tab in ("Board", "Backlog", "History"):
        assert f">{tab}</a>" in page.text
    assert (
        f'<a class="project-settings-link" '
        f'href="/projects/{active_sprint_world.project.slug}/settings">Settings</a>' in page.text
    )


def test_project_prefers_active_sprint_over_planning(client, active_sprint_world, engine, login_as):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    with Session(engine) as session:
        planning = Sprint(
            project_id=project.id,
            name="Sprint planning",
            goal="Choose next work",
            status=SprintStatus.PLANNING,
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        session.add(planning)
        session.flush()
        session.add_all(
            [
                Ticket(
                    ticket_number=1,
                    project_id=project.id,
                    title="Active ticket",
                    sprint_id=active_sprint_world.sprint.id,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=2,
                    project_id=project.id,
                    title="Planning ticket",
                    sprint_id=planning.id,
                    creator_id=owner.id,
                ),
            ]
        )
        session.commit()

    login_as(owner.email)
    page = client.get(f"/projects/{project.slug}").text

    assert "Active ticket" in page
    assert "Planning ticket" not in page


def test_sprint_actions_are_owner_only(
    client, active_sprint_world, make_user, add_member, login_as
):
    owner = active_sprint_world.owner
    member = make_user(email="grace@example.com")
    add_member(active_sprint_world.project, member)

    login_as(owner.email)
    owner_page = client.get(f"/projects/{active_sprint_world.project.slug}").text
    login_as(member.email)
    member_page = client.get(f"/projects/{active_sprint_world.project.slug}").text

    assert 'data-testid="owner-sprint-actions"' in owner_page
    assert 'data-testid="owner-sprint-actions"' not in member_page


def test_board_nav_marks_the_board_tab_current(client, active_sprint_world, login_as):
    login_as(active_sprint_world.owner.email)

    page = client.get(f"/projects/{active_sprint_world.project.slug}")

    assert f'href="/projects/{active_sprint_world.project.slug}" aria-current="page"' in page.text


def test_board_css_keeps_columns_horizontally_scrollable_on_mobile():
    stylesheet = Path("app/static/app.css").read_text()

    assert ".board-scroll { overflow-x: auto;" in stylesheet
    assert "grid-template-columns: repeat(4, minmax(240px, 1fr));" in stylesheet


def test_board_accessibility_uses_labeled_filters_and_human_status_text(
    client, active_sprint_world, login_as
):
    login_as(active_sprint_world.owner.email)

    page = client.get(f"/projects/{active_sprint_world.project.slug}").text

    assert 'aria-label="Board filters"' in page
    assert '<label for="assignee-filter">Assignee</label>' in page
    assert '<label for="type-filter">Type</label>' in page
    assert '<label for="priority-filter">Priority</label>' in page
    assert 'role="region" aria-label="Board columns" tabindex="0"' in page
    assert 'aria-current="page">Board</a>' in page
    assert "In progress" in page


def test_project_falls_back_to_planning_sprint(client, make_user, make_project, engine, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        session.add(
            Sprint(
                project_id=project.id,
                name="Sprint planning",
                goal="Choose the work",
                status=SprintStatus.PLANNING,
                start_date=date(2026, 9, 21),
                end_date=date(2026, 9, 28),
            )
        )
        session.commit()

    login_as(owner.email)
    page = client.get(f"/projects/{project.slug}")

    assert page.status_code == 200
    assert "Sprint planning" in page.text
    assert 'data-testid="sprint-summary"' not in page.text


def test_project_without_sprint_redirects_to_backlog(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}", follow_redirects=False)

    assert page.status_code == 303
    assert page.headers["location"] == f"/projects/{project.slug}/backlog"


def test_board_filters_selected_sprint_and_renders_human_labels(
    client, active_sprint_world, make_user, add_member, engine, login_as
):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    assignee = make_user(email="grace@example.com")
    add_member(project, assignee)
    with Session(engine) as session:
        closed_sprint = Sprint(
            project_id=project.id,
            name="Closed sprint",
            goal="Earlier work",
            status=SprintStatus.CLOSED,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 8),
        )
        session.add(closed_sprint)
        session.flush()
        session.add_all(
            [
                Ticket(
                    ticket_number=1,
                    project_id=project.id,
                    title="Mine bug",
                    type=TicketType.BUG,
                    status=TicketStatus.IN_PROGRESS,
                    priority=Priority.HIGH,
                    sprint_id=active_sprint_world.sprint.id,
                    creator_id=owner.id,
                    assignee_id=owner.id,
                ),
                Ticket(
                    ticket_number=2,
                    project_id=project.id,
                    title="Grace task",
                    type=TicketType.TASK,
                    status=TicketStatus.BACKLOG,
                    priority=Priority.LOW,
                    sprint_id=active_sprint_world.sprint.id,
                    creator_id=owner.id,
                    assignee_id=assignee.id,
                ),
                Ticket(
                    ticket_number=3,
                    project_id=project.id,
                    title="Closed sprint ticket",
                    type=TicketType.BUG,
                    status=TicketStatus.DONE,
                    priority=Priority.HIGH,
                    sprint_id=closed_sprint.id,
                    creator_id=owner.id,
                ),
            ]
        )
        session.commit()

    login_as(owner.email)
    page = client.get(f"/projects/{project.slug}?mine=1&type=BUG&priority=HIGH").text

    assert "Mine bug" in page
    assert "Grace task" not in page
    assert "Closed sprint ticket" not in page
    assert "In progress" in page
    assert 'data-testid="column-count-IN_PROGRESS">1' in page


def test_board_filters_by_assignee(
    client, active_sprint_world, make_user, add_member, engine, login_as
):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    assignee = make_user(email="grace@example.com")
    add_member(project, assignee)
    with Session(engine) as session:
        session.add(
            Ticket(
                ticket_number=1,
                project_id=project.id,
                title="Grace ticket",
                type=TicketType.TASK,
                status=TicketStatus.SELECTED,
                sprint_id=active_sprint_world.sprint.id,
                creator_id=owner.id,
                assignee_id=assignee.id,
            )
        )
        session.commit()

    login_as(owner.email)
    response = client.get(f"/projects/{project.slug}?assignee_id={assignee.id}")

    assert response.status_code == 200, response.text
    page = response.text

    assert "Grace ticket" in page
    assert 'data-testid="column-count-SELECTED">1' in page


def test_board_card_shows_assignee_name(
    client, active_sprint_world, make_user, add_member, engine, login_as
):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    assignee = make_user(email="grace@example.com", name="Grace Hopper")
    add_member(project, assignee)
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        title="Assigned ticket",
        sprint_id=active_sprint_world.sprint.id,
        creator_id=owner.id,
        assignee_id=assignee.id,
    )
    ticket_id = ticket.id
    with Session(engine) as session:
        session.add(ticket)
        session.commit()

    login_as(owner.email)
    page = client.get(f"/projects/{project.slug}").text
    card = page.split(f'id="ticket-{ticket_id}"', 1)[1].split("</article>", 1)[0]

    assert "Grace Hopper" in card


def test_board_card_labels_unassigned_ticket(client, active_sprint_world, engine, login_as):
    owner = active_sprint_world.owner
    project = active_sprint_world.project
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        title="Needs an owner",
        sprint_id=active_sprint_world.sprint.id,
        creator_id=owner.id,
    )
    ticket_id = ticket.id
    with Session(engine) as session:
        session.add(ticket)
        session.commit()

    login_as(owner.email)
    page = client.get(f"/projects/{project.slug}").text
    card = page.split(f'id="ticket-{ticket_id}"', 1)[1].split("</article>", 1)[0]

    assert "Unassigned" in card
