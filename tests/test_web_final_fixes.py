from datetime import date
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import (
    Project,
    ProjectMember,
    Role,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketStatus,
    TicketType,
    WebhookType,
)
from app.notifications import EVENT_TICKET_DONE
from app.services import set_chat_webhook


@pytest.fixture
def web_world(make_user, make_project, add_member, engine):
    owner = make_user(email="ada@example.com", name="Ada")
    member = make_user(email="bob@example.com", name="Bob")
    project = make_project(owner)
    add_member(project, member)
    with Session(engine) as session:
        active = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Current work",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        planning = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Next work",
            status=SprintStatus.PLANNING,
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        closed = Sprint(
            project_id=project.id,
            name="Sprint 0",
            goal="Past work",
            status=SprintStatus.CLOSED,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 8),
            committed_points=8,
            completed_points=3,
        )
        session.add_all([active, planning, closed])
        session.flush()
        ticket = Ticket(
            ticket_number=1,
            project_id=project.id,
            title="Current ticket",
            type=TicketType.TASK,
            status=TicketStatus.SELECTED,
            priority="MEDIUM",
            sprint_id=active.id,
            creator_id=owner.id,
        )
        done = Ticket(
            ticket_number=2,
            project_id=project.id,
            title="Finished ticket",
            type=TicketType.TASK,
            status=TicketStatus.DONE,
            story_points=3,
            sprint_id=closed.id,
            creator_id=owner.id,
        )
        unfinished = Ticket(
            ticket_number=3,
            project_id=project.id,
            title="Rolled ticket",
            type=TicketType.TASK,
            status=TicketStatus.IN_PROGRESS,
            story_points=5,
            sprint_id=closed.id,
            creator_id=owner.id,
        )
        session.add_all([ticket, done, unfinished])
        session.flush()
        session.add_all(
            [
                SprintTicketHistory(
                    sprint_id=closed.id,
                    ticket_id=done.id,
                    status_at_close=TicketStatus.DONE,
                    story_points_at_close=3,
                    was_completed=True,
                ),
                SprintTicketHistory(
                    sprint_id=closed.id,
                    ticket_id=unfinished.id,
                    status_at_close=TicketStatus.IN_PROGRESS,
                    story_points_at_close=5,
                    was_completed=False,
                ),
            ]
        )
        session.commit()
        return SimpleNamespace(
            owner=owner,
            member=member,
            project=project,
            active_id=active.id,
            planning_id=planning.id,
            closed_id=closed.id,
            ticket_id=ticket.id,
        )


def _detail_data(world, *, status="SELECTED", sprint_id=None, **extra):
    return {
        "title": "Current ticket",
        "description": "",
        "type": "TASK",
        "priority": "MEDIUM",
        "status": status,
        "sprint_id": world.active_id if sprint_id is None else sprint_id,
        "_csrf": make_csrf_token(world.owner.id),
        **extra,
    }


def test_mobile_backdrop_is_visible_when_alpine_shows_it(client):
    css = client.get("/static/app.css")

    assert css.status_code == 200
    mobile_css = css.text.split("@media (max-width: 767px)", 1)[1]
    assert ".app-drawer-backdrop" in mobile_css
    assert "display: block" in mobile_css.split(".app-drawer-backdrop", 1)[1].split("}", 1)[0]


def test_detail_422_html_swaps_but_csrf_403_does_not(client, web_world, login_as):
    login_as(web_world.owner.email)
    board = client.get(f"/projects/{web_world.project.slug}")
    invalid = client.post(
        f"/projects/{web_world.project.slug}/tickets/1",
        data=_detail_data(web_world, story_points="4"),
        headers={"HX-Request": "true"},
    )
    csrf_error = client.post(
        f"/projects/{web_world.project.slug}/tickets/1",
        data={**_detail_data(web_world), "_csrf": ""},
        headers={"HX-Request": "true"},
    )

    assert board.status_code == 200
    assert 'id="ticket-detail-root"' in board.text
    assert "@htmx:before-swap.camel" in board.text
    assert "$event.detail.xhr.status === 422" in board.text
    assert "$event.detail.xhr.getResponseHeader('Content-Type')" in board.text
    assert ".startsWith('text/html')" in board.text
    assert "$event.detail.xhr.status >= 400" not in board.text
    assert 'hx-target="#ticket-detail-root"' in board.text
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("text/html")
    assert 'id="ticket-detail-panel"' in invalid.text
    assert "Save changes" in invalid.text
    assert csrf_error.status_code == 403
    assert csrf_error.headers["content-type"].startswith("application/json")
    assert 'id="ticket-detail-panel"' not in csrf_error.text


def test_detail_move_requests_a_board_refresh(client, web_world, engine, login_as):
    login_as(web_world.owner.email)
    response = client.post(
        f"/projects/{web_world.project.slug}/tickets/1",
        data=_detail_data(web_world, status="DONE", sprint_id=web_world.planning_id),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Refresh") == "true"
    assert "HX-Trigger" not in response.headers
    with Session(engine) as session:
        ticket = session.exec(select(Ticket).where(Ticket.id == web_world.ticket_id)).one()
        assert (ticket.status, ticket.sprint_id) == (TicketStatus.DONE, web_world.planning_id)


def test_reaffirming_done_does_not_queue_another_done_notification(
    client, web_world, engine, login_as, monkeypatch
):
    sent = []
    with Session(engine) as session:
        project = session.get(Project, web_world.project.id)
        set_chat_webhook(session, project, WebhookType.SLACK, "https://example.com/hook")
    monkeypatch.setattr("app.notifications.dispatch", lambda *args, **kwargs: sent.append(args[2]))
    login_as(web_world.owner.email)

    first = client.post(
        f"/projects/{web_world.project.slug}/tickets/1",
        data=_detail_data(web_world, status="DONE"),
        headers={"HX-Request": "true"},
    )
    second = client.post(
        f"/projects/{web_world.project.slug}/tickets/1",
        data=_detail_data(web_world, status="DONE"),
        headers={"HX-Request": "true"},
    )
    failed = client.post(
        f"/projects/{web_world.project.slug}/tickets/1",
        data=_detail_data(web_world, status="DONE", assignee_id="not-a-member"),
        headers={"HX-Request": "true"},
    )

    assert (first.status_code, second.status_code, failed.status_code) == (200, 200, 422)
    assert [payload["event"] for payload in sent] == [EVENT_TICKET_DONE]


def test_non_htmx_ticket_detail_is_a_full_member_page_and_hx_is_a_fragment(
    client, web_world, login_as
):
    login_as(web_world.owner.email)
    page = client.get(f"/projects/{web_world.project.slug}/tickets/1")
    fragment = client.get(
        f"/projects/{web_world.project.slug}/tickets/1", headers={"HX-Request": "true"}
    )
    login_as(web_world.member.email)
    member_page = client.get(f"/projects/{web_world.project.slug}/tickets/1")
    missing = client.get(f"/projects/{web_world.project.slug}/tickets/999")

    assert page.status_code == 200
    assert "<html" in page.text
    assert 'data-testid="app-sidebar"' in page.text
    assert '<div class="project-board" x-data>' in page.text
    assert fragment.status_code == 200
    assert "<html" not in fragment.text
    assert 'data-description-view>' in page.text
    assert 'data-description-fields hidden>' in page.text
    assert 'data-description-view>' in fragment.text
    assert 'data-description-fields hidden>' in fragment.text
    assert member_page.status_code == 200
    assert missing.status_code == 404


def test_non_htmx_detail_post_redirects_to_a_saved_full_page(client, web_world, login_as):
    login_as(web_world.owner.email)
    url = f"/projects/{web_world.project.slug}/tickets/1"

    response = client.post(
        url,
        data=_detail_data(web_world, title="Native update"),
        follow_redirects=False,
    )
    page = client.get(f"{url}?saved=true")

    assert response.status_code == 303
    assert response.headers["location"] == f"{url}?saved=true"
    assert page.status_code == 200
    assert "<html" in page.text
    assert "Native update" in page.text
    assert '<p class="form-status" role="status">Ticket saved.</p>' in page.text
    assert f'<form class="app-form ticket-detail-form" method="post" action="{url}"' in page.text
    assert f'href="/projects/{web_world.project.slug}">Back to board</a>' in page.text
    assert "ticketOpener" not in page.text


def test_non_htmx_detail_validation_renders_a_full_page_form(client, web_world, login_as):
    login_as(web_world.owner.email)
    url = f"/projects/{web_world.project.slug}/tickets/1"

    response = client.post(url, data=_detail_data(web_world, story_points="4"))

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "<html" in response.text
    assert "Save changes" in response.text
    assert (
        f'<form class="app-form ticket-detail-form" method="post" action="{url}"'
        in response.text
    )
    assert f'href="/projects/{web_world.project.slug}">Back to board</a>' in response.text
    assert "ticketOpener" not in response.text


@pytest.mark.parametrize(
    ("headers", "full_page"),
    [({}, True), ({"HX-Request": "true"}, False)],
    ids=["native", "htmx"],
)
def test_detail_typed_validation_keeps_every_attempted_field(
    client, web_world, login_as, headers, full_page
):
    login_as(web_world.owner.email)
    response = client.post(
        f"/projects/{web_world.project.slug}/tickets/1",
        data=_detail_data(
            web_world,
            title="<b>Draft title</b>",
            description="<script>draft()</script>",
            type="INVALID_TYPE",
            priority="INVALID_PRIORITY",
            story_points="4",
            assignee_id="missing-assignee",
            status="INVALID_STATUS",
            resolution_notes="<img src=x onerror=alert(1)>",
            sprint_id="missing-sprint",
        ),
        headers=headers,
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert ("<html" in response.text) is full_page
    assert 'role="alert"' in response.text
    assert 'value="&lt;b&gt;Draft title&lt;/b&gt;"' in response.text
    assert "&lt;script&gt;draft()&lt;/script&gt;" in response.text
    assert 'data-description-view hidden>' in response.text
    assert 'data-description-fields>' in response.text
    assert '<option value="INVALID_TYPE" selected>INVALID_TYPE</option>' in response.text
    assert '<option value="INVALID_PRIORITY" selected>INVALID_PRIORITY</option>' in response.text
    assert '<option value="4" selected>4</option>' in response.text
    assert '<option value="missing-assignee" selected>missing-assignee</option>' in response.text
    assert '<option value="INVALID_STATUS" selected>INVALID_STATUS</option>' in response.text
    assert '<option value="missing-sprint" selected>missing-sprint</option>' in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
    assert "<script>draft()</script>" not in response.text


@pytest.mark.parametrize(
    ("headers", "full_page"),
    [({}, True), ({"HX-Request": "true"}, False)],
    ids=["native", "htmx"],
)
@pytest.mark.parametrize("title", [None, ""], ids=["missing", "blank"])
def test_detail_missing_or_blank_title_returns_an_html_form_with_attempted_values(
    client, web_world, login_as, headers, full_page, title
):
    login_as(web_world.owner.email)
    data = _detail_data(
        web_world,
        description="Missing title description",
        type="BUG",
        priority="HIGH",
        story_points="5",
        assignee_id=web_world.member.id,
        status="IN_PROGRESS",
        resolution_notes="Missing title notes",
        sprint_id=web_world.planning_id,
    )
    if title is None:
        del data["title"]
    else:
        data["title"] = title

    response = client.post(
        f"/projects/{web_world.project.slug}/tickets/1", data=data, headers=headers
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert ("<html" in response.text) is full_page
    assert 'role="alert"' in response.text
    assert 'name="title" value="" required' in response.text
    assert "Missing title description" in response.text
    assert '<option value="BUG" selected>Issue</option>' in response.text
    assert '<option value="HIGH" selected>High</option>' in response.text
    assert '<option value="5" selected>5</option>' in response.text
    assert f'<option value="{web_world.member.id}" selected>Bob</option>' in response.text
    assert '<option value="IN_PROGRESS" selected>In progress</option>' in response.text
    assert f'<option value="{web_world.planning_id}" selected>Sprint 2</option>' in response.text
    assert "Missing title notes" in response.text


def test_allowed_owner_self_removal_redirects_to_dashboard(
    client, web_world, make_user, add_member, engine, login_as
):
    other_owner = make_user(email="grace@example.com", name="Grace")
    add_member(web_world.project, other_owner, Role.OWNER)
    login_as(web_world.owner.email)

    response = client.post(
        f"/projects/{web_world.project.slug}/settings/members/{web_world.owner.id}/remove",
        data={"_csrf": make_csrf_token(web_world.owner.id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    with Session(engine) as session:
        assert (
            session.exec(
                select(ProjectMember).where(
                    ProjectMember.project_id == web_world.project.id,
                    ProjectMember.user_id == web_world.owner.id,
                )
            ).first()
            is None
        )


def test_integration_controls_keep_minimum_touch_target(client):
    css = client.get("/static/app.css").text
    controls = css.split(".integration-card input, .integration-card button {", 1)[1].split(
        "}", 1
    )[0]

    assert "min-height: 40px" in controls


def test_project_sprint_selector_opens_open_boards_and_closed_history_for_members(
    client, web_world, login_as
):
    login_as(web_world.owner.email)
    board = client.get(f"/projects/{web_world.project.slug}")
    planning_board = client.get(
        f"/projects/{web_world.project.slug}?sprint_id={web_world.planning_id}"
    )
    closed_history = client.get(f"/projects/{web_world.project.slug}/sprints/{web_world.closed_id}")
    login_as(web_world.member.email)
    member_board = client.get(
        f"/projects/{web_world.project.slug}?sprint_id={web_world.planning_id}"
    )
    active_option = (
        f'<option value="/projects/{web_world.project.slug}'
        f'?sprint_id={web_world.active_id}" selected>'
    )
    planning_option = (
        f'<option value="/projects/{web_world.project.slug}'
        f'?sprint_id={web_world.planning_id}" selected>'
    )

    assert board.status_code == 200
    assert '<label for="sprint-selector">Sprint</label>' in board.text
    assert active_option in board.text
    assert "Sprint 1 · Active" in board.text
    assert (
        f'<option value="/projects/{web_world.project.slug}?sprint_id={web_world.planning_id}">'
        in board.text
    )
    assert "Sprint 2 · Planning" in board.text
    assert f"/projects/{web_world.project.slug}/sprints/{web_world.closed_id}" in board.text
    assert "Sprint 0 · Closed" in board.text
    assert planning_board.status_code == 200
    assert planning_option in planning_board.text
    assert closed_history.status_code == 200
    assert member_board.status_code == 200


def test_history_summary_counts_completed_tickets_from_close_snapshot(client, web_world, login_as):
    login_as(web_world.owner.email)
    page = client.get(f"/projects/{web_world.project.slug}/sprints")

    assert page.status_code == 200
    assert "<dt>Completed tickets</dt><dd>1</dd>" in page.text


def test_web_role_labels_are_human_readable(client, web_world, login_as):
    login_as(web_world.owner.email)
    dashboard = client.get("/dashboard")
    settings = client.get(f"/projects/{web_world.project.slug}/settings")

    assert dashboard.status_code == 200
    assert '<span>Owner</span>' in dashboard.text
    assert f"{web_world.owner.email} · Owner" in settings.text
    assert f"{web_world.member.email} · Member" in settings.text
