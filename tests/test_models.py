from datetime import date

import pytest
from sqlalchemy import JSON, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

import app.models as models
from app.models import (
    ApiToken,
    Project,
    ProjectMember,
    Role,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketStatus,
    TicketType,
    User,
)


def test_ticket_comment_tracks_author_mentions_and_edit_time():
    assert hasattr(models, "TicketComment")

    comment = models.TicketComment(ticket_id="ticket", author_id="author", body="Please review")

    assert comment.mentioned_user_ids == []
    assert comment.updated_at is None


def make_user(session: Session, email: str = "a@b.com") -> User:
    user = User(name="A", email=email, password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_api_token_stores_hash_and_prefix(session, make_user):
    user = make_user()
    token = ApiToken(
        user_id=user.id,
        label="Cursor",
        prefix="kf_ab12",
        token_hash="0" * 64,
    )
    session.add(token)
    session.commit()

    assert token.revoked_at is None


def test_api_token_requires_an_existing_user(session):
    session.add(
        ApiToken(
            user_id="missing-user",
            label="Cursor",
            prefix="kf_ab12",
            token_hash="0" * 64,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_api_token_hash_is_unique(session, make_user):
    user = make_user()
    session.add_all(
        [
            ApiToken(
                user_id=user.id,
                label="Cursor",
                prefix="kf_ab12",
                token_hash="0" * 64,
            ),
            ApiToken(
                user_id=user.id,
                label="Claude",
                prefix="kf_cd34",
                token_hash="0" * 64,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_sprint_and_history_models(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        title="Checkout",
        creator_id=owner.id,
        story_points=3,
    )
    sprint = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add_all([ticket, sprint])
    session.commit()

    session.add(
        SprintTicketHistory(
            sprint_id=sprint.id,
            ticket_id=ticket.id,
            status_at_close=TicketStatus.DONE,
            story_points_at_close=ticket.story_points,
            was_completed=True,
        )
    )
    session.commit()

    assert sprint.status == SprintStatus.PLANNING
    assert ticket.sprint_id is None
    assert ticket.first_sprint_entered_at is None
    assert ticket.delayed_days is None
    assert ticket.rollover_count == 0


@pytest.mark.parametrize("status", [SprintStatus.PLANNING, SprintStatus.ACTIVE])
def test_sprint_rejects_duplicate_open_statuses(session, make_user, make_project, status):
    owner = make_user()
    project = make_project(owner)
    session.add_all(
        [
            Sprint(
                project_id=project.id,
                name="Sprint 1",
                goal="First",
                status=status,
                start_date=date(2026, 9, 21),
                end_date=date(2026, 9, 28),
            ),
            Sprint(
                project_id=project.id,
                name="Sprint 2",
                goal="Second",
                status=status,
                start_date=date(2026, 9, 29),
                end_date=date(2026, 10, 6),
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_sprint_allows_multiple_closed_statuses(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    session.add_all(
        [
            Sprint(
                project_id=project.id,
                name="Sprint 1",
                goal="First",
                status=SprintStatus.CLOSED,
                start_date=date(2026, 9, 21),
                end_date=date(2026, 9, 28),
            ),
            Sprint(
                project_id=project.id,
                name="Sprint 2",
                goal="Second",
                status=SprintStatus.CLOSED,
                start_date=date(2026, 9, 29),
                end_date=date(2026, 10, 6),
            ),
        ]
    )

    session.commit()


def test_sprint_rejects_end_date_before_start(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    session.add(
        Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Impossible schedule",
            start_date=date(2026, 9, 28),
            end_date=date(2026, 9, 21),
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_sprint_history_rejects_duplicate_ticket_snapshot(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    ticket = Ticket(ticket_number=1, project_id=project.id, title="Checkout", creator_id=owner.id)
    sprint = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship checkout",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add_all([ticket, sprint])
    session.commit()
    session.add_all(
        [
            SprintTicketHistory(
                sprint_id=sprint.id,
                ticket_id=ticket.id,
                status_at_close=TicketStatus.DONE,
                was_completed=True,
            ),
            SprintTicketHistory(
                sprint_id=sprint.id,
                ticket_id=ticket.id,
                status_at_close=TicketStatus.BACKLOG,
                was_completed=False,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_foreign_keys_are_enforced(session: Session):
    ticket = Ticket(
        ticket_number=1,
        project_id="does-not-exist",
        title="t",
        type=TicketType.TASK,
        status=TicketStatus.BACKLOG,
        creator_id="also-missing",
    )
    session.add(ticket)
    with pytest.raises(IntegrityError):
        session.commit()


def test_email_is_unique(session: Session):
    make_user(session, "dup@example.com")
    session.add(User(name="B", email="dup@example.com", password_hash="y"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_member_pair_is_unique(session: Session):
    user = make_user(session)
    project = Project(name="P", slug="p", key="P")
    session.add(project)
    session.commit()
    session.refresh(project)
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.OWNER))
    session.commit()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role=Role.MEMBER))
    with pytest.raises(IntegrityError):
        session.commit()


def test_ticket_number_is_unique_per_project(session: Session):
    user = make_user(session)
    project = Project(name="P", slug="p", key="P")
    session.add(project)
    session.commit()
    session.refresh(project)
    for _ in range(2):
        session.add(
            Ticket(
                ticket_number=7,
                project_id=project.id,
                title="t",
                type=TicketType.TASK,
                status=TicketStatus.BACKLOG,
                creator_id=user.id,
            )
        )
    with pytest.raises(IntegrityError):
        session.commit()


def test_ticket_meta_is_a_non_null_json_column_defaulting_to_empty(session: Session):
    # Spec §4: meta is JSON and NOT NULL with a {} default. The default comes
    # from the model (default_factory), the NOT NULL from the column -- assert
    # both, since patch_ticket's null guard depends on the column being NOT NULL.
    user = make_user(session)
    project = Project(name="P", slug="p", key="P")
    session.add(project)
    session.commit()
    session.refresh(project)

    column = Ticket.__table__.c.meta
    assert isinstance(column.type, JSON)
    assert column.nullable is False

    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        title="t",
        type=TicketType.TASK,
        status=TicketStatus.BACKLOG,
        creator_id=user.id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    assert ticket.meta == {}

    # NOT NULL is enforced by the database, not just declared on the model.
    # (Assigning Python None through the ORM would *not* trip it: SQLAlchemy's
    # JSON type serialises None to the JSON null literal, which is exactly why
    # patch_ticket has to reject {"meta": null} itself.)
    with pytest.raises(IntegrityError):
        session.exec(text("UPDATE ticket SET meta = NULL WHERE id = :id").bindparams(id=ticket.id))
        session.commit()
