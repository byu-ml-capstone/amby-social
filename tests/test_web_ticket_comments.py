from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app.auth import make_csrf_token
from app.models import Project, Ticket, TicketComment, WebhookType
from app.services import delete_project, set_chat_webhook


@pytest.fixture
def comment_world(make_user, make_project, add_member, engine):
    owner = make_user(email="ada@example.com", name="Ada Lovelace")
    member = make_user(email="bob@example.com", name="Bob Builder")
    project = make_project(owner)
    add_member(project, member)
    with Session(engine) as session:
        stored_project = session.get(Project, project.id)
        set_chat_webhook(
            session, stored_project, WebhookType.SLACK, "https://example.com/hook"
        )
        ticket = Ticket(
            ticket_number=1,
            project_id=project.id,
            title="Campaign review",
            creator_id=owner.id,
        )
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
    return SimpleNamespace(owner=owner, member=member, project=project, ticket=ticket)


def test_member_adds_an_escaped_comment_with_mentions_and_webhook(
    client, comment_world, login_as, monkeypatch, session
):
    sent = []
    monkeypatch.setattr("app.notifications.dispatch", lambda _, __, payload: sent.append(payload))
    login_as(comment_world.owner.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments",
        data={
            "body": "Please review <script>alert(1)</script>",
            "mention_ids": [comment_world.member.id],
            "_csrf": make_csrf_token(comment_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.text
    assert "Please review &lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "Ada Lovelace" in response.text
    assert "@Bob Builder" in response.text
    comment = session.exec(select(TicketComment)).one()
    assert comment.mentioned_user_ids == [comment_world.member.id]
    assert sent[0]["event"] == "COMMENT_MENTION"
    assert sent[0]["mentioned_names"] == ["Bob Builder"]


def test_ticket_detail_includes_the_comment_composer_and_project_members(
    client, comment_world, login_as
):
    login_as(comment_world.owner.email)

    response = client.get(
        f"/projects/{comment_world.project.slug}/tickets/1",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'id="ticket-comments"' in response.text
    assert "No comments yet. Start the conversation." in response.text
    assert 'data-mention-input aria-autocomplete="list"' in response.text
    assert 'role="listbox"' in response.text
    assert f'data-mention-id="{comment_world.member.id}"' in response.text
    assert "@Bob Builder" in response.text
    assert "No details provided." in response.text
    assert 'data-description-fields hidden>' in response.text

    page = client.get(f"/projects/{comment_world.project.slug}/tickets/1")
    assert '<script src="/static/app.js?v=' in page.text


def test_comment_actions_are_positioned_at_the_card_top_right(client):
    css = client.get("/static/app.css").text

    comment_css = css.split(".ticket-comment {", 1)[1].split("}", 1)[0]
    actions_css = css.rsplit(".ticket-comment-actions {", 1)[1].split("}", 1)[0]
    assert "position: relative" in comment_css
    assert "position: absolute" in actions_css
    assert "right: var(--space-3)" in actions_css
    assert "top: var(--space-2)" in actions_css


def test_comment_time_exposes_utc_for_browser_localization(
    client, comment_world, login_as, session
):
    session.add(
        TicketComment(
            ticket_id=comment_world.ticket.id,
            author_id=comment_world.owner.id,
            body="Check the time",
        )
    )
    session.commit()
    login_as(comment_world.owner.email)

    response = client.get(
        f"/projects/{comment_world.project.slug}/tickets/1",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "data-local-time" in response.text
    assert 'Z">' in response.text


def test_ticket_detail_uses_department_neutral_labels(client, comment_world, login_as):
    login_as(comment_world.owner.email)

    response = client.get(
        f"/projects/{comment_world.project.slug}/tickets/1",
        headers={"HX-Request": "true"},
    )

    for label in (
        "Work item title",
        "Details",
        "Category",
        "Effort",
        "Owner",
        "Progress",
        "Schedule",
        "Outcome notes",
        "Initiative",
        "Issue",
        "Review request",
    ):
        assert label in response.text


def test_comment_author_edits_the_comment_and_new_mentions_notify(
    client, comment_world, login_as, monkeypatch, session
):
    comment = TicketComment(
        ticket_id=comment_world.ticket.id,
        author_id=comment_world.member.id,
        body="Original",
    )
    session.add(comment)
    session.commit()
    sent = []
    monkeypatch.setattr("app.notifications.dispatch", lambda _, __, payload: sent.append(payload))
    login_as(comment_world.member.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments/{comment.id}/edit",
        data={
            "body": "Updated comment",
            "mention_ids": [comment_world.owner.id],
            "_csrf": make_csrf_token(comment_world.member.id),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200, response.text
    assert "Updated comment" in response.text
    assert "Edited" in response.text
    session.expire_all()
    stored = session.get(TicketComment, comment.id)
    assert stored.body == "Updated comment"
    assert stored.updated_at is not None
    assert stored.mentioned_user_ids == [comment_world.owner.id]
    assert sent[0]["mentioned_names"] == ["Ada Lovelace"]


def test_comment_permissions_render_accessible_icon_actions(
    client, comment_world, login_as, session
):
    comment = TicketComment(
        ticket_id=comment_world.ticket.id,
        author_id=comment_world.member.id,
        body="Owned by Bob",
    )
    session.add(comment)
    session.commit()

    login_as(comment_world.member.email)
    author_view = client.get(
        f"/projects/{comment_world.project.slug}/tickets/1",
        headers={"HX-Request": "true"},
    )
    login_as(comment_world.owner.email)
    owner_view = client.get(
        f"/projects/{comment_world.project.slug}/tickets/1",
        headers={"HX-Request": "true"},
    )

    assert 'aria-label="Edit comment" title="Edit comment"' in author_view.text
    assert 'aria-label="Delete comment" title="Delete comment"' in author_view.text
    assert author_view.text.count('<svg aria-hidden="true" focusable="false"') >= 3
    assert 'hx-confirm="Delete this comment?"' in author_view.text
    assert 'aria-label="Edit comment"' not in owner_view.text
    assert 'aria-label="Delete comment" title="Delete comment"' in owner_view.text


def test_member_cannot_edit_another_members_comment(client, comment_world, login_as, session):
    comment = TicketComment(
        ticket_id=comment_world.ticket.id,
        author_id=comment_world.owner.id,
        body="Owner comment",
    )
    session.add(comment)
    session.commit()
    login_as(comment_world.member.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments/{comment.id}/edit",
        data={"body": "Changed", "_csrf": make_csrf_token(comment_world.member.id)},
    )

    assert response.status_code == 403
    session.expire_all()
    assert session.get(TicketComment, comment.id).body == "Owner comment"


def test_comment_author_can_delete_their_comment(client, comment_world, login_as, session):
    comment = TicketComment(
        ticket_id=comment_world.ticket.id,
        author_id=comment_world.member.id,
        body="Remove me",
    )
    session.add(comment)
    session.commit()
    comment_id = comment.id
    login_as(comment_world.member.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments/{comment_id}/delete",
        data={"_csrf": make_csrf_token(comment_world.member.id)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    session.expire_all()
    assert session.get(TicketComment, comment_id) is None


def test_project_owner_can_delete_another_members_comment(
    client, comment_world, login_as, session
):
    comment = TicketComment(
        ticket_id=comment_world.ticket.id,
        author_id=comment_world.member.id,
        body="Moderate me",
    )
    session.add(comment)
    session.commit()
    comment_id = comment.id
    login_as(comment_world.owner.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments/{comment_id}/delete",
        data={"_csrf": make_csrf_token(comment_world.owner.id)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    session.expire_all()
    assert session.get(TicketComment, comment_id) is None


def test_comment_rejects_a_nonmember_mention_without_writing(
    client, comment_world, make_user, login_as, session
):
    outsider = make_user(email="eve@example.com", name="Eve Outside")
    login_as(comment_world.owner.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments",
        data={
            "body": "Private mention",
            "mention_ids": [outsider.id],
            "_csrf": make_csrf_token(comment_world.owner.id),
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 422
    assert "Mentions must be project members" in response.text
    assert "@htmx:before-swap.stop.camel" in response.text
    assert "$event.detail.shouldSwap = true" in response.text
    assert session.exec(select(TicketComment)).all() == []


def test_invalid_plain_comment_create_renders_the_full_ticket_page(
    client, comment_world, login_as
):
    login_as(comment_world.owner.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments",
        data={"body": "  ", "_csrf": make_csrf_token(comment_world.owner.id)},
    )

    assert response.status_code == 422
    assert "<html" in response.text
    assert 'id="ticket-detail-panel"' in response.text
    assert "Comment cannot be empty" in response.text


def test_invalid_plain_comment_edit_renders_the_full_ticket_page(
    client, comment_world, login_as, session
):
    comment = TicketComment(
        ticket_id=comment_world.ticket.id,
        author_id=comment_world.owner.id,
        body="Original",
    )
    session.add(comment)
    session.commit()
    login_as(comment_world.owner.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments/{comment.id}/edit",
        data={"body": "  ", "_csrf": make_csrf_token(comment_world.owner.id)},
    )

    assert response.status_code == 422
    assert "<html" in response.text
    assert 'id="ticket-detail-panel"' in response.text
    assert "Comment cannot be empty" in response.text


def test_comment_creation_requires_csrf(client, comment_world, login_as, session):
    login_as(comment_world.owner.email)

    response = client.post(
        f"/projects/{comment_world.project.slug}/tickets/1/comments",
        data={"body": "No token"},
    )

    assert response.status_code == 403
    assert session.exec(select(TicketComment)).all() == []


def test_deleting_a_ticket_also_deletes_its_comments(
    client, comment_world, login_as, session
):
    comment = TicketComment(
        ticket_id=comment_world.ticket.id,
        author_id=comment_world.member.id,
        body="Delete with ticket",
    )
    session.add(comment)
    session.commit()
    comment_id = comment.id
    login_as(comment_world.owner.email)

    response = client.delete(f"/api/v1/tickets/{comment_world.ticket.id}")

    assert response.status_code == 204, response.text
    session.expire_all()
    assert session.get(TicketComment, comment_id) is None


def test_deleting_a_project_also_deletes_its_comments(comment_world, session):
    comment = TicketComment(
        ticket_id=comment_world.ticket.id,
        author_id=comment_world.member.id,
        body="Delete with project",
    )
    session.add(comment)
    session.commit()
    comment_id = comment.id
    project = session.get(Project, comment_world.project.id)

    delete_project(session, project, project.slug)

    assert session.get(TicketComment, comment_id) is None
