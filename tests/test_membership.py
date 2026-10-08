from sqlmodel import Session

from app.models import ProjectMember, Role


def test_owner_adds_member_by_email(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/api/v1/projects/{project.slug}/members",
        json={"email": "bob@example.com", "role": "MEMBER"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == "bob@example.com"


def test_adding_unknown_email_is_404_and_duplicate_is_409(
    client, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert (
        client.post(
            f"/api/v1/projects/{project.slug}/members",
            json={"email": "ghost@example.com", "role": "MEMBER"},
        ).status_code
        == 404
    )
    body = {"email": "bob@example.com", "role": "MEMBER"}
    client.post(f"/api/v1/projects/{project.slug}/members", json=body)
    assert client.post(f"/api/v1/projects/{project.slug}/members", json=body).status_code == 409


def test_member_cannot_manage_membership(client, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, bob)
    login_as("bob@example.com")
    assert client.get(f"/api/v1/projects/{project.slug}/members").status_code == 200
    assert (
        client.post(
            f"/api/v1/projects/{project.slug}/members",
            json={"email": "ada@example.com", "role": "MEMBER"},
        ).status_code
        == 403
    )


def test_last_owner_cannot_be_demoted_or_removed(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    assert (
        client.patch(
            f"/api/v1/projects/{project.slug}/members/{owner.id}", json={"role": "MEMBER"}
        ).status_code
        == 409
    )
    assert client.delete(f"/api/v1/projects/{project.slug}/members/{owner.id}").status_code == 409


def test_removing_a_member_nulls_their_assigned_tickets(
    client, make_user, make_project, add_member, login_as, session
):
    from app.models import Ticket, TicketType

    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, bob)
    ticket = Ticket(
        ticket_number=1,
        project_id=project.id,
        title="t",
        type=TicketType.TASK,
        creator_id=owner.id,
        assignee_id=bob.id,
    )
    session.add(ticket)
    session.commit()
    login_as("ada@example.com")
    assert client.delete(f"/api/v1/projects/{project.slug}/members/{bob.id}").status_code == 204
    session.expire_all()
    assert session.get(Ticket, ticket.id).assignee_id is None


def test_second_owner_can_be_demoted_then_removed(
    client, make_user, make_project, add_member, login_as
):
    # With two owners, neither guard should fire: demoting one still leaves
    # one OWNER, and removing that now-MEMBER user leaves the project intact.
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, bob, role=Role.OWNER)
    login_as("ada@example.com")
    response = client.patch(
        f"/api/v1/projects/{project.slug}/members/{bob.id}", json={"role": "MEMBER"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "MEMBER"
    assert client.delete(f"/api/v1/projects/{project.slug}/members/{bob.id}").status_code == 204


def test_list_members_returns_every_member_not_just_caller(
    client, make_user, make_project, add_member, login_as
):
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, bob)
    login_as("bob@example.com")
    response = client.get(f"/api/v1/projects/{project.slug}/members")
    assert response.status_code == 200
    emails = {m["email"] for m in response.json()}
    assert emails == {"ada@example.com", "bob@example.com"}


def test_concurrent_member_add_race_returns_409_not_500(
    client, make_user, make_project, login_as, engine, monkeypatch
):
    # Same shape as test_concurrent_project_creation_race_returns_409_not_500
    # and test_concurrent_registration_race_returns_409_not_500 (Ruling R16),
    # but the hook position differs from both: neither "hook the first
    # session.exec()" (Task 8's project-slug race) nor "hook the function
    # that runs after the pre-check" (Task 6's hash_password race) lands in
    # the right place here.
    #
    # add_member's dependency, project_owner, issues its own two exec() calls
    # before the route body runs at all (project lookup, then the caller's
    # own membership lookup via _load). The route body then makes exec() call
    # #3 (the target user's email lookup) and exec() call #4 (the actual
    # duplicate-membership pre-check) before session.add()/session.commit().
    # So the racer has to fire after call #4, not call #1 — verified by
    # instrumenting every exec() call in this handler and counting them
    # (5 calls total on the success path: 2 from project_owner, 2 from the
    # route's own lookups, 1 from _members() building the response).
    # Firing on call #1 (as in the naive version of this test) lets the
    # racer's row land before the route's own pre-check ever reads, so the
    # ordinary pre-check branch rejects the request with its own 409 and the
    # except IntegrityError handler this test claims to cover never runs.
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("ada@example.com")

    original_exec = Session.exec
    state = {"count": 0, "triggered": False}

    def exec_then_insert_racer(self, *args, **kwargs):
        result = original_exec(self, *args, **kwargs)
        state["count"] += 1
        if state["count"] == 4 and not state["triggered"]:
            state["triggered"] = True
            with Session(engine) as racer_session:
                racer_session.add(
                    ProjectMember(project_id=project.id, user_id=bob.id, role=Role.MEMBER)
                )
                racer_session.commit()
        return result

    monkeypatch.setattr(Session, "exec", exec_then_insert_racer)

    response = client.post(
        f"/api/v1/projects/{project.slug}/members",
        json={"email": "bob@example.com", "role": "MEMBER"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Already a member"


def test_non_owner_gets_403_on_patch_and_delete(
    client, make_user, make_project, add_member, login_as
):
    # POST's 403 is covered by test_member_cannot_manage_membership; PATCH
    # and DELETE share the same project_owner dependency, so cover them too.
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, bob)
    login_as("bob@example.com")
    assert (
        client.patch(
            f"/api/v1/projects/{project.slug}/members/{owner.id}", json={"role": "MEMBER"}
        ).status_code
        == 403
    )
    assert client.delete(f"/api/v1/projects/{project.slug}/members/{owner.id}").status_code == 403
