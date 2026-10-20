from datetime import date

from sqlmodel import select

from app.models import Sprint, SprintStatus, SprintTicketHistory, Ticket, TicketStatus


def _sprint(project, name, status, start_date):
    return Sprint(
        project_id=project.id,
        name=name,
        goal=f"Goal for {name}",
        status=status,
        start_date=start_date,
        end_date=start_date.replace(day=start_date.day + 1),
    )


def test_owner_creates_generated_sprint_list(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    response = client.post(f"/api/v1/projects/{project.slug}/sprints", json={"count": 2})

    assert response.status_code == 201
    assert [row["name"] for row in response.json()] == ["Sprint 02", "Sprint 03"]


def test_member_cannot_create_sprint(client, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    add_member(project, member)
    login_as(member.email)
    assert client.post(f"/api/v1/projects/{project.slug}/sprints", json={}).status_code == 403


def test_sprint_reads_hide_projects_from_outsiders(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    outsider = make_user(email="outsider@example.com")
    project = make_project(owner)
    sprint = _sprint(project, "Extra", SprintStatus.PLANNING, date(2026, 10, 21))
    session.add(sprint)
    session.commit()
    login_as(outsider.email)
    assert client.get(f"/api/v1/projects/{project.slug}/sprints").status_code == 404
    assert client.get(f"/api/v1/sprints/{sprint.id}").status_code == 404


def test_member_lists_sprints_in_canonical_order(
    client, session, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    early = _sprint(project, "Early", SprintStatus.CLOSED, date(2026, 9, 1))
    late = _sprint(project, "Late", SprintStatus.ACTIVE, date(2026, 10, 8))
    session.add_all([late, early])
    session.commit()
    add_member(project, member)
    login_as(member.email)

    rows = client.get(f"/api/v1/projects/{project.slug}/sprints").json()

    assert [row["name"] for row in rows] == ["Early", "Sprint 01", "Late"]


def test_owner_updates_goal_and_starts_earliest(client, session, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = session.exec(select(Sprint).where(Sprint.project_id == project.id)).one()
    login_as(owner.email)

    assert client.patch(f"/api/v1/sprints/{sprint.id}", json={"goal": "Ship"}).status_code == 200
    response = client.patch(f"/api/v1/sprints/{sprint.id}", json={"status": "ACTIVE"})
    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"


def test_close_is_destination_free_and_legacy_field_is_rejected(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    active = _sprint(project, "Active", SprintStatus.ACTIVE, date(2026, 10, 21))
    session.add(active)
    session.commit()
    login_as(owner.email)

    assert (
        client.post(
            f"/api/v1/sprints/{active.id}/close", json={"next_sprint_id": "legacy"}
        ).status_code
        == 422
    )
    response = client.post(f"/api/v1/sprints/{active.id}/close", json={})
    assert response.status_code == 200
    assert response.json()["id"] == active.id


def test_member_cannot_close_sprint(client, session, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    active = _sprint(project, "Active", SprintStatus.ACTIVE, date(2026, 10, 21))
    session.add(active)
    session.commit()
    add_member(project, member)
    login_as(member.email)
    assert client.post(f"/api/v1/sprints/{active.id}/close", json={}).status_code == 403


def test_member_reads_close_time_history(
    client, session, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    sprint = _sprint(project, "Closed", SprintStatus.CLOSED, date(2026, 10, 21))
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=sprint.id,
        title="Shipped",
        creator_id=owner.id,
    )
    session.add_all([sprint, ticket])
    session.commit()
    session.add(
        SprintTicketHistory(
            sprint_id=sprint.id,
            ticket_id=ticket.id,
            status_at_close=TicketStatus.DONE,
            story_points_at_close=5,
            was_completed=True,
        )
    )
    session.commit()
    add_member(project, member)
    login_as(member.email)
    assert client.get(f"/api/v1/sprints/{sprint.id}/history").json()[0]["title"] == "Shipped"
