from datetime import date
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app.models import Priority, Sprint, SprintStatus, Ticket, TicketStatus, TicketType


@pytest.fixture
def list_world(make_user, make_project, add_member, engine):
    owner = make_user(email="ada@example.com", name="Ada")
    member = make_user(email="bob@example.com", name="Bob")
    project = make_project(owner)
    add_member(project, member)
    with Session(engine) as session:
        sprint = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship checkout",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        session.add(sprint)
        session.flush()
        rows = [
            # number, title, status, type, priority, assignee, sprint, due
            (
                1,
                "Card declines",
                TicketStatus.IN_PROGRESS,
                TicketType.BUG,
                Priority.URGENT,
                owner.id,
                sprint.id,
                date(2026, 9, 25),
            ),
            (
                2,
                "Refund flow",
                TicketStatus.BACKLOG,
                TicketType.STORY,
                Priority.LOW,
                member.id,
                None,
                None,
            ),
            (
                3,
                "Receipt email",
                TicketStatus.DONE,
                TicketType.TASK,
                Priority.HIGH,
                None,
                sprint.id,
                date(2026, 9, 22),
            ),
        ]
        for number, title, status, type_, priority, assignee, sprint_id, due in rows:
            session.add(
                Ticket(
                    ticket_number=number,
                    project_id=project.id,
                    title=title,
                    status=status,
                    type=type_,
                    priority=priority,
                    assignee_id=assignee,
                    sprint_id=sprint_id,
                    due_date=due,
                    creator_id=owner.id,
                )
            )
        session.commit()
        session.refresh(sprint)
    return SimpleNamespace(owner=owner, member=member, project=project, sprint=sprint)


def _titles(client, world, **params):
    response = client.get(f"/projects/{world.project.slug}/list", params=params)
    assert response.status_code == 200, response.text
    return [
        title
        for title in ("Card declines", "Refund flow", "Receipt email")
        if title in response.text
    ]


def test_list_shows_every_ticket_across_sprints_and_backlog(client, list_world, login_as):
    login_as(list_world.owner.email)
    assert _titles(client, list_world) == ["Card declines", "Refund flow", "Receipt email"]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"q": "refund"}, ["Refund flow"]),
        ({"status": "DONE"}, ["Receipt email"]),
        ({"type": "BUG"}, ["Card declines"]),
        ({"priority": "LOW"}, ["Refund flow"]),
        ({"assignee": "me"}, ["Card declines"]),
        ({"assignee": "none"}, ["Receipt email"]),
        ({"sprint": "none"}, ["Refund flow"]),
        ({"status": "DONE", "type": "BUG"}, []),
    ],
)
def test_list_filters_combine(client, list_world, login_as, params, expected):
    login_as(list_world.owner.email)
    assert _titles(client, list_world, **params) == expected


def test_list_filters_by_member_and_sprint_id(client, list_world, login_as):
    login_as(list_world.owner.email)
    assert _titles(client, list_world, assignee=list_world.member.id) == ["Refund flow"]
    assert _titles(client, list_world, sprint=list_world.sprint.id) == [
        "Card declines",
        "Receipt email",
    ]


def test_list_rejects_unknown_enum_filter(client, list_world, login_as):
    login_as(list_world.owner.email)
    response = client.get(f"/projects/{list_world.project.slug}/list", params={"status": "NOPE"})
    assert response.status_code == 422


def test_list_is_hidden_from_non_members(client, list_world, make_user, login_as):
    make_user(email="eve@example.com")
    login_as("eve@example.com")
    assert client.get(f"/projects/{list_world.project.slug}/list").status_code == 404


def _order(client, world, sort):
    page = client.get(f"/projects/{world.project.slug}/list", params={"sort": sort}).text
    titles = ["Card declines", "Refund flow", "Receipt email"]
    return sorted(titles, key=page.index)


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("number", ["Receipt email", "Refund flow", "Card declines"]),
        ("priority", ["Card declines", "Receipt email", "Refund flow"]),
        ("due", ["Receipt email", "Card declines", "Refund flow"]),
    ],
)
def test_list_sorts(client, list_world, login_as, sort, expected):
    login_as(list_world.owner.email)
    assert _order(client, list_world, sort) == expected


def test_list_rejects_unknown_sort(client, list_world, login_as):
    login_as(list_world.owner.email)
    response = client.get(f"/projects/{list_world.project.slug}/list", params={"sort": "title"})
    assert response.status_code == 422


def test_project_tabs_link_to_the_list_and_mark_it_current(client, list_world, login_as):
    login_as(list_world.owner.email)
    slug = list_world.project.slug

    page = client.get(f"/projects/{slug}/list").text

    assert (
        f'<a class="project-tab is-active" href="/projects/{slug}/list" aria-current="page">'
        "List</a>"
    ) in page
    assert f'href="/projects/{slug}/list"' in client.get(f"/projects/{slug}/backlog").text


def test_list_lines_fields_up_in_fixed_columns(client, list_world, login_as):
    login_as(list_world.owner.email)
    page = client.get(f"/projects/{list_world.project.slug}/list").text

    headers = ["Ticket", "Progress", "Category", "Priority", "Owner", "Schedule", "Due"]
    assert "".join(f'<th scope="col">{name}</th>' for name in headers) in page
    # A ticket with no assignee, sprint or due date still fills every column.
    assert "<td>Unassigned</td>" in page
    assert "<td>No sprint</td>" in page
    assert "<td>—</td>" in page
