from datetime import date

import pytest
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import Sprint, SprintStatus, Ticket, TicketStatus


@pytest.fixture
def active_sprint(make_user, make_project, engine):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        sprint = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship it",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        session.add(sprint)
        session.commit()
        session.refresh(sprint)
    return owner, project, sprint


def make_ticket(client, project, owner, sprint_id=None):
    return client.post(
        f"/projects/{project.slug}/tickets",
        data={
            "title": "T",
            "type": "TASK",
            "sprint_id": sprint_id,
            "_csrf": make_csrf_token(owner.id),
        },
    )


def ticket_by_number(session, project, ticket_number):
    return session.exec(
        select(Ticket).where(Ticket.project_id == project.id, Ticket.ticket_number == ticket_number)
    ).first()


def test_dropdown_moves_the_card_and_returns_a_fragment(client, active_sprint, login_as):
    owner, project, sprint = active_sprint
    login_as("ada@example.com")
    make_ticket(client, project, owner, sprint.id)
    response = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "IN_PROGRESS", "_csrf": make_csrf_token(owner.id)},
    )
    assert response.status_code == 200
    assert response.text.strip().startswith("<article")
    assert "<html" not in response.text
    page = client.get(f"/projects/{project.slug}").text
    in_progress = page.split('id="column-IN_PROGRESS"')[1].split("</section>")[0]
    assert "#1" in in_progress


def test_unknown_ticket_number_is_404(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets/999/status",
        data={"status": "DONE", "_csrf": make_csrf_token(owner.id)},
    )
    assert response.status_code == 404


def test_status_change_without_csrf_is_403(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    make_ticket(client, project, owner)
    response = client.post(f"/projects/{project.slug}/tickets/1/status", data={"status": "DONE"})
    assert response.status_code == 403


def test_ticket_numbers_do_not_leak_across_projects(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    first = make_project(owner, name="First")
    second = make_project(owner, name="Second")
    login_as("ada@example.com")
    make_ticket(client, first, owner)
    response = client.post(
        f"/projects/{second.slug}/tickets/1/status",
        data={"status": "DONE", "_csrf": make_csrf_token(owner.id)},
    )
    assert response.status_code == 404


def test_non_member_gets_403_not_404(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    make_ticket(client, project, owner)
    outsider = make_user(email="eve@example.com")
    login_as("eve@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "DONE", "_csrf": make_csrf_token(outsider.id)},
    )
    assert response.status_code == 403


def test_any_member_may_transition_not_only_owner(
    client, make_user, make_project, login_as, add_member
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    make_ticket(client, project, owner)
    member = make_user(email="bob@example.com")
    add_member(project, member)
    login_as("bob@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "SELECTED", "_csrf": make_csrf_token(member.id)},
    )
    assert response.status_code == 200


def test_invalid_status_value_is_rejected(client, make_user, make_project, login_as, session):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    make_ticket(client, project, owner)
    response = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "NOT_A_STATUS", "_csrf": make_csrf_token(owner.id)},
    )
    assert response.status_code == 422
    ticket = ticket_by_number(session, project, 1)
    assert ticket.status == TicketStatus.BACKLOG


def test_fragment_carries_the_outer_html_swap_target_id(
    client, make_user, make_project, login_as, session
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    make_ticket(client, project, owner)
    ticket = ticket_by_number(session, project, 1)
    response = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "IN_PROGRESS", "_csrf": make_csrf_token(owner.id)},
    )
    assert f'id="ticket-{ticket.id}"' in response.text


def test_second_status_change_still_has_a_csrf_token_r41(client, make_user, make_project, login_as):
    # Ruling R41: the returned card must carry csrf_token, or a second status
    # change on the same page 403s while the first silently worked.
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    make_ticket(client, project, owner)
    client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "SELECTED", "_csrf": make_csrf_token(owner.id)},
    )
    second = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "IN_PROGRESS", "_csrf": make_csrf_token(owner.id)},
    )
    assert second.status_code == 200


def test_done_round_trip_through_route(client, make_user, make_project, login_as, session):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    make_ticket(client, project, owner)

    done = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "DONE", "_csrf": make_csrf_token(owner.id)},
    )
    assert done.status_code == 200
    ticket = ticket_by_number(session, project, 1)
    assert ticket.status == TicketStatus.DONE
    assert ticket.completed_at is not None

    ticket.resolution_notes = "fixed it"
    session.add(ticket)
    session.commit()

    back = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "SELECTED", "_csrf": make_csrf_token(owner.id)},
    )
    assert back.status_code == 200

    session.refresh(ticket)
    assert ticket.status == TicketStatus.SELECTED
    assert ticket.completed_at is None
    assert ticket.resolution_notes == "fixed it"


def test_status_fragment_keeps_assignee_name(
    client, active_sprint, make_user, add_member, engine, login_as
):
    owner, project, sprint = active_sprint
    assignee = make_user(email="grace@example.com", name="Grace Hopper")
    add_member(project, assignee)
    with Session(engine) as session:
        session.add(
            Ticket(
                ticket_number=1,
                project_id=project.id,
                title="Assigned ticket",
                sprint_id=sprint.id,
                creator_id=owner.id,
                assignee_id=assignee.id,
            )
        )
        session.commit()

    login_as(owner.email)
    response = client.post(
        f"/projects/{project.slug}/tickets/1/status",
        data={"status": "IN_PROGRESS", "_csrf": make_csrf_token(owner.id)},
    )

    assert response.status_code == 200
    assert "Grace Hopper" in response.text
