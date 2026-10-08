import httpx
import pytest

import app.notifications as notifications
from app.models import WebhookType
from app.notifications import BACKOFF_SECONDS, dispatch

PAYLOAD = {
    "event": "TICKET_CREATED",
    "project_id": "p1",
    "project_name": "P",
    "project_slug": "p",
    "ticket_number": 42,
    "title": "T",
    "status": "BACKLOG",
    "type": "BUG",
    "priority": "HIGH",
    "assignee_id": None,
}


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("app.notifications.time.sleep", lambda seconds: slept.append(seconds))
    return slept


def test_successful_delivery_posts_once(no_sleep):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)
        assert not client.is_closed

    assert len(calls) == 1
    assert no_sleep == []


def test_delivery_rechecks_dns_and_blocks_private_address(no_sleep, monkeypatch):
    monkeypatch.setattr(
        "app.services.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)

    assert calls == []
    assert no_sleep == []


def test_failure_retries_three_times_then_warns(no_sleep, caplog):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("black hole", request=request)

    with caplog.at_level("WARNING", logger="app.notifications"):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)
            assert not client.is_closed

    assert attempts["n"] == 4
    assert no_sleep == list(BACKOFF_SECONDS)
    assert len(caplog.records) == 1
    assert "p1" in caplog.text and "42" in caplog.text


def test_server_error_is_retried(no_sleep):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500 if attempts["n"] < 3 else 200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)

    assert attempts["n"] == 3


def test_dispatch_is_a_noop_without_a_url(no_sleep):
    dispatch(WebhookType.NONE, "", PAYLOAD)


def test_dispatch_is_a_noop_with_empty_url_for_a_real_type(no_sleep):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dispatch(WebhookType.SLACK, "", PAYLOAD, client=client)

    assert calls == []
    assert no_sleep == []


def test_timeout_is_retried_then_warns(no_sleep, caplog):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.TimeoutException("too slow", request=request)

    with caplog.at_level("WARNING", logger="app.notifications"):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)

    assert attempts["n"] == 4
    assert no_sleep == list(BACKOFF_SECONDS)
    assert len(caplog.records) == 1
    assert "p1" in caplog.text and "42" in caplog.text


def test_successful_delivery_emits_no_warning(no_sleep, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    with caplog.at_level("WARNING", logger="app.notifications"):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)

    assert caplog.records == []


def test_client_error_response_is_retried_then_warns(no_sleep, caplog):
    # A permanently misconfigured webhook URL (e.g. a deleted Slack hook) returns
    # 404/410 on every attempt. Per spec, only 2xx/3xx stops immediately, so a 4xx
    # is treated the same as a transient 5xx: it retries through the full backoff
    # schedule and then warns exactly once. See self-review notes in the report.
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(404)

    with caplog.at_level("WARNING", logger="app.notifications"):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD, client=client)
            assert not client.is_closed

    assert attempts["n"] == 4
    assert no_sleep == list(BACKOFF_SECONDS)
    assert len(caplog.records) == 1


def test_malformed_payload_does_not_raise(no_sleep, caplog):
    # PAYLOAD without "project_id": the formatter doesn't need it, so delivery
    # proceeds normally, but the final failure-warning line used to index it
    # with payload["project_id"] (no .get) and would raise a bare KeyError
    # with nothing to catch it. That must not escape dispatch.
    malformed = {k: v for k, v in PAYLOAD.items() if k != "project_id"}

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("black hole", request=request)

    with caplog.at_level("WARNING", logger="app.notifications"):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            dispatch(WebhookType.SLACK, "https://example.com/hook", malformed, client=client)
            assert not client.is_closed

    assert no_sleep == list(BACKOFF_SECONDS)
    assert len(caplog.records) == 1
    assert "42" in caplog.text


def test_formatter_exception_does_not_raise(no_sleep, caplog, monkeypatch):
    def raising_formatter(payload: dict) -> dict:
        raise RuntimeError("formatter bug")

    monkeypatch.setitem(notifications.FORMATTERS, WebhookType.SLACK, raising_formatter)

    with caplog.at_level("WARNING", logger="app.notifications"):
        dispatch(WebhookType.SLACK, "https://example.com/hook", PAYLOAD)

    assert no_sleep == []
    assert len(caplog.records) == 1
    assert "p1" in caplog.text and "42" in caplog.text
