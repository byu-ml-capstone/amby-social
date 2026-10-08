import pytest
from fastapi import HTTPException
from sqlmodel import Session
from starlette.requests import Request

from app.auth import SESSION_COOKIE, current_user, hash_password, make_session_cookie, optional_user
from app.config import settings
from app.models import User


def test_register_sets_a_session_cookie(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "Ada@Example.com", "password": "hunter22"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == "ada@example.com"
    assert SESSION_COOKIE in response.cookies


def test_duplicate_email_is_rejected_case_insensitively(client):
    payload = {"name": "Ada", "email": "ada@example.com", "password": "hunter22"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    payload["email"] = "ADA@example.com"
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_login_succeeds_and_logout_clears_the_cookie(client, make_user):
    make_user(email="ada@example.com", password="hunter22")
    response = client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": "hunter22"}
    )
    assert response.status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.cookies.get(SESSION_COOKIE) in (None, "")


def test_unknown_email_and_wrong_password_are_indistinguishable(client, make_user):
    make_user(email="ada@example.com", password="hunter22")
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "hunter22"}
    )
    wrong = client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "nope"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_login_rate_limit_blocks_repeated_attempts(client, make_user):
    make_user(email="rate-limit@example.com", password="hunter22")
    payload = {"email": "rate-limit@example.com", "password": "wrong-password"}

    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401

    response = client.post("/api/v1/auth/login", json=payload)

    assert response.status_code == 429
    assert response.headers["retry-after"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_registration_rate_limit_blocks_repeated_attempts(client):
    for index in range(3):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "name": "Bot",
                "email": f"bot-{index}@example.com",
                "password": "hunter22",
            },
        )
        assert response.status_code == 201

    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Bot", "email": "bot-4@example.com", "password": "hunter22"},
    )

    assert response.status_code == 429


def test_short_password_is_rejected(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "ada@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_register_rejects_password_over_72_bytes_of_multibyte_text(client):
    # 30 Korean characters * 3 bytes/char = 90 bytes, but only 30 chars, so it
    # passes Pydantic's character-counting max_length=72 and must be caught by
    # the explicit byte-length validator instead.
    password = "가" * 30
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "ada@example.com", "password": password},
    )
    assert response.status_code == 422


def test_login_rejects_password_over_72_bytes_of_multibyte_text(client, make_user):
    make_user(email="ada@example.com", password="hunter22")
    password = "가" * 30
    response = client.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": password}
    )
    assert response.status_code == 422


def test_current_user_raises_401_for_cookie_naming_a_deleted_user(make_user, session):
    user = make_user(email="ada@example.com", password="hunter22")
    cookie = make_session_cookie(user.id)
    session.delete(session.get(User, user.id))
    session.commit()

    scope = {"type": "http", "headers": [(b"cookie", f"kf_session={cookie}".encode())]}
    request = Request(scope)

    assert optional_user(request, session) is None
    with pytest.raises(HTTPException) as exc_info:
        current_user(optional_user(request, session))
    assert exc_info.value.status_code == 401


def test_concurrent_registration_race_returns_409_not_500(client, engine, monkeypatch):
    # Simulate two concurrent /register calls racing on the same email: a
    # "racer" request commits its own row for the email in the gap between
    # our request's pre-check and its own commit, so our commit hits the
    # real unique constraint and must surface as 409, not an unhandled 500.
    def hash_password_and_insert_racer(password: str) -> str:
        with Session(engine) as racer_session:
            racer_session.add(
                User(
                    name="Racer",
                    email="ada@example.com",
                    password_hash=hash_password(password),
                )
            )
            racer_session.commit()
        return hash_password(password)

    monkeypatch.setattr("app.services.hash_password", hash_password_and_insert_racer)

    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "ada@example.com", "password": "hunter22"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Email already registered"


def test_session_cookie_has_expected_attributes(client, monkeypatch):
    monkeypatch.setattr(settings, "secure_cookies", True)
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "ada@example.com", "password": "hunter22"},
    )
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Secure" in set_cookie


def test_login_is_case_insensitive_on_email(client):
    client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": "ada@example.com", "password": "hunter22"},
    )
    response = client.post(
        "/api/v1/auth/login", json={"email": "ADA@Example.com", "password": "hunter22"}
    )
    assert response.status_code == 200
