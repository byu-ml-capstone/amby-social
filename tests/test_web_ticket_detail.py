from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import (
    Priority,
    Project,
    Sprint,
    SprintStatus,
    Ticket,
    TicketStatus,
    TicketType,
    WebhookType,
)
from app.notifications import EVENT_TICKET_DONE
from app.services import set_chat_webhook


@pytest.fixture
def ticket_world(make_user, make_project, add_member, engine):
    owner = make_user(email="ada@example.com", name="Ada Lovelace")
    member = make_user(email="bob@example.com", name="Bob Builder")
    outsider = make_user(email="eve@example.com", name="Eve Outside")
    project = make_project(owner)
    other_project = make_project(outsider, name="Other Project")
    add_member(project, member)
    with Session(engine) as session:
        active = Sprint(
            project_id=project.id,
            name="Sprint 1",
            goal="Ship details",
            status=SprintStatus.ACTIVE,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 28),
        )
        planning = Sprint(
            project_id=project.id,
            name="Sprint 2",
            goal="Next work",
            status=SprintStatus.PLANNING,
            start_date=date(2026, 9, 29),
            end_date=date(2026, 10, 6),
        )
        closed = Sprint(
            project_id=project.id,
            name="Closed sprint",
            goal="Past work",
            status=SprintStatus.CLOSED,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 8),
        )
        session.add_all([active, planning, closed])
        session.flush()
        ticket = Ticket(
            ticket_number=1,
            project_id=project.id,
            title="Unsafe details",
            description="**Safe** <script>alert('xss')</script>",
            type=TicketType.BUG,
            priority=Priority.HIGH,
            status=TicketStatus.SELECTED,
            sprint_id=active.id,
            creator_id=owner.id,
            assignee_id=owner.id,
        )
        session.add(ticket)
        session.commit()
        return SimpleNamespace(
            owner=owner,
            member=member,
            outsider=outsider,
            project=project,
            other_project=other_project,
            active_id=active.id,
            planning_id=planning.id,
            closed_id=closed.id,
            ticket_id=ticket.id,
        )


def _ticket(session, world):
    return session.exec(
        select(Ticket).where(Ticket.project_id == world.project.id, Ticket.ticket_number == 1)
    ).one()


def test_detail_fragment_renders_sanitized_markdown_and_project_members(
    client, ticket_world, login_as
):
    login_as(ticket_world.owner.email)

    response = client.get(
        f"/projects/{ticket_world.project.slug}/tickets/1", headers={"HX-Request": "true"}
    )

    assert response.status_code == 200, response.text
    assert "<html" not in response.text
    assert "<strong>Safe</strong>" in response.text
    assert "<script>" not in response.text
    assert "data-description-view>" in response.text
    assert "data-description-fields hidden>" in response.text
    assert 'data-description-edit aria-label="Edit details" title="Edit details"' in response.text
    assert "data-description-cancel>Cancel</button>" in response.text
    assert '<svg aria-hidden="true" focusable="false"' in response.text
    assert f'value="{ticket_world.owner.id}"' in response.text
    assert f'value="{ticket_world.member.id}"' in response.text
    assert ticket_world.outsider.id not in response.text
    assert f'value="{ticket_world.active_id}"' in response.text
    assert f'value="{ticket_world.planning_id}"' in response.text
    assert ticket_world.closed_id not in response.text

    standalone = client.get(f"/projects/{ticket_world.project.slug}/tickets/1")
    assert standalone.status_code == 200
    assert 'class="ticket-detail-page"' in standalone.text
    assert "sketch-ticket-detail" not in standalone.text
    assert 'class="project-board sketch-board"' not in standalone.text


def test_owner_updates_every_ticket_detail_field_and_requests_card_refresh(
    client, ticket_world, login_as, session
):
    login_as(ticket_world.owner.email)

    response = client.post(
        f"/projects/{ticket_world.project.slug}/tickets/1",
        data={
            "title": "Updated title",
            "description": "**Updated**",
            "type": "STORY",
            "priority": "URGENT",
            "story_points": "8",
            "assignee_id": ticket_world.member.id,
            "status": "DONE",
            "resolution_notes": "Verified",
            "sprint_id": ticket_world.planning_id,
            "_csrf": make_csrf_token(ticket_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.text
    assert "Updated title" in response.text
    assert response.headers["HX-Refresh"] == "true"
    assert "HX-Trigger" not in response.headers
    ticket = _ticket(session, ticket_world)
    assert (
        ticket.title,
        ticket.description,
        ticket.type,
        ticket.priority,
        ticket.story_points,
        ticket.assignee_id,
        ticket.status,
        ticket.resolution_notes,
        ticket.sprint_id,
    ) == (
        "Updated title",
        "**Updated**",
        TicketType.STORY,
        Priority.URGENT,
        8,
        ticket_world.member.id,
        TicketStatus.DONE,
        "Verified",
        ticket_world.planning_id,
    )


def test_detail_due_date_input_persists_and_clears(client, ticket_world, login_as, session):
    login_as(ticket_world.owner.email)
    url = f"/projects/{ticket_world.project.slug}/tickets/1"
    fields = {
        "title": "Unsafe details",
        "description": "Text",
        "type": "BUG",
        "priority": "HIGH",
        "status": "SELECTED",
        "_csrf": make_csrf_token(ticket_world.owner.id),
    }

    form = client.get(url, headers={"HX-Request": "true"})
    saved = client.post(
        url, data={**fields, "due_date": "2026-09-30"}, headers={"HX-Request": "true"}
    )
    session.expire_all()

    assert 'type="date" name="due_date"' in form.text
    assert saved.status_code == 200
    assert 'value="2026-09-30"' in saved.text
    assert _ticket(session, ticket_world).due_date == date(2026, 9, 30)

    cleared = client.post(url, data={**fields, "due_date": ""}, headers={"HX-Request": "true"})
    session.expire_all()

    assert cleared.status_code == 200
    assert _ticket(session, ticket_world).due_date is None


def test_member_can_update_ticket_details(client, ticket_world, login_as, session):
    login_as(ticket_world.member.email)

    response = client.post(
        f"/projects/{ticket_world.project.slug}/tickets/1",
        data={
            "title": "Member edit",
            "description": "Text",
            "type": "TASK",
            "priority": "LOW",
            "status": "IN_PROGRESS",
            "_csrf": make_csrf_token(ticket_world.member.id),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.text
    ticket = _ticket(session, ticket_world)
    assert ticket.title == "Member edit"
    assert ticket.status == TicketStatus.IN_PROGRESS


@pytest.mark.parametrize("field", ["assignee_id", "sprint_id"])
def test_detail_rejects_foreign_assignee_and_closed_sprint(
    client, ticket_world, login_as, session, field
):
    login_as(ticket_world.owner.email)
    value = ticket_world.outsider.id if field == "assignee_id" else ticket_world.closed_id
    response = client.post(
        f"/projects/{ticket_world.project.slug}/tickets/1",
        data={
            "title": "Unsafe details",
            "description": "Text",
            "type": "BUG",
            "priority": "HIGH",
            "status": "SELECTED",
            field: value,
            "_csrf": make_csrf_token(ticket_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 422
    assert "project" in response.text
    assert _ticket(session, ticket_world).assignee_id == ticket_world.owner.id
    assert _ticket(session, ticket_world).sprint_id == ticket_world.active_id


def test_detail_validation_errors_and_csrf_do_not_mutate_ticket(
    client, ticket_world, login_as, session
):
    login_as(ticket_world.owner.email)
    invalid = client.post(
        f"/projects/{ticket_world.project.slug}/tickets/1",
        data={
            "title": "Unsafe details",
            "description": "Text",
            "type": "BUG",
            "priority": "HIGH",
            "story_points": "4",
            "status": "SELECTED",
            "_csrf": make_csrf_token(ticket_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )
    missing_csrf = client.post(
        f"/projects/{ticket_world.project.slug}/tickets/1",
        data={"title": "No CSRF", "status": "DONE"},
    )

    assert invalid.status_code == 422
    assert "story_points" in invalid.text
    assert "data-description-view hidden>" in invalid.text
    assert "data-description-fields>" in invalid.text
    assert ">Text</textarea>" in invalid.text
    assert (
        'data-description-edit aria-label="Edit details" title="Edit details" hidden>'
        in invalid.text
    )
    assert missing_csrf.status_code == 403
    assert _ticket(session, ticket_world).title == "Unsafe details"


def test_compact_card_opens_the_detail_panel_and_refreshes_itself(client, ticket_world, login_as):
    login_as(ticket_world.owner.email)

    page = client.get(f"/projects/{ticket_world.project.slug}")
    card = client.get(f"/projects/{ticket_world.project.slug}/tickets/1?card=true")

    card_html = page.text.split('id="ticket-', 1)[1].split("</article>", 1)[0]
    assert "<strong>Safe</strong>" not in card_html
    assert f'hx-get="/projects/{ticket_world.project.slug}/tickets/1"' in page.text
    assert 'hx-target="#ticket-detail-root"' in page.text
    assert f"refresh-ticket-card-{ticket_world.ticket_id}" in card.text


def test_ticket_save_returns_a_textual_status(client, ticket_world, login_as):
    login_as(ticket_world.owner.email)

    response = client.post(
        f"/projects/{ticket_world.project.slug}/tickets/1",
        data={
            "title": "Unsafe details",
            "description": "Text",
            "type": "BUG",
            "priority": "HIGH",
            "status": "SELECTED",
            "_csrf": make_csrf_token(ticket_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert '<p class="form-status" role="status">Ticket saved.</p>' in response.text
    assert "Updated:" in response.text
    assert "status" in response.text
    assert response.headers["HX-Refresh"] == "true"
    assert "HX-Trigger" not in response.headers


def test_ticket_drawer_resolves_the_current_card_button_after_a_refresh():
    board = Path("app/templates/board.html").read_text()
    card = Path("app/templates/partials/ticket_card.html").read_text()

    assert "ticketOpener = '{{ ticket.id }}'" in card
    assert (
        "document.getElementById(`ticket-${ticketOpener}`)?.querySelector('button')?.focus()"
        in board
    )


def test_outsider_cannot_read_ticket_detail(client, ticket_world, login_as):
    login_as(ticket_world.outsider.email)

    response = client.get(f"/projects/{ticket_world.project.slug}/tickets/1")

    assert response.status_code == 404


def test_detail_done_update_queues_one_done_notification_and_failed_update_queues_none(
    client, ticket_world, engine, login_as, monkeypatch
):
    sent = []
    with Session(engine) as session:
        project = session.get(Project, ticket_world.project.id)
        set_chat_webhook(session, project, WebhookType.SLACK, "https://example.com/hook")
    monkeypatch.setattr("app.notifications.dispatch", lambda _, __, payload: sent.append(payload))
    login_as(ticket_world.owner.email)

    done = client.post(
        f"/projects/{ticket_world.project.slug}/tickets/1",
        data={
            "title": "Unsafe details",
            "description": "Text",
            "type": "BUG",
            "priority": "HIGH",
            "status": "DONE",
            "_csrf": make_csrf_token(ticket_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )
    failed = client.post(
        f"/projects/{ticket_world.project.slug}/tickets/1",
        data={
            "title": "Unsafe details",
            "description": "Text",
            "type": "BUG",
            "priority": "HIGH",
            "status": "DONE",
            "assignee_id": ticket_world.outsider.id,
            "_csrf": make_csrf_token(ticket_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )

    assert done.status_code == 200
    assert failed.status_code == 422
    assert [payload["event"] for payload in sent] == [EVENT_TICKET_DONE]
