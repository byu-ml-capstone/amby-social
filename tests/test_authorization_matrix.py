"""V-8: every mutating endpoint enforces the permission matrix for every caller role.

The matrix is data (the `endpoints` table below), not thirty hand-written functions --
a missing row is a visible gap in the table, not a silently-absent test function.

Two things the naive version of this test would get wrong (Ruling: Task 8 already
made this mistake once):

- An HTML route's CSRF dependency runs alongside its role check. Without a valid
  CSRF token, a blocked write comes back 403 whether or not the role check ever
  ran, which would hide a missing/broken role check behind a CSRF failure. Every
  HTML call below carries a valid token for the *acting* user, so the status
  observed is always the authorization decision, never a CSRF or routing miss.
- A denied write can still mutate before it fails. `snapshot()` captures every
  place these endpoints could write (project row, ticket set, ticket fields,
  membership rows) and every denied cell asserts that snapshot is byte-for-byte
  unchanged.
"""

from datetime import date

import pytest
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import (
    Project,
    ProjectMember,
    Sprint,
    SprintStatus,
    SprintTicketHistory,
    Ticket,
    TicketStatus,
)

OWNER_ONLY = "owner_only"
ANY_MEMBER = "any_member"

JSON = "json"
FORM = "form"


def endpoints(world):
    """(kind, transport, method, url, kwargs) for every mutating route in the app.

    kind says who the spec allows: ANY_MEMBER for ticket create/edit/transition,
    OWNER_ONLY for project settings, membership, project delete, and ticket delete.
    transport says whether the call needs a CSRF token (FORM) or not (JSON api).

    Destructive rows (member removal, project delete) are ordered last -- deleting
    the project first would turn every later row's assertion into a 404 for the
    wrong reason.

    Project creation (POST /api/v1/projects, POST /projects) is deliberately not
    a row here: any authenticated user may create a project, so there is no
    membership dimension for this matrix to check.
    """
    project = world["project"]
    ticket_id = world["ticket_id"]
    other_user_id = world["member"].id
    slug = project.slug
    return [
        # -- JSON API: any project member may create/edit/transition tickets --
        (ANY_MEMBER, JSON, "post", "/api/v1/tickets", {"json": {"slug": slug, "title": "T"}}),
        (ANY_MEMBER, JSON, "patch", f"/api/v1/tickets/{ticket_id}", {"json": {"title": "Edited"}}),
        (
            ANY_MEMBER,
            JSON,
            "patch",
            f"/api/v1/tickets/{ticket_id}/status",
            {"json": {"status": "DONE"}},
        ),
        # -- HTML: same rules, reached through the htmx board instead of the API --
        (ANY_MEMBER, FORM, "post", f"/projects/{slug}/tickets", {"data": {"title": "Form T"}}),
        (
            ANY_MEMBER,
            FORM,
            "post",
            f"/projects/{slug}/tickets/1/status",
            {"data": {"status": "DONE"}},
        ),
        # -- OWNER_ONLY: sprint lifecycle mutations use separate valid setups --
        (
            OWNER_ONLY,
            FORM,
            "post",
            f"/projects/{world['sprint_web_create_project'].slug}/sprints",
            {
                "data": {
                    "name": "Created Sprint",
                    "goal": "Ship",
                    "start_date": "2026-09-21",
                    "end_date": "2026-09-28",
                }
            },
        ),
        (
            OWNER_ONLY,
            FORM,
            "post",
            f"/projects/{world['sprint_start_project'].slug}/sprints/{world['planning_sprint'].id}/tickets",
            {"data": {"ticket_ids": world["planning_ticket"].id}},
        ),
        (
            OWNER_ONLY,
            FORM,
            "post",
            f"/projects/{world['sprint_web_start_project'].slug}/sprints/{world['web_planning_sprint'].id}/start",
            {"data": {}},
        ),
        (
            OWNER_ONLY,
            JSON,
            "post",
            f"/api/v1/projects/{world['sprint_create_project'].slug}/sprints",
            {
                "json": {
                    "name": "Created Sprint",
                    "goal": "Ship",
                    "start_date": "2026-09-21",
                    "end_date": "2026-09-28",
                }
            },
        ),
        (
            OWNER_ONLY,
            JSON,
            "patch",
            f"/api/v1/sprints/{world['planning_sprint'].id}",
            {"json": {"status": "ACTIVE"}},
        ),
        (
            OWNER_ONLY,
            JSON,
            "post",
            f"/api/v1/sprints/{world['active_sprint'].id}/close",
            {"json": {"next_sprint_id": world["next_sprint"].id}},
        ),
        (
            OWNER_ONLY,
            FORM,
            "post",
            f"/projects/{world['sprint_web_close_project'].slug}/sprints/{world['web_active_sprint'].id}/close",
            {"data": {"next_sprint_id": world["web_next_sprint"].id}},
        ),
        # -- OWNER_ONLY: ticket delete, project settings, membership, project delete --
        (OWNER_ONLY, JSON, "delete", f"/api/v1/tickets/{ticket_id}", {}),
        (OWNER_ONLY, JSON, "patch", f"/api/v1/projects/{slug}", {"json": {"name": "Renamed"}}),
        (
            OWNER_ONLY,
            JSON,
            "post",
            f"/api/v1/projects/{slug}/members",
            {"json": {"email": "carol@example.com", "role": "MEMBER"}},
        ),
        (
            OWNER_ONLY,
            JSON,
            "patch",
            f"/api/v1/projects/{slug}/members/{other_user_id}",
            {"json": {"role": "OWNER"}},
        ),
        (OWNER_ONLY, JSON, "delete", f"/api/v1/projects/{slug}/members/{other_user_id}", {}),
        (OWNER_ONLY, JSON, "delete", f"/api/v1/projects/{slug}?confirm={slug}", {}),
    ]


@pytest.fixture
def world(client, session, make_user, make_project, add_member, login_as):
    owner = make_user(email="ada@example.com")
    member = make_user(email="bob@example.com")
    make_user(email="carol@example.com")
    outsider = make_user(email="dan@example.com")
    project = make_project(owner)
    sprint_create_project = make_project(owner, name="Sprint Create")
    sprint_web_create_project = make_project(owner, name="Sprint Web Create")
    sprint_start_project = make_project(owner, name="Sprint Start")
    sprint_close_project = make_project(owner, name="Sprint Close")
    sprint_web_start_project = make_project(owner, name="Sprint Web Start")
    sprint_web_close_project = make_project(owner, name="Sprint Web Close")
    for current_project in [
        project,
        sprint_create_project,
        sprint_start_project,
        sprint_close_project,
        sprint_web_start_project,
        sprint_web_close_project,
    ]:
        add_member(current_project, member)
    planning_sprint = Sprint(
        project_id=sprint_start_project.id,
        name="Planning Sprint",
        goal="Start me",
        status=SprintStatus.PLANNING,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    active_sprint = Sprint(
        project_id=sprint_close_project.id,
        name="Active Sprint",
        goal="Close me",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    next_sprint = Sprint(
        project_id=sprint_close_project.id,
        name="Next Sprint",
        goal="Receive tickets",
        status=SprintStatus.PLANNING,
        start_date=date(2026, 9, 29),
        end_date=date(2026, 10, 6),
    )
    web_planning_sprint = Sprint(
        project_id=sprint_web_start_project.id,
        name="Web Planning Sprint",
        goal="Start through web",
        status=SprintStatus.PLANNING,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    web_active_sprint = Sprint(
        project_id=sprint_web_close_project.id,
        name="Web Active Sprint",
        goal="Close through web",
        status=SprintStatus.ACTIVE,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 9, 28),
    )
    web_next_sprint = Sprint(
        project_id=sprint_web_close_project.id,
        name="Web Next Sprint",
        goal="Receive rollover",
        status=SprintStatus.PLANNING,
        start_date=date(2026, 9, 29),
        end_date=date(2026, 10, 6),
    )
    session.add_all(
        [
            planning_sprint,
            active_sprint,
            next_sprint,
            web_planning_sprint,
            web_active_sprint,
            web_next_sprint,
        ]
    )
    session.flush()
    sprint_ticket = Ticket(
        ticket_number=1,
        project_id=sprint_close_project.id,
        sprint_id=active_sprint.id,
        title="Sprint ticket",
        creator_id=owner.id,
    )
    planning_ticket = Ticket(
        ticket_number=1,
        project_id=sprint_start_project.id,
        title="Planning ticket",
        creator_id=owner.id,
    )
    session.add_all([sprint_ticket, planning_ticket])
    session.commit()
    login_as("ada@example.com")
    ticket_id = client.post("/api/v1/tickets", json={"slug": project.slug, "title": "Seed"}).json()[
        "id"
    ]
    client.post("/api/v1/auth/logout")
    return {
        "project": project,
        "ticket_id": ticket_id,
        "sprint_create_project": sprint_create_project,
        "sprint_web_create_project": sprint_web_create_project,
        "sprint_start_project": sprint_start_project,
        "sprint_close_project": sprint_close_project,
        "sprint_web_start_project": sprint_web_start_project,
        "sprint_web_close_project": sprint_web_close_project,
        "planning_sprint": planning_sprint,
        "web_planning_sprint": web_planning_sprint,
        "active_sprint": active_sprint,
        "next_sprint": next_sprint,
        "web_active_sprint": web_active_sprint,
        "web_next_sprint": web_next_sprint,
        "sprint_ticket": sprint_ticket,
        "planning_ticket": planning_ticket,
        "owner": owner,
        "member": member,
        "outsider": outsider,
    }


def snapshot(engine, project_slug, ticket_id):
    """Full state of everything a row in `endpoints()` could write to.

    One composite snapshot instead of a per-endpoint diff function: a denied call
    should touch nothing, so comparing this whole picture before/after is both
    simpler and stricter than only checking the field the row's handler happens
    to touch.
    """
    with Session(engine) as session:
        project = session.exec(select(Project).where(Project.slug == project_slug)).first()
        tickets = (
            session.exec(select(Ticket).where(Ticket.project_id == project.id)).all()
            if project
            else []
        )
        sprints = (
            session.exec(select(Sprint).where(Sprint.project_id == project.id)).all()
            if project
            else []
        )
        members = (
            session.exec(select(ProjectMember).where(ProjectMember.project_id == project.id)).all()
            if project
            else []
        )
        ticket = session.get(Ticket, ticket_id)
        histories = (
            session.exec(
                select(SprintTicketHistory).where(
                    SprintTicketHistory.sprint_id.in_([sprint.id for sprint in sprints])
                )
            ).all()
            if sprints
            else []
        )
        return {
            "project_exists": project is not None,
            "project_name": project.name if project else None,
            "ticket_ids": sorted(t.id for t in tickets),
            "ticket_title": ticket.title if ticket else None,
            "ticket_status": ticket.status if ticket else None,
            "ticket_sprints": sorted((t.id, t.sprint_id) for t in tickets),
            "ticket_lifecycle": sorted(
                (
                    t.id,
                    t.status,
                    t.story_points,
                    t.sprint_id,
                    t.first_sprint_entered_at,
                    t.delayed_days,
                    t.rollover_count,
                )
                for t in tickets
            ),
            "sprints": sorted(
                (s.id, s.status, s.committed_points, s.completed_points, s.closed_at)
                for s in sprints
            ),
            "history": sorted(
                (
                    h.id,
                    h.sprint_id,
                    h.ticket_id,
                    h.status_at_close,
                    h.story_points_at_close,
                    h.was_completed,
                    h.recorded_at,
                )
                for h in histories
            ),
            "members": sorted((m.user_id, m.role) for m in members),
        }


def test_snapshot_captures_sprint_lifecycle_state(engine, world):
    state = snapshot(engine, world["sprint_close_project"].slug, world["ticket_id"])

    assert state["sprints"] == sorted(
        [
            (world["active_sprint"].id, SprintStatus.ACTIVE, None, None, None),
            (world["next_sprint"].id, SprintStatus.PLANNING, None, None, None),
        ]
    )
    assert state["ticket_lifecycle"] == sorted(
        [
            (
                world["sprint_ticket"].id,
                TicketStatus.BACKLOG,
                None,
                world["active_sprint"].id,
                None,
                None,
                0,
            ),
        ]
    )
    assert state["history"] == []


def call(client, transport, method, url, kwargs, actor):
    """Issue the request. FORM (HTML) writes get a valid CSRF token for `actor` so
    the observed status is the authorization decision, not a CSRF rejection."""
    if transport == FORM:
        data = dict(kwargs.get("data", {}))
        data["_csrf"] = make_csrf_token(actor.id)
        kwargs = {**kwargs, "data": data}
    return getattr(client, method)(url, **kwargs)


def endpoint_project_slug(world, url):
    if "/projects/" in url:
        return url.split("/projects/", 1)[1].split("/", 1)[0]
    if world["planning_sprint"].id in url:
        return world["sprint_start_project"].slug
    if world["active_sprint"].id in url:
        return world["sprint_close_project"].slug
    return world["project"].slug


def test_owner_is_allowed_everywhere(client, world, engine, login_as):
    login_as("ada@example.com")
    for _, transport, method, url, kwargs in endpoints(world):
        response = call(client, transport, method, url, kwargs, world["owner"])
        assert response.status_code < 400, f"{method} {url} -> {response.status_code}"


def test_member_is_blocked_only_on_owner_actions(client, world, engine, login_as):
    login_as("bob@example.com")
    for kind, transport, method, url, kwargs in endpoints(world):
        if kind == OWNER_ONLY:
            project_slug = endpoint_project_slug(world, url)
            before = snapshot(engine, project_slug, world["ticket_id"])
            response = call(client, transport, method, url, kwargs, world["member"])
            assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
            after = snapshot(engine, project_slug, world["ticket_id"])
            assert after == before, f"side effect on denied {method} {url}"
        else:
            response = call(client, transport, method, url, kwargs, world["member"])
            assert response.status_code < 400, f"{method} {url} -> {response.status_code}"


def test_outsider_writes_are_403(client, world, engine, login_as):
    login_as("dan@example.com")
    for _, transport, method, url, kwargs in endpoints(world):
        project_slug = endpoint_project_slug(world, url)
        before = snapshot(engine, project_slug, world["ticket_id"])
        response = call(client, transport, method, url, kwargs, world["outsider"])
        # Spec (cs482_slice1_design.md:140): a non-member gets 403 on every write,
        # full stop -- so project/ticket existence never leaks through a 404 vs.
        # 403 distinction. A single exact expected value here (not a permissive
        # tuple) is what makes this cell able to fail when a route's dependency
        # gets this wrong.
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        after = snapshot(engine, project_slug, world["ticket_id"])
        assert after == before, f"side effect on denied {method} {url}"


def test_outsider_reads_are_404(client, world, login_as):
    login_as("dan@example.com")
    slug = world["project"].slug
    for url in [
        f"/api/v1/projects/{slug}",
        f"/api/v1/projects/{slug}/members",
        f"/api/v1/projects/{slug}/tickets",
        f"/api/v1/tickets/{world['ticket_id']}",
        f"/projects/{slug}",
    ]:
        assert client.get(url).status_code == 404, url


def test_anonymous_api_calls_are_401(client, world):
    slug = world["project"].slug
    assert client.get(f"/api/v1/projects/{slug}").status_code == 401
    assert client.post("/api/v1/tickets", json={"slug": slug, "title": "T"}).status_code == 401


def test_anonymous_html_writes_are_401(client, world):
    """Ruling: cover the HTML mutating routes too, not only /api/v1/*. Both reach
    current_user the same way the JSON routes do, so an anonymous caller should
    get the same 401 (not a redirect) rather than a special HTML-only path."""
    slug = world["project"].slug
    assert client.post(f"/projects/{slug}/tickets", data={"title": "T"}).status_code == 401
    assert (
        client.post(f"/projects/{slug}/tickets/1/status", data={"status": "DONE"}).status_code
        == 401
    )
