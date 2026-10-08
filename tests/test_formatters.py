import json

from app.models import WebhookType
from app.notifications import (
    EVENT_COMMENT_MENTION,
    EVENT_TICKET_CREATED,
    FORMATTERS,
    build_payload,
    format_discord,
    format_slack,
    format_teams,
)
from app.services import create_ticket

PAYLOAD = {
    "event": EVENT_TICKET_CREATED,
    "project_id": "p1",
    "project_name": "Payment Gateway",
    "project_slug": "payment-gateway",
    "ticket_number": 42,
    "title": "Card declines on retry",
    "status": "BACKLOG",
    "type": "BUG",
    "priority": "HIGH",
    "assignee_id": None,
}


def test_every_webhook_type_except_none_has_a_formatter():
    assert set(FORMATTERS) == {WebhookType.SLACK, WebhookType.DISCORD, WebhookType.TEAMS}


def test_slack_payload_has_blocks_and_mentions_the_ticket():
    body = format_slack(PAYLOAD)
    assert "blocks" in body
    assert "#42" in json.dumps(body)


def test_discord_payload_has_embeds():
    body = format_discord(PAYLOAD)
    assert body["embeds"][0]["title"].startswith("#42")


def test_teams_payload_is_an_adaptive_card():
    body = format_teams(PAYLOAD)
    assert body["type"] == "message"
    assert body["attachments"][0]["contentType"] == ("application/vnd.microsoft.card.adaptive")
    assert "#42" in json.dumps(body)


def test_formatters_are_json_serializable():
    for formatter in FORMATTERS.values():
        json.dumps(formatter(PAYLOAD))


def test_comment_mention_names_are_visible_in_every_webhook():
    payload = {
        **PAYLOAD,
        "event": EVENT_COMMENT_MENTION,
        "author_name": "Ada Lovelace",
        "mentioned_names": ["Bob Builder"],
        "comment_excerpt": "Please review",
    }

    for formatter in FORMATTERS.values():
        body = json.dumps(formatter(payload))
        assert "Ada Lovelace" in body
        assert "Bob Builder" in body


def test_comment_mentions_cannot_trigger_native_slack_or_discord_pings():
    payload = {
        **PAYLOAD,
        "event": EVENT_COMMENT_MENTION,
        "author_name": "Ada <!channel>",
        "mentioned_names": ["Bob <@U123>"],
        "comment_excerpt": "@everyone <!here>",
    }

    slack = json.dumps(format_slack(payload))
    assert "<!channel>" not in slack
    assert "<@U123>" not in slack
    assert "<!here>" not in slack
    assert format_discord(payload)["allowed_mentions"] == {"parse": []}


def test_unassigned_ticket_renders_sensibly_in_every_formatter():
    for formatter in FORMATTERS.values():
        text = json.dumps(formatter(PAYLOAD))
        assert "None" not in text


def test_build_payload_is_a_flat_orm_free_json_serializable_dict(session, make_user, make_project):
    owner = make_user()
    project = make_project(owner)
    ticket = create_ticket(session, project, owner, title="Card declines on retry")

    payload = build_payload(project, EVENT_TICKET_CREATED, ticket)

    assert payload["assignee_id"] is None
    for value in payload.values():
        # Exact type check, not isinstance: every enum in app.models is a StrEnum,
        # i.e. a subclass of str, so isinstance(value, str) would also accept a raw
        # enum member (e.g. ticket.status instead of ticket.status.value) and this
        # guard would stop catching the regression it exists to catch.
        assert type(value) is str or type(value) is int or value is None
    json.dumps(payload)


def test_slack_escapes_ticket_title_from_outside_senders():
    body = json.dumps(format_slack({**PAYLOAD, "title": "<!channel> <https://evil|ok>"}))
    assert "<!channel>" not in body
    assert "&lt;!channel&gt;" in body
