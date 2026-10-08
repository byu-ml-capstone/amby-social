from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.models import Project, Sprint, SprintStatus, Ticket
from app.services import create_ticket, validate_meta


def test_meta_must_be_an_object():
    with pytest.raises(HTTPException) as excinfo:
        validate_meta(["not", "an", "object"])
    assert excinfo.value.status_code == 422


def test_meta_depth_limit():
    validate_meta({"a": {"b": {"c": 1}}})
    with pytest.raises(HTTPException):
        validate_meta({"a": {"b": {"c": {"d": 1}}}})


def test_meta_depth_limit_via_lists():
    # Nesting depth must be enforced through lists too, not just dicts —
    # otherwise wrapping an over-deep structure in a list would defeat the
    # limit if the walker only recursed into dict values.
    with pytest.raises(HTTPException):
        validate_meta({"a": [{"b": [{"c": {"d": 1}}]}]})


def test_meta_depth_limit_not_defeated_by_width():
    # Many sibling keys at the same level must not accumulate into a fake
    # depth violation — depth is about nesting, not key count.
    wide = {f"key{i}": i for i in range(200)}
    validate_meta(wide)


def test_meta_size_limit():
    with pytest.raises(HTTPException):
        validate_meta({"blob": "x" * 9000})


def test_ticket_numbers_start_at_one_and_increment(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    numbers = []
    for i in range(3):
        response = client.post(
            "/api/v1/tickets", json={"slug": project.slug, "title": f"Ticket {i}"}
        )
        assert response.status_code == 201
        numbers.append(response.json()["ticket_number"])
    assert numbers == [1, 2, 3]


def test_created_ticket_defaults(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    body = client.post(
        "/api/v1/tickets", json={"slug": project.slug, "title": "  Trimmed  "}
    ).json()
    assert body["title"] == "Trimmed"
    assert body["status"] == "BACKLOG"
    assert body["priority"] == "MEDIUM"
    assert body["story_points"] is None
    assert body["due_date"] is None
    assert body["meta"] == {}


def test_create_ticket_assigns_a_planning_sprint(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    sprint = Sprint(
        project_id=project.id,
        name="Sprint 1",
        goal="Ship",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add(sprint)
    session.commit()
    login_as(owner.email)

    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "Assigned", "sprint_id": sprint.id},
    )

    assert response.status_code == 201
    assert response.json()["sprint_id"] == sprint.id
    stored = session.get(Ticket, response.json()["id"])
    assert stored is not None
    assert stored.first_sprint_entered_at is not None


def test_create_ticket_rejects_closed_or_foreign_sprints(
    client, session, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    foreign_project = make_project(make_user(email="other@example.com"), name="Other Project")
    closed = Sprint(
        project_id=project.id,
        name="Closed",
        goal="Done",
        status=SprintStatus.CLOSED,
        start_date=date(2026, 9, 7),
        end_date=date(2026, 9, 14),
    )
    foreign = Sprint(
        project_id=foreign_project.id,
        name="Foreign",
        goal="Elsewhere",
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    session.add_all([closed, foreign])
    session.commit()
    login_as(owner.email)

    for sprint in (closed, foreign):
        response = client.post(
            "/api/v1/tickets",
            json={"slug": project.slug, "title": "Invalid", "sprint_id": sprint.id},
        )
        assert response.status_code == 422


def test_assignee_must_be_a_project_member(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    outsider = make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        "/api/v1/tickets",
        json={"slug": project.slug, "title": "T", "assignee_id": outsider.id},
    )
    assert response.status_code == 422


def test_invalid_story_points_rejected(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert (
        client.post(
            "/api/v1/tickets", json={"slug": project.slug, "title": "T", "story_points": 4}
        ).status_code
        == 422
    )


def test_non_member_cannot_create(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    assert (
        client.post("/api/v1/tickets", json={"slug": project.slug, "title": "T"}).status_code == 403
    )


def test_create_ticket_rejects_non_dict_meta_even_when_falsy(session, make_user, make_project):
    # meta is a trust boundary: create_ticket must not let a falsy-but-wrong
    # type (an empty list, say) slip past validate_meta by being coalesced
    # into {} the way `meta or {}` would.
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with pytest.raises(HTTPException) as excinfo:
        create_ticket(session, project, owner, title="T", meta=[])
    assert excinfo.value.status_code == 422


def test_create_ticket_rejects_invalid_story_points_directly(session, make_user, make_project):
    # TicketCreate's STORY_POINTS Literal only guards HTTP callers. Task 12
    # and Task 17 call create_ticket directly, and Task 22 calls it from a
    # Form(...) route with no Pydantic model in between — so the domain
    # constraint must also be enforced here, at the one choke point every
    # caller funnels through.
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with pytest.raises(HTTPException) as excinfo:
        create_ticket(session, project, owner, title="T", story_points=999)
    assert excinfo.value.status_code == 422


def test_create_ticket_rejects_title_over_255_chars_directly(session, make_user, make_project):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with pytest.raises(HTTPException) as excinfo:
        create_ticket(session, project, owner, title="x" * 256)
    assert excinfo.value.status_code == 422


def test_create_ticket_rejects_description_over_20000_chars_directly(
    session, make_user, make_project
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with pytest.raises(HTTPException) as excinfo:
        create_ticket(session, project, owner, title="T", description="x" * 20001)
    assert excinfo.value.status_code == 422


def test_ticket_number_gapless_after_rollback(session, make_user, make_project):
    # Proves the property the shared transaction buys: if the commit that
    # carries the ticket insert fails AFTER the counter has been
    # incremented, rolling back must undo the increment too, so the number
    # that almost leaked is the next one actually issued — not skipped.
    #
    # The hook fires on session "before_commit" and only raises when a
    # Ticket is actually pending in session.new. That targets the specific
    # commit that would persist the insert, rather than just "whichever
    # commit call happens first" — if allocate_ticket_number were ever
    # changed to commit the counter on its own (breaking the shared
    # transaction), that earlier commit has no Ticket pending yet and would
    # pass through untouched, and this test would then correctly observe a
    # gap instead of false-passing. Verified by temporarily adding such a
    # commit to allocate_ticket_number: this test fails (next_ticket_number
    # stays at 2 and a number is skipped) exactly as intended.
    owner = make_user(email="ada@example.com")
    project = make_project(owner)

    def _fail_when_ticket_pending(sess):
        if any(isinstance(obj, Ticket) for obj in sess.new):
            raise RuntimeError("forced failure after allocation, before commit")

    event.listen(session, "before_commit", _fail_when_ticket_pending)
    try:
        with pytest.raises(RuntimeError):
            create_ticket(session, project, owner, title="Doomed")
    finally:
        event.remove(session, "before_commit", _fail_when_ticket_pending)
    session.rollback()

    db_project = session.get(Project, project.id)
    assert db_project.next_ticket_number == 1, "rollback must restore the counter"

    ticket = create_ticket(session, project, owner, title="Survivor")
    assert ticket.ticket_number == 1, "the almost-leaked number must be reissued, not skipped"
