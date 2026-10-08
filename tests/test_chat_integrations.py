import socket

import pytest
from fastapi import HTTPException

from app.models import WebhookType
from app.services import (
    chat_webhooks,
    delete_project,
    disconnect_chat_webhook,
    set_chat_webhook,
)


def test_project_can_store_each_chat_provider(session, make_user, make_project):
    project = make_project(make_user(email="chat-owner@example.com"))
    set_chat_webhook(session, project, WebhookType.SLACK, "https://hooks.example.test/slack")
    set_chat_webhook(session, project, WebhookType.DISCORD, "https://hooks.example.test/discord")
    assert [(row.provider, row.project_id) for row in chat_webhooks(session, project.id)] == [
        (WebhookType.DISCORD, project.id),
        (WebhookType.SLACK, project.id),
    ]


@pytest.mark.parametrize(
    ("url", "email"),
    [
        ("https://127.0.0.1/hook", "loopback@example.test"),
        ("https://10.0.0.1/hook", "private@example.test"),
        ("https://169.254.1.1/hook", "linklocal@example.test"),
        ("https://224.0.0.1/hook", "multicast@example.test"),
        ("https://240.0.0.1/hook", "reserved@example.test"),
        ("https://0.0.0.0/hook", "unspecified@example.test"),
        ("https://127.1/hook", "abbreviated@example.test"),
        ("https://0x7f000001/hook", "hex@example.test"),
        ("https://2130706433/hook", "integer@example.test"),
        ("https://0177.0.0.1/hook", "octal@example.test"),
    ],
)
def test_chat_webhook_rejects_disallowed_literal_ip(session, make_user, make_project, url, email):
    owner = make_user(email=email)
    project = make_project(owner)
    with pytest.raises(HTTPException, match="safe https URL"):
        set_chat_webhook(session, project, WebhookType.SLACK, url)


def test_deleting_project_deletes_chat_webhooks(session, make_user, make_project):
    project = make_project(make_user(email="delete-owner@example.com"))
    set_chat_webhook(session, project, WebhookType.SLACK, "https://hooks.example.test/x")
    delete_project(session, project, project.slug)
    assert chat_webhooks(session, project.id) == []


def test_replacing_provider_keeps_one_row(session, make_user, make_project):
    project = make_project(make_user(email="replace-owner@example.com"))
    first = set_chat_webhook(
        session, project, WebhookType.TEAMS, "https://hooks.example.test/first"
    )
    second = set_chat_webhook(
        session, project, WebhookType.TEAMS, "https://hooks.example.test/second"
    )

    rows = chat_webhooks(session, project.id)
    assert len(rows) == 1
    assert (second.id, rows[0].url) == (first.id, "https://hooks.example.test/second")


def test_disconnect_is_idempotent(session, make_user, make_project):
    project = make_project(make_user(email="disconnect-owner@example.com"))
    set_chat_webhook(session, project, WebhookType.DISCORD, "https://hooks.example.test/x")

    disconnect_chat_webhook(session, project, WebhookType.DISCORD)
    disconnect_chat_webhook(session, project, WebhookType.DISCORD)

    assert chat_webhooks(session, project.id) == []


def test_none_provider_is_rejected(session, make_user, make_project):
    project = make_project(make_user(email="none-owner@example.com"))
    with pytest.raises(HTTPException, match="provider"):
        set_chat_webhook(session, project, WebhookType.NONE, "https://hooks.example.test/x")


def test_url_over_500_is_rejected(session, make_user, make_project):
    project = make_project(make_user(email="long-url-owner@example.com"))
    with pytest.raises(HTTPException, match="500"):
        set_chat_webhook(session, project, WebhookType.SLACK, "https://x.test/" + "a" * 500)


def test_http_url_is_rejected(session, make_user, make_project):
    project = make_project(make_user(email="http-owner@example.com"))
    with pytest.raises(HTTPException, match="safe https URL"):
        set_chat_webhook(session, project, WebhookType.SLACK, "http://hooks.example.test/x")


def test_chat_webhook_rejects_hostname_resolving_to_private_ip(
    session, make_user, make_project, monkeypatch
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    project = make_project(make_user(email="hostname-owner@example.com"))

    with pytest.raises(HTTPException, match="safe https URL"):
        set_chat_webhook(session, project, WebhookType.SLACK, "https://hooks.example.test/x")


def test_chat_webhook_accepts_hostname_resolving_to_public_ip(
    session, make_user, make_project, monkeypatch
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    project = make_project(make_user(email="public-hostname-owner@example.com"))

    webhook = set_chat_webhook(session, project, WebhookType.SLACK, "https://hooks.example.test/x")
    assert webhook.url == "https://hooks.example.test/x"


def test_chat_webhook_rejects_unapproved_public_hostname(
    session, make_user, make_project, monkeypatch
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    project = make_project(make_user(email="unapproved-hostname-owner@example.com"))

    with pytest.raises(HTTPException, match="approved webhook host"):
        set_chat_webhook(session, project, WebhookType.SLACK, "https://attacker.example.org/x")
