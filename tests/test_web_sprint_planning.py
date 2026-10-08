from datetime import date
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import Priority, Sprint, SprintStatus, SprintTicketHistory, Ticket, TicketStatus
from app.routers import web_sprints


@pytest.fixture
def backlog_world(make_user, make_project, engine):
    owner = make_user(email="ada@example.com")
    member = make_user(email="bob@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        sprint = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship it",
            status=SprintStatus.PLANNING,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        session.add(sprint)
        session.flush()
        unassigned = Ticket(
            ticket_number=1,
            project_id=project.id,
            title="Backlog ticket",
            creator_id=owner.id,
        )
        assigned = Ticket(
            ticket_number=2,
            project_id=project.id,
            sprint_id=sprint.id,
            title="Planned ticket",
            creator_id=owner.id,
        )
        session.add_all([unassigned, assigned])
        session.commit()
        session.refresh(sprint)
        session.refresh(unassigned)
    return SimpleNamespace(
        owner=owner,
        member=member,
        project=project,
        sprint=sprint,
        unassigned=unassigned,
    )


def test_member_can_read_backlog_and_planning_state(client, backlog_world, add_member, login_as):
    add_member(backlog_world.project, backlog_world.member)
    login_as(backlog_world.member.email)

    page = client.get(f"/projects/{backlog_world.project.slug}/backlog")

    assert page.status_code == 200
    assert "Sprint 1" in page.text
    assert "Backlog ticket" in page.text
    assert 'data-testid="owner-planning-controls"' not in page.text


def test_unscheduled_section_only_lists_unassigned_tickets(client, backlog_world, login_as):
    login_as(backlog_world.owner.email)

    page = client.get(f"/projects/{backlog_world.project.slug}/backlog")

    unscheduled = page.text.split("Unscheduled tickets", 1)[1]
    assert "Backlog ticket" in unscheduled
    assert "Planned ticket" not in unscheduled


def test_backlog_shows_planning_commitment_and_tickets(client, backlog_world, engine, login_as):
    with Session(engine) as session:
        planned = session.exec(
            select(Ticket).where(Ticket.sprint_id == backlog_world.sprint.id)
        ).one()
        planned.story_points = 5
        session.add(
            Ticket(
                ticket_number=3,
                project_id=backlog_world.project.id,
                sprint_id=backlog_world.sprint.id,
                title="Unestimated planned ticket",
                creator_id=backlog_world.owner.id,
            )
        )
        session.commit()
    login_as(backlog_world.owner.email)

    page = client.get(f"/projects/{backlog_world.project.slug}/backlog").text

    assert "2 tickets · 5 points · 1 unestimated" in page
    assert "Planned ticket" in page
    assert "Unestimated planned ticket" in page


def test_backlog_filters_and_reorders_unscheduled_tickets(client, backlog_world, engine, login_as):
    with Session(engine) as session:
        urgent = Ticket(
            ticket_number=3,
            project_id=backlog_world.project.id,
            title="Urgent checkout fix",
            priority=Priority.URGENT,
            backlog_rank=3,
            creator_id=backlog_world.owner.id,
        )
        session.add(urgent)
        session.commit()
        session.refresh(urgent)
    login_as(backlog_world.owner.email)

    filtered = client.get(
        f"/projects/{backlog_world.project.slug}/backlog",
        params={"q": "checkout", "priority": "URGENT"},
    ).text
    assert "Urgent checkout fix" in filtered
    assert "Backlog ticket" not in filtered.split("Unscheduled tickets", 1)[1]

    empty = client.get(
        f"/projects/{backlog_world.project.slug}/backlog", params={"q": "missing"}
    ).text
    assert 'aria-label="Backlog filters"' in empty
    assert "No matching tickets." in empty

    response = client.post(
        f"/projects/{backlog_world.project.slug}/backlog/{urgent.id}/move",
        data={"direction": "down", "_csrf": make_csrf_token(backlog_world.owner.id)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(engine) as session:
        moved = session.get(Ticket, urgent.id)
        original = session.get(Ticket, backlog_world.unassigned.id)
        assert moved.backlog_rank < original.backlog_rank


def test_planning_warns_when_commitment_exceeds_recent_velocity(
    client, backlog_world, engine, login_as
):
    with Session(engine) as session:
        planned = session.exec(
            select(Ticket).where(Ticket.sprint_id == backlog_world.sprint.id)
        ).one()
        planned.story_points = 8
        session.add(
            Sprint(
                project_id=backlog_world.project.id,
                name="Previous sprint",
                goal="Delivered",
                status=SprintStatus.CLOSED,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 8),
                completed_points=4,
            )
        )
        session.commit()
    login_as(backlog_world.owner.email)

    page = client.get(f"/projects/{backlog_world.project.slug}/backlog").text

    assert "Above recent velocity: 8 planned / 4 average" in page


def test_unscheduled_ticket_opens_editor_and_owner_can_delete(client, backlog_world, login_as):
    login_as(backlog_world.owner.email)

    page = client.get(f"/projects/{backlog_world.project.slug}/backlog").text

    assert f'id="ticket-{backlog_world.unassigned.id}"' in page
    assert f'hx-get="/projects/{backlog_world.project.slug}/tickets/1"' in page
    assert 'hx-target="#ticket-detail-root"' in page
    assert f'hx-post="/projects/{backlog_world.project.slug}/tickets/1/delete"' in page
    assert 'hx-confirm="Delete this ticket?"' in page
    assert 'id="ticket-detail-root"' in page


def test_member_can_edit_unscheduled_ticket_but_cannot_delete(
    client, backlog_world, add_member, login_as
):
    add_member(backlog_world.project, backlog_world.member)
    login_as(backlog_world.member.email)

    page = client.get(f"/projects/{backlog_world.project.slug}/backlog").text

    assert f'hx-get="/projects/{backlog_world.project.slug}/tickets/1"' in page
    assert f"/projects/{backlog_world.project.slug}/tickets/1/delete" not in page


def test_owner_deletes_unscheduled_ticket_from_backlog(client, backlog_world, engine, login_as):
    login_as(backlog_world.owner.email)

    response = client.post(
        f"/projects/{backlog_world.project.slug}/tickets/1/delete",
        data={"_csrf": make_csrf_token(backlog_world.owner.id)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204, response.text
    assert response.headers["HX-Refresh"] == "true"
    with Session(engine) as session:
        assert session.get(Ticket, backlog_world.unassigned.id) is None


def test_backlog_delete_reports_closed_sprint_history_conflict(
    client, backlog_world, engine, login_as
):
    with Session(engine) as session:
        closed = Sprint(
            project_id=backlog_world.project.id,
            name="Closed",
            goal="Done",
            status=SprintStatus.CLOSED,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 8),
        )
        session.add(closed)
        session.flush()
        session.add(
            SprintTicketHistory(
                sprint_id=closed.id,
                ticket_id=backlog_world.unassigned.id,
                status_at_close=TicketStatus.DONE,
                was_completed=True,
            )
        )
        session.commit()
    login_as(backlog_world.owner.email)

    response = client.post(
        f"/projects/{backlog_world.project.slug}/tickets/1/delete",
        data={"_csrf": make_csrf_token(backlog_world.owner.id)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 409
    assert response.text == "Ticket belongs to closed sprint history"


def test_planning_backlog_prioritizes_start_and_hides_empty_selection_action(
    client, backlog_world, login_as
):
    login_as(backlog_world.owner.email)

    page = client.get(f"/projects/{backlog_world.project.slug}/backlog").text

    assert "Unscheduled tickets" in page
    assert ">Queue<" not in page
    assert ">Add sprint</button>" not in page
    assert ">Start sprint</button>" in page
    assert 'x-show="selectedCount > 0"' in page
    assert ':disabled="selectedCount === 0"' in page


def test_backlog_hides_unscheduled_section_when_empty(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog").text

    assert 'class="backlog-list"' not in page
    assert 'id="backlog-tickets"' not in page
    assert "No unassigned tickets." not in page


def test_owner_creates_a_dated_planning_sprint(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    response = client.post(
        f"/projects/{project.slug}/sprints",
        data={
            "name": "Sprint 1",
            "goal": "Ship it",
            "start_date": "2026-09-21",
            "end_date": "2026-09-28",
            "_csrf": make_csrf_token(owner.id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/projects/{project.slug}/backlog"


def test_second_planning_sprint_returns_backlog_conflict(client, backlog_world, login_as):
    login_as(backlog_world.owner.email)

    response = client.post(
        f"/projects/{backlog_world.project.slug}/sprints",
        data={
            "name": "Sprint 2",
            "goal": "More work",
            "start_date": "2026-09-29",
            "end_date": "2026-10-06",
            "_csrf": make_csrf_token(backlog_world.owner.id),
        },
    )

    assert response.status_code == 409
    assert "A planning sprint already exists" in response.text


def test_invalid_sprint_dates_return_a_stable_form_error(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    response = client.post(
        f"/projects/{project.slug}/sprints",
        data={
            "name": "Sprint 1",
            "goal": "Ship it",
            "start_date": "2026-09-28",
            "end_date": "2026-09-21",
            "_csrf": make_csrf_token(owner.id),
        },
    )

    assert response.status_code == 422
    assert "end_date must be after start_date" in response.text
    for value in ("Sprint 1", "Ship it", "2026-09-28", "2026-09-21"):
        assert value in response.text


def test_owner_assigns_multiple_unassigned_tickets_to_planning_sprint(
    client, backlog_world, engine, login_as
):
    with Session(engine) as session:
        another = Ticket(
            ticket_number=3,
            project_id=backlog_world.project.id,
            title="Another backlog ticket",
            creator_id=backlog_world.owner.id,
        )
        session.add(another)
        session.commit()
        session.refresh(another)
    login_as(backlog_world.owner.email)

    response = client.post(
        f"/projects/{backlog_world.project.slug}/sprints/{backlog_world.sprint.id}/tickets",
        data={
            "ticket_ids": [backlog_world.unassigned.id, another.id],
            "_csrf": make_csrf_token(backlog_world.owner.id),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(engine) as session:
        tickets = session.exec(
            select(Ticket).where(Ticket.id.in_([backlog_world.unassigned.id, another.id]))
        ).all()
    assert {ticket.sprint_id for ticket in tickets} == {backlog_world.sprint.id}


def test_assignment_rolls_back_after_a_partial_update(
    client, backlog_world, engine, login_as, monkeypatch
):
    original_update_ticket = web_sprints.update_ticket

    def fail_after_first_update(*args, **kwargs):
        original_update_ticket(*args, **kwargs)
        args[0].flush()
        raise RuntimeError("injected failure")

    monkeypatch.setattr(web_sprints, "update_ticket", fail_after_first_update)
    login_as(backlog_world.owner.email)

    with pytest.raises(RuntimeError, match="injected failure"):
        client.post(
            f"/projects/{backlog_world.project.slug}/sprints/{backlog_world.sprint.id}/tickets",
            data={
                "ticket_ids": backlog_world.unassigned.id,
                "_csrf": make_csrf_token(backlog_world.owner.id),
            },
        )

    with Session(engine) as session:
        ticket = session.get(Ticket, backlog_world.unassigned.id)
    assert ticket.sprint_id is None
    assert ticket.first_sprint_entered_at is None


def test_backlog_nav_marks_the_backlog_tab_current(client, backlog_world, login_as):
    login_as(backlog_world.owner.email)

    page = client.get(f"/projects/{backlog_world.project.slug}/backlog")

    assert f'href="/projects/{backlog_world.project.slug}/backlog" aria-current="page"' in page.text


def test_member_cannot_create_or_assign_sprints(client, backlog_world, add_member, login_as):
    add_member(backlog_world.project, backlog_world.member)
    login_as(backlog_world.member.email)

    create = client.post(
        f"/projects/{backlog_world.project.slug}/sprints",
        data={
            "name": "Sneaky",
            "goal": "Nope",
            "start_date": "2026-09-29",
            "end_date": "2026-10-06",
            "_csrf": make_csrf_token(backlog_world.member.id),
        },
    )
    assign = client.post(
        f"/projects/{backlog_world.project.slug}/sprints/{backlog_world.sprint.id}/tickets",
        data={
            "ticket_ids": backlog_world.unassigned.id,
            "_csrf": make_csrf_token(backlog_world.member.id),
        },
    )

    assert create.status_code == 403
    assert assign.status_code == 403
