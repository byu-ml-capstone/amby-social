import re
from datetime import date

import pytest
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import Sprint, SprintStatus, Ticket


@pytest.fixture
def active_sprint(make_user, make_project, engine):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    with Session(engine) as session:
        sprint = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship it",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        session.add(sprint)
        session.commit()
        session.refresh(sprint)
    return owner, project, sprint


def test_modal_redirects_after_a_successful_htmx_submission(client, active_sprint, login_as):
    owner, project, sprint = active_sprint
    login_as("ada@example.com")
    page = client.get(f"/projects/{project.slug}").text
    assert 'hx-swap="none"' in page
    assert f'hx-post="/projects/{project.slug}/tickets"' in page
    assert f'<option value="{sprint.id}" selected>' in page
    assert ">Backlog</option>" not in page


def test_web_ticket_create_accepts_due_date(client, active_sprint, engine, login_as):
    owner, project, sprint = active_sprint
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}").text
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={
            "_csrf": make_csrf_token(owner.id),
            "title": "Deadline",
            "type": "TASK",
            "sprint_id": sprint.id,
            "due_date": "2026-09-30",
        },
        follow_redirects=False,
    )

    assert 'type="date" name="due_date"' in page
    assert response.status_code == 303
    with Session(engine) as session:
        ticket = session.exec(select(Ticket).where(Ticket.title == "Deadline")).one()
    assert ticket.due_date == date(2026, 9, 30)


def test_submitting_the_modal_redirects_after_an_htmx_success(
    client, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={
            "title": "Card declines on retry",
            "type": "BUG",
            "description": "Happens on the second attempt",
            "_csrf": make_csrf_token(owner.id),
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/projects/{project.slug}"
    assert response.text == ""


def test_non_htmx_ticket_submission_redirects_to_the_project(
    client, make_user, make_project, login_as
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as(owner.email)

    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "Card declines on retry", "type": "BUG", "_csrf": make_csrf_token(owner.id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/projects/{project.slug}"


def test_the_created_ticket_appears_on_the_board(client, active_sprint, login_as):
    owner, project, sprint = active_sprint
    login_as("ada@example.com")
    client.post(
        f"/projects/{project.slug}/tickets",
        data={
            "title": "Visible later",
            "type": "TASK",
            "sprint_id": sprint.id,
            "_csrf": make_csrf_token(owner.id),
        },
    )
    page = client.get(f"/projects/{project.slug}").text
    backlog = page.split('id="column-BACKLOG"')[1].split("</section>")[0]
    assert "Visible later" in backlog


def test_empty_title_is_422(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "   ", "type": "TASK", "_csrf": make_csrf_token(owner.id)},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/plain")
    assert "<html" not in response.text


@pytest.mark.parametrize("sprint_status", [SprintStatus.ACTIVE, SprintStatus.CLOSED])
def test_ticket_form_rejects_foreign_or_closed_sprint_destinations(
    client, make_user, make_project, engine, login_as, sprint_status
):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    foreign_project = make_project(owner, name="Other project")
    with Session(engine) as session:
        sprint = Sprint(
            project_id=foreign_project.id if sprint_status is SprintStatus.ACTIVE else project.id,
            name="Unavailable sprint",
            goal="Not available",
            status=sprint_status,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        session.add(sprint)
        session.commit()
        session.refresh(sprint)
    login_as(owner.email)

    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={
            "title": "Invalid destination",
            "type": "TASK",
            "sprint_id": sprint.id,
            "_csrf": make_csrf_token(owner.id),
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/plain")
    with Session(engine) as session:
        assert session.exec(select(Ticket).where(Ticket.project_id == project.id)).first() is None


def test_non_member_submission_is_403(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "Sneaky", "type": "TASK", "_csrf": make_csrf_token(bob.id)},
    )
    assert response.status_code == 403


def test_non_member_submission_creates_nothing(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    bob = make_user(email="bob@example.com")
    project = make_project(owner)
    login_as("bob@example.com")
    client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "Sneaky", "type": "TASK", "_csrf": make_csrf_token(bob.id)},
    )
    login_as("ada@example.com")
    page = client.get(f"/projects/{project.slug}").text
    assert "Sneaky" not in page


def test_missing_csrf_is_403_and_creates_nothing(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "No token", "type": "TASK"},
    )
    assert response.status_code == 403
    page = client.get(f"/projects/{project.slug}").text
    assert "No token" not in page


def test_tampered_csrf_is_403_and_creates_nothing(client, make_user, make_project, login_as):
    owner = make_user(email="ada@example.com")
    project = make_project(owner)
    login_as("ada@example.com")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "Bad token", "type": "TASK", "_csrf": "tampered.token.value"},
    )
    assert response.status_code == 403
    page = client.get(f"/projects/{project.slug}").text
    assert "Bad token" not in page


def test_five_interactions_or_fewer(client, active_sprint, login_as):
    """Open modal, title, type, description, submit — the form must ask for nothing else."""
    owner, project, _ = active_sprint
    login_as("ada@example.com")
    page = client.get(f"/projects/{project.slug}").text
    modal = page.split('id="ticket-modal"')[1].split("</form>")[0]
    required = re.findall(r"<(?:input|select|textarea)[^>]*\brequired\b[^>]*>", modal)
    assert len(required) <= 2, "only title and type may be required"


def test_backlog_without_open_sprints_hides_new_ticket_but_keeps_legacy_post(
    client, make_user, make_project, add_member, engine, login_as
):
    owner = make_user(email="ada@example.com")
    member = make_user(email="bob@example.com")
    project = make_project(owner)
    add_member(project, member)
    login_as(member.email)

    page = client.get(f"/projects/{project.slug}/backlog")
    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={"title": "Backlog ticket", "type": "TASK", "_csrf": make_csrf_token(member.id)},
        follow_redirects=False,
    )
    with Session(engine) as session:
        ticket = session.exec(
            select(Ticket).where(Ticket.project_id == project.id, Ticket.ticket_number == 1)
        ).one()

    assert "New ticket" not in page.text
    assert ">Backlog</option>" not in page.text
    assert response.status_code == 303
    assert ticket.sprint_id is None


def test_backlog_modal_lists_only_this_projects_open_sprints(
    client, active_sprint, make_project, engine, login_as
):
    owner, project, active = active_sprint
    other_project = make_project(owner, name="Other project")
    with Session(engine) as session:
        planning = Sprint(
            project_id=project.id,
            name="Next sprint",
            goal="Next work",
            status=SprintStatus.PLANNING,
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        foreign = Sprint(
            project_id=other_project.id,
            name="Foreign sprint",
            goal="Not this project",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        session.add_all([planning, foreign])
        session.commit()
        session.refresh(planning)
    login_as(owner.email)

    page = client.get(f"/projects/{project.slug}/backlog").text

    assert ">Backlog</option>" not in page
    assert f'<option value="{active.id}">Sprint 1</option>' in page
    assert f'<option value="{planning.id}">Next sprint</option>' in page
    assert "Foreign sprint" not in page


def test_backlog_sprint_destination_uses_an_htmx_redirect(client, active_sprint, engine, login_as):
    owner, project, _ = active_sprint
    with Session(engine) as session:
        planning = Sprint(
            project_id=project.id,
            name="Next sprint",
            goal="Next work",
            status=SprintStatus.PLANNING,
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        session.add(planning)
        session.commit()
        session.refresh(planning)
    login_as(owner.email)

    response = client.post(
        f"/projects/{project.slug}/tickets",
        data={
            "title": "Planned from backlog",
            "type": "TASK",
            "sprint_id": planning.id,
            "_csrf": make_csrf_token(owner.id),
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/projects/{project.slug}"
    assert response.text == ""
    with Session(engine) as session:
        ticket = session.exec(select(Ticket).where(Ticket.title == "Planned from backlog")).one()
    assert ticket.sprint_id == planning.id
