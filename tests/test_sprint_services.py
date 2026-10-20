from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.models import (
    Project,
    ProjectMember,
    Sprint,
    SprintReport,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketStatus,
)
from app.services import (
    close_sprint,
    create_project,
    create_sprints,
    first_sprint_date,
    start_sprint,
    update_sprint,
)


@pytest.mark.parametrize(
    ("project_date", "expected"),
    [
        (date(2026, 9, 21), date(2026, 9, 21)),
        (date(2026, 9, 22), date(2026, 9, 28)),
        (date(2026, 9, 23), date(2026, 9, 28)),
        (date(2026, 9, 24), date(2026, 9, 28)),
        (date(2026, 9, 25), date(2026, 9, 28)),
        (date(2026, 9, 26), date(2026, 9, 28)),
        (date(2026, 9, 27), date(2026, 9, 28)),
    ],
)
def test_first_sprint_date_is_monday_on_or_after_project_date(project_date, expected):
    assert first_sprint_date(project_date) == expected


def test_create_sprints_generates_atomic_cadence(session, make_user, make_project):
    project = make_project(make_user())

    created = create_sprints(session, project, 3)

    assert [sprint.name for sprint in created] == ["Sprint 02", "Sprint 03", "Sprint 04"]
    assert all(sprint.goal == "" and sprint.status is SprintStatus.PLANNING for sprint in created)
    assert [sprint.start_date for sprint in created] == [
        date(2026, 10, 5),
        date(2026, 10, 19),
        date(2026, 11, 2),
    ]
    assert all(sprint.end_date - sprint.start_date == timedelta(days=13) for sprint in created)


def test_create_sprints_uses_project_schedule_when_legacy_project_has_none(
    session, make_user, make_project
):
    project = make_project(make_user())
    for sprint in session.exec(select(Sprint).where(Sprint.project_id == project.id)).all():
        session.delete(sprint)
    session.commit()

    created = create_sprints(session, project)

    assert created[0].start_date == date(2026, 9, 21)
    assert created[0].name == "Sprint 01"


def test_invalid_name_pattern_rolls_back_batch(session, make_user, make_project, monkeypatch):
    project = make_project(make_user())
    before = session.exec(select(Sprint).where(Sprint.project_id == project.id)).all()
    monkeypatch.setattr(settings, "sprint_name_pattern", "{unknown}")

    with pytest.raises(HTTPException) as error:
        create_sprints(session, project, 2)

    assert error.value.status_code == 422
    after = session.exec(select(Sprint).where(Sprint.project_id == project.id)).all()
    assert [row.id for row in after] == [row.id for row in before]


def test_update_allows_blank_goal_but_start_requires_nonblank(session, make_user, make_project):
    project = make_project(make_user())
    sprint = session.exec(select(Sprint).where(Sprint.project_id == project.id)).one()
    update_sprint(session, sprint, goal="   ")

    with pytest.raises(HTTPException) as error:
        start_sprint(session, sprint)

    assert error.value.status_code == 422
    session.refresh(sprint)
    assert sprint.status is SprintStatus.PLANNING


def test_only_canonical_earliest_planning_sprint_starts(session, make_user, make_project):
    project = make_project(make_user())
    first = session.exec(select(Sprint).where(Sprint.project_id == project.id)).one()
    second = create_sprints(session, project)[0]
    update_sprint(session, first, goal="First")
    update_sprint(session, second, goal="Second")

    with pytest.raises(HTTPException) as error:
        start_sprint(session, second)
    assert error.value.status_code == 409
    assert start_sprint(session, first).status is SprintStatus.ACTIVE


def test_close_rolls_to_earliest_plan_and_creates_report(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    active = session.exec(select(Sprint).where(Sprint.project_id == project.id)).one()
    update_sprint(session, active, goal="Ship")
    start_sprint(session, active)
    destination = create_sprints(session, project)[0]
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=active.id,
        title="Carry",
        creator_id=owner.id,
        status=TicketStatus.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()

    closed = close_sprint(session, active)

    session.refresh(ticket)
    assert closed.status is SprintStatus.CLOSED
    assert ticket.sprint_id == destination.id
    assert session.exec(select(SprintReport).where(SprintReport.sprint_id == active.id)).one()


def test_project_creation_rolls_back_project_member_and_initial_sprint(
    engine, make_user, monkeypatch
):
    owner = make_user()

    def fail_initial_sprint(*args, **kwargs):
        raise RuntimeError("injected initial sprint failure")

    monkeypatch.setattr("app.services._build_sprint", fail_initial_sprint)
    with Session(engine) as session, pytest.raises(RuntimeError):
        create_project(session, "Atomic project", date(2026, 9, 21), owner)

    with Session(engine) as check:
        assert check.exec(select(Project).where(Project.slug == "atomic-project")).all() == []
        assert check.exec(select(ProjectMember)).all() == []
        assert check.exec(select(Sprint)).all() == []


def test_batch_creation_rolls_back_when_a_later_row_fails(
    session, engine, make_user, make_project, monkeypatch
):
    project = make_project(make_user())
    from app import services

    original_build = services._build_sprint
    calls = 0

    def fail_second(project, number, start_date):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected batch failure")
        return original_build(project, number, start_date)

    monkeypatch.setattr(services, "_build_sprint", fail_second)
    with pytest.raises(RuntimeError):
        create_sprints(session, project, 3)

    with Session(engine) as check:
        rows = check.exec(select(Sprint).where(Sprint.project_id == project.id)).all()
        assert [row.name for row in rows] == ["Sprint 01"]


def test_close_rolls_back_destination_history_report_and_rollover_on_commit_failure(
    session, engine, make_user, make_project, monkeypatch
):
    owner = make_user()
    project = make_project(owner)
    active = session.exec(select(Sprint).where(Sprint.project_id == project.id)).one()
    update_sprint(session, active, goal="Ship")
    start_sprint(session, active)
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=active.id,
        title="Carry",
        creator_id=owner.id,
        status=TicketStatus.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    original_commit = session.commit

    def fail_commit():
        raise RuntimeError("injected close failure")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        close_sprint(session, active)
    monkeypatch.setattr(session, "commit", original_commit)

    with Session(engine) as check:
        assert check.get(Sprint, active.id).status is SprintStatus.ACTIVE
        assert check.get(Ticket, ticket.id).sprint_id == active.id
        assert (
            check.exec(select(SprintReport).where(SprintReport.sprint_id == active.id)).all() == []
        )
        assert (
            check.exec(
                select(SprintTicketHistory).where(SprintTicketHistory.sprint_id == active.id)
            ).all()
            == []
        )
        assert (
            check.exec(
                select(Sprint).where(
                    Sprint.project_id == project.id,
                    Sprint.status == SprintStatus.PLANNING,
                )
            ).all()
            == []
        )


def test_close_creates_successor_even_without_tickets(session, make_user, make_project):
    project = make_project(make_user())
    active = session.exec(select(Sprint).where(Sprint.project_id == project.id)).one()
    update_sprint(session, active, goal="Ship")
    start_sprint(session, active)

    close_sprint(session, active)

    planned = session.exec(
        select(Sprint).where(
            Sprint.project_id == project.id, Sprint.status == SprintStatus.PLANNING
        )
    ).all()
    assert len(planned) == 1
    assert planned[0].name == "Sprint 02"
