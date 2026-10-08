import time

import pytest
from fastapi import Depends, FastAPI, Form, HTTPException
from fastapi.testclient import TestClient

from app.auth import (
    CSRF_FIELD,
    CSRF_MAX_AGE,
    _csrf_serializer,
    current_user,
    make_csrf_token,
    make_session_cookie,
    read_csrf_token,
    read_session_cookie,
    verify_csrf,
)
from app.models import User


def test_csrf_round_trip():
    assert read_csrf_token(make_csrf_token("user-1")) == "user-1"


def test_tampered_csrf_token_returns_none():
    # Tamper the payload segment (before the first "."), not the last
    # character of the token: per R13, the trailing base64 character of an
    # HMAC signature only encodes the tail bits of its last byte, so several
    # distinct characters can decode to the same signature bytes, making a
    # last-character flip an intermittent no-op (~1 run in 25 observed here).
    # Tampering the payload always changes the signed content, which the
    # HMAC deterministically rejects.
    token = make_csrf_token("user-1")
    payload, sep, rest = token.partition(".")
    tampered_payload = ("x" if payload[0] != "x" else "y") + payload[1:]
    assert read_csrf_token(tampered_payload + sep + rest) is None


def test_validly_signed_corrupt_csrf_payload_is_rejected():
    # Mirrors test_auth_primitives.py::test_validly_signed_corrupt_payload_is_rejected.
    # A payload that is not valid base64/JSON but is signed with the real secret
    # passes signature verification and fails during deserialization instead
    # (itsdangerous.BadPayload, a sibling of BadSignature, not a subclass of it,
    # so a bare `except BadSignature` would let this crash as an unhandled 500).
    signer = _csrf_serializer.make_signer()
    signed = signer.sign(b"not-valid-base64!!!")
    assert read_csrf_token(signed.decode()) is None


def test_session_cookie_does_not_validate_as_csrf_token():
    # The session and CSRF serializers use different salts ("kf-session" vs
    # "kf-csrf") specifically so a session cookie can't be replayed as a CSRF
    # token. Prove it rather than just inferring it from the constants.
    cookie = make_session_cookie("user-1")
    assert read_csrf_token(cookie) is None


def test_csrf_token_does_not_validate_as_session_cookie():
    token = make_csrf_token("user-1")
    assert read_session_cookie(token) is None


def test_expired_csrf_token_is_rejected(monkeypatch):
    # CSRF_MAX_AGE is a produced interface value with real expiry-handling
    # logic (read_csrf_token catches SignatureExpired), but the brief's test
    # list doesn't exercise it. Age a real token past CSRF_MAX_AGE via
    # monkeypatching time.time (mirroring test_expired_cookie_is_rejected in
    # test_auth_primitives.py) rather than constructing a token designed to
    # fail some other way.
    token = make_csrf_token("user-1")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + CSRF_MAX_AGE + 10)
    assert read_csrf_token(token) is None


def test_csrf_token_is_bound_to_its_user():
    from app.auth import assert_csrf_matches

    with pytest.raises(HTTPException) as excinfo:
        assert_csrf_matches(make_csrf_token("user-1"), "user-2")
    assert excinfo.value.status_code == 403


def test_missing_csrf_token_is_rejected():
    from app.auth import assert_csrf_matches

    with pytest.raises(HTTPException) as excinfo:
        assert_csrf_matches(None, "user-1")
    assert excinfo.value.status_code == 403


def test_form_body_is_still_readable_by_route_after_verify_csrf_dependency():
    # verify_csrf reads request.form() inside a dependency, before the route
    # handler gets a chance to parse its own form fields. Starlette caches the
    # parsed form on the request the first time it's read, so a second read
    # (here, the route's own `Form(...)` parameter) returns the cached data
    # instead of trying to re-read an already-consumed body stream. This test
    # fails if that caching doesn't hold.
    probe_app = FastAPI()

    @probe_app.post("/echo")
    def echo(_: None = Depends(verify_csrf), name: str = Form(...)):
        return {"name": name}

    fake_user = User(id="user-1", name="Test", email="t@example.com", password_hash="x")
    probe_app.dependency_overrides[current_user] = lambda: fake_user

    token = make_csrf_token("user-1")
    with TestClient(probe_app) as probe_client:
        response = probe_client.post("/echo", data={CSRF_FIELD: token, "name": "hello"})

    assert response.status_code == 200
    assert response.json() == {"name": "hello"}
