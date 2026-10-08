from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.models import (
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketStatus,
)


@pytest.fixture
def history_world(make_user, make_project, add_member, engine):
    owner = make_user(email="ada@example.com")
    member = make_user(email="bob@example.com")
    outsider = make_user(email="eve@example.com")
    project = make_project(owner)
    other_project = make_project(outsider, name="Other Project")
    add_member(project, member)
    with Session(engine) as session:
        older = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Lay foundations",
            status=SprintStatus.CLOSED,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 7),
            committed_points=5,
            completed_points=3,
        )
        newer = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Ship history",
            status=SprintStatus.CLOSED,
            start_date=date(2026, 9, 8),
            end_date=date(2026, 9, 14),
            committed_points=8,
            completed_points=5,
        )
        foreign = Sprint(
            project_id=other_project.id,
            name="Foreign Sprint",
            goal="Keep private",
            status=SprintStatus.CLOSED,
            start_date=date(2026, 9, 8),
            end_date=date(2026, 9, 14),
        )
        session.add_all([older, newer, foreign])
        session.flush()
        done = Ticket(
            ticket_number=1,
            project_id=project.id,
            title="Done now",
            status=TicketStatus.BACKLOG,
            story_points=1,
            creator_id=owner.id,
        )
        rolled = Ticket(
            ticket_number=2,
            project_id=project.id,
            title="Still working",
            status=TicketStatus.DONE,
            story_points=8,
            first_sprint_entered_at=datetime(2026, 9, 7, tzinfo=UTC),
            delayed_days=14,
            rollover_count=1,
            creator_id=owner.id,
        )
        session.add_all([done, rolled])
        session.flush()
        newer_id = newer.id
        foreign_id = foreign.id
        rolled_number = rolled.ticket_number
        session.add_all(
            [
                SprintTicketHistory(
                    sprint_id=older.id,
                    ticket_id=done.id,
                    status_at_close=TicketStatus.DONE,
                    story_points_at_close=3,
                    was_completed=True,
                ),
                SprintTicketHistory(
                    sprint_id=newer.id,
                    ticket_id=rolled.id,
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
            outsider=outsider,
            project=project,
            newer_id=newer_id,
            foreign_id=foreign_id,
            rolled_number=rolled_number,
        )


def test_history_lists_closed_sprints_newest_first_with_totals(client, history_world, login_as):
    login_as(history_world.owner.email)

    page = client.get(f"/projects/{history_world.project.slug}/sprints")

    assert page.status_code == 200
    summaries = page.text.split('<section class="sprint-history app-surface"', 1)[1]
    assert summaries.index("Sprint 2") < summaries.index("Sprint 1")
    assert "<dt>Committed</dt><dd>8 pts</dd>" in page.text
    assert "<dt>Completed</dt><dd>5 pts</dd>" in page.text
    assert "<dt>Rollover</dt><dd>1</dd>" in page.text
    assert "<dt>Delayed</dt><dd>7 days</dd>" in page.text
    assert "Velocity trend: 3 → 5 pts" in page.text
    assert f'href="/projects/{history_world.project.slug}/sprints" aria-current="page"' in page.text


def test_history_uses_labeled_metric_rows(client, history_world, login_as):
    """Removing the row metrics must make this fail."""
    login_as(history_world.owner.email)

    page = client.get(f"/projects/{history_world.project.slug}/sprints")

    assert '<article class="sprint-history-row">' in page.text
    assert '<dl class="sprint-history-metrics">' in page.text
    assert "<dt>Committed</dt><dd>8 pts</dd>" in page.text
    assert "<dt>Completed</dt><dd>5 pts</dd>" in page.text
    assert "<dt>Completed tickets</dt><dd>0</dd>" in page.text
    assert "<dt>Rollover</dt><dd>1</dd>" in page.text
    assert "<dt>Delayed</dt><dd>7 days</dd>" in page.text


def test_empty_history_keeps_its_message_inside_the_archive_surface(
    client, make_user, make_project, login_as
):
    """Removing the padded archive empty state must make this fail."""
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/sprints")

    assert '<p class="app-empty-state sprint-history-empty">No closed sprints.</p>' in page.text


def test_history_detail_uses_close_time_ticket_snapshot_and_is_read_only(
    client, history_world, login_as
):
    login_as(history_world.owner.email)

    page = client.get(f"/projects/{history_world.project.slug}/sprints/{history_world.newer_id}")

    assert page.status_code == 200
    assert "In progress" in page.text
    assert "5 pts at close" in page.text
    assert "<dt>Rollover</dt><dd>1</dd>" in page.text
    ticket_path = f"/projects/{history_world.project.slug}/tickets/{history_world.rolled_number}"
    ticket_link = f'href="{ticket_path}"'
    assert ticket_link in page.text
    sprint_action = (
        f'action="/projects/{history_world.project.slug}/sprints/{history_world.newer_id}/'
    )
    assert sprint_action not in page.text
    assert "Close sprint" not in page.text
    assert "Start sprint" not in page.text


def test_member_can_read_history(client, history_world, login_as):
    login_as(history_world.member.email)

    page = client.get(f"/projects/{history_world.project.slug}/sprints")

    assert page.status_code == 200
    assert "Sprint 2" in page.text


def test_outsider_cannot_read_history(client, history_world, login_as):
    login_as(history_world.outsider.email)

    response = client.get(f"/projects/{history_world.project.slug}/sprints")

    assert response.status_code == 404


def test_history_hides_other_project_sprint(client, history_world, login_as):
    login_as(history_world.member.email)

    response = client.get(
        f"/projects/{history_world.project.slug}/sprints/{history_world.foreign_id}"
    )

    assert response.status_code == 404
