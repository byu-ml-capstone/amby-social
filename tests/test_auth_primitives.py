import time

from app.auth import (
    DUMMY_HASH,
    SESSION_MAX_AGE,
    _serializer,
    hash_password,
    make_session_cookie,
    read_session_cookie,
    verify_password,
)


def test_password_round_trip():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_dummy_hash_never_verifies():
    assert verify_password("anything at all", DUMMY_HASH) is False


def test_session_cookie_round_trip():
    raw = make_session_cookie("user-123")
    assert read_session_cookie(raw) == "user-123"


def test_tampered_cookie_is_rejected():
    # Flip a character in the payload segment (before the first "."), not the
    # last character of the cookie: the trailing base64 character of the
    # signature only encodes the tail bits of its last byte, so several
    # distinct characters can decode to the same signature bytes, making a
    # last-character flip an intermittent no-op. Tampering the payload always
    # changes the signed content, which the HMAC deterministically rejects.
    raw = make_session_cookie("user-123")
    payload, sep, rest = raw.partition(".")
    tampered_payload = ("x" if payload[0] != "x" else "y") + payload[1:]
    assert read_session_cookie(tampered_payload + sep + rest) is None


def test_expired_cookie_is_rejected(monkeypatch):
    raw = make_session_cookie("user-123")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + SESSION_MAX_AGE + 10)
    assert read_session_cookie(raw) is None


def test_validly_signed_corrupt_payload_is_rejected():
    # A payload that is not valid base64/JSON but is signed with the real secret,
    # so it passes signature verification and fails during deserialization instead
    # (itsdangerous.BadPayload, a sibling of BadSignature, not a subclass of it).
    signer = _serializer.make_signer()
    signed = signer.sign(b"not-valid-base64!!!")
    assert read_session_cookie(signed.decode()) is None
