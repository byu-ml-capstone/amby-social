from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import Sprint, SprintStatus, Ticket, TicketStatus
from app.routers import web_sprints


@pytest.fixture
def lifecycle_world(make_user, make_project, add_member, engine):
    owner = make_user(email="ada@example.com")
    member = make_user(email="bob@example.com")
    project = make_project(owner)
    start_project = make_project(owner, name="Start Project")
    add_member(project, member)
    add_member(start_project, member)
    with Session(engine) as session:
        active = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship it",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        next_sprint = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Next up",
            start_date=date(2026, 9, 28),
            end_date=date(2026, 10, 5),
        )
        planning = Sprint(
            project_id=start_project.id,
            name="Start me",
            goal="Commit scope",
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        session.add_all([active, next_sprint, planning])
        session.flush()
        session.add_all(
            [
                Ticket(
                    ticket_number=1,
                    project_id=project.id,
                    sprint_id=active.id,
                    title="Done ticket",
                    status=TicketStatus.DONE,
                    story_points=3,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=2,
                    project_id=project.id,
                    sprint_id=active.id,
                    title="Unfinished ticket",
                    story_points=5,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=3,
                    project_id=project.id,
                    sprint_id=active.id,
                    title="Unestimated ticket",
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=1,
                    project_id=start_project.id,
                    sprint_id=planning.id,
                    title="Committed ticket",
                    story_points=2,
                    creator_id=owner.id,
                ),
                Ticket(
                    ticket_number=2,
                    project_id=start_project.id,
                    sprint_id=planning.id,
                    title="Another committed ticket",
                    story_points=5,
                    creator_id=owner.id,
                ),
            ]
        )
        session.commit()
        session.refresh(active)
        session.refresh(next_sprint)
        session.refresh(planning)
    return SimpleNamespace(
        owner=owner,
        member=member,
        project=project,
        start_project=start_project,
        active=active,
        next_sprint=next_sprint,
        planning=planning,
    )


def test_owner_starts_planning_sprint_with_committed_point_total(
    client, lifecycle_world, engine, login_as
):
    login_as(lifecycle_world.owner.email)

    response = client.post(
        f"/projects/{lifecycle_world.start_project.slug}/sprints/{lifecycle_world.planning.id}/start",
        data={"_csrf": make_csrf_token(lifecycle_world.owner.id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(engine) as session:
        sprint = session.get(Sprint, lifecycle_world.planning.id)
    assert (sprint.status, sprint.committed_points) == (SprintStatus.ACTIVE, 7)


def test_member_cannot_start_or_close_sprints(client, lifecycle_world, engine, login_as):
    login_as(lifecycle_world.member.email)
    token = make_csrf_token(lifecycle_world.member.id)

    start = client.post(
        f"/projects/{lifecycle_world.start_project.slug}/sprints/{lifecycle_world.planning.id}/start",
        data={"_csrf": token},
    )
    close = client.post(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close",
        data={"_csrf": token, "next_sprint_id": lifecycle_world.next_sprint.id},
    )

    assert (start.status_code, close.status_code) == (403, 403)
    with Session(engine) as session:
        assert session.get(Sprint, lifecycle_world.planning.id).status is SprintStatus.PLANNING
        assert session.get(Sprint, lifecycle_world.active.id).status is SprintStatus.ACTIVE


def test_lifecycle_mutations_require_csrf(client, lifecycle_world, engine, login_as):
    login_as(lifecycle_world.owner.email)

    response = client.post(
        f"/projects/{lifecycle_world.start_project.slug}/sprints/{lifecycle_world.planning.id}/start"
    )

    assert response.status_code == 403
    with Session(engine) as session:
        assert session.get(Sprint, lifecycle_world.planning.id).status is SprintStatus.PLANNING


def test_close_preview_shows_totals_destination_and_accessible_dialog(
    client, lifecycle_world, login_as
):
    login_as(lifecycle_world.owner.email)

    response = client.get(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close"
    )

    assert response.status_code == 200
    for text in ("3 completed points", "2 unfinished tickets", "Sprint 2"):
        assert text in response.text
    assert "<dialog" in response.text
    assert "autofocus" in response.text
    assert "@submit=\"$el.querySelector('[type=submit]').disabled = true\"" in response.text


def test_close_preview_explains_how_to_create_a_rollover_destination(
    client, lifecycle_world, engine, login_as
):
    with Session(engine) as session:
        next_sprint = session.get(Sprint, lifecycle_world.next_sprint.id)
        next_sprint.status = SprintStatus.CLOSED
        session.add(next_sprint)
        session.commit()

    login_as(lifecycle_world.owner.email)
    response = client.get(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close"
    )

    assert response.status_code == 200
    assert "Create a planning sprint from Backlog before closing this sprint." in response.text


def test_close_requires_a_planning_destination_without_mutation(
    client, lifecycle_world, engine, login_as
):
    login_as(lifecycle_world.owner.email)

    response = client.post(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close",
        data={"_csrf": make_csrf_token(lifecycle_world.owner.id)},
    )

    assert response.status_code == 422
    assert "Select a planning sprint" in response.text
    with Session(engine) as session:
        assert session.get(Sprint, lifecycle_world.active.id).status is SprintStatus.ACTIVE


def test_htmx_close_error_fragment_swaps_on_the_persistent_dialog_host(
    client, lifecycle_world, login_as
):
    login_as(lifecycle_world.owner.email)

    board = client.get(f"/projects/{lifecycle_world.project.slug}")
    assert "@htmx:before-swap.camel" in board.text
    assert "$event.detail.xhr.status === 409 || $event.detail.xhr.status === 422" in board.text
    assert "$event.detail.shouldSwap = true" in board.text
    assert "$event.detail.target = $el" in board.text
    assert "$event.detail.swapOverride = 'innerHTML'" in board.text

    response = client.post(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close",
        data={"_csrf": make_csrf_token(lifecycle_world.owner.id)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 422
    assert "@htmx:before-swap" not in response.text
    assert "@htmx:response-error" not in response.text
    assert (
        "x-init=\"$nextTick(() => { $el.showModal(); $el.querySelector('h2').focus() })\""
        in response.text
    )


def test_close_rejects_an_unknown_rollover_destination(client, lifecycle_world, engine, login_as):
    login_as(lifecycle_world.owner.email)

    response = client.post(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close",
        data={
            "_csrf": make_csrf_token(lifecycle_world.owner.id),
            "next_sprint_id": "not-a-sprint",
        },
    )

    assert response.status_code == 422
    assert "Select a planning sprint" in response.text
    with Session(engine) as session:
        assert session.get(Sprint, lifecycle_world.active.id).status is SprintStatus.ACTIVE


def test_owner_closes_sprint_and_rolls_unfinished_tickets_forward(
    client, lifecycle_world, engine, login_as
):
    login_as(lifecycle_world.owner.email)

    response = client.post(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close",
        data={
            "_csrf": make_csrf_token(lifecycle_world.owner.id),
            "next_sprint_id": lifecycle_world.next_sprint.id,
            "goal_achieved": "yes",
            "review_notes": "Strong delivery; reduce review wait next sprint.",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(engine) as session:
        sprint = session.get(Sprint, lifecycle_world.active.id)
        tickets = session.exec(
            select(Ticket).where(Ticket.project_id == lifecycle_world.project.id)
        ).all()
        assert (sprint.status, sprint.completed_points) == (SprintStatus.CLOSED, 3)
        assert sprint.goal_achieved is True
        assert sprint.review_notes == "Strong delivery; reduce review wait next sprint."
    rolled_over = {
        ticket.title for ticket in tickets if ticket.sprint_id == lifecycle_world.next_sprint.id
    }
    assert rolled_over == {
        "Unfinished ticket",
        "Unestimated ticket",
    }


def test_htmx_close_redirects_to_the_updated_board(client, lifecycle_world, login_as):
    login_as(lifecycle_world.owner.email)

    response = client.post(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close",
        data={
            "_csrf": make_csrf_token(lifecycle_world.owner.id),
            "next_sprint_id": lifecycle_world.next_sprint.id,
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/projects/{lifecycle_world.project.slug}"


def test_duplicate_close_submission_returns_stable_conflict(client, lifecycle_world, login_as):
    login_as(lifecycle_world.owner.email)
    url = f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close"
    data = {
        "_csrf": make_csrf_token(lifecycle_world.owner.id),
        "next_sprint_id": lifecycle_world.next_sprint.id,
    }

    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    duplicate = client.post(url, data=data)

    assert duplicate.status_code == 409
    assert "Sprint must be active" in duplicate.text


def test_close_service_error_is_rendered_without_partial_mutation(
    client, lifecycle_world, engine, login_as, monkeypatch
):
    def fail_close(*_args):
        raise HTTPException(409, "Rollover unavailable")

    monkeypatch.setattr(web_sprints, "close_sprint", fail_close)
    login_as(lifecycle_world.owner.email)

    response = client.post(
        f"/projects/{lifecycle_world.project.slug}/sprints/{lifecycle_world.active.id}/close",
        data={
            "_csrf": make_csrf_token(lifecycle_world.owner.id),
            "next_sprint_id": lifecycle_world.next_sprint.id,
        },
    )

    assert response.status_code == 409
    assert "Rollover unavailable" in response.text
    with Session(engine) as session:
        assert session.get(Sprint, lifecycle_world.active.id).status is SprintStatus.ACTIVE
