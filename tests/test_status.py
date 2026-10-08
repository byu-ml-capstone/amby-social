import pytest

from app.models import Role


@pytest.fixture
def ticket_id(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    return client.post("/api/v1/tickets", json={"slug": project.slug, "title": "T"}).json()["id"]


def test_any_transition_is_allowed_in_both_directions(client, ticket_id):
    for target in ["IN_PROGRESS", "BACKLOG", "DONE", "SELECTED", "DONE"]:
        response = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": target})
        assert response.status_code == 200
        assert response.json()["status"] == target


def test_completed_at_is_stamped_and_cleared(client, ticket_id):
    done = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"}).json()
    assert done["completed_at"] is not None
    back = client.patch(
        f"/api/v1/tickets/{ticket_id}/status", json={"status": "IN_PROGRESS"}
    ).json()
    assert back["completed_at"] is None


def test_resolution_notes_survive_leaving_done(client, ticket_id):
    client.patch(
        f"/api/v1/tickets/{ticket_id}/status",
        json={"status": "DONE", "resolution_notes": "fixed in abc1234"},
    )
    back = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "BACKLOG"}).json()
    assert back["completed_at"] is None
    assert back["resolution_notes"] == "fixed in abc1234"


def test_completed_at_restamps_on_reentry_after_leaving_done(client, ticket_id):
    # R29: the two tests above cover "stamp on first DONE" and "clear on
    # leaving DONE" separately. Neither would catch a guard that stops
    # restamping on re-entry (e.g. an edit that stamps only when
    # ticket.completed_at is falsy-but-persisted, or that forgets to clear
    # it going the other way). Round-trip through DONE twice and require a
    # distinct, non-null timestamp both times.
    first = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"}).json()
    assert first["completed_at"] is not None
    client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "IN_PROGRESS"})
    second = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"}).json()
    assert second["completed_at"] is not None
    assert second["completed_at"] != first["completed_at"]


def test_unknown_status_is_422(client, ticket_id):
    assert (
        client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "SHIPPED"}).status_code
        == 422
    )


def test_non_member_cannot_transition(client, ticket_id, make_user, login_as):
    make_user(email="bob@example.com")
    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    # Spec: a non-member gets 403 on a write (not 404 -- that's the read rule).
    # See Task 25 fix round 1: this route used to resolve membership through
    # the read-flavoured lookup and leaked a 404 here instead.
    assert (
        client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"}).status_code
        == 403
    )


def test_plain_member_can_transition(client, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    member = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, member, role=Role.MEMBER)

    login_as("ada@example.com")
    ticket_id = client.post("/api/v1/tickets", json={"slug": project.slug, "title": "T"}).json()[
        "id"
    ]

    client.post("/api/v1/auth/logout")
    login_as("bob@example.com")
    response = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"})
    assert response.status_code == 200
    assert response.json()["status"] == "DONE"


def test_same_status_transition_is_idempotent(client, ticket_id):
    first = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"}).json()
    second = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "DONE"}).json()
    assert second["completed_at"] == first["completed_at"]

    client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "BACKLOG"})
    again = client.patch(f"/api/v1/tickets/{ticket_id}/status", json={"status": "BACKLOG"}).json()
    assert again["completed_at"] is None
