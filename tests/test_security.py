import secrets

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings


def _production_settings(**overrides):
    values = {
        "environment": "production",
        "session_secret": secrets.token_urlsafe(32),
        "secure_cookies": True,
        "allowed_origins": ["https://kanban.example.com"],
        "allowed_hosts": ["kanban.example.com"],
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_secret": "dev-insecure-secret-change-me"},
        {"session_secret": "a" * 32},
        {"session_secret": "0123456789abcdef" * 2},
        {"secure_cookies": False},
        {"allowed_origins": ["http://kanban.example.com"]},
        {"allowed_hosts": ["*.example.com"]},
    ],
)
def test_production_settings_reject_insecure_values(overrides):
    with pytest.raises(ValidationError):
        _production_settings(**overrides)


def test_production_settings_accept_secure_values():
    assert _production_settings().environment == "production"


def test_responses_include_baseline_security_headers(client):
    response = client.get("/health")

    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["referrer-policy"] == "same-origin"


def test_unknown_host_is_rejected(client):
    response = client.get("/health", headers={"host": "attacker.example"})

    assert response.status_code == 400


def test_origin_rejection_includes_security_headers(client):
    client.cookies.set("kf_session", "invalid")
    response = client.post("/api/v1/auth/logout", headers={"origin": "https://attacker.example"})

    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"


_GMAIL = {
    "google_client_id": "gid",
    "google_client_secret": "gsecret",
    "google_redirect_uri": "https://kanban.example.com/integrations/gmail/callback",
}


@pytest.mark.parametrize(
    "overrides",
    [
        _GMAIL,
        {**_GMAIL, "token_encryption_key": "not-a-fernet-key"},
        {
            **_GMAIL,
            "token_encryption_key": Fernet.generate_key().decode(),
            "google_redirect_uri": "http://kanban.example.com/integrations/gmail/callback",
        },
    ],
)
def test_production_gmail_settings_reject_unsafe_values(overrides):
    with pytest.raises(ValidationError):
        _production_settings(**overrides)


def test_production_gmail_settings_accept_a_fernet_key():
    config = _production_settings(**_GMAIL, token_encryption_key=Fernet.generate_key().decode())
    assert config.google_client_id == "gid"


def test_production_without_gmail_needs_no_encryption_key():
    assert _production_settings().token_encryption_key is None
