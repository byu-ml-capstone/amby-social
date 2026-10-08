import base64
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

import app.gmail as gmail_module
from app.config import Settings
from app.gmail import (
    SCOPE,
    GmailAuthError,
    GmailClient,
    authorize_url,
    decrypt_token,
    encrypt_token,
    gmail_is_configured,
    issue_gmail_state,
    read_gmail_state,
)

REDIRECT = "https://kanban.example.com/integrations/gmail/callback"


@pytest.fixture
def gmail_settings():
    return Settings(
        _env_file=None,
        session_secret="test-session-secret",
        google_client_id="gid",
        google_client_secret="gsecret",
        google_redirect_uri=REDIRECT,
        token_encryption_key=Fernet.generate_key().decode(),
    )


def _client(config, handler):
    return GmailClient(config, httpx.Client(transport=httpx.MockTransport(handler)))


def test_configuration_requires_every_google_setting(gmail_settings):
    assert gmail_is_configured(gmail_settings) is True
    for field in (
        "google_client_id",
        "google_client_secret",
        "google_redirect_uri",
        "token_encryption_key",
    ):
        assert gmail_is_configured(gmail_settings.model_copy(update={field: None})) is False


def test_refresh_token_round_trips_through_encryption(gmail_settings):
    stored = encrypt_token("1//refresh", gmail_settings)
    assert "1//refresh" not in stored
    assert decrypt_token(stored, gmail_settings) == "1//refresh"


def test_authorize_url_requests_offline_readonly_access(gmail_settings):
    url = authorize_url("signed-state", gmail_settings)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert parse_qs(urlparse(url).query) == {
        "client_id": ["gid"],
        "redirect_uri": [REDIRECT],
        "response_type": ["code"],
        "scope": [SCOPE],
        "access_type": ["offline"],
        "prompt": ["consent"],
        "state": ["signed-state"],
    }
    assert SCOPE == "https://www.googleapis.com/auth/gmail.readonly"


def test_state_is_bound_to_the_user():
    state = issue_gmail_state("project-1", "user-1")
    assert read_gmail_state(state, "user-1") == "project-1"
    with pytest.raises(HTTPException) as other_user:
        read_gmail_state(state, "user-2")
    assert other_user.value.status_code == 403
    with pytest.raises(HTTPException) as tampered:
        read_gmail_state(state + "x", "user-1")
    assert tampered.value.status_code == 400


def test_expired_state_is_rejected(monkeypatch):
    state = issue_gmail_state("project-1", "user-1")
    monkeypatch.setattr(gmail_module, "_STATE_MAX_AGE", -1)
    with pytest.raises(HTTPException) as expired:
        read_gmail_state(state, "user-1")
    assert expired.value.status_code == 400


def test_exchange_code_returns_access_and_refresh_tokens(gmail_settings):
    def handler(request):
        assert request.url == "https://oauth2.googleapis.com/token"
        assert parse_qs(request.content.decode()) == {
            "code": ["auth-code"],
            "redirect_uri": [REDIRECT],
            "grant_type": ["authorization_code"],
            "client_id": ["gid"],
            "client_secret": ["gsecret"],
        }
        return httpx.Response(
            200,
            json={"access_token": "ya29.access", "refresh_token": "1//refresh", "expires_in": 3599},
        )

    with _client(gmail_settings, handler) as gmail:
        assert gmail.exchange_code("auth-code") == ("ya29.access", "1//refresh")


def test_exchange_code_without_refresh_token_is_rejected(gmail_settings):
    def handler(request):
        return httpx.Response(200, json={"access_token": "ya29.access"})

    with _client(gmail_settings, handler) as gmail, pytest.raises(ValueError):
        gmail.exchange_code("auth-code")


def test_access_token_uses_the_refresh_grant(gmail_settings):
    def handler(request):
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == ["1//refresh"]
        return httpx.Response(200, json={"access_token": "ya29.fresh", "expires_in": 3599})

    with _client(gmail_settings, handler) as gmail:
        assert gmail.access_token("1//refresh") == ("ya29.fresh", 3599)


def test_invalid_grant_raises_auth_error(gmail_settings):
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with _client(gmail_settings, handler) as gmail, pytest.raises(GmailAuthError):
        gmail.access_token("1//revoked")


def test_other_token_failures_raise_http_errors(gmail_settings):
    def handler(request):
        return httpx.Response(503, json={"error": "backend_error"})

    with _client(gmail_settings, handler) as gmail, pytest.raises(httpx.HTTPStatusError):
        gmail.access_token("1//refresh")


def test_history_follows_pages_and_returns_latest_history_id(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/history"
        assert request.headers["Authorization"] == "Bearer ya29.access"
        assert request.url.params["startHistoryId"] == "100"
        assert request.url.params.get_list("historyTypes") == ["messageAdded", "labelAdded"]
        if request.url.params.get("pageToken") == "p2":
            return httpx.Response(200, json={"history": [{"id": "102"}], "historyId": "106"})
        return httpx.Response(
            200,
            json={"history": [{"id": "101"}], "nextPageToken": "p2", "historyId": "105"},
        )

    with _client(gmail_settings, handler) as gmail:
        assert gmail.history("ya29.access", "100") == ([{"id": "101"}, {"id": "102"}], "106")


def test_history_without_changes(gmail_settings):
    def handler(request):
        return httpx.Response(200, json={"historyId": "100"})

    with _client(gmail_settings, handler) as gmail:
        assert gmail.history("ya29.access", "100") == ([], "100")


def test_expired_history_raises_404(gmail_settings):
    def handler(request):
        return httpx.Response(404, json={"error": {"code": 404}})

    with _client(gmail_settings, handler) as gmail, pytest.raises(httpx.HTTPStatusError) as exc:
        gmail.history("ya29.access", "1")
    assert exc.value.response.status_code == 404


def test_labels_returns_only_user_labels(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/labels"
        return httpx.Response(
            200,
            json={
                "labels": [
                    {"id": "INBOX", "name": "INBOX", "type": "system"},
                    {"id": "Label_1", "name": "Flyers", "type": "user"},
                ]
            },
        )

    with _client(gmail_settings, handler) as gmail:
        assert gmail.labels("ya29.access") == [{"id": "Label_1", "name": "Flyers", "type": "user"}]


def test_recent_label_messages_filters_by_label_and_age(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/messages"
        assert request.url.params["labelIds"] == "Label_1"
        assert request.url.params["q"] == "newer_than:2d"
        if request.url.params.get("pageToken") == "p2":
            return httpx.Response(200, json={"resultSizeEstimate": 0})
        return httpx.Response(
            200,
            json={"messages": [{"id": "m1", "threadId": "m1"}], "nextPageToken": "p2"},
        )

    with _client(gmail_settings, handler) as gmail:
        assert gmail.recent_label_messages("ya29.access", "Label_1") == [
            {"id": "m1", "threadId": "m1"}
        ]


def test_message_fetches_full_format(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/messages/m1"
        assert request.url.params["format"] == "full"
        return httpx.Response(200, json={"id": "m1"})

    with _client(gmail_settings, handler) as gmail:
        assert gmail.message("ya29.access", "m1") == {"id": "m1"}


def test_revoke_posts_the_refresh_token(gmail_settings):
    def handler(request):
        assert request.url == "https://oauth2.googleapis.com/revoke"
        assert parse_qs(request.content.decode()) == {"token": ["1//refresh"]}
        return httpx.Response(200)

    with _client(gmail_settings, handler) as gmail:
        gmail.revoke("1//refresh")


def test_attachment_downloads_and_decodes_the_data(gmail_settings):
    def handler(request):
        assert request.url.path == "/gmail/v1/users/me/messages/m1/attachments/att-1"
        assert request.headers["Authorization"] == "Bearer ya29.access"
        data = base64.urlsafe_b64encode(b"%PDF-1.4 brief").decode().rstrip("=")
        return httpx.Response(200, json={"size": 14, "data": data})

    with _client(gmail_settings, handler) as gmail:
        assert gmail.attachment("ya29.access", "m1", "att-1") == b"%PDF-1.4 brief"
