from datetime import date

from app.models import Sprint, SprintStatus, SprintTicketHistory, Ticket, TicketStatus


def _sprint(project, name, status, start_date):
    return Sprint(
        project_id=project.id,
        name=name,
        goal=f"Goal for {name}",
        status=status,
        start_date=start_date,
        end_date=date(start_date.year, start_date.month, start_date.day + 1),
    )


def test_owner_can_create_sprint(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    response = client.post(
        f"/api/v1/projects/{project.slug}/sprints",
        json={
            "name": "Sprint 1",
            "goal": "Ship",
            "start_date": "2026-09-21",
            "end_date": "2026-09-28",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "PLANNING"


def test_member_cannot_create_sprint(client, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    add_member(project, member)
    login_as(member.email)

    response = client.post(
        f"/api/v1/projects/{project.slug}/sprints",
        json={
            "name": "Sprint 1",
            "goal": "Ship",
            "start_date": "2026-09-21",
            "end_date": "2026-09-28",
        },
    )

    assert response.status_code == 403


def test_sprint_reads_hide_projects_from_outsiders(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    outsider = make_user(email="outsider@example.com")
    project = make_project(owner)
    sprint = _sprint(project, "Sprint 1", SprintStatus.PLANNING, date(2026, 9, 21))
    session.add(sprint)
    session.commit()
    login_as(outsider.email)

    assert client.get(f"/api/v1/projects/{project.slug}/sprints").status_code == 404
    assert client.get(f"/api/v1/sprints/{sprint.id}").status_code == 404
    assert client.get(f"/api/v1/sprints/{sprint.id}/history").status_code == 404


def test_member_can_list_sprints_newest_first(
    client, session, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    older = _sprint(project, "Older", SprintStatus.CLOSED, date(2026, 9, 1))
    active = _sprint(project, "Active", SprintStatus.ACTIVE, date(2026, 9, 8))
    planning = _sprint(project, "Planning", SprintStatus.PLANNING, date(2026, 9, 15))
    session.add_all([older, active, planning])
    session.commit()
    add_member(project, member)
    login_as(member.email)

    response = client.get(f"/api/v1/projects/{project.slug}/sprints")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [planning.id, active.id, older.id]


def test_member_can_get_sprint_detail(
    client, session, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    sprint = _sprint(project, "Sprint 1", SprintStatus.ACTIVE, date(2026, 9, 21))
    sprint.committed_points = 8
    session.add(sprint)
    session.commit()
    add_member(project, member)
    login_as(member.email)

    response = client.get(f"/api/v1/sprints/{sprint.id}")

    assert response.status_code == 200
    assert response.json()["committed_points"] == 8


def test_owner_can_start_sprint_and_member_cannot(
    client, session, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    sprint = _sprint(project, "Sprint 1", SprintStatus.PLANNING, date(2026, 9, 21))
    session.add(sprint)
    session.commit()
    add_member(project, member)
    login_as(member.email)

    assert (
        client.patch(f"/api/v1/sprints/{sprint.id}", json={"status": "ACTIVE"}).status_code == 403
    )

    client.post("/api/v1/auth/logout")
    login_as(owner.email)
    response = client.patch(f"/api/v1/sprints/{sprint.id}", json={"status": "ACTIVE"})

    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"


def test_start_rejects_detail_changes_without_mutating_sprint(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = _sprint(project, "Sprint 1", SprintStatus.PLANNING, date(2026, 9, 21))
    session.add(sprint)
    session.commit()
    login_as(owner.email)

    response = client.patch(
        f"/api/v1/sprints/{sprint.id}", json={"name": "Renamed", "status": "ACTIVE"}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "status may not be combined with sprint details"
    session.refresh(sprint)
    assert sprint.status is SprintStatus.PLANNING
    assert sprint.name == "Sprint 1"


def test_close_requires_next_sprint_id(client, session, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = _sprint(project, "Sprint 1", SprintStatus.ACTIVE, date(2026, 9, 21))
    session.add(sprint)
    session.commit()
    login_as(owner.email)

    response = client.post(f"/api/v1/sprints/{sprint.id}/close", json={})

    assert response.status_code == 422


def test_owner_can_close_sprint(client, session, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    active = _sprint(project, "Sprint 1", SprintStatus.ACTIVE, date(2026, 9, 21))
    planning = _sprint(project, "Sprint 2", SprintStatus.PLANNING, date(2026, 9, 29))
    session.add_all([active, planning])
    session.commit()
    login_as(owner.email)

    response = client.post(
        f"/api/v1/sprints/{active.id}/close", json={"next_sprint_id": planning.id}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CLOSED"


def test_member_cannot_close_sprint(client, session, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    active = _sprint(project, "Sprint 1", SprintStatus.ACTIVE, date(2026, 9, 21))
    planning = _sprint(project, "Sprint 2", SprintStatus.PLANNING, date(2026, 9, 29))
    session.add_all([active, planning])
    session.commit()
    add_member(project, member)
    login_as(member.email)

    response = client.post(
        f"/api/v1/sprints/{active.id}/close", json={"next_sprint_id": planning.id}
    )

    assert response.status_code == 403
    session.refresh(active)
    assert active.status is SprintStatus.ACTIVE


def test_member_can_read_close_time_history(
    client, session, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    sprint = _sprint(project, "Sprint 1", SprintStatus.CLOSED, date(2026, 9, 21))
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=sprint.id,
        title="Shipped ticket",
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

    response = client.get(f"/api/v1/sprints/{sprint.id}/history")

    assert response.status_code == 200
    assert response.json() == [
        {
            "ticket_id": ticket.id,
            "ticket_number": 1,
            "title": "Shipped ticket",
            "status_at_close": "DONE",
            "story_points_at_close": 5,
            "was_completed": True,
        }
    ]
