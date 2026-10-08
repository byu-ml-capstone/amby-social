import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Barrier, Lock

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from app.models import Sprint, SprintStatus, SprintTicketHistory, Ticket, TicketStatus
from app.services import close_sprint, create_sprint, start_sprint, update_sprint


def test_create_sprint_creates_a_planning_sprint(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)

    sprint = create_sprint(
        session,
        project,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )

    assert sprint.status is SprintStatus.PLANNING
    assert sprint.project_id == project.id


def test_create_sprint_rejects_a_second_planning_sprint(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    create_sprint(
        session,
        project,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )

    with pytest.raises(HTTPException) as exc_info:
        create_sprint(
            session,
            project,
            name="Sprint 2",
            goal="Ship billing",
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "A planning sprint already exists"


@pytest.mark.parametrize("end_date", [date(2026, 9, 21), date(2026, 9, 28)])
def test_create_sprint_rejects_invalid_dates(session, make_user, make_project, end_date):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)

    with pytest.raises(HTTPException) as exc_info:
        create_sprint(
            session,
            project,
            name="Sprint 1",
            goal="Ship checkout",
            start_date=date(2026, 9, 28),
            end_date=end_date,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "end_date must be after start_date"


def test_update_sprint_changes_planning_details(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = create_sprint(
        session,
        project,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )

    updated = update_sprint(
        session,
        sprint,
        name="Sprint 2",
        goal="Ship billing",
        start_date=date(2026, 9, 22),
        end_date=date(2026, 9, 29),
    )

    assert (updated.name, updated.goal) == ("Sprint 2", "Ship billing")
    assert (updated.start_date, updated.end_date) == (date(2026, 9, 22), date(2026, 9, 29))


@pytest.mark.parametrize(
    "changes", [{"start_date": date(2026, 9, 28)}, {"end_date": date(2026, 9, 21)}]
)
def test_update_sprint_rejects_invalid_one_sided_date_changes(
    session, make_user, make_project, changes
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = create_sprint(
        session,
        project,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )

    with pytest.raises(HTTPException) as exc_info:
        update_sprint(session, sprint, **changes)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "end_date must be after start_date"


def test_update_sprint_rejects_non_planning_sprints(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
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

    with pytest.raises(HTTPException) as exc_info:
        update_sprint(session, sprint, name="Changed")

    assert exc_info.value.status_code == 409
    session.refresh(sprint)
    assert sprint.name == "Sprint 1"


@pytest.mark.parametrize(
    "changes", [{"name": None}, {"goal": None}, {"start_date": None}, {"end_date": None}]
)
def test_update_sprint_rejects_null_required_fields(session, make_user, make_project, changes):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = create_sprint(
        session,
        project,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )

    with pytest.raises(HTTPException) as exc_info:
        update_sprint(session, sprint, **changes)

    assert exc_info.value.status_code == 422
    session.refresh(sprint)
    assert (sprint.name, sprint.goal, sprint.start_date, sprint.end_date) == (
        "Sprint 1",
        "Ship checkout",
        date(2026, 9, 21),
        date(2026, 9, 28),
    )


def test_update_sprint_rolls_back_an_integrity_error(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = create_sprint(
        session,
        project,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )

    def fail_commit(_session):
        raise IntegrityError("UPDATE", {}, RuntimeError("simulated update conflict"))

    event.listen(session, "before_commit", fail_commit)
    try:
        with pytest.raises(HTTPException) as exc_info:
            update_sprint(session, sprint, name="Changed")
    finally:
        event.remove(session, "before_commit", fail_commit)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Sprint update conflict"
    session.refresh(sprint)
    assert sprint.name == "Sprint 1"
    assert update_sprint(session, sprint, name="Changed").name == "Changed"


def test_start_sprint_freezes_committed_points(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    planning_sprint = create_sprint(
        session,
        project,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add_all(
        [
            Ticket(
                ticket_number=1,
                project_id=project.id,
                sprint_id=planning_sprint.id,
                title="Complete checkout",
                story_points=5,
                creator_id=owner.id,
            ),
            Ticket(
                ticket_number=2,
                project_id=project.id,
                sprint_id=planning_sprint.id,
                title="Unestimated",
                creator_id=owner.id,
            ),
        ]
    )
    session.commit()

    started = start_sprint(session, planning_sprint)

    assert started.status is SprintStatus.ACTIVE
    assert started.committed_points == 5


def test_start_sprint_rejects_a_second_active_sprint(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    active = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add(active)
    session.commit()
    planning = create_sprint(
        session,
        project,
        name="Sprint 2",
        goal="Ship billing",
        start_date=date(2026, 9, 29),
        end_date=date(2026, 10, 6),
    )

    with pytest.raises(HTTPException) as exc_info:
        start_sprint(session, planning)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "An active sprint already exists"
    session.refresh(planning)
    assert planning.status is SprintStatus.PLANNING


def test_start_sprint_requires_a_planning_sprint(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        status=SprintStatus.CLOSED,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add(sprint)
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        start_sprint(session, sprint)

    assert exc_info.value.status_code == 409


def test_create_sprint_rolls_back_a_planning_index_race(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)

    def fail_commit(_session):
        raise IntegrityError("INSERT", {}, RuntimeError("simulated planning race"))

    event.listen(session, "before_commit", fail_commit)
    try:
        with pytest.raises(HTTPException) as exc_info:
            create_sprint(
                session,
                project,
                name="Sprint 1",
                goal="Ship checkout",
                start_date=date(2026, 9, 21),
                end_date=date(2026, 9, 28),
            )
    finally:
        event.remove(session, "before_commit", fail_commit)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "A planning sprint already exists"
    assert session.exec(select(Sprint)).first() is None


def test_start_sprint_rolls_back_an_active_index_race(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = create_sprint(
        session,
        project,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )

    def fail_commit(_session):
        raise IntegrityError("UPDATE", {}, RuntimeError("simulated active race"))

    event.listen(session, "before_commit", fail_commit)
    try:
        with pytest.raises(HTTPException) as exc_info:
            start_sprint(session, sprint)
    finally:
        event.remove(session, "before_commit", fail_commit)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "An active sprint already exists"
    session.refresh(sprint)
    assert sprint.status is SprintStatus.PLANNING


def test_close_snapshots_and_rolls_over_unfinished_tickets(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    active = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    planning = Sprint(
        project_id=project.id,
        name="Sprint 2",
        goal="Ship billing",
        start_date=date(2026, 9, 29),
        end_date=date(2026, 10, 6),
    )
    created_at = datetime(2026, 9, 2, 8, 30)
    done = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=active.id,
        title="Complete checkout",
        status=TicketStatus.DONE,
        story_points=5,
        creator_id=owner.id,
    )
    unfinished = Ticket(
        ticket_number=2,
        project_id=project.id,
        sprint_id=active.id,
        title="Complete billing",
        status=TicketStatus.IN_PROGRESS,
        story_points=3,
        first_sprint_entered_at=datetime(2026, 9, 20, 9),
        rollover_count=1,
        creator_id=owner.id,
        created_at=created_at,
    )
    unestimated = Ticket(
        ticket_number=3,
        project_id=project.id,
        sprint_id=active.id,
        title="Review copy",
        status=TicketStatus.SELECTED,
        creator_id=owner.id,
    )
    session.add_all([active, planning, done, unfinished, unestimated])
    session.commit()

    commits = 0

    def count_commit(_session):
        nonlocal commits
        commits += 1

    event.listen(session, "before_commit", count_commit)
    try:
        closed = close_sprint(session, active, planning)
    finally:
        event.remove(session, "before_commit", count_commit)

    session.refresh(done)
    session.refresh(unfinished)
    session.refresh(unestimated)
    session.refresh(planning)
    histories = {
        history.ticket_id: history
        for history in session.exec(
            select(SprintTicketHistory).where(SprintTicketHistory.sprint_id == active.id)
        )
    }

    assert commits == 1
    assert closed.status is SprintStatus.CLOSED
    assert closed.completed_points == 5
    assert closed.closed_at is not None
    assert planning.status is SprintStatus.PLANNING
    assert done.sprint_id == active.id
    assert done.rollover_count == 0
    assert done.delayed_days is None
    assert unfinished.sprint_id == planning.id
    assert unfinished.status is TicketStatus.IN_PROGRESS
    assert unfinished.created_at == created_at
    assert unfinished.rollover_count == 2
    assert unfinished.delayed_days == 8
    assert unestimated.sprint_id == planning.id
    assert unestimated.status is TicketStatus.SELECTED
    assert unestimated.delayed_days is None
    assert {
        ticket_id: (
            history.status_at_close,
            history.story_points_at_close,
            history.was_completed,
        )
        for ticket_id, history in histories.items()
    } == {
        done.id: (TicketStatus.DONE, 5, True),
        unfinished.id: (TicketStatus.IN_PROGRESS, 3, False),
        unestimated.id: (TicketStatus.SELECTED, None, False),
    }

    done.story_points = 8
    unfinished.status = TicketStatus.BACKLOG
    unfinished.story_points = 13
    session.commit()
    session.refresh(closed)
    snapshot = session.exec(
        select(SprintTicketHistory).where(SprintTicketHistory.ticket_id == unfinished.id)
    ).one()

    assert closed.completed_points == 5
    assert (snapshot.status_at_close, snapshot.story_points_at_close) == (
        TicketStatus.IN_PROGRESS,
        3,
    )


def test_close_rejects_a_destination_in_another_project(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    other_project = make_project(owner, name="Other project")
    active = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    foreign_planning = Sprint(
        project_id=other_project.id,
        name="Sprint 2",
        goal="Ship billing",
        start_date=date(2026, 9, 29),
        end_date=date(2026, 10, 6),
    )
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=active.id,
        title="Complete checkout",
        creator_id=owner.id,
    )
    session.add_all([active, foreign_planning, ticket])
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        close_sprint(session, active, foreign_planning)

    assert exc_info.value.status_code == 409
    session.refresh(active)
    session.refresh(foreign_planning)
    session.refresh(ticket)
    assert active.status is SprintStatus.ACTIVE
    assert foreign_planning.status is SprintStatus.PLANNING
    assert ticket.sprint_id == active.id
    assert session.exec(select(SprintTicketHistory)).first() is None


def test_close_reloads_a_stale_nonplanning_destination(engine, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as setup:
        active = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship checkout",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        planning = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Ship billing",
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        ticket = Ticket(
            ticket_number=1,
            project_id=project.id,
            sprint_id=active.id,
            title="Complete checkout",
            creator_id=owner.id,
        )
        setup.add_all([active, planning, ticket])
        setup.commit()
        active_id, planning_id, ticket_id = active.id, planning.id, ticket.id

    with Session(engine) as session:
        active = session.get(Sprint, active_id)
        planning = session.get(Sprint, planning_id)
        assert active is not None
        assert planning is not None

        with Session(engine) as modifier:
            current_destination = modifier.get(Sprint, planning_id)
            assert current_destination is not None
            current_destination.status = SprintStatus.CLOSED
            modifier.commit()

        with pytest.raises(HTTPException) as exc_info:
            close_sprint(session, active, planning)

        assert exc_info.value.status_code == 409

    with Session(engine) as check:
        active = check.get(Sprint, active_id)
        ticket = check.get(Ticket, ticket_id)

        assert active is not None
        assert ticket is not None
        assert active.status is SprintStatus.ACTIVE
        assert ticket.sprint_id == active_id
        assert check.exec(select(SprintTicketHistory)).first() is None


def test_close_maps_duplicate_history_to_a_conflict(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    active = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    planning = Sprint(
        project_id=project.id,
        name="Sprint 2",
        goal="Ship billing",
        start_date=date(2026, 9, 29),
        end_date=date(2026, 10, 6),
    )
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=active.id,
        title="Complete checkout",
        status=TicketStatus.IN_PROGRESS,
        creator_id=owner.id,
    )
    history = SprintTicketHistory(
        sprint_id=active.id,
        ticket_id=ticket.id,
        status_at_close=TicketStatus.IN_PROGRESS,
        was_completed=False,
    )
    session.add_all([active, planning, ticket])
    session.commit()
    session.add(history)
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        close_sprint(session, active, planning)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Sprint close conflict"
    session.refresh(active)
    session.refresh(ticket)
    assert active.status is SprintStatus.ACTIVE
    assert ticket.sprint_id == active.id
    assert len(session.exec(select(SprintTicketHistory)).all()) == 1


def test_close_rejects_an_already_closed_sprint(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    active = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    planning = Sprint(
        project_id=project.id,
        name="Sprint 2",
        goal="Ship billing",
        start_date=date(2026, 9, 29),
        end_date=date(2026, 10, 6),
    )
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=active.id,
        title="Complete checkout",
        creator_id=owner.id,
    )
    session.add_all([active, planning, ticket])
    session.commit()
    close_sprint(session, active, planning)

    with pytest.raises(HTTPException) as exc_info:
        close_sprint(session, active, planning)

    assert exc_info.value.status_code == 409
    session.refresh(ticket)
    assert ticket.sprint_id == planning.id
    assert ticket.rollover_count == 1
    assert len(session.exec(select(SprintTicketHistory)).all()) == 1


@pytest.mark.parametrize("_attempt", range(5))
def test_close_allows_only_one_of_two_stale_sessions_at_claim_boundary(
    engine, make_user, make_project, _attempt
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as setup:
        active = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship checkout",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        planning = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Ship billing",
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        ticket = Ticket(
            ticket_number=1,
            project_id=project.id,
            sprint_id=active.id,
            title="Complete checkout",
            status=TicketStatus.IN_PROGRESS,
            creator_id=owner.id,
        )
        setup.add_all([active, planning, ticket])
        setup.commit()
        active_id, planning_id, ticket_id = active.id, planning.id, ticket.id

    claim_barrier = Barrier(2)
    claim_hits = 0
    claim_lock = Lock()

    def wait_at_source_claim(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal claim_hits
        if statement.startswith("UPDATE sprint SET") and "sprint.status" in statement:
            with claim_lock:
                claim_hits += 1
            claim_barrier.wait(timeout=5)

    def close_once(_index: int) -> int:
        with Session(engine) as session:
            source = session.get(Sprint, active_id)
            destination = session.get(Sprint, planning_id)
            assert source is not None
            assert destination is not None
            try:
                close_sprint(session, source, destination)
            except HTTPException as exc:
                return exc.status_code
            return 200

    event.listen(engine, "before_cursor_execute", wait_at_source_claim)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(close_once, range(2)))
    finally:
        event.remove(engine, "before_cursor_execute", wait_at_source_claim)

    assert claim_hits == 2
    assert sorted(statuses) == [200, 409]
    with Session(engine) as check:
        source = check.get(Sprint, active_id)
        destination = check.get(Sprint, planning_id)
        ticket = check.get(Ticket, ticket_id)
        histories = check.exec(
            select(SprintTicketHistory).where(SprintTicketHistory.sprint_id == active_id)
        ).all()

        assert source is not None
        assert destination is not None
        assert ticket is not None
        assert source.status is SprintStatus.CLOSED
        assert destination.status is SprintStatus.PLANNING
        assert ticket.sprint_id == planning_id
        assert len(histories) == 1


def test_close_maps_a_post_dml_sqlite_lock_to_conflict_and_rolls_back(
    engine, make_user, make_project
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as setup:
        active = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship checkout",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        planning = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Ship billing",
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        ticket = Ticket(
            ticket_number=1,
            project_id=project.id,
            sprint_id=active.id,
            title="Complete checkout",
            status=TicketStatus.IN_PROGRESS,
            first_sprint_entered_at=datetime(2026, 9, 20),
            creator_id=owner.id,
        )
        setup.add_all([active, planning, ticket])
        setup.commit()
        active_id, planning_id, ticket_id = active.id, planning.id, ticket.id

    with Session(engine) as session:
        active = session.get(Sprint, active_id)
        planning = session.get(Sprint, planning_id)
        assert active is not None
        assert planning is not None

        def fail_after_flush(*_):
            raise OperationalError("INSERT", {}, sqlite3.OperationalError("database is locked"))

        event.listen(session, "after_flush_postexec", fail_after_flush)
        try:
            with pytest.raises(HTTPException) as exc_info:
                close_sprint(session, active, planning)
        finally:
            event.remove(session, "after_flush_postexec", fail_after_flush)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Sprint close conflict"

    with Session(engine) as check:
        active = check.get(Sprint, active_id)
        planning = check.get(Sprint, planning_id)
        ticket = check.get(Ticket, ticket_id)

        assert active is not None
        assert planning is not None
        assert ticket is not None
        assert active.status is SprintStatus.ACTIVE
        assert active.completed_points is None
        assert active.closed_at is None
        assert planning.status is SprintStatus.PLANNING
        assert ticket.sprint_id == active_id
        assert ticket.rollover_count == 0
        assert ticket.delayed_days is None
        assert check.exec(select(SprintTicketHistory)).first() is None


def test_close_reraises_unrelated_operational_error_after_rollback(engine, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as setup:
        active = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship checkout",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        planning = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Ship billing",
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        ticket = Ticket(
            ticket_number=1,
            project_id=project.id,
            sprint_id=active.id,
            title="Complete checkout",
            status=TicketStatus.IN_PROGRESS,
            creator_id=owner.id,
        )
        setup.add_all([active, planning, ticket])
        setup.commit()
        active_id, planning_id, ticket_id = active.id, planning.id, ticket.id

    with Session(engine) as session:
        active = session.get(Sprint, active_id)
        planning = session.get(Sprint, planning_id)
        assert active is not None
        assert planning is not None

        def fail_after_flush(*_):
            raise OperationalError(
                "INSERT", {}, sqlite3.OperationalError("no such table: unrelated")
            )

        event.listen(session, "after_flush_postexec", fail_after_flush)
        try:
            with pytest.raises(OperationalError, match="no such table: unrelated"):
                close_sprint(session, active, planning)
        finally:
            event.remove(session, "after_flush_postexec", fail_after_flush)

        assert not session.in_transaction()

    with Session(engine) as check:
        active = check.get(Sprint, active_id)
        planning = check.get(Sprint, planning_id)
        ticket = check.get(Ticket, ticket_id)

        assert active is not None
        assert planning is not None
        assert ticket is not None
        assert active.status is SprintStatus.ACTIVE
        assert planning.status is SprintStatus.PLANNING
        assert ticket.sprint_id == active_id
        assert check.exec(select(SprintTicketHistory)).first() is None
