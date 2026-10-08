from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    Project,
    ProjectMember,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketStatus,
)
from app.routers.api_projects import slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Payment Gateway", "payment-gateway"),
        ("  Spaced  Out  ", "spaced-out"),
        ("Rock & Roll!!", "rock-roll"),
        ("CS482 — Team 3", "cs482-team-3"),
        ("x" * 80, "x" * 50),
        # Entirely non-alphanumeric input (ASCII punctuation or a non-Latin
        # script) has nothing for the regex to keep, so it collapses to the
        # empty string. The brief specifies the resulting behaviour at the
        # route level (test_unslugifiable_name_returns_422 below): an empty
        # slug is rejected with 422 rather than silently stored or defaulted,
        # since an empty slug would be a value that collides with every other
        # unslugifiable name and produces unreachable URLs.
        ("!!!", ""),
        ("日本語", ""),
        # Truncating to 50 chars can land exactly on a separator hyphen
        # (position 50 here falls on the hyphen from the space at index 49).
        # The trailing .strip("-") after the slice exists specifically to
        # clean that up; without it this would end in "-".
        ("a" * 49 + " " + "b" * 10, "a" * 49),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_create_project_makes_the_creator_an_owner(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    response = client.post("/api/v1/projects", json={"name": "Payment Gateway"})
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "payment-gateway"
    assert body["role"] == "OWNER"


def test_duplicate_slug_returns_409(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    client.post("/api/v1/projects", json={"name": "Payment Gateway"})
    response = client.post("/api/v1/projects", json={"name": "payment gateway"})
    assert response.status_code == 409
    assert "payment-gateway" in response.text


def test_unslugifiable_name_returns_422(client, make_user, login_as):
    make_user(email="ada@example.com")
    login_as("ada@example.com")
    assert client.post("/api/v1/projects", json={"name": "!!!"}).status_code == 422


def test_project_list_shows_only_projects_you_belong_to(client, make_user, login_as):
    make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    login_as("ada@example.com")
    client.post("/api/v1/projects", json={"name": "Ada Project"})

    own_projects = client.get("/api/v1/projects").json()
    assert len(own_projects) == 1
    assert own_projects[0]["slug"] == "ada-project"
    assert own_projects[0]["role"] == "OWNER"

    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    assert client.get("/api/v1/projects").json() == []


def test_non_member_gets_404_on_project_detail(client, make_user, login_as):
    make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    login_as("ada@example.com")
    client.post("/api/v1/projects", json={"name": "Ada Project"})
    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    assert client.get("/api/v1/projects/ada-project").status_code == 404


def test_anonymous_request_is_401(client):
    assert client.post("/api/v1/projects", json={"name": "X"}).status_code == 401


def test_owner_deletes_project_with_sprint_history(
    client, engine, session, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    member = make_user(email="member@example.com")
    project = make_project(owner)
    other_project = make_project(owner, name="Other")
    add_member(project, member)
    closed = Sprint(
        project_id=project.id,
        name="Closed",
        goal="Done",
        status=SprintStatus.CLOSED,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 8),
    )
    active = Sprint(
        project_id=project.id,
        name="Active",
        goal="Now",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 8),
        end_date=date(2026, 9, 15),
    )
    planning = Sprint(
        project_id=project.id,
        name="Planning",
        goal="Next",
        status=SprintStatus.PLANNING,
        start_date=date(2026, 9, 15),
        end_date=date(2026, 9, 22),
    )
    session.add_all([closed, active, planning])
    session.flush()
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        sprint_id=active.id,
        title="Sprint ticket",
        creator_id=owner.id,
    )
    session.add(ticket)
    session.flush()
    session.add(
        SprintTicketHistory(
            sprint_id=closed.id,
            ticket_id=ticket.id,
            status_at_close=TicketStatus.DONE,
            story_points_at_close=3,
            was_completed=True,
        )
    )
    other_sprint = Sprint(
        project_id=other_project.id,
        name="Other closed",
        goal="Keep",
        status=SprintStatus.CLOSED,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 8),
    )
    session.add(other_sprint)
    session.flush()
    other_ticket = Ticket(
        ticket_number=1,
        project_id=other_project.id,
        sprint_id=other_sprint.id,
        title="Other ticket",
        creator_id=owner.id,
    )
    session.add(other_ticket)
    session.flush()
    other_history = SprintTicketHistory(
        sprint_id=other_sprint.id,
        ticket_id=other_ticket.id,
        status_at_close=TicketStatus.DONE,
        story_points_at_close=5,
        was_completed=True,
    )
    session.add(other_history)
    session.commit()
    project_id = project.id
    closed_id = closed.id
    login_as(owner.email)

    try:
        response = client.delete(f"/api/v1/projects/{project.slug}?confirm={project.slug}")
    except IntegrityError as exc:
        pytest.fail(f"project deletion leaves sprint history behind: {exc}")
    assert response.status_code == 204

    with Session(engine) as check:
        assert check.get(Project, project_id) is None
        assert (
            check.exec(select(ProjectMember).where(ProjectMember.project_id == project_id)).all()
            == []
        )
        assert check.exec(select(Ticket).where(Ticket.project_id == project_id)).all() == []
        assert check.exec(select(Sprint).where(Sprint.project_id == project_id)).all() == []
        assert (
            check.exec(
                select(SprintTicketHistory).where(SprintTicketHistory.sprint_id == closed_id)
            ).all()
            == []
        )
        assert check.get(Project, other_project.id) is not None
        assert [
            (member.user_id, member.role.value)
            for member in check.exec(
                select(ProjectMember).where(ProjectMember.project_id == other_project.id)
            ).all()
        ] == [(owner.id, "OWNER")]
        assert check.get(Ticket, other_ticket.id) is not None
        assert check.get(Sprint, other_sprint.id) is not None
        assert check.get(SprintTicketHistory, other_history.id) is not None


def test_concurrent_project_creation_race_returns_409_not_500(
    client, make_user, login_as, engine, monkeypatch
):
    # Simulate two concurrent POST /api/v1/projects calls deriving the same
    # slug: a "racer" request commits its own row for the slug in the gap
    # between our request's pre-check and its own insert, so our insert hits
    # the real unique constraint on Project.slug and must surface as 409, not
    # an unhandled 500 (Ruling R16). We hook the first session.exec() call a
    # request makes — that's the pre-check's select() — since there's no
    # standalone function analogous to api_auth's hash_password to monkeypatch
    # in this route.
    make_user(email="ada@example.com")
    login_as("ada@example.com")

    original_exec = Session.exec
    state = {"triggered": False}

    # Assumes app.auth.optional_user resolves the logged-in user via
    # session.get(User, user_id) rather than session.exec(select(User)...)
    # (app/auth.py:53) — so the first exec() call this request makes is the
    # route's own slug pre-check, not a lookup from login_as's session cookie.
    # If optional_user ever switches to session.exec(), the racer would fire
    # before the pre-check instead of after it: the pre-check would then see
    # the conflict itself and return its ordinary 409, and this test would
    # keep passing while silently testing the wrong branch (the pre-check,
    # not the except IntegrityError handler).
    def exec_then_insert_racer(self, *args, **kwargs):
        result = original_exec(self, *args, **kwargs)
        if not state["triggered"]:
            state["triggered"] = True
            with Session(engine) as racer_session:
                racer_session.add(Project(name="Racer", slug="payment-gateway", key="RAC"))
                racer_session.commit()
        return result

    monkeypatch.setattr(Session, "exec", exec_then_insert_racer)

    response = client.post("/api/v1/projects", json={"name": "Payment Gateway"})
    assert response.status_code == 409
    assert "payment-gateway" in response.text
