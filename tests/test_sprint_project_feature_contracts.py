from datetime import date

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.models import Project, Sprint, SprintStatus
from app.schemas import ProjectCreate, ProjectUpdate, SprintClose, SprintCreate


def test_project_create_requires_start_date_and_project_update_forbids_it():
    with pytest.raises(ValidationError):
        ProjectCreate(name="Roadmap")

    with pytest.raises(ValidationError):
        ProjectUpdate(start_date=date(2026, 9, 21))


def test_project_api_persists_start_date_and_creates_initial_sprint(
    client, session, make_user, login_as
):
    owner = make_user(email="owner@example.com")
    login_as(owner.email)

    response = client.post(
        "/api/v1/projects",
        json={"name": "Roadmap", "start_date": "2026-09-23"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["start_date"] == "2026-09-23"
    project = session.exec(select(Project).where(Project.slug == "roadmap")).one()
    sprints = session.exec(select(Sprint).where(Sprint.project_id == project.id)).all()
    assert len(sprints) == 1
    assert (sprints[0].name, sprints[0].goal, sprints[0].status) == (
        "Sprint 01",
        "",
        SprintStatus.PLANNING,
    )
    assert (sprints[0].start_date, sprints[0].end_date) == (
        date(2026, 9, 28),
        date(2026, 10, 11),
    )


@pytest.mark.parametrize("count", [0, 6])
def test_sprint_create_rejects_counts_outside_one_to_five(count):
    with pytest.raises(ValidationError):
        SprintCreate(count=count)


def test_sprint_create_is_count_only_and_defaults_to_one():
    assert SprintCreate().count == 1
    assert SprintCreate(count=5).count == 5
    with pytest.raises(ValidationError):
        SprintCreate(count=1, name="legacy input")


def test_invalid_sprint_batch_count_creates_no_rows(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="owner@example.com")
    project = make_project(owner)
    before = session.exec(select(Sprint).where(Sprint.project_id == project.id)).all()
    login_as(owner.email)

    response = client.post(
        f"/api/v1/projects/{project.slug}/sprints",
        json={"count": 6},
    )

    assert response.status_code == 422
    after = session.exec(select(Sprint).where(Sprint.project_id == project.id)).all()
    assert [sprint.id for sprint in after] == [sprint.id for sprint in before]


def test_sprint_api_creates_a_list_of_empty_generated_sprints(
    client, make_user, make_project, login_as
):
    owner = make_user(email="owner@example.com")
    project = make_project(owner)
    login_as(owner.email)

    response = client.post(
        f"/api/v1/projects/{project.slug}/sprints",
        json={"count": 2},
    )

    assert response.status_code == 201
    created = response.json()
    assert isinstance(created, list)
    assert [sprint["name"] for sprint in created] == ["Sprint 02", "Sprint 03"]
    assert all(sprint["goal"] == "" for sprint in created)


def test_goal_is_editable_but_metadata_is_immutable_and_goal_is_required_to_start(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="owner@example.com")
    project = make_project(owner)
    sprint = session.exec(select(Sprint).where(Sprint.project_id == project.id)).one()
    login_as(owner.email)

    blank_start = client.patch(f"/api/v1/sprints/{sprint.id}", json={"status": "ACTIVE"})
    assert blank_start.status_code == 422
    session.refresh(sprint)
    assert sprint.status is SprintStatus.PLANNING

    assert (
        client.patch(f"/api/v1/sprints/{sprint.id}", json={"goal": "  Ship  "}).json()["goal"]
        == "Ship"
    )
    assert client.patch(f"/api/v1/sprints/{sprint.id}", json={"name": "Renamed"}).status_code == 422
    assert (
        client.patch(f"/api/v1/sprints/{sprint.id}", json={"status": "ACTIVE"}).status_code == 200
    )


def test_only_earliest_planning_sprint_can_start(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="owner@example.com")
    project = make_project(owner)
    login_as(owner.email)
    created = client.post(f"/api/v1/projects/{project.slug}/sprints", json={"count": 1}).json()[0]
    client.patch(f"/api/v1/sprints/{created['id']}", json={"goal": "Later"})

    response = client.patch(f"/api/v1/sprints/{created['id']}", json={"status": "ACTIVE"})

    assert response.status_code == 409
    later = session.get(Sprint, created["id"])
    assert later.status is SprintStatus.PLANNING


def test_close_payload_is_empty_and_rejects_legacy_destination():
    assert SprintClose().model_dump() == {}
    with pytest.raises(ValidationError):
        SprintClose(next_sprint_id="legacy")


def test_close_api_automatically_creates_the_next_sprint_when_needed(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="owner@example.com")
    project = make_project(owner)
    active = Sprint(
        project_id=project.id,
        name="Active",
        goal="Ship",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 10, 4),
    )
    session.add(active)
    session.commit()
    login_as(owner.email)

    response = client.post(f"/api/v1/sprints/{active.id}/close", json={})

    assert response.status_code == 200
    assert response.json()["id"] == active.id
    planned = session.exec(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == SprintStatus.PLANNING,
        )
    ).all()
    assert len(planned) == 1


def test_sprint_api_lists_all_statuses_in_canonical_ascending_order(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="owner@example.com")
    project = make_project(owner)
    rows = [
        Sprint(
            project_id=project.id,
            name="late",
            goal="",
            status=SprintStatus.CLOSED,
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 14),
        ),
        Sprint(
            project_id=project.id,
            name="early",
            goal="",
            status=SprintStatus.PLANNING,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 14),
        ),
    ]
    session.add_all(rows)
    session.commit()
    login_as(owner.email)

    response = client.get(f"/api/v1/projects/{project.slug}/sprints")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["early", "Sprint 01", "late"]
